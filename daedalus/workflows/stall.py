"""Stall detection (Symphony Â§8.5)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol

_DEFAULT_TIMEOUT_MS = 300_000


@dataclass(frozen=True)
class StallVerdict:
    issue_id: str
    elapsed_seconds: float
    threshold_seconds: float
    action: Literal["terminate", "warn", "noop"]


class _RunningEntry(Protocol):
    started_at_monotonic: float

    def runtime(self): ...


def reconcile_stalls(
    snapshot: Any,
    running: Mapping[str, object],
    now: float,
) -> list[StallVerdict]:
    stall_cfg = (snapshot.config or {}).get("stall") or {}
    threshold_ms = stall_cfg.get("timeout_ms", _DEFAULT_TIMEOUT_MS)
    if threshold_ms <= 0:
        return []
    threshold_s = threshold_ms / 1000.0

    out: list[StallVerdict] = []
    for issue_id, entry in running.items():
        rt = getattr(entry, "runtime", None)
        if rt is None or not hasattr(rt, "last_activity_ts"):
            continue
        last = rt.last_activity_ts()
        baseline = last if last is not None else entry.started_at_monotonic
        elapsed = now - baseline
        if elapsed > threshold_s:
            out.append(
                StallVerdict(
                    issue_id=issue_id,
                    elapsed_seconds=elapsed,
                    threshold_seconds=threshold_s,
                    action="terminate",
                )
            )
    return out


