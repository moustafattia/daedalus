from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass
from typing import Any

from engine.lifecycle import clear_work_entries, mark_running_work, schedule_retry_entry
from engine.scheduler import retry_due_at
from engine.storage import load_optional_json as _load_optional_json
from engine.work_items import work_item_from_issue
from runtimes.types import PromptRunResult
from workflows.issue_runner.config import (
    max_retry_backoff_ms_from_config,
    poll_interval_seconds_from_config,
    scheduler_state_from_config as _typed_scheduler_state_from_config,
)
from workflows.issue_runner.tracker import eligible_issues, issue_session_name


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_epoch() -> float:
    return time.time()


def scheduler_state_from_config(config: dict[str, Any]) -> dict[str, Any]:
    return _typed_scheduler_state_from_config(config)


@dataclass
class IssueRunnerOrchestrator:
    """Decision authority for issue-runner scheduling state transitions.

    The workspace remains the composition root for persistence, runtime,
    tracker, and filesystem helpers. This object owns the orchestration flow:
    select work, mark it running, reconcile workers, apply outcomes, and queue
    retries.
    """

    workspace: Any

    def _poll_interval_seconds(self, override: int | None) -> int:
        if override is not None:
            return max(int(override), 1)
        return poll_interval_seconds_from_config(self.workspace.config)

    def _dispatch_slots(self) -> int:
        w = self.workspace
        scheduler = scheduler_state_from_config(w.config)
        running_count = len(w.running_entries)
        return max(int(scheduler["max_concurrent_agents"]) - running_count, 0)

    def select_issue_batch(
        self,
        *,
        issues: list[dict[str, Any]],
        issues_by_id: dict[str, dict[str, Any]],
    ) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
        w = self.workspace
        slots = self._dispatch_slots()
        if slots <= 0:
            return []

        tracker_cfg = w.config.get("tracker") or {}
        scheduler = scheduler_state_from_config(w.config)
        state_limits = dict(scheduler["max_concurrent_agents_by_state"])
        pending_retry_ids = {
            issue_id
            for issue_id, entry in w.retry_entries.items()
            if retry_due_at(entry, default=0.0) > _now_epoch()
        }
        running_ids = set(w.running_entries)
        selected: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        selected_ids: set[str] = set()
        state_counts: dict[str, int] = {}

        def can_take(issue: dict[str, Any]) -> bool:
            issue_id = str(issue.get("id") or "").strip()
            if not issue_id or issue_id in selected_ids or issue_id in running_ids:
                return False
            if issue_id in pending_retry_ids:
                return False
            state = str(issue.get("state") or "").strip().lower()
            limit = state_limits.get(state)
            if limit is not None and state_counts.get(state, 0) >= limit:
                return False
            return True

        due_retry_entries = sorted(
            [
                (issue_id, entry)
                for issue_id, entry in w.retry_entries.items()
                if retry_due_at(entry, default=0.0) <= _now_epoch()
            ],
            key=lambda item: (
                retry_due_at(item[1], default=0.0),
                int((item[1] or {}).get("attempt") or 0),
                str((item[1] or {}).get("identifier") or item[0]),
            ),
        )
        for issue_id, retry_entry in due_retry_entries:
            if len(selected) >= slots:
                break
            issue = issues_by_id.get(issue_id)
            if issue is None:
                w.retry_entries.pop(issue_id, None)
                continue
            if not can_take(issue):
                continue
            selected.append((issue, retry_entry))
            selected_ids.add(issue_id)
            state = str(issue.get("state") or "").strip().lower()
            state_counts[state] = state_counts.get(state, 0) + 1

        if len(selected) >= slots:
            return selected

        for issue in eligible_issues(tracker_cfg=tracker_cfg, issues=issues):
            if len(selected) >= slots:
                break
            if not can_take(issue):
                continue
            issue_id = str(issue.get("id") or "").strip()
            selected.append((issue, w.retry_entries.get(issue_id)))
            selected_ids.add(issue_id)
            state = str(issue.get("state") or "").strip().lower()
            state_counts[state] = state_counts.get(state, 0) + 1
        return selected

    def issue_attempt(self, *, issue: dict[str, Any], retry_entry: dict[str, Any] | None) -> int:
        if retry_entry is not None:
            return int(retry_entry.get("attempt") or 0) + 1
        last_run = (_load_optional_json(self.workspace.status_path) or {}).get("lastRun") or {}
        if (last_run.get("issue") or {}).get("id") == issue.get("id"):
            return int(last_run.get("attempt") or 0) + 1
        return 1

    def mark_running(
        self,
        selections: list[tuple[dict[str, Any], dict[str, Any] | None]],
        *,
        run_id: str | None = None,
    ) -> None:
        w = self.workspace
        now_epoch = _now_epoch()
        tracker_kind = str((w.config.get("tracker") or {}).get("kind") or "tracker")
        w.running_entries = mark_running_work(
            w.running_entries,
            work_items=[
                (
                    work_item_from_issue(issue, source=tracker_kind),
                    self.issue_attempt(issue=issue, retry_entry=retry_entry),
                )
                for issue, retry_entry in selections
                if str(issue.get("id") or "").strip()
            ],
            now_epoch=now_epoch,
        )
        if run_id:
            for issue, _retry_entry in selections:
                issue_id = str(issue.get("id") or "").strip()
                if issue_id in w.running_entries:
                    w.running_entries[issue_id]["run_id"] = run_id
        w.running_issue_id = next(iter(w.running_entries), None)
        w._persist_scheduler_state()

    def clear_running(self, issue_ids: list[str]) -> None:
        w = self.workspace
        w.running_entries = clear_work_entries(w.running_entries, issue_ids)
        w.running_issue_id = next(iter(w.running_entries), None)

    def schedule_retry(
        self,
        *,
        issue: dict[str, Any],
        error: str,
        current_attempt: int | None,
        delay_type: str = "failure",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        w = self.workspace
        max_backoff_ms = max_retry_backoff_ms_from_config(w.config)
        work_item = work_item_from_issue(issue, source=str((w.config.get("tracker") or {}).get("kind") or "tracker"))
        entry, retry = schedule_retry_entry(
            work_item=work_item,
            existing_entry=w.retry_entries.get(work_item.id),
            error=error,
            current_attempt=current_attempt,
            delay_type=delay_type,
            max_backoff_ms=max_backoff_ms,
            now_epoch=_now_epoch(),
        )
        if run_id:
            entry["run_id"] = run_id
            retry["run_id"] = run_id
        w.retry_entries[work_item.id] = entry
        w._emit_event(
            "issue_runner.retry.scheduled",
            {
                **retry,
                "error": error,
                "run_id": run_id,
            },
        )
        return retry

    def clear_retry(self, issue_id: str | None) -> None:
        self.workspace.retry_entries = clear_work_entries(self.workspace.retry_entries, [issue_id])

    def apply_issue_results(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        w = self.workspace
        applied: list[dict[str, Any]] = []
        for result in results:
            issue = result.get("issue") or {}
            issue_id = str(issue.get("id") or "")
            run_id = result.get("run_id") or result.get("runId")
            metrics = result.get("metrics") or {}
            if metrics:
                recorded_metrics = w._record_metrics(
                    PromptRunResult(
                        output="",
                        session_id=metrics.get("session_id"),
                        thread_id=metrics.get("thread_id"),
                        turn_id=metrics.get("turn_id"),
                        last_event=metrics.get("last_event"),
                        last_message=metrics.get("last_message"),
                        turn_count=int(metrics.get("turn_count") or 0),
                        tokens=metrics.get("tokens"),
                        rate_limits=metrics.get("rate_limits"),
                    )
                )
                w._record_runtime_session(
                    issue=issue,
                    session_name=issue_session_name(issue),
                    metrics=recorded_metrics,
                    runtime_name=result.get("runtime"),
                    runtime_kind=result.get("runtimeKind"),
                    run_id=run_id,
                )
                result["metrics"] = recorded_metrics

            if result.get("ok"):
                self.clear_retry(issue_id)
                if result.get("suppressRetry"):
                    result["retry"] = None
                else:
                    retry = self.schedule_retry(
                        issue=issue,
                        error="continuation",
                        current_attempt=result.get("attempt"),
                        delay_type="continuation",
                        run_id=run_id,
                    )
                    result["retry"] = retry
                w._emit_event(
                    "issue_runner.tick.completed",
                    {
                        "issue_id": issue.get("id"),
                        "attempt": result.get("attempt"),
                        "workspace": result.get("workspace"),
                        "output_path": result.get("outputPath"),
                        "run_id": run_id,
                        "continuation_retry_attempt": (result.get("retry") or {}).get("retry_attempt"),
                        "continuation_retry_delay_ms": (result.get("retry") or {}).get("delay_ms"),
                    },
                )
            else:
                if result.get("suppressRetry"):
                    result["retry"] = None
                    w._emit_event(
                        "issue_runner.tick.canceled",
                        {
                            "issue_id": issue.get("id"),
                            "attempt": result.get("attempt"),
                            "workspace": result.get("workspace"),
                            "error": result.get("error"),
                            "run_id": run_id,
                            "retry_suppressed": True,
                        },
                    )
                else:
                    retry = self.schedule_retry(
                        issue=issue,
                        error=str(result.get("error") or "issue execution failed"),
                        current_attempt=result.get("attempt"),
                        run_id=run_id,
                    )
                    result["retry"] = retry
                    w._emit_event(
                        "issue_runner.tick.failed",
                        {
                            "issue_id": issue.get("id"),
                            "attempt": result.get("attempt"),
                            "workspace": result.get("workspace"),
                            "error": result.get("error"),
                            "run_id": run_id,
                            "retry_attempt": retry.get("retry_attempt"),
                            "retry_delay_ms": retry.get("delay_ms"),
                        },
                    )
            applied.append(result)
        return applied

    def status_from_results(
        self,
        *,
        base_status: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        results.sort(
            key=lambda item: str(
                ((item.get("issue") or {}).get("identifier"))
                or ((item.get("issue") or {}).get("id"))
                or ""
            )
        )
        applied = self.apply_issue_results(results)
        tick_metrics = [result.get("metrics") or {} for result in applied if result.get("metrics")]
        first = applied[0] if applied else {}
        base_status.update(
            {
                "ok": all(result.get("ok") or result.get("suppressRetry") for result in applied),
                "attempt": first.get("attempt"),
                "outputPath": first.get("outputPath"),
                "workspace": first.get("workspace"),
                "createdWorkspace": first.get("createdWorkspace"),
                "hookResults": first.get("hookResults"),
                "results": applied,
                "metrics": self.workspace._aggregate_metrics(tick_metrics),
            }
        )
        failed = next((result for result in applied if not result.get("ok") and not result.get("suppressRetry")), None)
        if failed:
            base_status["error"] = failed.get("error")
            base_status["retry"] = failed.get("retry")
        return base_status

    def request_supervised_cancel(self, issue_id: str, *, reason: str) -> bool:
        w = self.workspace
        if not issue_id or issue_id not in w.running_entries:
            return False
        entry = w.running_entries[issue_id]
        if entry.get("cancel_requested"):
            return False
        entry["cancel_requested"] = True
        entry["cancel_reason"] = reason
        entry["worker_status"] = "canceling"
        entry["heartbeat_at_epoch"] = _now_epoch()
        event = w._supervisor_cancel_events.get(issue_id)
        if event is not None:
            event.set()
        future = w._supervisor_futures.get(issue_id)
        if future is not None:
            future.cancel()
        w._emit_event(
            "issue_runner.worker.cancel_requested",
            {
                "issue_id": issue_id,
                "identifier": entry.get("identifier"),
                "reason": reason,
                "worker_id": entry.get("worker_id"),
                "run_id": entry.get("run_id") or entry.get("runId"),
            },
        )
        w._persist_scheduler_state()
        return True

    def request_terminal_cancellations(self, terminal_issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        w = self.workspace
        requested = []
        for issue in terminal_issues:
            issue_id = str(issue.get("id") or "").strip()
            if not issue_id or issue_id not in w.running_entries:
                continue
            if self.request_supervised_cancel(issue_id, reason="terminal-state"):
                requested.append(
                    {
                        "issue_id": issue_id,
                        "identifier": issue.get("identifier"),
                        "reason": "terminal-state",
                    }
                )
        return requested

    def reconcile_supervised_workers(self) -> list[dict[str, Any]]:
        w = self.workspace
        completed: list[dict[str, Any]] = []
        for issue_id, future in list(w._supervisor_futures.items()):
            if not future.done():
                entry = w.running_entries.get(issue_id)
                if entry is not None:
                    entry["heartbeat_at_epoch"] = _now_epoch()
                continue
            w._supervisor_futures.pop(issue_id, None)
            w._supervisor_cancel_events.pop(issue_id, None)
            entry = w.running_entries.get(issue_id) or {}
            if future.cancelled():
                result = w._cancel_result(
                    issue={
                        "id": issue_id,
                        "identifier": entry.get("identifier"),
                        "state": entry.get("state"),
                    },
                    attempt=int(entry.get("attempt") or 0),
                    reason=str(entry.get("cancel_reason") or "canceled"),
                    run_id=entry.get("run_id") or entry.get("runId"),
                )
            else:
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "ok": False,
                        "issue": {
                            "id": issue_id,
                            "identifier": entry.get("identifier"),
                            "state": entry.get("state"),
                        },
                        "attempt": int(entry.get("attempt") or 0),
                        "workspace": None,
                        "createdWorkspace": False,
                        "hookResults": [],
                        "outputPath": None,
                        "error": f"{type(exc).__name__}: {exc}",
                        "metrics": {},
                        "runtime": None,
                        "runtimeKind": None,
                        "runId": entry.get("run_id") or entry.get("runId"),
                    }
            if entry.get("cancel_requested"):
                result["cancelRequested"] = True
                result["cancelReason"] = entry.get("cancel_reason")
                if entry.get("cancel_reason") == "terminal-state":
                    result["suppressRetry"] = True
            metrics = result.get("metrics") or {}
            if metrics.get("thread_id"):
                entry["thread_id"] = metrics.get("thread_id")
            if metrics.get("turn_id"):
                entry["turn_id"] = metrics.get("turn_id")
            entry["worker_status"] = "completed" if result.get("ok") else ("canceled" if result.get("canceled") else "failed")
            entry["heartbeat_at_epoch"] = _now_epoch()
            self.clear_running([issue_id])
            completed.append(result)
            w._emit_event(
                "issue_runner.worker.completed",
                {
                    "issue_id": issue_id,
                    "identifier": (result.get("issue") or {}).get("identifier") or entry.get("identifier"),
                    "worker_id": entry.get("worker_id"),
                    "run_id": entry.get("run_id") or entry.get("runId") or result.get("runId"),
                    "ok": result.get("ok"),
                    "canceled": result.get("canceled"),
                    "error": result.get("error"),
                },
            )
        if completed:
            w._persist_scheduler_state()
        return completed

    def dispatch_supervised_workers(
        self,
        selections: list[tuple[dict[str, Any], dict[str, Any] | None]],
        *,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        w = self.workspace
        if not selections:
            return []
        executor = w._ensure_supervisor_executor()
        self.mark_running(selections, run_id=run_id)
        dispatched: list[dict[str, Any]] = []
        for issue, retry_entry in selections:
            issue_id = str(issue.get("id") or "").strip()
            if not issue_id:
                continue
            cancel_event = threading.Event()
            w._supervisor_cancel_events[issue_id] = cancel_event
            future = executor.submit(
                w._execute_issue,
                issue=issue,
                retry_entry=retry_entry,
                cancel_event=cancel_event,
                run_id=run_id,
            )
            w._supervisor_futures[issue_id] = future
            entry = w.running_entries.get(issue_id) or {}
            resume_session_id = w._runtime_session_id_for_issue(issue)
            if resume_session_id:
                entry["session_id"] = resume_session_id
                persisted_session = w.runtime_sessions.get(issue_id) or {}
                if persisted_session.get("thread_id"):
                    entry["thread_id"] = persisted_session.get("thread_id")
            dispatched.append(dict(entry))
            w._emit_event(
                "issue_runner.worker.dispatched",
                {
                    "issue_id": issue_id,
                    "identifier": issue.get("identifier"),
                    "worker_id": entry.get("worker_id"),
                    "attempt": entry.get("attempt"),
                    "state": issue.get("state"),
                    "run_id": run_id,
                },
            )
        w._persist_scheduler_state()
        return dispatched

    def supervise_once(self) -> dict[str, Any]:
        w = self.workspace
        engine_run = w.engine_store.start_run(mode="supervised")
        status = {
            "ok": True,
            "workflow": "issue-runner",
            "mode": "supervised",
            "updatedAt": _now_iso(),
            "selectedIssue": None,
            "selectedIssues": [],
            "attempt": None,
            "outputPath": None,
            "metrics": {},
            "results": [],
            "completedResults": [],
            "dispatchedWorkers": [],
            "cancellationRequests": [],
        }
        try:
            try:
                issues = w.tracker_client.list_all()
                terminal_issues = w.tracker_client.list_terminal()
            except Exception as exc:
                status["ok"] = False
                status["error"] = f"{type(exc).__name__}: {exc}"
                status["engineRun"] = w._finish_engine_run_for_status(
                    engine_run,
                    status,
                    selected_count=0,
                    completed_count=0,
                    metadata={"reason": "tracker-load-failed"},
                )
                w._write_status(status, health="error")
                w._emit_event(
                    "issue_runner.tick.failed",
                    {
                        "error": status["error"],
                        "reason": "tracker-load-failed",
                        "run_id": engine_run["run_id"],
                    },
                )
                return status

            status["cancellationRequests"] = self.request_terminal_cancellations(terminal_issues)
            completed = self.reconcile_supervised_workers()
            if completed:
                status = self.status_from_results(base_status=status, results=completed)
                status["completedResults"] = status.get("results") or []
            suppressed_completed_ids = {
                str((result.get("issue") or {}).get("id") or "").strip()
                for result in completed
                if result.get("suppressRetry")
            }
            cleanup = w._cleanup_terminal_workspaces(terminal_issues)
            status["cleanup"] = cleanup

            dispatch_issues = [
                issue
                for issue in issues
                if str(issue.get("id") or "").strip() not in suppressed_completed_ids
            ]
            issues_by_id = {
                str(issue.get("id")): issue
                for issue in dispatch_issues
                if str(issue.get("id") or "").strip()
            }
            selections = self.select_issue_batch(issues=dispatch_issues, issues_by_id=issues_by_id)
            selections = self._refresh_selections(selections)
            status["selectedIssues"] = [issue for issue, _retry_entry in selections]
            status["selectedIssue"] = selections[0][0] if selections else None
            dispatched = self.dispatch_supervised_workers(selections, run_id=engine_run["run_id"])
            status["dispatchedWorkers"] = dispatched
            if not completed and not dispatched:
                status["message"] = "no dispatchable issues"
                w._emit_event(
                    "issue_runner.tick.noop",
                    {"reason": "no-dispatchable-issues", "run_id": engine_run["run_id"]},
                )

            if completed and not all(result.get("ok") or result.get("suppressRetry") for result in completed):
                status["ok"] = False
            status["engineRun"] = w._finish_engine_run_for_status(
                engine_run,
                status,
                metadata={
                    "dispatched_count": len(dispatched),
                    "cancellation_count": len(status.get("cancellationRequests") or []),
                },
            )
            w._write_status(status, health="healthy" if status["ok"] else "error")
            w._persist_scheduler_state()
            return status
        except Exception as exc:
            w._fail_engine_run_after_exception(engine_run, exc)
            raise

    def reconcile_before_loop_exit(self, last_result: dict[str, Any] | None) -> dict[str, Any] | None:
        w = self.workspace
        engine_run = w.engine_store.start_run(mode="supervised-exit-reconcile")
        try:
            completed = self.reconcile_supervised_workers()
            if not completed:
                w._persist_scheduler_state()
                w.engine_store.complete_run(
                    engine_run["run_id"],
                    selected_count=0,
                    completed_count=0,
                    metadata={"changed": False},
                )
                return last_result
            status = {
                "ok": True,
                "workflow": "issue-runner",
                "mode": "supervised-exit-reconcile",
                "updatedAt": _now_iso(),
                "selectedIssue": None,
                "selectedIssues": [],
                "attempt": None,
                "outputPath": None,
                "metrics": {},
                "results": [],
                "completedResults": [],
                "dispatchedWorkers": [],
                "cancellationRequests": [],
            }
            status = self.status_from_results(base_status=status, results=completed)
            status["completedResults"] = status.get("results") or []
            status["engineRun"] = w._finish_engine_run_for_status(
                engine_run,
                status,
                selected_count=0,
                completed_count=len(completed),
                metadata={"changed": True},
            )
            w._write_status(status, health="healthy" if status["ok"] else "error")
            w._persist_scheduler_state()
            return status
        except Exception as exc:
            w._fail_engine_run_after_exception(engine_run, exc)
            raise

    def tick(self) -> dict[str, Any]:
        w = self.workspace
        engine_run = w.engine_store.start_run(mode="tick")
        status = {
            "ok": False,
            "workflow": "issue-runner",
            "updatedAt": _now_iso(),
            "selectedIssue": None,
            "selectedIssues": [],
            "attempt": None,
            "outputPath": None,
            "metrics": {},
        }
        try:
            try:
                issues = w.tracker_client.list_all()
                cleanup = w._cleanup_terminal_workspaces(w.tracker_client.list_terminal())
            except Exception as exc:
                status["error"] = f"{type(exc).__name__}: {exc}"
                status["engineRun"] = w._finish_engine_run_for_status(
                    engine_run,
                    status,
                    selected_count=0,
                    completed_count=0,
                    metadata={"reason": "tracker-load-failed"},
                )
                w._write_status(status, health="error")
                w._emit_event(
                    "issue_runner.tick.failed",
                    {
                        "error": status["error"],
                        "reason": "tracker-load-failed",
                        "run_id": engine_run["run_id"],
                    },
                )
                return status
            issues_by_id = {str(issue.get("id")): issue for issue in issues if str(issue.get("id") or "").strip()}
            selections = self.select_issue_batch(issues=issues, issues_by_id=issues_by_id)
            selections = self._refresh_selections(selections)
            status["selectedIssues"] = [issue for issue, _retry_entry in selections]
            status["selectedIssue"] = selections[0][0] if selections else None
            status["cleanup"] = cleanup
            if not selections:
                status["ok"] = True
                status["message"] = "no dispatchable issues"
                status["engineRun"] = w._finish_engine_run_for_status(
                    engine_run,
                    status,
                    selected_count=0,
                    completed_count=0,
                    metadata={"reason": "no-dispatchable-issues"},
                )
                w._write_status(status, health="healthy")
                w._persist_scheduler_state()
                w._emit_event(
                    "issue_runner.tick.noop",
                    {"reason": "no-dispatchable-issues", "run_id": engine_run["run_id"]},
                )
                return status

            self.mark_running(selections, run_id=engine_run["run_id"])
            results: list[dict[str, Any]] = []
            try:
                if len(selections) == 1:
                    issue, retry_entry = selections[0]
                    results = [
                        w._execute_issue(
                            issue=issue,
                            retry_entry=retry_entry,
                            run_id=engine_run["run_id"],
                        )
                    ]
                else:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(selections)) as executor:
                        future_map = {
                            executor.submit(
                                w._execute_issue,
                                issue=issue,
                                retry_entry=retry_entry,
                                run_id=engine_run["run_id"],
                            ): (issue, retry_entry)
                            for issue, retry_entry in selections
                        }
                        for future in concurrent.futures.as_completed(future_map):
                            results.append(future.result())

                status = self.status_from_results(base_status=status, results=results)
                status["engineRun"] = w._finish_engine_run_for_status(
                    engine_run,
                    status,
                    selected_count=len(selections),
                    completed_count=len(results),
                )
                w._write_status(status, health="healthy" if status["ok"] else "error")
                return status
            finally:
                self.clear_running([str(issue.get("id") or "") for issue, _retry_entry in selections])
                w._persist_scheduler_state()
        except Exception as exc:
            w._fail_engine_run_after_exception(engine_run, exc)
            raise
        finally:
            w._apply_event_retention()

    def run_loop(
        self,
        *,
        interval_seconds: int | None,
        max_iterations: int | None = None,
        sleep_fn=time.sleep,
    ) -> dict[str, Any]:
        w = self.workspace
        iterations = 0
        last_result = None
        last_retention = w._apply_event_retention()
        loop_status = "completed"
        try:
            while True:
                w.reload_contract()
                # Call through the workspace facade so tests and operators can
                # wrap supervise_once without bypassing orchestration.
                last_result = w.supervise_once()
                last_retention = w._apply_event_retention()
                iterations += 1
                if max_iterations is not None and iterations >= max_iterations:
                    break
                sleep_fn(self._poll_interval_seconds(interval_seconds))
        except KeyboardInterrupt:
            loop_status = "interrupted"
        last_result = self.reconcile_before_loop_exit(last_result)
        if not w._supervisor_futures:
            w.close()
        return {
            "loop_status": loop_status,
            "iterations": iterations,
            "last_result": last_result,
            "event_retention": last_retention,
        }

    def _refresh_selections(
        self,
        selections: list[tuple[dict[str, Any], dict[str, Any] | None]],
    ) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
        refreshed: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for selected, retry_entry in selections:
            issue_id = str(selected.get("id") or "").strip()
            if issue_id:
                selected = self.workspace.tracker_client.refresh([issue_id]).get(issue_id, selected)
            refreshed.append((selected, retry_entry))
        return refreshed
