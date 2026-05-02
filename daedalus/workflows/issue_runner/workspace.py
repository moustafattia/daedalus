from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from engine.scheduler import (
    build_scheduler_payload,
    runtime_sessions_snapshot,
    restore_scheduler_state,
    retry_due_at,
    retry_queue_snapshot,
    running_snapshot,
)
try:
    from engine import WorkflowDriver
except ModuleNotFoundError:
    from daedalus.engine import WorkflowDriver
from engine.lifecycle import (
    clear_work_entries,
    mark_running_work,
    recover_running_as_retry,
    schedule_retry_entry,
)
from engine.store import EngineStore
from engine.storage import append_jsonl as _append_jsonl
from engine.storage import load_optional_json as _load_optional_json
from engine.storage import write_json_atomic as _write_json
from engine.work_items import work_item_from_issue
from runtimes.registry import build_runtimes
from runtimes.types import PromptRunResult, Runtime
from runtimes.stages import prompt_result_from_stage, run_runtime_stage
from workflows.contract import WORKFLOW_POLICY_KEY, WorkflowContractError, load_workflow_contract
from workflows.config import ConfigError
from workflows.hooks import build_hook_env, run_shell_hook
from workflows.prompts import render_prompt_template
from workflows.config_snapshot import AtomicRef, ConfigSnapshot
from workflows.paths import runtime_paths
from workflows.issue_runner.config import (
    IssueRunnerConfig,
    max_retry_backoff_ms_from_config,
    poll_interval_seconds_from_config,
    scheduler_state_from_config as _typed_scheduler_state_from_config,
    terminal_states_from_config,
)
from workflows.issue_runner.orchestrator import IssueRunnerOrchestrator
from workflows.issue_runner.tracker import (
    TrackerClient,
    TrackerConfigError,
    build_tracker_client,
    describe_tracker_source,
    eligible_issues,
    issue_session_name,
    issue_workspace_slug,
    select_issue,
)
from workflows.readiness import build_readiness_recommendations
from workflows.runtime_presets import (
    runtime_availability_checks,
    runtime_binding_checks,
    runtime_capability_checks,
    runtime_stage_checks,
)
from trackers.github import (
    github_auth_host_from_slug,
    github_auth_success_accounts,
    github_name_with_owner_from_slug,
    github_slug_from_config,
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_epoch() -> float:
    return time.time()


def _repository_path_from_config(workflow_root: Path, config: dict[str, Any]) -> Path | None:
    repository_cfg = config.get("repository") or {}
    raw = str(
        repository_cfg.get("local-path")
        or repository_cfg.get("local_path")
        or ""
    ).strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (workflow_root / path).resolve()
    return path


def _tracker_config_for_client(config: dict[str, Any]) -> dict[str, Any]:
    tracker_cfg = dict(config.get("tracker") or {})
    if str(tracker_cfg.get("kind") or "").strip() != "github":
        return tracker_cfg
    if tracker_cfg.get("github-slug"):
        raise TrackerConfigError(
            "issue-runner GitHub config uses tracker.github_slug; remove tracker.github-slug"
        )
    repository_cfg = config.get("repository") or {}
    if repository_cfg.get("github_slug") or repository_cfg.get("github-slug"):
        raise TrackerConfigError(
            "issue-runner GitHub config uses tracker.github_slug; remove repository.github-slug"
        )
    if not github_slug_from_config(tracker_cfg):
        raise TrackerConfigError("tracker.kind='github' requires tracker.github_slug")
    return tracker_cfg


def _schema_path() -> Path:
    return Path(__file__).with_name("schema.yaml")


def _validate_issue_runner_config(config: dict[str, Any]) -> None:
    schema = yaml.safe_load(_schema_path().read_text(encoding="utf-8"))
    Draft7Validator(schema).validate(config)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_issue_workspace_path(workspace_root: Path, issue: dict[str, Any]) -> Path:
    root = workspace_root.expanduser().resolve()
    workspace_key = issue_workspace_slug(issue)
    path = root / workspace_key
    lexical_path = path.resolve(strict=False)
    if lexical_path == root or not _is_relative_to(lexical_path, root):
        raise RuntimeError(
            f"invalid workspace path for issue {issue.get('identifier') or issue.get('id')!r}: "
            f"{lexical_path} is not a child of {root}"
        )
    return path


def _assert_workspace_inside_root(workspace_root: Path, issue_workspace: Path) -> None:
    root = workspace_root.expanduser().resolve()
    resolved = issue_workspace.expanduser().resolve()
    if resolved == root or not _is_relative_to(resolved, root):
        raise RuntimeError(f"invalid workspace path: {resolved} is not a child of {root}")


def _render_prompt(*, prompt_template: str, issue: dict[str, Any], attempt: int | None) -> str:
    return render_prompt_template(
        prompt_template=prompt_template,
        default_template="You are working on an issue.\n\nIssue: {{ issue.identifier }} - {{ issue.title }}",
        variables={"issue": issue, "attempt": attempt},
    )


def _subprocess_run(command: list[str], *, cwd: Path | None = None, timeout: int | None = None, env: dict[str, str] | None = None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=merged_env,
    )


def _subprocess_run_json(command: list[str], *, cwd: Path | None = None, timeout: int | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    completed = _subprocess_run(command, cwd=cwd, timeout=timeout, env=env)
    payload = json.loads(completed.stdout or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("expected JSON object payload")
    return payload


def _runtime_profiles_from_config(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    daedalus_cfg = config.get("daedalus") or {}
    raw_profiles = config.get("runtimes") or (
        daedalus_cfg.get("runtimes") if isinstance(daedalus_cfg, dict) else {}
    ) or {}
    return {
        str(name): dict(profile_cfg or {})
        for name, profile_cfg in raw_profiles.items()
    }


def _build_runtimes_from_config(
    config: dict[str, Any],
    *,
    run: Callable[..., Any],
    run_json: Callable[..., dict[str, Any]],
) -> dict[str, Runtime]:
    return build_runtimes(_runtime_profiles_from_config(config), run=run, run_json=run_json)


def _scheduler_state_from_config(config: dict[str, Any]) -> dict[str, Any]:
    return _typed_scheduler_state_from_config(config)


@dataclass
class IssueRunnerWorkspace(WorkflowDriver):
    path: Path
    config: dict[str, Any]
    snapshot_ref: AtomicRef[ConfigSnapshot]
    contract_path: Path
    tracker_source: str
    tracker_client: TrackerClient
    issue_workspace_root: Path
    status_path: Path
    health_path: Path
    audit_log_path: Path
    scheduler_path: Path
    db_path: Path
    engine_store: EngineStore
    prompt_template: str
    runtimes: dict[str, Runtime]
    _run: Callable[..., Any]
    _run_json: Callable[..., dict[str, Any]]
    retry_entries: dict[str, dict[str, Any]]
    running_entries: dict[str, dict[str, Any]]
    runtime_sessions: dict[str, dict[str, Any]]
    running_issue_id: str | None = None
    runtime_totals: dict[str, Any] | None = None
    _supervisor_executor: concurrent.futures.ThreadPoolExecutor | None = field(default=None, init=False, repr=False)
    _supervisor_futures: dict[str, concurrent.futures.Future] = field(default_factory=dict, init=False, repr=False)
    _supervisor_cancel_events: dict[str, threading.Event] = field(default_factory=dict, init=False, repr=False)

    def orchestrator(self) -> IssueRunnerOrchestrator:
        return IssueRunnerOrchestrator(self)

    def runtime(self, name: str) -> Runtime:
        return self.runtimes[name]

    def close(self) -> None:
        if self._supervisor_executor is not None:
            self._supervisor_executor.shutdown(wait=False, cancel_futures=False)
            self._supervisor_executor = None
        self._close_runtimes()

    def _close_runtimes(self) -> None:
        for runtime in self.runtimes.values():
            close = getattr(runtime, "close", None)
            if callable(close):
                close()

    def _agent_runtime_name(self) -> str:
        agent_cfg = self.config.get("agent") or {}
        runtime_name = str(agent_cfg.get("runtime") or "").strip()
        if runtime_name:
            return runtime_name
        raise RuntimeError("issue-runner requires agent.runtime")

    def _agent_runtime(self) -> tuple[str, Runtime, dict[str, Any]]:
        runtime_name = self._agent_runtime_name()
        runtime_profiles = _runtime_profiles_from_config(self.config)
        runtime_cfg = runtime_profiles.get(runtime_name) or {}
        try:
            runtime = self.runtime(runtime_name)
        except KeyError as exc:
            raise RuntimeError(f"unknown runtime profile {runtime_name!r}") from exc
        return runtime_name, runtime, runtime_cfg

    def _poll_interval_seconds(self, override: int | None) -> int:
        if override is not None:
            return max(int(override), 1)
        return poll_interval_seconds_from_config(self.config)

    def build_status(self) -> dict[str, Any]:
        snapshot = self.snapshot_ref.get()
        tracker_cfg = snapshot.config.get("tracker") or {}
        try:
            issues = self.tracker_client.list_all()
            eligible = eligible_issues(tracker_cfg=tracker_cfg, issues=issues)
            selected = eligible[0] if eligible else None
            eligible_count = len(eligible)
            health = "healthy"
            error = None
        except Exception as exc:
            issues = []
            selected = None
            eligible_count = 0
            health = "error"
            error = f"{type(exc).__name__}: {exc}"
        last_run = _load_optional_json(self.status_path)
        return {
            "workflow": "issue-runner",
            "source": "issue-runner",
            "workflowRoot": str(self.path),
            "contractPath": str(self.contract_path),
            "health": health,
            "error": error,
            "tracker": {
                "kind": tracker_cfg.get("kind"),
                "path": self.tracker_source,
                "issueCount": len(issues),
                "eligibleCount": eligible_count,
            },
            "scheduler": {
                **_scheduler_state_from_config(self.config),
                "running": self._running_snapshot(),
                "retry_queue": self._retry_queue_snapshot(),
                "runtime_totals": dict(self.runtime_totals or {}),
                "runtime_sessions": self._runtime_sessions_snapshot(),
            },
            "runtimeDiagnostics": self._runtime_diagnostics(),
            "selectedIssue": selected,
            "workspaceRoot": str(self.issue_workspace_root),
            "lastRun": (last_run or {}).get("lastRun"),
            "metrics": (last_run or {}).get("metrics") or {},
            "updatedAt": _now_iso(),
        }

    def doctor(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        try:
            issues = self.tracker_client.list_all()
            checks.append({"name": "tracker", "status": "pass", "detail": f"{len(issues)} issue(s) loaded"})
        except Exception as exc:
            checks.append({"name": "tracker", "status": "fail", "detail": str(exc)})

        tracker_cfg = self.config.get("tracker") or {}
        if str(tracker_cfg.get("kind") or "").strip() == "github":
            checks.extend(self._github_doctor_checks())

        try:
            self.issue_workspace_root.mkdir(parents=True, exist_ok=True)
            checks.append({"name": "workspace-root", "status": "pass", "detail": str(self.issue_workspace_root)})
        except OSError as exc:
            checks.append({"name": "workspace-root", "status": "fail", "detail": str(exc)})

        try:
            runtime_name, _, _ = self._agent_runtime()
            checks.append({"name": "agent-runtime", "status": "pass", "detail": runtime_name})
        except Exception as exc:
            checks.append({"name": "agent-runtime", "status": "fail", "detail": str(exc)})
        checks.extend(runtime_stage_checks(self.config))
        checks.extend(runtime_binding_checks(self.config))
        checks.extend(runtime_capability_checks(self.config))
        checks.extend(runtime_availability_checks(self.config))

        checks.extend(self.engine_store.doctor(event_retention=self.config.get("retention") or {}))
        ok = all(check["status"] != "fail" for check in checks)
        return {
            "ok": ok,
            "workflow": "issue-runner",
            "checks": checks,
            "recommendations": build_readiness_recommendations(
                checks,
                workflow="issue-runner",
                workflow_root=self.path,
                source_path=self.contract_path,
            ),
            "updatedAt": _now_iso(),
        }

    def _github_doctor_checks(self) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        try:
            expected = github_slug_from_config(
                self.config.get("tracker") or {},
            )
            auth_host = github_auth_host_from_slug(expected)
            auth_payload = getattr(self.tracker_client, "auth_status_payload")(hostname=auth_host)
            resolved_host, accounts = github_auth_success_accounts(auth_payload, hostname=auth_host)
            active = next(
                (
                    account
                    for account in accounts
                    if account.get("active") and account.get("state") == "success"
                ),
                None,
            )
            login = (active or accounts[0]).get("login") if accounts else None
            detail = f"gh authenticated as {login or 'unknown'}"
            if resolved_host and resolved_host != "github.com":
                detail = f"{detail} on {resolved_host}"
            checks.append({"name": "github-auth", "status": "pass", "detail": detail})
        except Exception as exc:
            checks.append({"name": "github-auth", "status": "fail", "detail": str(exc)})

        try:
            repo_payload = getattr(self.tracker_client, "repo_view_payload")()
            resolved = str(repo_payload.get("nameWithOwner") or "").strip()
            expected = github_slug_from_config(
                self.config.get("tracker") or {},
            )
            expected_name_with_owner = github_name_with_owner_from_slug(expected)
            if (
                expected_name_with_owner
                and resolved
                and resolved.lower() != expected_name_with_owner.lower()
            ):
                raise RuntimeError(
                    f"gh resolved repository {resolved!r}, expected {expected_name_with_owner!r}"
                )
            checks.append(
                {
                    "name": "github-repo",
                    "status": "pass",
                    "detail": resolved or (expected_name_with_owner or "resolved"),
                }
            )
        except Exception as exc:
            checks.append({"name": "github-repo", "status": "fail", "detail": str(exc)})
        return checks

    def _apply_event_retention(self) -> dict[str, Any]:
        try:
            return self.engine_store.apply_event_retention(self.config.get("retention") or {})
        except Exception as exc:
            return {
                "workflow": "issue-runner",
                "applied": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }

    def _runtime_diagnostics(self) -> dict[str, dict[str, Any]]:
        diagnostics: dict[str, dict[str, Any]] = {}
        for name, runtime in sorted(self.runtimes.items()):
            provider = getattr(runtime, "diagnostics", None)
            if not callable(provider):
                continue
            try:
                payload = provider()
            except Exception as exc:
                payload = {"error": f"{type(exc).__name__}: {exc}"}
            if isinstance(payload, dict):
                diagnostics[name] = payload
        return diagnostics

    def _load_scheduler_state(self) -> dict[str, Any]:
        return self.engine_store.load_scheduler()

    def _persist_scheduler_state(self) -> None:
        now_iso = _now_iso()
        now_epoch = _now_epoch()
        self.engine_store.save_scheduler(
            retry_entries=self.retry_entries,
            running_entries=self.running_entries,
            runtime_totals=self.runtime_totals,
            runtime_sessions=self.runtime_sessions,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )
        _write_json(
            self.scheduler_path,
            build_scheduler_payload(
                workflow="issue-runner",
                retry_entries=self.retry_entries,
                running_entries=self.running_entries,
                runtime_totals=self.runtime_totals,
                runtime_sessions=self.runtime_sessions,
                now_iso=now_iso,
                now_epoch=now_epoch,
            ),
        )

    def _finish_engine_run_for_status(
        self,
        engine_run: dict[str, Any],
        status: dict[str, Any],
        *,
        selected_count: int | None = None,
        completed_count: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected = status.get("selectedIssues") or []
        completed = status.get("completedResults") or status.get("results") or []
        final_selected_count = len(selected) if selected_count is None else selected_count
        final_completed_count = len(completed) if completed_count is None else completed_count
        run_metadata = {
            "status_mode": status.get("mode") or "tick",
            **(metadata or {}),
        }
        if status.get("message"):
            run_metadata["message"] = status.get("message")
        if status.get("ok"):
            return self.engine_store.complete_run(
                engine_run["run_id"],
                selected_count=final_selected_count,
                completed_count=final_completed_count,
                metadata=run_metadata,
            )
        return self.engine_store.fail_run(
            engine_run["run_id"],
            error=str(status.get("error") or "workflow run reported failure"),
            selected_count=final_selected_count,
            completed_count=final_completed_count,
            metadata=run_metadata,
        )

    def _fail_engine_run_after_exception(self, engine_run: dict[str, Any], exc: Exception) -> None:
        try:
            self.engine_store.fail_run(
                engine_run["run_id"],
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            pass

    def _restore_scheduler_state(self) -> None:
        payload = self._load_scheduler_state()
        restored = restore_scheduler_state(payload, now_epoch=_now_epoch())
        self.retry_entries = restored.retry_entries
        self.running_entries = {}
        self.runtime_totals = dict(restored.runtime_totals)
        self.runtime_sessions = restored.runtime_sessions
        self.running_issue_id = None

        if restored.recovered_running:
            now_epoch = _now_epoch()
            self.retry_entries = recover_running_as_retry(
                self.retry_entries,
                restored.recovered_running,
                now_epoch=now_epoch,
            )
            self._persist_scheduler_state()

    def _running_snapshot(self) -> list[dict[str, Any]]:
        return running_snapshot(self.running_entries, now_epoch=_now_epoch())

    def _retry_queue_snapshot(self) -> list[dict[str, Any]]:
        return retry_queue_snapshot(self.retry_entries, now_epoch=_now_epoch())

    def _runtime_sessions_snapshot(self) -> dict[str, dict[str, Any]]:
        return runtime_sessions_snapshot(self.runtime_sessions)

    def _runtime_session_id_for_issue(self, issue: dict[str, Any]) -> str | None:
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            return None
        entry = self.runtime_sessions.get(issue_id) or {}
        session_id = str(entry.get("session_id") or entry.get("thread_id") or "").strip()
        return session_id or None

    def _record_runtime_session(
        self,
        *,
        issue: dict[str, Any],
        session_name: str,
        metrics: dict[str, Any],
        runtime_name: str | None = None,
        runtime_kind: str | None = None,
        run_id: str | None = None,
    ) -> None:
        issue_id = str(issue.get("id") or "").strip()
        session_id = str(metrics.get("session_id") or metrics.get("thread_id") or "").strip()
        thread_id = str(metrics.get("thread_id") or "").strip()
        if not issue_id or not session_id:
            return
        self.runtime_sessions[issue_id] = {
            "issue_id": issue_id,
            "identifier": issue.get("identifier"),
            "session_name": session_name,
            "session_id": session_id,
            "thread_id": thread_id or None,
            "turn_id": metrics.get("turn_id"),
            "runtime_name": runtime_name,
            "runtime_kind": runtime_kind,
            "run_id": run_id,
            "updated_at": _now_iso(),
        }

    def _clear_runtime_session(self, issue_id: str | None) -> None:
        if issue_id:
            self.runtime_sessions.pop(issue_id, None)

    def _due_retry_issue(self, *, issues_by_id: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        now_epoch = _now_epoch()
        due_entries = sorted(
            self.retry_entries.items(),
            key=lambda item: (
                retry_due_at(item[1], default=0.0),
                int((item[1] or {}).get("attempt") or 0),
                str((item[1] or {}).get("identifier") or item[0]),
            ),
        )
        for issue_id, entry in due_entries:
            if retry_due_at(entry, default=0.0) > now_epoch:
                continue
            issue = issues_by_id.get(issue_id)
            if issue is None:
                self.retry_entries.pop(issue_id, None)
                continue
            return issue, entry
        return None, None

    def _schedule_retry(
        self,
        *,
        issue: dict[str, Any],
        error: str,
        current_attempt: int | None,
        delay_type: str = "failure",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        max_backoff_ms = max_retry_backoff_ms_from_config(self.config)
        work_item = work_item_from_issue(issue, source=str((self.config.get("tracker") or {}).get("kind") or "tracker"))
        entry, retry = schedule_retry_entry(
            work_item=work_item,
            existing_entry=self.retry_entries.get(work_item.id),
            error=error,
            current_attempt=current_attempt,
            delay_type=delay_type,
            max_backoff_ms=max_backoff_ms,
            now_epoch=_now_epoch(),
        )
        if run_id:
            entry["run_id"] = run_id
            retry["run_id"] = run_id
        self.retry_entries[work_item.id] = entry
        self._emit_event(
            "issue_runner.retry.scheduled",
            {
                **retry,
                "error": error,
                "run_id": run_id,
            },
        )
        return retry

    def _clear_retry(self, issue_id: str | None) -> None:
        self.retry_entries = clear_work_entries(self.retry_entries, [issue_id])

    def _terminal_states(self) -> set[str]:
        return terminal_states_from_config(self.config)

    def _record_metrics(self, result: PromptRunResult) -> dict[str, Any]:
        metrics = self._metrics_payload(result)
        totals = dict(self.runtime_totals or {})
        tokens = metrics.get("tokens") or {}
        totals["input_tokens"] = int(totals.get("input_tokens") or 0) + int(tokens.get("input_tokens") or 0)
        totals["output_tokens"] = int(totals.get("output_tokens") or 0) + int(tokens.get("output_tokens") or 0)
        totals["total_tokens"] = int(totals.get("total_tokens") or 0) + int(tokens.get("total_tokens") or 0)
        totals["turn_count"] = int(totals.get("turn_count") or 0) + int(metrics.get("turn_count") or 0)
        if metrics.get("rate_limits") is not None:
            totals["rate_limits"] = metrics.get("rate_limits")
        self.runtime_totals = totals
        return metrics

    def _dispatch_slots(self) -> int:
        scheduler = _scheduler_state_from_config(self.config)
        running_count = len(self.running_entries)
        return max(int(scheduler["max_concurrent_agents"]) - running_count, 0)

    def _select_issue_batch(
        self,
        *,
        issues: list[dict[str, Any]],
        issues_by_id: dict[str, dict[str, Any]],
    ) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
        slots = self._dispatch_slots()
        if slots <= 0:
            return []

        tracker_cfg = self.config.get("tracker") or {}
        scheduler = _scheduler_state_from_config(self.config)
        state_limits = dict(scheduler["max_concurrent_agents_by_state"])
        pending_retry_ids = {
            issue_id
            for issue_id, entry in self.retry_entries.items()
            if retry_due_at(entry, default=0.0) > _now_epoch()
        }
        running_ids = set(self.running_entries)
        selected: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        selected_ids: set[str] = set()
        state_counts: dict[str, int] = {}

        def _can_take(issue: dict[str, Any]) -> bool:
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
                for issue_id, entry in self.retry_entries.items()
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
                self.retry_entries.pop(issue_id, None)
                continue
            if not _can_take(issue):
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
            if not _can_take(issue):
                continue
            issue_id = str(issue.get("id") or "").strip()
            selected.append((issue, self.retry_entries.get(issue_id)))
            selected_ids.add(issue_id)
            state = str(issue.get("state") or "").strip().lower()
            state_counts[state] = state_counts.get(state, 0) + 1
        return selected

    def _issue_attempt(self, *, issue: dict[str, Any], retry_entry: dict[str, Any] | None) -> int:
        if retry_entry is not None:
            return int(retry_entry.get("attempt") or 0) + 1
        last_run = (_load_optional_json(self.status_path) or {}).get("lastRun") or {}
        if (last_run.get("issue") or {}).get("id") == issue.get("id"):
            return int(last_run.get("attempt") or 0) + 1
        return 1

    def _mark_running(
        self,
        selections: list[tuple[dict[str, Any], dict[str, Any] | None]],
        *,
        run_id: str | None = None,
    ) -> None:
        now_epoch = _now_epoch()
        tracker_kind = str((self.config.get("tracker") or {}).get("kind") or "tracker")
        self.running_entries = mark_running_work(
            self.running_entries,
            work_items=[
                (
                    work_item_from_issue(issue, source=tracker_kind),
                    self._issue_attempt(issue=issue, retry_entry=retry_entry),
                )
                for issue, retry_entry in selections
                if str(issue.get("id") or "").strip()
            ],
            now_epoch=now_epoch,
        )
        if run_id:
            for issue, _retry_entry in selections:
                issue_id = str(issue.get("id") or "").strip()
                if issue_id in self.running_entries:
                    self.running_entries[issue_id]["run_id"] = run_id
        self.running_issue_id = next(iter(self.running_entries), None)
        self._persist_scheduler_state()

    def _clear_running(self, issue_ids: list[str]) -> None:
        self.running_entries = clear_work_entries(self.running_entries, issue_ids)
        self.running_issue_id = next(iter(self.running_entries), None)

    def _metrics_from_exception(self, exc: Exception, runtime: Runtime | None = None) -> dict[str, Any]:
        result = getattr(exc, "result", None)
        if result is None and runtime is not None:
            last_result = getattr(runtime, "last_result", None)
            if callable(last_result):
                result = last_result()
        if isinstance(result, PromptRunResult):
            return self._metrics_payload(result)
        return {}

    def _cancel_result(
        self,
        *,
        issue: dict[str, Any],
        attempt: int,
        reason: str,
        workspace: Path | None = None,
        output_path: Path | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "canceled": True,
            "runId": run_id,
            "suppressRetry": reason == "terminal-state",
            "issue": issue,
            "attempt": attempt,
            "workspace": str(workspace) if workspace is not None else None,
            "createdWorkspace": False,
            "hookResults": [],
            "outputPath": str(output_path) if output_path is not None else None,
            "error": f"worker canceled: {reason}",
            "metrics": {},
            "runtime": None,
            "runtimeKind": None,
        }

    def _execute_issue(
        self,
        *,
        issue: dict[str, Any],
        retry_entry: dict[str, Any] | None,
        cancel_event: threading.Event | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        hook_results: list[dict[str, Any]] = []
        runtime: Runtime | None = None
        runtime_name: str | None = None
        runtime_cfg: dict[str, Any] = {}
        issue_workspace: Path | None = None
        output_path: Path | None = None
        env: dict[str, str] | None = None
        created_workspace = False
        attempt = self._issue_attempt(issue=issue, retry_entry=retry_entry)

        try:
            if cancel_event is not None and cancel_event.is_set():
                return self._cancel_result(
                    issue=issue,
                    attempt=attempt,
                    reason="requested-before-start",
                    run_id=run_id,
                )
            issue_workspace = _safe_issue_workspace_path(self.issue_workspace_root, issue)
            issue_workspace.mkdir(parents=True, exist_ok=True)
            _assert_workspace_inside_root(self.issue_workspace_root, issue_workspace)
            daemon_dir = issue_workspace / ".daedalus"
            daemon_dir.mkdir(parents=True, exist_ok=True)
            created_marker = daemon_dir / "created.marker"
            created_workspace = not created_marker.exists()

            prompt = _render_prompt(
                prompt_template=self.prompt_template or str(self.config.get(WORKFLOW_POLICY_KEY) or ""),
                issue=issue,
                attempt=None if attempt <= 1 else attempt,
            )
            prompt_path = daemon_dir / "prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            output_path = daemon_dir / "last-output.txt"
            env = self._hook_env(issue=issue, issue_workspace=issue_workspace, prompt_path=prompt_path, output_path=output_path)
            if cancel_event is not None and cancel_event.is_set():
                return self._cancel_result(
                    issue=issue,
                    attempt=attempt,
                    reason="requested-before-run",
                    workspace=issue_workspace,
                    output_path=output_path,
                    run_id=run_id,
                )
            if created_workspace:
                hook_results.append(self._run_hook("after_create", issue_workspace, env))
                created_marker.write_text(_now_iso() + "\n", encoding="utf-8")
            hook_results.append(self._run_hook("before_run", issue_workspace, env))

            agent_cfg = self.config.get("agent") or {}
            runtime_name = self._agent_runtime_name()
            runtime_profiles = _runtime_profiles_from_config(self.config)
            runtime_cfg = runtime_profiles.get(runtime_name) or {}
            if self._supervisor_max_workers() == 1:
                runtime = self.runtime(runtime_name)
            else:
                runtime = _build_runtimes_from_config(self.config, run=self._run, run_json=self._run_json)[runtime_name]
            session_name = issue_session_name(issue)
            resume_session_id = self._runtime_session_id_for_issue(issue)
            stage_result = run_runtime_stage(
                runtime=runtime,
                runtime_cfg=runtime_cfg,
                agent_cfg=agent_cfg,
                stage_name="issue-runner",
                worktree=issue_workspace,
                session_name=session_name,
                prompt=prompt,
                prompt_path=prompt_path,
                env=env,
                resume_session_id=resume_session_id,
                cancel_event=cancel_event,
                placeholders={
                    "issue_id": str(issue.get("id") or ""),
                    "issue_identifier": str(issue.get("identifier") or ""),
                    "issue_title": str(issue.get("title") or ""),
                    "workflow_root": str(self.path),
                },
            )
            run_result = prompt_result_from_stage(stage_result)
            output = stage_result.output
            output_path.write_text(output, encoding="utf-8")
            hook_results.append(self._run_hook("after_run", issue_workspace, env))
            return {
                "ok": True,
                "runId": run_id,
                "issue": issue,
                "attempt": attempt,
                "workspace": str(issue_workspace),
                "createdWorkspace": created_workspace,
                "hookResults": hook_results,
                "outputPath": str(output_path),
                "metrics": self._metrics_payload(run_result),
                "runtime": runtime_name,
                "runtimeKind": runtime_cfg.get("kind"),
            }
        except Exception as exc:
            if issue_workspace is not None and env is not None:
                hook_results.append(self._run_hook("after_run", issue_workspace, env, ignore_failure=True))
            return {
                "ok": False,
                "runId": run_id,
                "issue": issue,
                "attempt": attempt,
                "workspace": str(issue_workspace) if issue_workspace is not None else None,
                "createdWorkspace": created_workspace,
                "hookResults": hook_results,
                "outputPath": str(output_path) if output_path is not None else None,
                "error": f"{type(exc).__name__}: {exc}",
                "metrics": self._metrics_from_exception(exc, runtime=runtime),
                "runtime": runtime_name if runtime is not None else None,
                "runtimeKind": runtime_cfg.get("kind") if runtime is not None else None,
            }
    def _apply_issue_results(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        for result in results:
            issue = result.get("issue") or {}
            issue_id = str(issue.get("id") or "")
            run_id = result.get("run_id") or result.get("runId")
            metrics = result.get("metrics") or {}
            if metrics:
                recorded_metrics = self._record_metrics(
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
                self._record_runtime_session(
                    issue=issue,
                    session_name=issue_session_name(issue),
                    metrics=recorded_metrics,
                    runtime_name=result.get("runtime"),
                    runtime_kind=result.get("runtimeKind"),
                    run_id=run_id,
                )
                result["metrics"] = recorded_metrics

            if result.get("ok"):
                self._clear_retry(issue_id)
                if result.get("suppressRetry"):
                    result["retry"] = None
                else:
                    retry = self._schedule_retry(
                        issue=issue,
                        error="continuation",
                        current_attempt=result.get("attempt"),
                        delay_type="continuation",
                        run_id=run_id,
                    )
                    result["retry"] = retry
                self._emit_event(
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
                    self._emit_event(
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
                    retry = self._schedule_retry(
                        issue=issue,
                        error=str(result.get("error") or "issue execution failed"),
                        current_attempt=result.get("attempt"),
                        run_id=run_id,
                    )
                    result["retry"] = retry
                    self._emit_event(
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

    def _status_from_results(
        self,
        *,
        base_status: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        results.sort(key=lambda item: str(((item.get("issue") or {}).get("identifier")) or ((item.get("issue") or {}).get("id")) or ""))
        applied = self._apply_issue_results(results)
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
                "metrics": self._aggregate_metrics(tick_metrics),
            }
        )
        failed = next((result for result in applied if not result.get("ok") and not result.get("suppressRetry")), None)
        if failed:
            base_status["error"] = failed.get("error")
            base_status["retry"] = failed.get("retry")
        return base_status

    def _supervisor_max_workers(self) -> int:
        scheduler = _scheduler_state_from_config(self.config)
        return max(int(scheduler["max_concurrent_agents"]), 1)

    def _ensure_supervisor_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._supervisor_executor is None:
            self._supervisor_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._supervisor_max_workers(),
                thread_name_prefix="daedalus-issue-runner",
            )
        return self._supervisor_executor

    def _request_supervised_cancel(self, issue_id: str, *, reason: str) -> bool:
        if not issue_id or issue_id not in self.running_entries:
            return False
        entry = self.running_entries[issue_id]
        if entry.get("cancel_requested"):
            return False
        entry["cancel_requested"] = True
        entry["cancel_reason"] = reason
        entry["worker_status"] = "canceling"
        entry["heartbeat_at_epoch"] = _now_epoch()
        event = self._supervisor_cancel_events.get(issue_id)
        if event is not None:
            event.set()
        future = self._supervisor_futures.get(issue_id)
        if future is not None:
            future.cancel()
        self._emit_event(
            "issue_runner.worker.cancel_requested",
            {
                "issue_id": issue_id,
                "identifier": entry.get("identifier"),
                "reason": reason,
                "worker_id": entry.get("worker_id"),
                "run_id": entry.get("run_id") or entry.get("runId"),
            },
        )
        self._persist_scheduler_state()
        return True

    def _request_terminal_cancellations(self, terminal_issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        requested = []
        for issue in terminal_issues:
            issue_id = str(issue.get("id") or "").strip()
            if not issue_id or issue_id not in self.running_entries:
                continue
            if self._request_supervised_cancel(issue_id, reason="terminal-state"):
                requested.append(
                    {
                        "issue_id": issue_id,
                        "identifier": issue.get("identifier"),
                        "reason": "terminal-state",
                    }
                )
        return requested

    def _reconcile_supervised_workers(self) -> list[dict[str, Any]]:
        completed: list[dict[str, Any]] = []
        for issue_id, future in list(self._supervisor_futures.items()):
            if not future.done():
                entry = self.running_entries.get(issue_id)
                if entry is not None:
                    entry["heartbeat_at_epoch"] = _now_epoch()
                continue
            self._supervisor_futures.pop(issue_id, None)
            self._supervisor_cancel_events.pop(issue_id, None)
            entry = self.running_entries.get(issue_id) or {}
            if future.cancelled():
                result = self._cancel_result(
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
            self._clear_running([issue_id])
            completed.append(result)
            self._emit_event(
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
            self._persist_scheduler_state()
        return completed

    def _dispatch_supervised_workers(
        self,
        selections: list[tuple[dict[str, Any], dict[str, Any] | None]],
        *,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not selections:
            return []
        executor = self._ensure_supervisor_executor()
        self._mark_running(selections, run_id=run_id)
        dispatched: list[dict[str, Any]] = []
        for issue, retry_entry in selections:
            issue_id = str(issue.get("id") or "").strip()
            if not issue_id:
                continue
            cancel_event = threading.Event()
            self._supervisor_cancel_events[issue_id] = cancel_event
            future = executor.submit(
                self._execute_issue,
                issue=issue,
                retry_entry=retry_entry,
                cancel_event=cancel_event,
                run_id=run_id,
            )
            self._supervisor_futures[issue_id] = future
            entry = self.running_entries.get(issue_id) or {}
            resume_session_id = self._runtime_session_id_for_issue(issue)
            if resume_session_id:
                entry["session_id"] = resume_session_id
                persisted_session = self.runtime_sessions.get(issue_id) or {}
                if persisted_session.get("thread_id"):
                    entry["thread_id"] = persisted_session.get("thread_id")
            dispatched.append(dict(entry))
            self._emit_event(
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
        self._persist_scheduler_state()
        return dispatched

    def supervise_once(self) -> dict[str, Any]:
        return self.orchestrator().supervise_once()

    def _reconcile_before_loop_exit(self, last_result: dict[str, Any] | None) -> dict[str, Any] | None:
        return self.orchestrator().reconcile_before_loop_exit(last_result)

    def tick(self) -> dict[str, Any]:
        return self.orchestrator().tick()

    def _cleanup_terminal_workspaces(self, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        terminal_states = self._terminal_states()
        cleaned: list[dict[str, Any]] = []
        for issue in issues:
            state = str(issue.get("state") or "").strip().lower()
            if state not in terminal_states:
                continue
            issue_id = str(issue.get("id") or "")
            if issue_id in self.running_entries:
                self.orchestrator().request_supervised_cancel(issue_id, reason="terminal-state")
                cleaned.append(
                    {
                        "issue_id": issue.get("id"),
                        "identifier": issue.get("identifier"),
                        "workspace": None,
                        "deferred": True,
                        "reason": "worker-running",
                    }
                )
                continue
            self.orchestrator().clear_retry(issue_id)
            self._clear_runtime_session(issue_id)
            issue_workspace = _safe_issue_workspace_path(self.issue_workspace_root, issue)
            if not issue_workspace.exists():
                continue
            _assert_workspace_inside_root(self.issue_workspace_root, issue_workspace)
            daemon_dir = issue_workspace / ".daedalus"
            env = self._hook_env(
                issue=issue,
                issue_workspace=issue_workspace,
                prompt_path=daemon_dir / "prompt.txt",
                output_path=daemon_dir / "last-output.txt",
            )
            hook_result = self._run_hook("before_remove", issue_workspace, env, ignore_failure=True)
            shutil.rmtree(issue_workspace, ignore_errors=True)
            cleaned.append(
                {
                    "issue_id": issue.get("id"),
                    "identifier": issue.get("identifier"),
                    "workspace": str(issue_workspace),
                    "hook": hook_result,
                }
            )
            self._emit_event(
                "issue_runner.workspace.cleaned",
                {
                    "issue_id": issue.get("id"),
                    "identifier": issue.get("identifier"),
                    "workspace": str(issue_workspace),
                },
            )
        return cleaned

    def _write_status(self, tick_result: dict[str, Any], *, health: str) -> None:
        results = tick_result.get("results") or tick_result.get("completedResults") or []
        result_issues = [result.get("issue") for result in results if result.get("issue")]
        selected_issue = result_issues[0] if result_issues else tick_result.get("selectedIssue")
        selected_issues = result_issues or tick_result.get("selectedIssues") or ([selected_issue] if selected_issue else [])
        payload = {
            "workflow": "issue-runner",
            "health": health,
            "lastRun": {
                "ok": tick_result.get("ok"),
                "issue": selected_issue,
                "issues": selected_issues,
                "attempt": tick_result.get("attempt"),
                "outputPath": tick_result.get("outputPath"),
                "results": results,
                "updatedAt": tick_result.get("updatedAt") or _now_iso(),
            },
            "metrics": tick_result.get("metrics") or {},
        }
        _write_json(self.status_path, payload)
        _write_json(
            self.health_path,
            {
                "workflow": "issue-runner",
                "health": health,
                "updatedAt": payload["lastRun"]["updatedAt"],
            },
        )

    def _emit_event(self, event: str, payload: dict[str, Any]) -> None:
        event_payload = {"event": event, "at": _now_iso(), **payload}
        _append_jsonl(self.audit_log_path, event_payload)
        try:
            self.engine_store.append_event(
                event_type=event,
                payload=event_payload,
                run_id=event_payload.get("run_id") or event_payload.get("runId"),
                work_id=event_payload.get("issue_id") or event_payload.get("work_id"),
                created_at=event_payload.get("at"),
            )
        except Exception:
            # The JSONL audit row is already durable; event indexing is best effort.
            pass

    def _hook_env(
        self,
        *,
        issue: dict[str, Any],
        issue_workspace: Path,
        prompt_path: Path,
        output_path: Path,
    ) -> dict[str, str]:
        repository_cfg = self.config.get("repository") or {}
        return build_hook_env(
            {
                "WORKFLOW_ROOT": self.path,
                "ISSUE_ID": issue.get("id") or "",
                "ISSUE_IDENTIFIER": issue.get("identifier") or "",
                "ISSUE_TITLE": issue.get("title") or "",
                "ISSUE_STATE": issue.get("state") or "",
                "ISSUE_LABELS": ",".join(issue.get("labels") or []),
                "ISSUE_WORKSPACE": issue_workspace,
                "PROMPT_PATH": prompt_path,
                "OUTPUT_PATH": output_path,
                "REPOSITORY_PATH": repository_cfg.get("local-path") or "",
            }
        )

    def _run_hook(
        self,
        hook_name: str,
        worktree: Path,
        env: dict[str, str],
        *,
        ignore_failure: bool = False,
    ) -> dict[str, Any]:
        return run_shell_hook(
            hooks_config=self.config.get("hooks") or {},
            hook_name=hook_name,
            worktree=worktree,
            env=env,
            run=self._run,
            ignore_failure=ignore_failure,
        )

    def _metrics_payload(self, result: PromptRunResult) -> dict[str, Any]:
        return {
            "session_id": result.session_id,
            "thread_id": result.thread_id,
            "turn_id": result.turn_id,
            "last_event": result.last_event,
            "last_message": result.last_message,
            "turn_count": result.turn_count,
            "tokens": result.tokens or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "rate_limits": result.rate_limits,
        }

    def _aggregate_metrics(self, metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
        if not metrics_list:
            return {}
        aggregate = {
            "session_id": metrics_list[0].get("session_id") if len(metrics_list) == 1 else None,
            "thread_id": metrics_list[0].get("thread_id") if len(metrics_list) == 1 else None,
            "turn_id": metrics_list[-1].get("turn_id"),
            "last_event": metrics_list[-1].get("last_event"),
            "last_message": metrics_list[-1].get("last_message"),
            "turn_count": 0,
            "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "rate_limits": None,
        }
        for metrics in metrics_list:
            tokens = metrics.get("tokens") or {}
            aggregate["turn_count"] += int(metrics.get("turn_count") or 0)
            aggregate["tokens"]["input_tokens"] += int(tokens.get("input_tokens") or 0)
            aggregate["tokens"]["output_tokens"] += int(tokens.get("output_tokens") or 0)
            aggregate["tokens"]["total_tokens"] += int(tokens.get("total_tokens") or 0)
            if metrics.get("rate_limits") is not None:
                aggregate["rate_limits"] = metrics.get("rate_limits")
        return aggregate

    def run_loop(
        self,
        *,
        interval_seconds: int | None,
        max_iterations: int | None = None,
        sleep_fn=time.sleep,
    ) -> dict[str, Any]:
        return self.orchestrator().run_loop(
            interval_seconds=interval_seconds,
            max_iterations=max_iterations,
            sleep_fn=sleep_fn,
        )

    def reload_contract(self) -> None:
        try:
            contract = load_workflow_contract(self.path)
            next_config = dict(contract.config)
            _validate_issue_runner_config(next_config)
            typed_cfg = IssueRunnerConfig.from_raw(next_config, workflow_root=self.path)
        except (
            FileNotFoundError,
            WorkflowContractError,
            ConfigError,
            JsonSchemaValidationError,
            OSError,
            UnicodeDecodeError,
            yaml.YAMLError,
        ) as exc:
            self._emit_event(
                "daedalus.config_reload_failed",
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "contract_path": str(self.contract_path),
                },
            )
            return
        st = contract.source_path.stat()
        current = self.snapshot_ref.get()
        key = (st.st_mtime, st.st_size, str(contract.source_path))
        current_key = (current.source_mtime, current.source_size, str(self.contract_path))
        if key == current_key:
            return
        cfg = next_config
        prompts = cfg.get("prompts") or {}
        snapshot = ConfigSnapshot(
            config=cfg,
            prompts=prompts,
            loaded_at=time.monotonic(),
            source_mtime=st.st_mtime,
            source_size=st.st_size,
        )
        self.config = cfg
        self.contract_path = contract.source_path
        self.prompt_template = contract.prompt_template
        self.snapshot_ref.set(snapshot)
        tracker_cfg = cfg.get("tracker") or {}
        tracker_client_cfg = _tracker_config_for_client(cfg)
        repo_path = _repository_path_from_config(self.path, cfg)
        tracker_source_cfg = dict(tracker_client_cfg)
        if repo_path is not None and str(tracker_cfg.get("kind") or "").strip() == "github":
            tracker_source_cfg.setdefault("repo_path", str(repo_path))
        self.tracker_source = describe_tracker_source(workflow_root=self.path, tracker_cfg=tracker_source_cfg)
        self.tracker_client = build_tracker_client(
            workflow_root=self.path,
            tracker_cfg=tracker_client_cfg,
            repo_path=repo_path,
            run=self._run,
            run_json=self._run_json,
        )
        previous_scheduler_path = self.scheduler_path

        self.issue_workspace_root = typed_cfg.workspace.root
        self.status_path = typed_cfg.storage.status
        self.health_path = typed_cfg.storage.health
        self.audit_log_path = typed_cfg.storage.audit_log
        self.scheduler_path = typed_cfg.storage.scheduler
        self.db_path = runtime_paths(self.path)["db_path"]
        self.engine_store = EngineStore(
            db_path=self.db_path,
            workflow="issue-runner",
            now_iso=_now_iso,
            now_epoch=_now_epoch,
        )
        if not self._supervisor_futures:
            self._close_runtimes()
        self.runtimes = _build_runtimes_from_config(cfg, run=self._run, run_json=self._run_json)
        if self.scheduler_path != previous_scheduler_path:
            self._restore_scheduler_state()


def load_workspace_from_config(
    *,
    workspace_root: Path,
    config: dict[str, Any] | None = None,
    run: Callable[..., Any] | None = None,
    run_json: Callable[..., dict[str, Any]] | None = None,
) -> IssueRunnerWorkspace:
    root = workspace_root.expanduser().resolve()
    contract = load_workflow_contract(root)
    cfg = dict(config or contract.config)
    typed_cfg = IssueRunnerConfig.from_raw(cfg, workflow_root=root)
    prompts = cfg.get("prompts") or {}
    st = contract.source_path.stat()
    snapshot = ConfigSnapshot(
        config=cfg,
        prompts=prompts,
        loaded_at=time.monotonic(),
        source_mtime=st.st_mtime,
        source_size=st.st_size,
    )
    tracker_cfg = cfg.get("tracker") or {}
    tracker_client_cfg = _tracker_config_for_client(cfg)
    repo_path = _repository_path_from_config(root, cfg)
    tracker_source_cfg = dict(tracker_client_cfg)
    if repo_path is not None and str(tracker_cfg.get("kind") or "").strip() == "github":
        tracker_source_cfg.setdefault("repo_path", str(repo_path))

    runner = run or _subprocess_run
    runner_json = run_json or _subprocess_run_json
    tracker_source = describe_tracker_source(workflow_root=root, tracker_cfg=tracker_source_cfg)
    tracker_client = build_tracker_client(
        workflow_root=root,
        tracker_cfg=tracker_client_cfg,
        repo_path=repo_path,
        run=runner,
        run_json=runner_json,
    )
    issue_workspace_root = typed_cfg.workspace.root
    status_path = typed_cfg.storage.status
    health_path = typed_cfg.storage.health
    audit_log_path = typed_cfg.storage.audit_log
    scheduler_path = typed_cfg.storage.scheduler
    db_path = runtime_paths(root)["db_path"]
    engine_store = EngineStore(
        db_path=db_path,
        workflow="issue-runner",
        now_iso=_now_iso,
        now_epoch=_now_epoch,
    )

    runtimes = _build_runtimes_from_config(cfg, run=runner, run_json=runner_json)

    workspace = IssueRunnerWorkspace(
        path=root,
        config=cfg,
        snapshot_ref=AtomicRef(snapshot),
        contract_path=contract.source_path,
        tracker_source=tracker_source,
        tracker_client=tracker_client,
        issue_workspace_root=issue_workspace_root,
        status_path=status_path,
        health_path=health_path,
        audit_log_path=audit_log_path,
        scheduler_path=scheduler_path,
        db_path=db_path,
        engine_store=engine_store,
        prompt_template=contract.prompt_template,
        runtimes=runtimes,
        _run=runner,
        _run_json=runner_json,
        retry_entries={},
        running_entries={},
        runtime_sessions={},
        runtime_totals={},
    )
    workspace._restore_scheduler_state()
    return workspace


def make_workspace(*, workflow_root: Path, config: dict) -> IssueRunnerWorkspace:
    return load_workspace_from_config(workspace_root=workflow_root, config=config)

