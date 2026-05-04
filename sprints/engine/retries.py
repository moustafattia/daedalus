from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
