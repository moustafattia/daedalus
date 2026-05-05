"""TUI frame rendering for /sprints watch.

Phase 2 (this file) implements the frame renderer. The live loop is wired
in later — this module exposes ``render_frame_to_string(snapshot)`` so the
CLI handler and tests can both produce frame text without spinning up a
real TTY.
"""

from __future__ import annotations

import json
import sys
import time
from io import StringIO
from pathlib import Path
from typing import Any, Mapping

from rich.console import Console
from rich.markup import escape as _esc
from rich.panel import Panel
from rich.table import Table

from sprints.observe import sources as _watch_sources


def _lanes_table(lanes: list[dict[str, Any]]) -> Table:
    t = Table(title="Active lanes", expand=True)
    t.add_column("Lane")
    t.add_column("Issue")
    t.add_column("Stage")
    t.add_column("Status")
    t.add_column("Actor")
    t.add_column("Dispatch")
    t.add_column("Try", justify="right")
    t.add_column("Effects", justify="right")
    t.add_column("PR")
    t.add_column("Retry")
    t.add_column("Attention")
    if not lanes:
        t.add_row("(no active lanes)", "", "", "", "", "", "", "", "", "", "")
        return t
    for lane in lanes:
        if lane.get("_stale"):
            t.add_row(
                _esc("[stale]"),
                _esc("[stale]"),
                _esc("[stale]"),
                _esc("[stale]"),
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            )
            continue
        t.add_row(
            _short(lane.get("lane_id"), 18),
            _short(lane.get("issue_identifier") or lane.get("issue_number"), 14),
            _short(lane.get("stage") or lane.get("workflow_state"), 12),
            _short(lane.get("status") or lane.get("lane_status"), 18),
            _short(lane.get("actor"), 14),
            _short(_dispatch_label(lane), 18),
            str(lane.get("attempt") or ""),
            str(lane.get("side_effect_count") or ""),
            _short(_pull_request_label(lane), 16),
            _short(_retry_label(lane), 18),
            _short(lane.get("operator_attention_reason"), 22),
        )
    return t


