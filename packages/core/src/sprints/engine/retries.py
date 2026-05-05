from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time
from typing import Any, Mapping


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay_seconds: int = 0
    backoff_multiplier: float = 2.0
    max_delay_seconds: int = 300


@dataclass(frozen=True)
class RetrySchedule:
    status: str
    current_attempt: int
    next_attempt: int
    max_attempts: int
    delay_seconds: int | None = None
    due_at_epoch: float | None = None
    engine_retry: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "current_attempt": self.current_attempt,
            "next_attempt": self.next_attempt,
            "max_attempts": self.max_attempts,
            "delay_seconds": self.delay_seconds,
            "due_at_epoch": self.due_at_epoch,
            "engine_retry": dict(self.engine_retry or {}),
        }


def plan_retry(
    *, policy: RetryPolicy, current_attempt: int, now_epoch: float
) -> RetrySchedule:
    current = max(int(current_attempt or 1), 1)
    if current >= policy.max_attempts:
        return RetrySchedule(
            status="limit_exceeded",
            current_attempt=current,
            next_attempt=current,
            max_attempts=policy.max_attempts,
        )
    next_attempt = current + 1
    delay_seconds = retry_delay_seconds(policy=policy, next_attempt=next_attempt)
    return RetrySchedule(
        status="queued",
        current_attempt=current,
        next_attempt=next_attempt,
        max_attempts=policy.max_attempts,
        delay_seconds=delay_seconds,
        due_at_epoch=now_epoch + delay_seconds,
    )


def retry_delay_seconds(*, policy: RetryPolicy, next_attempt: int) -> int:
    retry_index = max(int(next_attempt or 1) - 2, 0)
    delay = policy.initial_delay_seconds * (policy.backoff_multiplier**retry_index)
    return int(min(delay, policy.max_delay_seconds))


def retry_record(
    *,
    stage: str,
    target: str | None,
    reason: str | None,
    inputs: Mapping[str, Any] | None,
    schedule: Mapping[str, Any],
    now_iso: str | None = None,
) -> dict[str, Any]:
    due_at_epoch = retry_schedule_due_at_epoch(schedule)
    return {
        "status": schedule.get("status"),
        "queued_at": retry_schedule_updated_at(schedule) or now_iso or utc_now_iso(),
        "stage": stage,
        "target": target,
        "reason": reason,
        "inputs": dict(inputs or {}),
        "current_attempt": int(schedule.get("current_attempt") or 0),
        "next_attempt": int(schedule.get("next_attempt") or 0),
        "max_attempts": int(schedule.get("max_attempts") or 0),
        "delay_seconds": schedule.get("delay_seconds"),
        "due_at": epoch_to_iso(due_at_epoch) if due_at_epoch is not None else None,
        "due_at_epoch": due_at_epoch,
        "engine_retry": schedule.get("engine_retry") or None,
    }


def pending_retry_projection(
    *,
    stage: str,
    target: str | None,
    reason: str | None,
    inputs: Mapping[str, Any] | None,
    schedule: Mapping[str, Any],
    now_epoch: float | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    now = time.time() if now_epoch is None else now_epoch
    due_at_epoch = retry_schedule_due_at_epoch(schedule)
    due = due_at_epoch if due_at_epoch is not None else now
    return {
        "source": "engine_retry_queue",
        "stage": stage,
        "target": target,
        "reason": reason,
        "inputs": dict(inputs or {}),
        "attempt": int(schedule.get("next_attempt") or 0),
        "current_attempt": int(schedule.get("current_attempt") or 0),
        "queued_at": retry_schedule_updated_at(schedule) or now_iso or utc_now_iso(),
        "delay_seconds": int(schedule.get("delay_seconds") or 0),
        "due_at": epoch_to_iso(due),
        "due_at_epoch": due,
        "max_attempts": int(schedule.get("max_attempts") or 0),
        "engine_retry": schedule.get("engine_retry") or None,
    }


def retry_is_due(
    pending_retry: Mapping[str, Any], *, now_epoch: float | None = None
) -> bool:
    now = time.time() if now_epoch is None else now_epoch
    return now >= pending_retry_due_at_epoch(pending_retry, default=now)


def pending_retry_due_at_epoch(
    pending_retry: Mapping[str, Any], *, default: float | None = None
) -> float:
    fallback = time.time() if default is None else default
    value = pending_retry.get("due_at_epoch")
    if value not in (None, ""):
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return iso_to_epoch(str(pending_retry.get("due_at") or ""), default=fallback)


def retry_schedule_updated_at(schedule: Mapping[str, Any]) -> str:
    engine_retry = (
        schedule.get("engine_retry")
        if isinstance(schedule.get("engine_retry"), Mapping)
        else {}
    )
    return str(engine_retry.get("updated_at") or "").strip()


def retry_schedule_due_at_epoch(schedule: Mapping[str, Any]) -> float | None:
    value = schedule.get("due_at_epoch")
    if value in (None, ""):
        engine_retry = (
            schedule.get("engine_retry")
            if isinstance(schedule.get("engine_retry"), Mapping)
            else {}
        )
        value = engine_retry.get("due_at_epoch")
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def iso_to_epoch(value: str, *, default: float) -> float:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return default


def epoch_to_iso(value: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def utc_now_iso() -> str:
    return epoch_to_iso(time.time())
