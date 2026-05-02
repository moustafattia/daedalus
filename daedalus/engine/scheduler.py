from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RestoredSchedulerState:
    retry_entries: dict[str, dict[str, Any]]
    recovered_running: list[dict[str, Any]]
    codex_totals: dict[str, Any]
    codex_threads: dict[str, dict[str, Any]]


def _value_or_default(value: Any, default: Any) -> Any:
    return default if value in (None, "") else value


def _first_value_or_default(default: Any, *values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return default


def retry_due_at(
    entry: dict[str, Any] | None,
    *,
    default: float | None = None,
    now_epoch: float | None = None,
) -> float:
    payload = entry or {}
    if payload.get("due_at_monotonic") is not None:
        return float(payload.get("due_at_monotonic") or 0.0)
    if payload.get("due_at_epoch") is not None:
        return float(payload.get("due_at_epoch") or 0.0)
    if payload.get("dueAtEpoch") is not None:
        return float(payload.get("dueAtEpoch") or 0.0)
    if default is not None:
        return float(default)
    return float(now_epoch if now_epoch is not None else time.time())


def restore_scheduler_state(payload: dict[str, Any], *, now_epoch: float) -> RestoredSchedulerState:
    retry_entries: dict[str, dict[str, Any]] = {}
    for item in payload.get("retry_queue") or payload.get("retryQueue") or []:
        if not isinstance(item, dict):
            continue
        issue_id = str(item.get("issue_id") or item.get("issueId") or "").strip()
        if not issue_id:
            continue
        retry_entries[issue_id] = {
            "issue_id": issue_id,
            "identifier": item.get("identifier"),
            "attempt": int(item.get("attempt") or 0),
            "error": item.get("error"),
            "due_at_epoch": float(_first_value_or_default(now_epoch, item.get("due_at_epoch"), item.get("dueAtEpoch"))),
            "current_attempt": item.get("current_attempt") or item.get("currentAttempt"),
            "run_id": item.get("run_id") or item.get("runId"),
        }

    recovered_running: list[dict[str, Any]] = []
    for item in payload.get("running") or []:
        if not isinstance(item, dict):
            continue
        issue_id = str(item.get("issue_id") or item.get("issueId") or "").strip()
        if not issue_id:
            continue
        started_at_epoch = float(
            _first_value_or_default(now_epoch, item.get("started_at_epoch"), item.get("startedAtEpoch"))
        )
        recovered_running.append(
            {
                "issue_id": issue_id,
                "worker_id": item.get("worker_id") or item.get("workerId") or f"worker:{issue_id}:recovered",
                "identifier": item.get("identifier"),
                "attempt": int(item.get("attempt") or 0),
                "state": item.get("state"),
                "worker_status": item.get("worker_status") or item.get("workerStatus") or "recovered",
                "started_at_epoch": started_at_epoch,
                "heartbeat_at_epoch": float(
                    _first_value_or_default(
                        started_at_epoch,
                        item.get("heartbeat_at_epoch"),
                        item.get("heartbeatAtEpoch"),
                    )
                ),
                "cancel_requested": bool(item.get("cancel_requested") or item.get("cancelRequested") or False),
                "cancel_reason": item.get("cancel_reason") or item.get("cancelReason"),
                "run_id": item.get("run_id") or item.get("runId"),
            }
        )

    return RestoredSchedulerState(
        retry_entries=retry_entries,
        recovered_running=recovered_running,
        codex_totals=dict(payload.get("codex_totals") or payload.get("codexTotals") or {}),
        codex_threads=restore_codex_threads(payload.get("codex_threads") or payload.get("codexThreads") or {}),
    )


def running_snapshot(
    running_entries: dict[str, dict[str, Any]],
    *,
    now_epoch: float,
) -> list[dict[str, Any]]:
    running = []
    for issue_id, entry in running_entries.items():
        started_at_epoch = float(_value_or_default(entry.get("started_at_epoch"), now_epoch))
        heartbeat_at_epoch = float(_value_or_default(entry.get("heartbeat_at_epoch"), started_at_epoch))
        running.append(
            {
                "issue_id": issue_id,
                "worker_id": entry.get("worker_id"),
                "identifier": entry.get("identifier"),
                "attempt": int(entry.get("attempt") or 0),
                "state": entry.get("state"),
                "worker_status": entry.get("worker_status") or "running",
                "started_at_epoch": started_at_epoch,
                "heartbeat_at_epoch": heartbeat_at_epoch,
                "running_for_ms": max(int((now_epoch - started_at_epoch) * 1000), 0),
                "heartbeat_age_ms": max(int((now_epoch - heartbeat_at_epoch) * 1000), 0),
                "cancel_requested": bool(entry.get("cancel_requested") or False),
                "cancel_reason": entry.get("cancel_reason"),
                "thread_id": entry.get("thread_id"),
                "turn_id": entry.get("turn_id"),
                "run_id": entry.get("run_id") or entry.get("runId"),
            }
        )
    running.sort(key=lambda item: (item["state"] or "", item["identifier"] or item["issue_id"]))
    return running


def retry_queue_snapshot(
    retry_entries: dict[str, dict[str, Any]],
    *,
    now_epoch: float,
) -> list[dict[str, Any]]:
    entries = []
    for issue_id, entry in retry_entries.items():
        due_at = retry_due_at(entry, default=now_epoch)
        entries.append(
            {
                "issue_id": issue_id,
                "identifier": entry.get("identifier"),
                "attempt": int(entry.get("attempt") or 0),
                "error": entry.get("error"),
                "due_at_epoch": due_at,
                "due_in_ms": max(int((due_at - now_epoch) * 1000), 0),
                "run_id": entry.get("run_id") or entry.get("runId"),
            }
        )
    entries.sort(key=lambda item: (item["due_in_ms"], item["attempt"], item["identifier"] or item["issue_id"]))
    return entries


def restore_codex_threads(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    restored: dict[str, dict[str, Any]] = {}
    for issue_id, item in raw.items():
        if not isinstance(item, dict):
            continue
        normalized_issue_id = str(item.get("issue_id") or issue_id or "").strip()
        thread_id = str(item.get("thread_id") or "").strip()
        if not normalized_issue_id or not thread_id:
            continue
        restored[normalized_issue_id] = {
            "issue_id": normalized_issue_id,
            "identifier": item.get("identifier"),
            "session_name": item.get("session_name"),
            "thread_id": thread_id,
            "turn_id": item.get("turn_id"),
            "run_id": item.get("run_id") or item.get("runId"),
            "updated_at": item.get("updated_at"),
        }
    return restored


def codex_threads_snapshot(codex_threads: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        issue_id: dict(entry)
        for issue_id, entry in sorted(codex_threads.items(), key=lambda item: item[0])
    }


def build_scheduler_payload(
    *,
    workflow: str,
    retry_entries: dict[str, dict[str, Any]],
    running_entries: dict[str, dict[str, Any]],
    codex_totals: dict[str, Any] | None,
    codex_threads: dict[str, dict[str, Any]],
    now_iso: str,
    now_epoch: float,
) -> dict[str, Any]:
    return {
        "workflow": workflow,
        "updatedAt": now_iso,
        "retry_queue": retry_queue_snapshot(retry_entries, now_epoch=now_epoch),
        "running": running_snapshot(running_entries, now_epoch=now_epoch),
        "codex_totals": dict(codex_totals or {}),
        "codex_threads": codex_threads_snapshot(codex_threads),
    }
