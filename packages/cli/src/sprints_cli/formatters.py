"""Panel renderer + color helpers for /sprints inspection commands.

This module ships the human-readable text-mode output. ``--json`` mode lives
in ``tools.render_result`` and is unchanged.

Single primitive: :func:`format_panel` consumes a list of :class:`Section`
objects (each with :class:`Row` entries) and renders an aligned panel with
optional ANSI color and status glyphs. Per-command formatters
(``format_status``, ``format_doctor``, etc.) wrap result dicts into Section
objects and call ``format_panel``.
"""

from __future__ import annotations

import inspect as _inspect_for_self_register
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

# ─── Color & glyphs ────────────────────────────────────────────────

_ANSI = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "reset": "\033[0m",
}

_STATUS_GLYPH = {
    "pass": ("ok", "green"),
    "fail": ("x", "red"),
    "warn": ("!", "yellow"),
    "info": ("->", "cyan"),
}

EMPTY_VALUE = "-"
HINT_ARROW = "->"


# Dataclass type resolution expects the current module to be present in
# sys.modules. Self-register the in-flight module before defining dataclasses.
_self_module = _inspect_for_self_register.getmodule(
    _inspect_for_self_register.currentframe()
)
if _self_module is None:
    # Build a stub object that exposes __dict__ via globals().
    class _StubModule:
        pass

    _self_module = _StubModule()
    _self_module.__dict__.update(globals())
sys.modules.setdefault(__name__, _self_module)
del _inspect_for_self_register, _self_module


def _use_color() -> bool:
    """Color is enabled when stdout is a TTY and NO_COLOR is unset."""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _color(text: str, color_name: str, *, use_color: bool) -> str:
    if not use_color:
        return text
    code = _ANSI.get(color_name)
    if not code:
        return text
    return f"{code}{text}{_ANSI['reset']}"


# ─── Helpers used by per-command formatters ────────────────────────────────────────


def render_bool(value: Any) -> str:
    """Convert a boolean (or falsy) into a human-readable token.

    Used by per-command formatters so raw ``True``/``False`` Python literals
    never appear in text output.
    """
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if value is None:
        return EMPTY_VALUE
    return str(value)


def format_path(path: str | Path | None) -> str:
    if path is None or path == "":
        return EMPTY_VALUE
    p = str(path)
    home = os.environ.get("HOME") or str(Path.home())
    if home and p.startswith(home + "/"):
        return "~" + p[len(home) :]
    if home and p == home:
        return "~"
    return p


def _parse_iso(iso_str: str) -> datetime | None:
    if not iso_str:
        return None
    try:
        cleaned = iso_str.replace("Z", "+00:00") if iso_str.endswith("Z") else iso_str
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _humanize_age_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def format_timestamp(iso_str: str, *, now_iso: str | None = None) -> str:
    """Render an ISO-8601 UTC timestamp as ``HH:MM:SS UTC (Ns ago)``.

    Returns ``EMPTY_VALUE`` when input is empty or unparseable.
    """
    dt = _parse_iso(iso_str or "")
    if dt is None:
        return EMPTY_VALUE
    clock = dt.strftime("%H:%M:%S UTC")
    now = _parse_iso(now_iso) if now_iso else datetime.now(timezone.utc)
    if now is None:
        return clock
    age = int((now - dt).total_seconds())
    if age < 0:
        return clock
    return f"{clock} ({_humanize_age_seconds(age)})"


def _compact(value: Any, *, limit: int = 80) -> str:
    text = str(value or "").strip()
    if not text:
        return EMPTY_VALUE
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "."