def _short(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "."


def _pull_request_label(lane: Mapping[str, Any]) -> str:
    number = lane.get("pull_request_number")
    if number not in (None, ""):
        return f"#{number}"
    url = str(lane.get("pull_request_url") or "").strip()
    if not url:
        return ""
    return url.rstrip("/").rsplit("/", 1)[-1]


def _dispatch_label(lane: Mapping[str, Any]) -> str:
    status = str(lane.get("dispatch_status") or "").strip()
    if not status:
        return ""
    actor = str(lane.get("dispatch_actor") or lane.get("actor") or "").strip()
    mode = str(lane.get("dispatch_mode") or "").strip()
    pieces = [status]
    if actor:
        pieces.append(actor)
    if mode:
        pieces.append(mode)
    return " ".join(pieces)


def _retry_label(lane: Mapping[str, Any]) -> str:
    retry_at = str(lane.get("retry_at") or "").strip()
    target = str(lane.get("retry_target") or "").strip()
    attempt = lane.get("retry_attempt")
    max_attempts = lane.get("retry_max_attempts")
    delay = lane.get("retry_delay_seconds")
    reason = str(lane.get("retry_reason") or "").strip()
    pieces = []
    if target:
        pieces.append(target)
    if attempt not in (None, "") and max_attempts not in (None, ""):
        pieces.append(f"{attempt}/{max_attempts}")
    elif attempt not in (None, ""):
        pieces.append(f"try {attempt}")
    if retry_at:
        pieces.append(f"@ {retry_at[:16]}")
    if delay not in (None, ""):
        pieces.append(f"+{delay}s")
    if reason:
        pieces.append(reason)
    return " ".join(str(piece) for piece in pieces)


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
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        t.add_row(
            str(ev.get("at") or ev.get("created_at") or ev.get("time") or "")[:19],
            str(ev.get("source") or "engine-events"),
            str(ev.get("event") or ev.get("action") or ev.get("event_type") or ""),
            str(
                ev.get("detail")
                or ev.get("summary")
                or payload.get("summary")
                or payload.get("error")
                or ""
            ),
        )
    return t


def _workflow_status_panel(workflow_status: Mapping[str, Any]) -> Panel | None:
    if not workflow_status:
        return None
    lines = [
        f"workflow={workflow_status.get('workflow') or '?'}",
        f"health={workflow_status.get('health') or '?'}",
        f"active={workflow_status.get('active_lane_count') or 0}",
        f"decision_ready={workflow_status.get('decision_ready_count') or 0}",
        f"running={workflow_status.get('running_count') or 0}",
        f"retry={workflow_status.get('retry_count') or 0}",
        f"operator_attention={workflow_status.get('operator_attention_count') or 0}",
        f"canceling={workflow_status.get('canceling_count') or 0}",
        f"tokens={workflow_status.get('total_tokens') or 0}",
    ]
    retry_wakeup = (
        workflow_status.get("retry_wakeup")
        if isinstance(workflow_status.get("retry_wakeup"), Mapping)
        else {}
    )
    if retry_wakeup:
        lines.append(
            "retry_wakeup="
            f"queued={retry_wakeup.get('queued_count') or 0} "
            f"due={retry_wakeup.get('due_count') or 0} "
            f"next={retry_wakeup.get('next_due_in_seconds')}"
        )
    if workflow_status.get("selected_issue"):
        lines.append(f"selected={workflow_status.get('selected_issue')}")
    if workflow_status.get("rate_limits"):
        lines.append(f"rate_limits={workflow_status.get('rate_limits')}")
    for run in (workflow_status.get("latest_runs") or [])[:3]:
        lines.append(
            "run="
            f"{run.get('mode') or '?'}:{run.get('status') or '?'} "
            f"selected={run.get('selected_count') or 0} "
            f"completed={run.get('completed_count') or 0}"
        )
    for session in workflow_status.get("runtime_sessions") or []:
        if session.get("status") == "canceling" or session.get("cancel_requested"):
            lines.append(
                "runtime_canceling="
                f"{session.get('issue_identifier') or session.get('issue_id')} "
                f"thread={session.get('thread_id')} "
                f"turn={session.get('turn_id')} "
                f"reason={session.get('cancel_reason') or '?'}"
            )
    if workflow_status.get("updated_at"):
        lines.append(f"updated_at={workflow_status.get('updated_at')}")
    return Panel("\n".join(_esc(str(line)) for line in lines), title="Workflow status")


def render_frame_to_string(snapshot: Mapping[str, Any]) -> str:
    """Render one TUI frame as a plain string (suitable for tests + no-TTY)."""
    console = Console(
        record=True,
        width=120,
        force_terminal=False,
        file=StringIO(),
    )
    console.print(Panel("Sprints active lanes", style="bold"))
    console.print(_lanes_table(snapshot.get("active_lanes") or []))
    workflow_status_panel = _workflow_status_panel(
        snapshot.get("workflow_status") or {}
    )
    if workflow_status_panel is not None:
        console.print(workflow_status_panel)
    alerts_panel = _alerts_panel(snapshot.get("alert_state") or {})
    if alerts_panel is not None:
        console.print(alerts_panel)
    console.print(_events_table(snapshot.get("recent_events") or []))
    return console.export_text()

def build_snapshot(workflow_root) -> dict[str, Any]:
    """Aggregate all data sources into one TUI snapshot dict."""
    engine_events = _watch_sources.recent_engine_events(workflow_root, limit=50)
    if engine_events:
        merged = engine_events
    else:
        sprints_events = _watch_sources.recent_sprints_events(workflow_root, limit=25)
        workflow_audit = _watch_sources.recent_workflow_audit(workflow_root, limit=25)
        sprints_tagged = [{**e, "source": "sprints"} for e in sprints_events]
        workflow_tagged = [{**e, "source": "workflow"} for e in workflow_audit]
        merged = sprints_tagged + workflow_tagged
        merged.sort(key=lambda e: e.get("at") or "", reverse=True)

    return {
        "active_lanes": _watch_sources.active_lanes(workflow_root),
        "workflow_status": _watch_sources.workflow_status(workflow_root),
        "alert_state": _watch_sources.alert_state(workflow_root),
        "recent_events": merged[:50],
    }


def _stdout_is_tty() -> bool:
    return sys.stdout.isatty()


def cmd_watch(args, parser) -> str:
    """``/sprints watch`` handler.

    Renders a single frame and returns it. When stdout is a TTY and ``--once``
    is not set, enters a rich.live polling loop; that path returns the empty
    string after the user quits. Tests always exercise the one-shot path.
    """
    workflow_root = (
        Path(args.workflow_root)
        if not isinstance(args.workflow_root, Path)
        else args.workflow_root
    )
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
        with Live(
            render_frame_to_string(snapshot),
            console=console,
            refresh_per_second=4,
            screen=True,
        ) as live:
            while True:
                sleep(interval)
                snapshot = build_snapshot(workflow_root)
                live.update(render_frame_to_string(snapshot))
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
# `sprints.stall.detected` event, a termination, a
# `sprints.stall.terminated` event, and a queued retry.
def reconcile_stalls_tick(
    *,
    snapshot,
    running: Mapping[str, Any],
    event_log_path: Path,
    orchestrator,
    now: float | None = None,
) -> list:
    from sprints.observe.stalls import (
        SPRINTS_STALL_DETECTED,
        SPRINTS_STALL_TERMINATED,
        reconcile_stalls,
    )

    if now is None:
        now = time.monotonic()

    def append_event(event: dict[str, Any]) -> None:
        event_log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **event,
        }
        with event_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")

    verdicts = reconcile_stalls(snapshot, running, now=now)
    for verdict in verdicts:
        append_event(
            {
                "type": SPRINTS_STALL_DETECTED,
                "issue_id": verdict.issue_id,
                "elapsed_seconds": verdict.elapsed_seconds,
                "threshold_seconds": verdict.threshold_seconds,
            }
        )
        orchestrator.terminate_worker(verdict.issue_id, reason="stall")
        append_event({"type": SPRINTS_STALL_TERMINATED, "issue_id": verdict.issue_id})
        orchestrator.queue_retry(verdict.issue_id, error="stall_timeout")
    return verdicts
