"""Stall detection (Symphony §8.5).

Pure function: snapshot + running-state map + clock -> list of verdicts.
The caller (watch.py) acts on the verdicts (kills workers, queues retries).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Protocol

from workflows.change_delivery.config_snapshot import ConfigSnapshot


_DEFAULT_TIMEOUT_MS = 300_000


@dataclass(frozen=True)
class StallVerdict:
    issue_id: str
    elapsed_seconds: float
    threshold_seconds: float
    action: Literal["terminate", "warn", "noop"]


class _RunningEntry(Protocol):
    """Structural type for running-lane entries — only the two attrs we use."""

    started_at_monotonic: float

    def runtime(self): ...  # actually .runtime is a Runtime instance attr


def reconcile_stalls(
    snapshot: ConfigSnapshot,
    running: Mapping[str, object],
    now: float,
) -> list[StallVerdict]:
    """Return a `terminate` verdict for every running entry whose most-recent
    activity (or, if none, started_at_monotonic) is older than `now -
    snapshot.config.stall.timeout_ms`. `timeout_ms <= 0` disables the check.

    The map values are duck-typed: must expose `.runtime.last_activity_ts()`
    and `.started_at_monotonic`. This keeps the function decoupled from the
    concrete RunningEntry class in orchestrator.py.
    """
    stall_cfg = (snapshot.config or {}).get("stall") or {}
    threshold_ms = stall_cfg.get("timeout_ms", _DEFAULT_TIMEOUT_MS)
    if threshold_ms <= 0:
        return []
    threshold_s = threshold_ms / 1000.0

    out: list[StallVerdict] = []
    for issue_id, entry in running.items():
        rt = getattr(entry, "runtime", None)
        # OPT-OUT: runtime instance lacks `last_activity_ts` attribute entirely.
        # Per spec §8.1, opting out skips stall enforcement; we do NOT fall
        # back to started_at_monotonic for these. Codex P1 finding on PR #16.
        if rt is None or not hasattr(rt, "last_activity_ts"):
            continue
        last = rt.last_activity_ts()
        # Method defined and returned None = "still in startup, not yet
        # produced a signal" — fall back to started_at so a hung-startup
        # worker still has a deadline.
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
