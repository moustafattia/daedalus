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

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

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
    "pass": ("✓", "green"),
    "fail": ("✗", "red"),
    "warn": ("⚠", "yellow"),
    "info": ("→", "cyan"),
}

EMPTY_VALUE = "—"
HINT_ARROW = "→"


# When loaded via importlib.util.spec_from_file_location with a custom module
# name (test pattern in tests/test_formatters.py), the module isn't auto-
# registered in sys.modules. The @dataclass decorator below introspects
# sys.modules[cls.__module__] for type resolution, which crashes if the module
# isn't there. Self-register the in-flight module so both direct execution and
# spec-loaded test modules work.
import inspect as _inspect_for_self_register

_self_module = _inspect_for_self_register.getmodule(
    _inspect_for_self_register.currentframe()
)
if _self_module is None:
    # Best-effort fallback: build a stub object that exposes __dict__ via globals().
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

    runtime_state = result.get("runtime_status") or EMPTY_VALUE
    mode = result.get("current_mode")
    if mode:
        state_value = f"{runtime_state} ({mode} mode)"
    else:
        state_value = runtime_state

    schema_version = result.get("schema_version")
    schema_value = f"v{schema_version}" if schema_version else EMPTY_VALUE

    owner = result.get("active_orchestrator_instance_id") or EMPTY_VALUE
    lane_count = result.get("lane_count")
    lanes_str = str(lane_count) if lane_count is not None else EMPTY_VALUE

    instance_label = (
        result.get("instance_id") or result.get("workflow_root_name") or "workflow"
    )

    # Build sections
    top_rows = [
        Row(label="state", value=state_value),
        Row(label="owner", value=owner),
        Row(label="schema", value=schema_value),
    ]

    paths_rows = [
        Row(label="db", value=format_path(result.get("db_path"))),
        Row(label="events", value=format_path(result.get("event_log_path"))),
    ]

    heartbeat_value = format_timestamp(
        result.get("latest_heartbeat_at") or "", now_iso=now_iso
    )
    heartbeat_rows = [Row(label="last", value=heartbeat_value)]

    lanes_rows = [Row(label="total", value=lanes_str)]

    return format_panel(
        title=f"Sprints runtime — {instance_label}",
        sections=[
            Section(name=None, rows=top_rows),
            Section(name="paths", rows=paths_rows),
            Section(name="heartbeat", rows=heartbeat_rows),
            Section(name="lanes", rows=lanes_rows),
        ],
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
