from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkItemRef:
    """Tracker-neutral reference to one unit of workflow work."""

    id: str
    identifier: str | None = None
    state: str | None = None
    title: str | None = None
    url: str | None = None
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "identifier": self.identifier,
            "state": self.state,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RunningWork:
    work_item: WorkItemRef
    worker_id: str
    attempt: int
    started_at_epoch: float
    heartbeat_at_epoch: float
    worker_status: str = "running"
    cancel_requested: bool = False
    cancel_reason: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None

    def to_scheduler_entry(self) -> dict[str, Any]:
        return {
            "issue_id": self.work_item.id,
            "worker_id": self.worker_id,
            "identifier": self.work_item.identifier,
            "attempt": self.attempt,
            "state": self.work_item.state,
            "worker_status": self.worker_status,
            "started_at_epoch": self.started_at_epoch,
            "heartbeat_at_epoch": self.heartbeat_at_epoch,
            "cancel_requested": self.cancel_requested,
            "cancel_reason": self.cancel_reason,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
        }


@dataclass(frozen=True)
class RetryEntry:
    work_item: WorkItemRef
    attempt: int
    due_at_epoch: float
    error: str
    current_attempt: int | None = None
    delay_type: str = "failure"

    def to_scheduler_entry(self) -> dict[str, Any]:
        return {
            "issue_id": self.work_item.id,
            "identifier": self.work_item.identifier,
            "attempt": self.attempt,
            "due_at_epoch": self.due_at_epoch,
            "error": self.error,
            "current_attempt": self.current_attempt,
            "delay_type": self.delay_type,
        }


@dataclass(frozen=True)
class WorkResult:
    work_item: WorkItemRef
    ok: bool
    attempt: int
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


def work_item_from_issue(
    issue: dict[str, Any], *, source: str | None = None
) -> WorkItemRef:
    issue_id = str(issue.get("id") or "").strip()
    if not issue_id:
        raise ValueError("issue is missing id")
    return WorkItemRef(
        id=issue_id,
        identifier=str(issue.get("identifier") or issue_id).strip() or issue_id,
        state=str(issue.get("state") or "").strip() or None,
        title=str(issue.get("title") or "").strip() or None,
        url=str(issue.get("url") or "").strip() or None,
        source=source,
        metadata={"raw": issue},
    )
