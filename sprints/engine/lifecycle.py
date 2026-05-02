from __future__ import annotations

from typing import Any

from .work import RetryEntry, RunningWork, WorkItemRef


def clear_work_entries(
    entries: dict[str, dict[str, Any]], work_ids: list[str | None]
) -> dict[str, dict[str, Any]]:
    next_entries = dict(entries)
    for work_id in work_ids:
        if work_id:
            next_entries.pop(str(work_id), None)
    return next_entries


def mark_running_work(
    running_entries: dict[str, dict[str, Any]],
    *,
    work_items: list[tuple[WorkItemRef, int]],
    now_epoch: float,
) -> dict[str, dict[str, Any]]:
    entries = dict(running_entries)
    for work_item, attempt in work_items:
        running = RunningWork(
            work_item=work_item,
            worker_id=f"worker:{work_item.id}:{int(now_epoch * 1000)}",
            attempt=max(int(attempt or 0), 0),
            started_at_epoch=now_epoch,
            heartbeat_at_epoch=now_epoch,
        )
        entries[work_item.id] = running.to_scheduler_entry()
    return entries


def retry_delay(*, delay_type: str, retry_attempt: int, max_backoff_ms: int) -> int:
    if delay_type == "continuation":
        return 1000
    return min(max_backoff_ms, 10000 * (2 ** max(retry_attempt - 1, 0)))


def schedule_retry_entry(
    *,
    work_item: WorkItemRef,
    existing_entry: dict[str, Any] | None,
    error: str,
    current_attempt: int | None,
    delay_type: str,
    max_backoff_ms: int,
    now_epoch: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if delay_type == "continuation":
        retry_attempt = 1
    else:
        retry_attempt = int((existing_entry or {}).get("attempt") or 0) + 1
    delay_ms = retry_delay(
        delay_type=delay_type,
        retry_attempt=retry_attempt,
        max_backoff_ms=max_backoff_ms,
    )
    entry = RetryEntry(
        work_item=work_item,
        attempt=retry_attempt,
        due_at_epoch=now_epoch + (delay_ms / 1000.0),
        error=error,
        current_attempt=current_attempt,
        delay_type=delay_type,
    ).to_scheduler_entry()
    summary = {
        "issue_id": work_item.id,
        "identifier": work_item.identifier,
        "retry_attempt": retry_attempt,
        "delay_ms": delay_ms,
        "delay_type": delay_type,
    }
    return entry, summary


def recover_running_as_retry(
    retry_entries: dict[str, dict[str, Any]],
    recovered_running: list[dict[str, Any]],
    *,
    now_epoch: float,
    error: str = "scheduler restarted while work item was running",
) -> dict[str, dict[str, Any]]:
    entries = dict(retry_entries)
    for running in recovered_running:
        issue_id = str(running.get("issue_id") or "").strip()
        if not issue_id:
            continue
        existing = entries.get(issue_id) or {}
        entries[issue_id] = {
            "issue_id": issue_id,
            "identifier": running.get("identifier"),
            "attempt": max(
                int(existing.get("attempt") or 0), int(running.get("attempt") or 0), 1
            ),
            "error": error,
            "due_at_epoch": now_epoch,
            "current_attempt": running.get("attempt"),
            "run_id": running.get("run_id"),
        }
    return entries
