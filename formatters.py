"""Panel renderer + color helpers for /daedalus inspection commands.

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
    "bold":   "\033[1m",
    "dim":    "\033[2m",
    "red":    "\033[31m",
    "green":  "\033[32m",
    "yellow": "\033[33m",
    "cyan":   "\033[36m",
    "reset":  "\033[0m",
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
_self_module = _inspect_for_self_register.getmodule(_inspect_for_self_register.currentframe())
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
        return "~" + p[len(home):]
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
                line = f"{indent}{glyph_str} {row.label.ljust(label_width)}  {value_str}"
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


# ─── /daedalus status ────────────────────────────────────────

def format_status(
    result: Mapping[str, Any],
    *,
    use_color: bool | None = None,
    now_iso: str | None = None,
) -> str:
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

    instance_label = result.get("instance_id") or result.get("workflow_root_name") or "yoyopod"

    # Build sections
    top_rows = [
        Row(label="state",  value=state_value),
        Row(label="owner",  value=owner),
        Row(label="schema", value=schema_value),
    ]

    paths_rows = [
        Row(label="db",     value=format_path(result.get("db_path"))),
        Row(label="events", value=format_path(result.get("event_log_path"))),
    ]

    heartbeat_value = format_timestamp(result.get("latest_heartbeat_at") or "", now_iso=now_iso)
    heartbeat_rows = [Row(label="last", value=heartbeat_value)]

    lanes_rows = [Row(label="total", value=lanes_str)]

    return format_panel(
        title=f"Daedalus runtime — {instance_label}",
        sections=[
            Section(name=None,        rows=top_rows),
            Section(name="paths",     rows=paths_rows),
            Section(name="heartbeat", rows=heartbeat_rows),
            Section(name="lanes",     rows=lanes_rows),
        ],
        use_color=use_color,
    )


# ─── /daedalus active-gate-status ────────────────────────────────────────

# Map gate-failure reasons → (which row failed, remediation hint or None).
_REASON_TO_REMEDIATION = {
    "active-execution-disabled": (
        "active execution",
        "set via /daedalus set-active-execution --enabled true",
    ),
    "runtime-not-running": ("runtime mode", "start the daedalus-active service"),
    "runtime-not-active-mode": (
        "runtime mode",
        "the runtime is not in active mode (currently shadow); promote via cutover",
    ),
}


def format_active_gate_status(
    result: Mapping[str, Any],
    *,
    use_color: bool | None = None,
) -> str:
    allowed = bool(result.get("allowed"))
    reasons = result.get("reasons") or []
    execution = result.get("execution") or {}
    runtime = result.get("runtime") or {}
    primary_owner = result.get("primary_owner") or EMPTY_VALUE

    # Identify which rows are failing.
    failing_rows: dict[str, str] = {}  # row_label -> remediation text
    for reason in reasons:
        row_label, hint = _REASON_TO_REMEDIATION.get(reason, (None, None))
        if row_label:
            failing_rows[row_label] = hint or "blocked"

    # Build rows. Always show the same canonical four; mark failing ones.
    rows: list[Row] = []

    # Ownership row
    rows.append(Row(
        label="ownership posture",
        value=f"primary_owner = {primary_owner}",
        status="pass",
    ))

    # Active execution row
    enabled = execution.get("active_execution_enabled")
    if "active execution" in failing_rows:
        rows.append(Row(
            label="active execution",
            value="DISABLED",
            status="fail",
            detail=failing_rows["active execution"],
        ))
    else:
        rows.append(Row(
            label="active execution",
            value="enabled" if enabled else render_bool(enabled),
            status="pass",
        ))

    # Runtime mode row
    runtime_state = runtime.get("runtime_status") or "?"
    runtime_mode = runtime.get("current_mode") or "?"
    if "runtime mode" in failing_rows:
        rows.append(Row(
            label="runtime mode",
            value=f"{runtime_state} in {runtime_mode}",
            status="fail",
            detail=failing_rows["runtime mode"],
        ))
    else:
        rows.append(Row(
            label="runtime mode",
            value=f"{runtime_state} in {runtime_mode}",
            status="pass",
        ))

    # Legacy watchdog row (informational)
    rows.append(Row(
        label="legacy watchdog",
        value="retired (engine_owner = hermes)",
        status="pass",
    ))

    if allowed:
        footer = f"{HINT_ARROW} gate is open: actions can dispatch"
    else:
        footer = f"{HINT_ARROW} gate is BLOCKED: no actions will dispatch"

    return format_panel(
        title="Active execution gate",
        sections=[Section(name=None, rows=rows)],
        use_color=use_color,
        footer=footer,
    )
