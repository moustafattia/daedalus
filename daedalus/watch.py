"""TUI frame rendering for /daedalus watch.

Phase 2 (this file) implements the frame renderer. The live loop is wired
in later — this module exposes ``render_frame_to_string(snapshot)`` so the
CLI handler and tests can both produce frame text without spinning up a
real TTY.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from rich.console import Console
from rich.markup import escape as _esc
from rich.panel import Panel
from rich.table import Table


def _lanes_table(lanes: list[dict[str, Any]]) -> Table:
    t = Table(title="Active lanes", expand=True)
    t.add_column("Lane")
    t.add_column("State")
    t.add_column("GH Issue")
    if not lanes:
        t.add_row("(no active lanes)", "", "")
        return t
    for lane in lanes:
        if lane.get("_stale"):
            t.add_row(_esc("[stale]"), _esc("[stale]"), _esc("[stale]"))
            continue
        t.add_row(
            str(lane.get("lane_id") or ""),
            str(lane.get("state") or ""),
            str(lane.get("github_issue_number") or ""),
        )
    return t


def _alerts_panel(alert_state: Mapping[str, Any]) -> Panel | None:
    if alert_state.get("_stale"):
        return Panel(_esc("[stale] alert source unreadable"), title="⚠️  Active alerts")
    if not alert_state or not alert_state.get("active"):
        return None
    msg = alert_state.get("message") or alert_state.get("fingerprint") or "active alert"
    return Panel(str(msg), title="⚠️  Active alerts")


def _events_table(events: list[dict[str, Any]]) -> Table:
    t = Table(title="Recent events", expand=True)
    t.add_column("Time")
    t.add_column("Source")
    t.add_column("Event")
    t.add_column("Detail")
    if not events:
        t.add_row("(no events)", "", "", "")
        return t
    for ev in events[:50]:
        t.add_row(
            str(ev.get("at") or ev.get("time") or "")[:19],
            str(ev.get("source") or "daedalus"),
            str(ev.get("event") or ev.get("action") or ""),
            str(ev.get("detail") or ev.get("summary") or ""),
        )
    return t


def render_frame_to_string(snapshot: Mapping[str, Any]) -> str:
    """Render one TUI frame as a plain string (suitable for tests + no-TTY)."""
    console = Console(record=True, width=120, force_terminal=False)
    console.print(Panel("Daedalus active lanes", style="bold"))
    console.print(_lanes_table(snapshot.get("active_lanes") or []))
    alerts_panel = _alerts_panel(snapshot.get("alert_state") or {})
    if alerts_panel is not None:
        console.print(alerts_panel)
    console.print(_events_table(snapshot.get("recent_events") or []))
    return console.export_text()


# Sibling-import boilerplate for the aggregator.
try:
    from . import watch_sources as _watch_sources  # type: ignore[import-not-found]
except ImportError:
    import importlib.util as _ilu
    from pathlib import Path as _Path
    _spec = _ilu.spec_from_file_location("daedalus_watch_sources_for_watch", _Path(__file__).resolve().parent / "watch_sources.py")
    _watch_sources = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_watch_sources)


def build_snapshot(workflow_root) -> dict[str, Any]:
    """Aggregate all data sources into one TUI snapshot dict."""
    daedalus_events = _watch_sources.recent_daedalus_events(workflow_root, limit=25)
    workflow_audit = _watch_sources.recent_workflow_audit(workflow_root, limit=25)

    # Tag source onto each row, then merge + sort newest-first by 'at'.
    daedalus_tagged = [{**e, "source": "daedalus"} for e in daedalus_events]
    workflow_tagged = [{**e, "source": "workflow"} for e in workflow_audit]
    merged = daedalus_tagged + workflow_tagged
    merged.sort(key=lambda e: e.get("at") or "", reverse=True)

    return {
        "active_lanes": _watch_sources.active_lanes(workflow_root),
        "alert_state": _watch_sources.alert_state(workflow_root),
        "recent_events": merged[:50],
    }


import sys as _sys


def _stdout_is_tty() -> bool:
    return _sys.stdout.isatty()


def cmd_watch(args, parser) -> str:
    """``/daedalus watch`` handler.

    Renders a single frame and returns it. When stdout is a TTY and ``--once``
    is not set, enters a rich.live polling loop; that path returns the empty
    string after the user quits. Tests always exercise the one-shot path.
    """
    workflow_root = Path(args.workflow_root) if not isinstance(args.workflow_root, Path) else args.workflow_root
    snapshot = build_snapshot(workflow_root)
    text = render_frame_to_string(snapshot)
    if getattr(args, "once", False) or not _stdout_is_tty():
        return text

    # Live mode — rich.live polling at 2s.
    from rich.live import Live
    from rich.console import Console
    from time import sleep

    console = Console()
    interval = float(getattr(args, "interval", 2.0) or 2.0)
    try:
        with Live(render_frame_to_string(snapshot), console=console, refresh_per_second=4, screen=True):
            while True:
                sleep(interval)
                snapshot = build_snapshot(workflow_root)
                # rich.live can take Renderable; we render to text inside the live update for simplicity
                console.print(render_frame_to_string(snapshot))
    except KeyboardInterrupt:
        return ""
    return ""


# --- Stall reconciliation hook (Symphony §8.5) ---------------------------
#
# This function is called from the tick loop owner BEFORE tracker-state
# refresh per spec §8.6, so a stalled worker on a now-terminal issue still
# gets stall-terminated. The contract is intentionally minimal: the caller
# supplies a snapshot, a running-lanes mapping (issue_id -> entry exposing
# `.runtime` and `.started_at_monotonic`), an event-log path, and an
# orchestrator that supports `terminate_worker(issue_id, reason=...)` and
# `queue_retry(issue_id, error=...)`. Each detected stall produces a
# `daedalus.stall.detected` event, a termination, a
# `daedalus.stall.terminated` event, and a queued retry.
def reconcile_stalls_tick(
    *,
    snapshot,
    running: Mapping[str, Any],
    event_log_path: Path,
    orchestrator,
    now: float | None = None,
) -> list:
    import time as _time

    from workflows.code_review.event_taxonomy import (
        DAEDALUS_STALL_DETECTED,
        DAEDALUS_STALL_TERMINATED,
    )
    from workflows.code_review.stall import reconcile_stalls
    from runtime import append_daedalus_event

    if now is None:
        now = _time.monotonic()
    verdicts = reconcile_stalls(snapshot, running, now=now)
    for verdict in verdicts:
        append_daedalus_event(
            event_log_path=event_log_path,
            event={
                "type": DAEDALUS_STALL_DETECTED,
                "issue_id": verdict.issue_id,
                "elapsed_seconds": verdict.elapsed_seconds,
                "threshold_seconds": verdict.threshold_seconds,
            },
        )
        orchestrator.terminate_worker(verdict.issue_id, reason="stall")
        append_daedalus_event(
            event_log_path=event_log_path,
            event={"type": DAEDALUS_STALL_TERMINATED, "issue_id": verdict.issue_id},
        )
        orchestrator.queue_retry(verdict.issue_id, error="stall_timeout")
    return verdicts