def _status_lanes(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    lanes = result.get("lanes")
    if isinstance(lanes, Mapping):
        return [lane for lane in lanes.values() if isinstance(lane, Mapping)]
    if isinstance(lanes, list):
        return [lane for lane in lanes if isinstance(lane, Mapping)]
    return []


def _lane_issue_label(lane: Mapping[str, Any]) -> str:
    issue = lane.get("issue") if isinstance(lane.get("issue"), Mapping) else {}
    return str(
        issue.get("identifier")
        or issue.get("number")
        or lane.get("issue_identifier")
        or lane.get("lane_id")
        or EMPTY_VALUE
    )


def _lane_pull_request_label(lane: Mapping[str, Any]) -> str:
    pull_request = (
        lane.get("pull_request")
        if isinstance(lane.get("pull_request"), Mapping)
        else {}
    )
    number = pull_request.get("number") or lane.get("pull_request_number")
    if number not in (None, ""):
        return f"pr=#{number}"
    url = str(pull_request.get("url") or lane.get("pull_request_url") or "").strip()
    if not url:
        return ""
    return f"pr={url.rstrip('/').rsplit('/', 1)[-1]}"


def _lane_retry_label(lane: Mapping[str, Any]) -> str:
    retry = lane.get("retry") if isinstance(lane.get("retry"), Mapping) else {}
    pending = (
        lane.get("pending_retry")
        if isinstance(lane.get("pending_retry"), Mapping)
        else {}
    )
    retry_at = str(
        retry.get("due_at") or pending.get("due_at") or lane.get("retry_at") or ""
    ).strip()
    target = str(
        retry.get("target") or pending.get("target") or lane.get("retry_target") or ""
    ).strip()
    attempt = (
        retry.get("attempt")
        or pending.get("attempt")
        or lane.get("retry_attempt")
        or EMPTY_VALUE
    )
    max_attempts = (
        retry.get("max_attempts")
        or pending.get("max_attempts")
        or lane.get("retry_max_attempts")
    )
    delay = (
        retry.get("delay_seconds")
        or pending.get("delay_seconds")
        or lane.get("retry_delay_seconds")
    )
    reason = str(
        retry.get("failure_reason")
        or retry.get("reason")
        or pending.get("reason")
        or lane.get("retry_reason")
        or ""
    ).strip()
    pieces = []
    if target:
        pieces.append(target)
    if attempt != EMPTY_VALUE and max_attempts not in (None, ""):
        pieces.append(f"{attempt}/{max_attempts}")
    elif attempt != EMPTY_VALUE:
        pieces.append(f"try {attempt}")
    if retry_at:
        pieces.append(f"due={retry_at[:16]}")
    if delay not in (None, ""):
        pieces.append(f"backoff={delay}s")
    if reason:
        pieces.append(f"reason={reason}")
    return "retry=" + " ".join(str(piece) for piece in pieces) if pieces else ""


def _lane_dispatch_label(lane: Mapping[str, Any]) -> str:
    dispatch = (
        lane.get("actor_dispatch")
        if isinstance(lane.get("actor_dispatch"), Mapping)
        else {}
    )
    status = str(dispatch.get("status") or lane.get("dispatch_status") or "").strip()
    if not status:
        return ""
    runtime = (
        dispatch.get("runtime") if isinstance(dispatch.get("runtime"), Mapping) else {}
    )
    actor = str(
        dispatch.get("actor") or lane.get("dispatch_actor") or lane.get("actor") or ""
    ).strip()
    stage = str(dispatch.get("stage") or lane.get("dispatch_stage") or "").strip()
    mode = str(runtime.get("dispatch_mode") or lane.get("dispatch_mode") or "").strip()
    pieces = [status]
    if actor:
        pieces.append(actor)
    if stage:
        pieces.append(stage)
    if mode:
        pieces.append(mode)
    return "dispatch=" + "/".join(pieces)


def _lane_attention_label(lane: Mapping[str, Any]) -> str:
    attention = (
        lane.get("operator_attention")
        if isinstance(lane.get("operator_attention"), Mapping)
        else {}
    )
    reason = str(
        attention.get("reason") or lane.get("operator_attention_reason") or ""
    ).strip()
    return f"attention={reason}" if reason else ""


def _lane_status_glyph(
    lane: Mapping[str, Any],
) -> Literal["pass", "fail", "warn", "info"]:
    status = str(lane.get("status") or lane.get("lane_status") or "").strip()
    if status == "operator_attention":
        return "fail"
    if status in {"retry_queued", "running"}:
        return "warn"
    if status in {"complete", "released"}:
        return "pass"
    return "info"


# ─── Section / Row dataclasses ────────────────────────────────────────


@dataclass
class Row:
    label: str
    value: str
    status: Literal["pass", "fail", "warn", "info"] | None = None
    detail: str | None = None


@dataclass
class Section:
    name: str | None
    rows: list[Row] = field(default_factory=list)


# ─── Panel renderer ────────────────────────────────────────


def format_panel(
    title: str,
    sections: list[Section],
    *,
    use_color: bool | None = None,
    footer: str | None = None,
) -> str:
    """Render a multi-section panel as a string.

    ``use_color=None`` auto-detects via ``_use_color()``. Pass an explicit
    ``True``/``False`` from tests for deterministic output.
    """
    if use_color is None:
        use_color = _use_color()

    lines: list[str] = []
    lines.append(_color(title, "bold", use_color=use_color))

    for section in sections:
        if section.name:
            lines.append("  " + _color(section.name, "dim", use_color=use_color))
            indent = "    "
        else:
            indent = "  "

        rows = section.rows or []
        if not rows:
            continue

        # Compute label-column width for aligned values within this section.
        label_width = max(len(row.label) for row in rows)

        for row in rows:
            value_str = row.value if (row.value not in (None, "")) else EMPTY_VALUE
            if row.status and row.status in _STATUS_GLYPH:
                glyph, color_name = _STATUS_GLYPH[row.status]
                glyph_str = _color(glyph, color_name, use_color=use_color)
                # Glyph + space, then label, then padded value
                line = (
                    f"{indent}{glyph_str} {row.label.ljust(label_width)}  {value_str}"
                )
            else:
                line = f"{indent}{row.label.ljust(label_width)}  {value_str}"
            if row.detail:
                line += f"  {_color(row.detail, 'dim', use_color=use_color)}"
            lines.append(line)

    if footer:
        lines.append("")
        # Footer rendered with cyan arrow as visual hint.
        lines.append(_color(footer, "cyan", use_color=use_color))

    return "\n".join(lines)


# ─── Per-command formatters ────────────────────────────────────────


# ─── /sprints status ────────────────────────────────────────


def format_status(
    result: Mapping[str, Any],
    *,
    use_color: bool | None = None,
    now_iso: str | None = None,
) -> str:
    if result.get("workflow") == "issue-runner":
        tracker = result.get("tracker") or {}
        scheduler = result.get("scheduler") or {}
        selected = result.get("selectedIssue") or {}
        last_run = result.get("lastRun") or {}
        metrics = result.get("metrics") or {}
        tokens = metrics.get("tokens") or {}
        totals = scheduler.get("runtime_totals") or {}
        tracker_rows = [
            Row(label="workflow", value=str(result.get("workflow") or EMPTY_VALUE)),
            Row(label="health", value=str(result.get("health") or EMPTY_VALUE)),
            Row(label="kind", value=str(tracker.get("kind") or EMPTY_VALUE)),
            Row(label="source", value=format_path(tracker.get("path"))),
            Row(
                label="issues",
                value=str(tracker.get("issueCount"))
                if tracker.get("issueCount") is not None
                else EMPTY_VALUE,
            ),
            Row(
                label="eligible",
                value=str(tracker.get("eligibleCount"))
                if tracker.get("eligibleCount") is not None
                else EMPTY_VALUE,
            ),
        ]
        scheduler_rows = [
            Row(label="running", value=str(len(scheduler.get("running") or []))),
            Row(label="retry", value=str(len(scheduler.get("retry_queue") or []))),
            Row(
                label="max concurrent",
                value=str(scheduler.get("max_concurrent_agents") or EMPTY_VALUE),
            ),
        ]
        paths_rows = [
            Row(label="workflow", value=format_path(result.get("workflowRoot"))),
            Row(label="contract", value=format_path(result.get("contractPath"))),
            Row(label="workspace", value=format_path(result.get("workspaceRoot"))),
        ]
        selected_rows = [
            Row(
                label="issue",
                value=str(selected.get("identifier") or selected.get("id") or "none"),
            ),
            Row(label="title", value=str(selected.get("title") or EMPTY_VALUE)),
            Row(label="state", value=str(selected.get("state") or EMPTY_VALUE)),
        ]
        last_run_rows = [
            Row(label="ok", value=render_bool(last_run.get("ok"))),
            Row(
                label="attempt",
                value=str(last_run.get("attempt"))
                if last_run.get("attempt") is not None
                else EMPTY_VALUE,
            ),
            Row(
                label="updated",
                value=format_timestamp(
                    str(last_run.get("updatedAt") or ""), now_iso=now_iso
                ),
            ),
        ]
        token_rows = [
            Row(label="last total", value=str(int(tokens.get("total_tokens") or 0))),
            Row(
                label="last in/out",
                value=f"{int(tokens.get('input_tokens') or 0)}/{int(tokens.get('output_tokens') or 0)}",
            ),
            Row(label="agg total", value=str(int(totals.get("total_tokens") or 0))),
            Row(
                label="agg in/out",
                value=f"{int(totals.get('input_tokens') or 0)}/{int(totals.get('output_tokens') or 0)}",
            ),
            Row(
                label="rate limits",
                value=str(
                    metrics.get("rate_limits")
                    or totals.get("rate_limits")
                    or EMPTY_VALUE
                ),
            ),
        ]
        return format_panel(
            title=f"Issue runner — {result.get('workflowRoot') or result.get('contractPath') or 'workflow'}",
            sections=[
                Section(name="tracker", rows=tracker_rows),
                Section(name="scheduler", rows=scheduler_rows),
                Section(name="paths", rows=paths_rows),
                Section(name="selected", rows=selected_rows),
                Section(name="last run", rows=last_run_rows),
                Section(name="tokens", rows=token_rows),
            ],
            use_color=use_color,
        )

    workflow_name = str(result.get("workflow") or "workflow")
    status_value = str(
        result.get("status") or result.get("runtime_status") or EMPTY_VALUE
    )
    if result.get("current_mode"):
        status_value = f"{status_value} ({result.get('current_mode')} mode)"
    health = str(result.get("health") or EMPTY_VALUE)
    idle_reason = str(result.get("idle_reason") or EMPTY_VALUE)

    lane_count = result.get("lane_count")
    active_count = result.get("active_lane_count")
    decision_ready = result.get("decision_ready_count")

    top_rows = [
        Row(label="workflow", value=workflow_name),
        Row(label="health", value=health),
        Row(label="state", value=status_value),
        Row(label="idle", value=idle_reason),
        Row(label="tokens", value=str(int(result.get("total_tokens") or 0))),
    ]

    paths_rows = [
        Row(label="workflow", value=format_path(result.get("workflow_root"))),
        Row(label="contract", value=format_path(result.get("contract_path"))),
        Row(label="state", value=format_path(result.get("state_path"))),
        Row(label="audit", value=format_path(result.get("audit_log_path"))),
    ]

    lanes_rows = [
        Row(
            label="source",
            value=str(result.get("lane_status_source") or EMPTY_VALUE),
        ),
        Row(
            label="total",
            value=str(lane_count) if lane_count is not None else EMPTY_VALUE,
        ),
        Row(
            label="active",
            value=str(active_count) if active_count is not None else EMPTY_VALUE,
        ),
        Row(
            label="decision ready",
            value=str(decision_ready) if decision_ready is not None else EMPTY_VALUE,
        ),
        Row(label="running", value=str(result.get("running_count") or 0)),
        Row(label="dispatching", value=str(result.get("active_dispatch_count") or 0)),
        Row(label="retry", value=str(result.get("retry_count") or 0)),
        Row(label="side effects", value=str(result.get("side_effect_count") or 0)),
        Row(
            label="attention",
            value=str(result.get("operator_attention_count") or 0),
            status="fail" if result.get("operator_attention_count") else None,
        ),
    ]

    lane_rows: list[Row] = []
    for lane in _status_lanes(result)[:8]:
        lane_id = str(lane.get("lane_id") or EMPTY_VALUE)
        status = str(lane.get("status") or lane.get("lane_status") or EMPTY_VALUE)
        stage = str(lane.get("stage") or EMPTY_VALUE)
        actor = str(lane.get("actor") or EMPTY_VALUE)
        attempt = str(lane.get("attempt") or EMPTY_VALUE)
        detail_parts = [
            part
            for part in [
                f"stage={stage}",
                f"status={status}",
                f"actor={actor}",
                f"attempt={attempt}",
                _lane_pull_request_label(lane),
                _lane_dispatch_label(lane),
                f"effects={lane.get('side_effect_count')}"
                if lane.get("side_effect_count")
                else "",
                _lane_retry_label(lane),
                _lane_attention_label(lane),
            ]
            if part and not part.endswith(f"={EMPTY_VALUE}")
        ]
        lane_rows.append(
            Row(
                label=_compact(lane_id, limit=24),
                value=_compact(_lane_issue_label(lane), limit=24),
                status=_lane_status_glyph(lane),
                detail=_compact(" ".join(detail_parts), limit=140),
            )
        )
    if not lane_rows:
        lane_rows.append(Row(label="active", value="none"))

    retry_policy = (
        result.get("retry_policy")
        if isinstance(result.get("retry_policy"), Mapping)
        else {}
    )
    retry_wakeup = (
        result.get("retry_wakeup")
        if isinstance(result.get("retry_wakeup"), Mapping)
        else {}
    )
    retry_rows = [
        Row(
            label="max attempts",
            value=str(retry_policy.get("max_attempts") or EMPTY_VALUE),
        ),
        Row(
            label="initial delay",
            value=f"{retry_policy.get('initial_delay_seconds')}s"
            if retry_policy.get("initial_delay_seconds") is not None
            else EMPTY_VALUE,
        ),
        Row(
            label="backoff",
            value=str(retry_policy.get("backoff_multiplier") or EMPTY_VALUE),
        ),
        Row(
            label="max delay",
            value=f"{retry_policy.get('max_delay_seconds')}s"
            if retry_policy.get("max_delay_seconds") is not None
            else EMPTY_VALUE,
        ),
        Row(label="queued", value=str(retry_wakeup.get("queued_count") or 0)),
        Row(label="due now", value=str(retry_wakeup.get("due_count") or 0)),
        Row(
            label="next wakeup",
            value=(
                f"{float(retry_wakeup.get('next_due_in_seconds')):.1f}s"
                if retry_wakeup.get("next_due_in_seconds") is not None
                else EMPTY_VALUE
            ),
        ),
        Row(label="history", value=str(len(result.get("retry_audit") or []))),
    ]

    run_rows: list[Row] = []
    for run in (result.get("latest_runs") or [])[:5]:
        if not isinstance(run, Mapping):
            continue
        run_rows.append(
            Row(
                label=str(run.get("mode") or "run"),
                value=str(run.get("status") or EMPTY_VALUE),
                detail=_compact(
                    f"id={run.get('run_id') or EMPTY_VALUE} "
                    f"started={run.get('started_at') or EMPTY_VALUE}",
                    limit=120,
                ),
            )
        )

    tick_event_rows: list[Row] = []
    for event in (result.get("latest_tick_events") or [])[-5:]:
        if not isinstance(event, Mapping):
            continue
        payload = (
            event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        )
        details = (
            payload.get("details")
            if isinstance(payload.get("details"), Mapping)
            else {}
        )
        event_name = str(event.get("event_type") or EMPTY_VALUE).replace(
            "workflow.tick.", ""
        )
        detail = (
            details.get("reason")
            or details.get("error")
            or f"at={event.get('created_at') or EMPTY_VALUE}"
        )
        tick_event_rows.append(
            Row(
                label=_compact(event_name, limit=28),
                value=str(event.get("severity") or EMPTY_VALUE),
                detail=_compact(str(detail), limit=120),
            )
        )

    sections = [
        Section(name=None, rows=top_rows),
        Section(name="lanes", rows=lanes_rows),
        Section(name="active lanes", rows=lane_rows),
        Section(name="retry policy", rows=retry_rows),
        Section(name="paths", rows=paths_rows),
    ]
    if run_rows:
        sections.append(Section(name="latest runs", rows=run_rows))
    if tick_event_rows:
        sections.append(Section(name="latest tick journal", rows=tick_event_rows))

    return format_panel(
        title=f"Sprints workflow - {workflow_name}",
        sections=sections,
        use_color=use_color,
    )


def format_doctor(
    result: Mapping[str, Any],
    *,
    use_color: bool | None = None,
) -> str:
    if result.get("workflow") == "issue-runner":
        checks = result.get("checks") or []
        ok = bool(result.get("ok"))
        rows: list[Row] = []
        for check in checks:
            status = (check.get("status") or "info").lower()
            if status == "pass":
                row_status = "pass"
            elif status == "fail":
                row_status = "fail"
            elif status == "warn":
                row_status = "warn"
            else:
                row_status = "info"
            rows.append(
                Row(
                    label=str(check.get("name") or "check"),
                    value=str(check.get("detail") or EMPTY_VALUE),
                    status=row_status,
                )
            )
        sections = [
            Section(
                name=None,
                rows=[
                    Row(
                        label="workflow",
                        value=str(result.get("workflow") or EMPTY_VALUE),
                    ),
                    Row(
                        label="overall",
                        value="PASS" if ok else "FAIL",
                        status="pass" if ok else "fail",
                    ),
                ],
            ),
            Section(name="checks", rows=rows),
        ]
        recommendations = result.get("recommendations") or []
        if recommendations:
            sections.append(
                Section(
                    name="next steps",
                    rows=[
                        Row(label=str(index), value=str(item))
                        for index, item in enumerate(recommendations, start=1)
                    ],
                )
            )
        return format_panel(
            title="Issue runner doctor",
            sections=sections,
            use_color=use_color,
        )

    overall = (result.get("overall_status") or "?").lower()
    checks = result.get("checks") or []

    rows: list[Row] = []
    for check in checks:
        status = (check.get("status") or "info").lower()
        if status == "pass":
            row_status = "pass"
        elif status == "fail":
            row_status = "fail"
        elif status == "warn":
            row_status = "warn"
        else:
            row_status = "info"
        rows.append(
            Row(
                label=check.get("code") or "check",
                value=check.get("summary") or "",
                status=row_status,
            )
        )
    overall_value = overall.upper() if overall in {"pass", "fail", "warn"} else overall
    summary_section = Section(
        name=None,
        rows=[
            Row(
                label="overall",
                value=overall_value,
                status=(
                    "pass"
                    if overall == "pass"
                    else ("fail" if overall == "fail" else "warn")
                ),
            )
        ],
    )
    checks_section = Section(name="checks", rows=rows)
    sections = [summary_section, checks_section]
    recommendations = result.get("recommendations") or []
    repairs = result.get("repairs") or []
    skipped_repairs = result.get("skipped_repairs") or []
    repair_rows: list[Row] = []
    for repair in repairs:
        detail = str(repair.get("detail") or EMPTY_VALUE)
        path = str(repair.get("path") or "")
        value = f"{detail} ({format_path(path)})" if path else detail
        repair_rows.append(
            Row(
                label=str(repair.get("action") or "repair"),
                value=value,
                status="pass",
            )
        )
    for repair in skipped_repairs:
        detail = str(repair.get("detail") or EMPTY_VALUE)
        path = str(repair.get("path") or "")
        value = f"{detail} ({format_path(path)})" if path else detail
        repair_rows.append(
            Row(
                label=str(repair.get("action") or "skipped"),
                value=value,
                status="warn",
            )
        )
    if repair_rows:
        sections.append(Section(name="repairs", rows=repair_rows))
    if recommendations:
        sections.append(
            Section(
                name="next steps",
                rows=[
                    Row(label=str(index), value=str(item))
                    for index, item in enumerate(recommendations, start=1)
                ],
            )
        )

    return format_panel(
        title="Sprints doctor",
        sections=sections,
        use_color=use_color,
    )
