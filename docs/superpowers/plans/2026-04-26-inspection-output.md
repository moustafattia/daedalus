# Inspection Output Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Ship Daedalus issue #3 — replace terse `key=value` text output of `/daedalus` inspection commands with a structured human-readable panel renderer, with `--format text|json` flag (and `--json` alias for back-compat).

**Architecture:** New `formatters.py` module at repo root with `format_panel(title, sections)` core + per-command formatters (`format_status`, `format_doctor`, etc.). `tools.render_result` delegates to formatters for inspection commands; operational commands keep their existing terse confirmation-string output.

**Tech Stack:** Python 3.11 stdlib (no new deps).

**Spec:** `docs/superpowers/specs/2026-04-26-inspection-output-design.md`

**Tests baseline:** 285 passing on `main` (worktree was just created from there). Final state: 285 + N new tests, 0 failures.

**Worktree:** `.claude/worktrees/output-formatting-issue-3` on branch `claude/output-formatting-issue-3`. **Always use** `/usr/bin/python3`.

---

## Phase 0: Preflight

### Task 0.1: Verify baseline

- [ ] **Step 1**: `cd /home/radxa/WS/hermes-relay/.claude/worktrees/output-formatting-issue-3 && /usr/bin/python3 -m pytest -q 2>&1 | tail -3`
Expected: `285 passed`. If anything else, STOP and report.

No commit.

---

## Phase 1: `formatters.py` core

### Task 1.1: Color helpers + Section/Row dataclasses + `format_panel`

**Files:**
- Create: `formatters.py`
- Test: `tests/test_formatters.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_formatters.py`:

```python
"""Panel renderer + color helpers."""
import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return load_module("daedalus_formatters_test", "formatters.py")


# ─── Color gating ────────────────────────────────────────────────

def test_use_color_false_when_not_a_tty():
    fmt = _module()
    with mock.patch("sys.stdout") as fake:
        fake.isatty.return_value = False
        assert fmt._use_color() is False


def test_use_color_false_when_NO_COLOR_env_set():
    fmt = _module()
    with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
        with mock.patch("sys.stdout") as fake:
            fake.isatty.return_value = True
            assert fmt._use_color() is False


def test_use_color_true_when_tty_and_no_NO_COLOR():
    fmt = _module()
    env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch("sys.stdout") as fake:
            fake.isatty.return_value = True
            assert fmt._use_color() is True


def test_color_wrapper_returns_input_when_disabled():
    fmt = _module()
    assert fmt._color("hello", "green", use_color=False) == "hello"


def test_color_wrapper_wraps_with_ansi_when_enabled():
    fmt = _module()
    out = fmt._color("hello", "green", use_color=True)
    assert "\033[" in out
    assert out.endswith("\033[0m")
    assert "hello" in out


# ─── Panel rendering ────────────────────────────────────────────────

def test_panel_with_single_section_no_section_header():
    fmt = _module()
    out = fmt.format_panel(
        title="Daedalus runtime — yoyopod",
        sections=[
            fmt.Section(name=None, rows=[
                fmt.Row(label="state", value="running"),
                fmt.Row(label="owner", value="daedalus-active"),
            ]),
        ],
        use_color=False,
    )
    assert "Daedalus runtime — yoyopod" in out
    assert "state" in out
    assert "running" in out
    assert "owner" in out
    # No section header line when name is None
    lines = out.split("\n")
    assert all("None" not in line for line in lines)


def test_panel_with_named_sections():
    fmt = _module()
    out = fmt.format_panel(
        title="Active execution gate",
        sections=[
            fmt.Section(name="checks", rows=[
                fmt.Row(label="ownership", value="desired_owner = daedalus", status="pass"),
                fmt.Row(label="active execution", value="DISABLED", status="fail"),
            ]),
        ],
        use_color=False,
    )
    assert "checks" in out
    assert "ownership" in out
    assert "DISABLED" in out
    # Pass/fail glyphs present (ASCII forms acceptable here too)
    assert "✓" in out or "+" in out
    assert "✗" in out or "x" in out


def test_panel_aligns_labels_within_section():
    fmt = _module()
    out = fmt.format_panel(
        title="t",
        sections=[
            fmt.Section(name=None, rows=[
                fmt.Row(label="a", value="1"),
                fmt.Row(label="longer-label", value="2"),
            ]),
        ],
        use_color=False,
    )
    # Each row's value position should align — easy check: locate '1' and '2' column.
    lines = [l for l in out.split("\n") if "1" in l or "2" in l]
    # Both rows should have the same number of leading characters before the value
    pos1 = lines[0].index("1")
    pos2 = lines[1].index("2")
    assert pos1 == pos2


def test_panel_empty_value_renders_as_em_dash():
    fmt = _module()
    out = fmt.format_panel(
        title="t",
        sections=[fmt.Section(name=None, rows=[fmt.Row(label="reasons", value="")])],
        use_color=False,
    )
    assert "—" in out


def test_panel_footer_appears_when_provided():
    fmt = _module()
    out = fmt.format_panel(
        title="t",
        sections=[fmt.Section(name=None, rows=[fmt.Row(label="x", value="y")])],
        footer="→ gate is open",
        use_color=False,
    )
    assert "→ gate is open" in out


def test_panel_footer_omitted_when_none():
    fmt = _module()
    out = fmt.format_panel(
        title="t",
        sections=[fmt.Section(name=None, rows=[fmt.Row(label="x", value="y")])],
        footer=None,
        use_color=False,
    )
    assert "→" not in out  # no footer arrow


def test_panel_row_with_detail_appends_after_value():
    fmt = _module()
    out = fmt.format_panel(
        title="t",
        sections=[fmt.Section(name=None, rows=[fmt.Row(label="x", value="y", detail="(extra info)")])],
        use_color=False,
    )
    assert "y" in out
    assert "(extra info)" in out
    # detail should appear AFTER value on the same line
    line = next(l for l in out.split("\n") if "y" in l and "(extra" in l)
    assert line.index("y") < line.index("(extra")


# ─── No raw True/False leakage ────────────────────────────────────────

def test_render_bool_helper_returns_human_strings():
    fmt = _module()
    assert fmt.render_bool(True) in {"yes", "enabled", "✓"}
    # Helper used by per-command formatters; test it directly.
    assert "True" not in fmt.render_bool(True)
    assert "False" not in fmt.render_bool(False)


# ─── Path + timestamp helpers ────────────────────────────────────────

def test_format_path_collapses_home_to_tilde():
    fmt = _module()
    home = os.environ.get("HOME") or str(Path.home())
    p = f"{home}/some/sub/dir"
    assert fmt.format_path(p).startswith("~/")


def test_format_path_returns_unmodified_when_outside_home():
    fmt = _module()
    assert fmt.format_path("/var/log/daedalus.log") == "/var/log/daedalus.log"


def test_format_timestamp_with_relative_age():
    fmt = _module()
    out = fmt.format_timestamp("2026-04-26T22:43:01Z", now_iso="2026-04-26T22:43:18Z")
    assert "22:43:01" in out
    assert "17s ago" in out or "17 sec" in out


def test_format_timestamp_handles_empty():
    fmt = _module()
    assert fmt.format_timestamp("", now_iso="2026-04-26T22:43:01Z") == "—"
```

- [ ] **Step 2: Run failing tests**

Run: `/usr/bin/python3 -m pytest tests/test_formatters.py -v`
Expected: All fail — `formatters.py` doesn't exist.

- [ ] **Step 3: Implement `formatters.py`**

Create `formatters.py` at repo root:

```python
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
# Implemented in subsequent tasks (status / doctor / active-gate-status / ...).
```

- [ ] **Step 4: Run tests + verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_formatters.py -v`
Expected: All passed (count: ~14).

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -3`
Expected: 285+14 = 299 passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add formatters.py tests/test_formatters.py
git commit -m "feat(formatters): panel renderer + color/path/timestamp helpers

Single format_panel primitive renders a multi-section text panel with
ANSI color (auto-detected via isatty + NO_COLOR env), aligned key/value
rows, optional pass/fail/warn/info glyphs, optional per-row detail,
optional footer hint. Section/Row dataclasses provide the input shape.

Helpers: render_bool (yes/no instead of True/False), format_path
(\$HOME → ~/), format_timestamp (ISO → 'HH:MM:SS UTC (Ns ago)').
Empty values render as em-dash (—)."
```

---

## Phase 2: `--format` flag wiring

### Task 2.1: Add `--format text|json` to inspection subparsers

**Files:**
- Modify: `tools.py` (`configure_subcommands` function)
- Modify: `tools.py` (`execute_raw_args` to resolve format)
- Test: `tests/test_tools_format_flag.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools_format_flag.py`:

```python
"""--format text|json flag resolution and --json alias back-compat."""
import importlib.util
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tools():
    return load_module("daedalus_tools_format_flag_test", "tools.py")


def test_resolve_format_default_is_text():
    tools = _tools()
    assert tools._resolve_format(None, None) == "text"


def test_resolve_format_explicit_text():
    tools = _tools()
    assert tools._resolve_format("text", False) == "text"


def test_resolve_format_explicit_json():
    tools = _tools()
    assert tools._resolve_format("json", False) == "json"


def test_resolve_format_legacy_json_flag():
    tools = _tools()
    assert tools._resolve_format(None, True) == "json"


def test_json_flag_wins_over_format_text():
    """Pre-existing scripts using --json shouldn't be silently downgraded."""
    tools = _tools()
    assert tools._resolve_format("text", True) == "json"


def test_format_json_wins_over_default_text():
    tools = _tools()
    assert tools._resolve_format("json", False) == "json"


def test_status_subparser_accepts_format_flag():
    tools = _tools()
    parser = tools.build_parser()
    args = parser.parse_args(["status", "--format", "json"])
    assert args.format == "json"


def test_status_subparser_accepts_legacy_json_flag():
    tools = _tools()
    parser = tools.build_parser()
    args = parser.parse_args(["status", "--json"])
    assert args.json is True


def test_status_subparser_accepts_no_format_flag_defaults_text():
    tools = _tools()
    parser = tools.build_parser()
    args = parser.parse_args(["status"])
    assert getattr(args, "format", "text") == "text"
    assert getattr(args, "json", False) is False


def test_doctor_subparser_accepts_format_flag():
    tools = _tools()
    parser = tools.build_parser()
    args = parser.parse_args(["doctor", "--format", "json"])
    assert args.format == "json"


def test_active_gate_status_subparser_accepts_format_flag():
    tools = _tools()
    parser = tools.build_parser()
    args = parser.parse_args(["active-gate-status", "--format", "text"])
    assert args.format == "text"


def test_invalid_format_value_rejected():
    tools = _tools()
    parser = tools.build_parser()
    try:
        parser.parse_args(["status", "--format", "yaml"])
    except SystemExit:
        return
    raise AssertionError("expected SystemExit on invalid --format value")
```

- [ ] **Step 2: Run failing tests**

Run: `/usr/bin/python3 -m pytest tests/test_tools_format_flag.py -v`
Expected: All fail — `_resolve_format` doesn't exist; `--format` not registered.

- [ ] **Step 3: Add `_resolve_format` helper + register `--format` on inspection subparsers**

In `tools.py`, near the top of the module-level helpers (e.g. just before `def execute_namespace`):

```python
def _resolve_format(format_arg: str | None, json_flag: bool | None) -> str:
    """Resolve the effective output format from ``--format`` and ``--json``.

    The legacy ``--json`` flag wins when set so existing scripts don't get
    silently downgraded. Otherwise, ``--format`` is honored. Default is text.
    """
    if json_flag:
        return "json"
    if format_arg == "json":
        return "json"
    return "text"
```

In `configure_subcommands`, add a `--format` argument to each inspection subparser. Find each `_cmd.add_argument("--json", action="store_true")` line for the inspection commands listed below and add a sibling line right after it. Inspection commands:

- `status_cmd`
- `report_cmd` (shadow-report)
- `doctor_cmd`
- `service_status_cmd`
- `active_gate_status_cmd`
- `get_obs_cmd` (get-observability — added in observability feature)

Add right after each `--json` line:

```python
    <name>_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
```

- [ ] **Step 4: Update the `print` call in `execute_raw_args`**

Find the line:

```python
print(render_result(args.daedalus_command, execute_namespace(args), json_output=getattr(args, "json", False)))
```

Replace with:

```python
fmt = _resolve_format(getattr(args, "format", None), getattr(args, "json", False))
print(render_result(args.daedalus_command, execute_namespace(args), output_format=fmt))
```

Also find the similar return line earlier in `execute_raw_args`:

```python
return render_result(args.daedalus_command, result, json_output=getattr(args, "json", False))
```

Replace with:

```python
fmt = _resolve_format(getattr(args, "format", None), getattr(args, "json", False))
return render_result(args.daedalus_command, result, output_format=fmt)
```

- [ ] **Step 5: Update `render_result` signature for back-compat**

Change the signature to accept both old and new kwargs so any external caller still works:

```python
def render_result(
    command: str,
    result: dict[str, Any],
    *,
    json_output: bool | None = None,
    output_format: str | None = None,
) -> str:
    # Resolve effective format. New callers pass output_format; legacy callers pass json_output.
    if output_format is None:
        output_format = "json" if json_output else "text"
    if output_format == "json":
        return json.dumps(result, indent=2, sort_keys=True)
    # Legacy text rendering below stays as-is for now; per-command formatters
    # land in subsequent tasks.
    ...
```

(Just adjust the early return; the existing terse-text branches below stay intact.)

- [ ] **Step 6: Run tests + full suite**

Run: `/usr/bin/python3 -m pytest tests/test_tools_format_flag.py -v`
Expected: 12 passed.

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -3`
Expected: 299 + 12 = 311 passed, 0 failed.

- [ ] **Step 7: Commit**

```bash
git add tools.py tests/test_tools_format_flag.py
git commit -m "feat(tools): --format text|json flag with --json as back-compat alias

Adds --format argument (choices: text, json; default: text) to
status, doctor, shadow-report, active-gate-status, service-status,
get-observability subparsers. _resolve_format helper picks the
effective format with --json winning when set so pre-existing
scripts using --json continue to produce json output unchanged."
```

---

## Phase 3: First-batch formatters (status + active-gate-status)

### Task 3.1: `format_status` + integration with `render_result`

**Files:**
- Modify: `formatters.py` (add `format_status`)
- Modify: `tools.py` (`render_result`'s `status` branch delegates to formatters)
- Test: `tests/test_formatters_status.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_formatters_status.py`:

```python
"""Per-command formatter for /daedalus status."""
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fmt():
    return load_module("daedalus_formatters_status_test", "formatters.py")


def _example_status() -> dict:
    return {
        "runtime_status": "running",
        "current_mode": "active",
        "active_orchestrator_instance_id": "daedalus-active-yoyopod",
        "schema_version": 3,
        "lane_count": 14,
        "db_path": "/home/x/.hermes/workflows/yoyopod/runtime/state/daedalus/daedalus.db",
        "event_log_path": "/home/x/.hermes/workflows/yoyopod/runtime/memory/daedalus-events.jsonl",
        "latest_heartbeat_at": "2026-04-26T22:43:01Z",
    }


def test_format_status_includes_title_and_state():
    fmt = _fmt()
    out = fmt.format_status(_example_status(), use_color=False, now_iso="2026-04-26T22:43:18Z")
    assert "Daedalus runtime" in out
    assert "running" in out
    # Mode appears alongside state
    assert "active" in out


def test_format_status_includes_owner_and_schema():
    fmt = _fmt()
    out = fmt.format_status(_example_status(), use_color=False, now_iso="2026-04-26T22:43:18Z")
    assert "daedalus-active-yoyopod" in out
    assert "v3" in out or "schema" in out


def test_format_status_includes_lane_count():
    fmt = _fmt()
    out = fmt.format_status(_example_status(), use_color=False, now_iso="2026-04-26T22:43:18Z")
    assert "14" in out


def test_format_status_paths_section_includes_both_paths():
    fmt = _fmt()
    out = fmt.format_status(_example_status(), use_color=False, now_iso="2026-04-26T22:43:18Z")
    assert "daedalus.db" in out
    assert "daedalus-events.jsonl" in out


def test_format_status_heartbeat_renders_as_clock_with_age():
    fmt = _fmt()
    out = fmt.format_status(_example_status(), use_color=False, now_iso="2026-04-26T22:43:18Z")
    assert "22:43:01" in out
    assert "17s ago" in out


def test_format_status_handles_missing_optional_fields():
    fmt = _fmt()
    minimal = {"runtime_status": "blocked", "current_mode": None, "lane_count": 0}
    out = fmt.format_status(minimal, use_color=False)
    assert "blocked" in out
    # No crash on missing keys; em-dash for empty values
    assert "—" in out


def test_format_status_no_raw_python_bools_leak():
    fmt = _fmt()
    minimal = {"runtime_status": "running", "current_mode": "active", "lane_count": 0,
               "active_orchestrator_instance_id": "x"}
    out = fmt.format_status(minimal, use_color=False)
    assert " True" not in out
    assert " False" not in out
```

- [ ] **Step 2: Run failing tests**

Run: `/usr/bin/python3 -m pytest tests/test_formatters_status.py -v`
Expected: All fail — `format_status` doesn't exist.

- [ ] **Step 3: Add `format_status` to `formatters.py`**

Append to `formatters.py`:

```python
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
```

- [ ] **Step 4: Wire `format_status` into `render_result`**

In `tools.py`'s `render_result`, find the `if command == "status":` branch and replace its body with:

```python
    if command == "status":
        try:
            from formatters import format_status as _fmt_status
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "daedalus_formatters_for_render", PLUGIN_DIR / "formatters.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _fmt_status = mod.format_status
        return _fmt_status(result)
```

- [ ] **Step 5: Run tests + full suite**

Run: `/usr/bin/python3 -m pytest tests/test_formatters_status.py -v`
Expected: 7 passed.

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -3`
Expected: 311 + 7 = 318 passed, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add formatters.py tools.py tests/test_formatters_status.py
git commit -m "feat(formatters): structured panel for /daedalus status

Multi-section panel: state/owner/schema header, paths section,
heartbeat with relative age, lane count. render_result delegates
to format_status when output_format is text. JSON output unchanged."
```

---

### Task 3.2: `format_active_gate_status` with remediation hints

**Files:**
- Modify: `formatters.py` (add `format_active_gate_status`)
- Modify: `tools.py` (`render_result` delegates)
- Test: `tests/test_formatters_active_gate.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_formatters_active_gate.py`:

```python
"""Per-command formatter for /daedalus active-gate-status."""
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name, relative_path):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt():
    return load_module("daedalus_formatters_active_gate_test", "formatters.py")


def _gate_open():
    return {
        "allowed": True,
        "reasons": [],
        "execution": {"active_execution_enabled": True},
        "primary_owner": "daedalus",
        "runtime": {"runtime_status": "running", "current_mode": "active"},
        "legacy_health": None,
    }


def _gate_blocked_active_disabled():
    return {
        "allowed": False,
        "reasons": ["active-execution-disabled"],
        "execution": {"active_execution_enabled": False},
        "primary_owner": "daedalus",
        "runtime": {"runtime_status": "running", "current_mode": "active"},
    }


def _gate_blocked_runtime_not_active():
    return {
        "allowed": False,
        "reasons": ["runtime-not-active-mode"],
        "execution": {"active_execution_enabled": True},
        "primary_owner": "daedalus",
        "runtime": {"runtime_status": "running", "current_mode": "shadow"},
    }


def test_open_gate_renders_all_pass_and_open_footer():
    fmt = _fmt()
    out = fmt.format_active_gate_status(_gate_open(), use_color=False)
    assert "Active execution gate" in out
    # All four conditions present
    assert "ownership" in out
    assert "active execution" in out
    assert "runtime mode" in out
    # Open status footer
    assert "open" in out.lower()


def test_blocked_active_disabled_shows_remediation_hint():
    fmt = _fmt()
    out = fmt.format_active_gate_status(_gate_blocked_active_disabled(), use_color=False)
    assert "BLOCKED" in out or "blocked" in out.lower()
    # Remediation hint
    assert "set-active-execution" in out


def test_blocked_runtime_not_active_shows_correct_failing_row():
    fmt = _fmt()
    out = fmt.format_active_gate_status(_gate_blocked_runtime_not_active(), use_color=False)
    assert "BLOCKED" in out or "blocked" in out.lower()
    # The runtime mode row should be the failing one
    lines = [l for l in out.split("\n") if "runtime mode" in l]
    assert lines
    assert "✗" in lines[0] or "x" in lines[0].lower()


def test_no_raw_python_bools_in_output():
    fmt = _fmt()
    out = fmt.format_active_gate_status(_gate_open(), use_color=False)
    assert " True" not in out
    assert " False" not in out
```

- [ ] **Step 2: Run failing tests**

Expected: all fail.

- [ ] **Step 3: Implement `format_active_gate_status`**

Append to `formatters.py`:

```python
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
```

- [ ] **Step 4: Wire into `render_result`**

In `tools.py`, find `if command == "active-gate-status":` and replace its body with:

```python
    if command == "active-gate-status":
        try:
            from formatters import format_active_gate_status as _fmt
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "daedalus_formatters_for_active_gate", PLUGIN_DIR / "formatters.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _fmt = mod.format_active_gate_status
        return _fmt(result)
```

- [ ] **Step 5: Run tests + full suite**

Run: `/usr/bin/python3 -m pytest tests/test_formatters_active_gate.py -v`
Expected: 4 passed.

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -3`
Expected: 318 + 4 = 322 passed, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add formatters.py tools.py tests/test_formatters_active_gate.py
git commit -m "feat(formatters): structured panel for /daedalus active-gate-status

Four canonical gate-condition rows with PASS/FAIL glyphs. When a
condition fails, the row shows status='fail' and a per-reason
remediation hint (e.g. 'set via /daedalus set-active-execution
--enabled true' for disabled execution). Footer summarizes
'gate is open' / 'gate is BLOCKED'."
```

---

## Phase 4: Doctor + shadow-report

### Task 4.1: `format_doctor`

**Files:**
- Modify: `formatters.py`
- Modify: `tools.py`
- Test: `tests/test_formatters_doctor.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_formatters_doctor.py`:

```python
"""Per-command formatter for /daedalus doctor."""
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name, relative_path):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt():
    return load_module("daedalus_formatters_doctor_test", "formatters.py")


def _doctor_all_pass():
    return {
        "overall_status": "pass",
        "checks": [
            {"code": "missing_lease", "status": "pass", "summary": "Runtime lease present"},
            {"code": "shadow_compatible", "status": "pass", "summary": "Shadow decision matches legacy"},
            {"code": "active_execution_failures", "status": "pass", "summary": "No active execution failures"},
        ],
    }


def _doctor_one_fail():
    return {
        "overall_status": "fail",
        "checks": [
            {"code": "missing_lease", "status": "pass", "summary": "Runtime lease present"},
            {"code": "shadow_compatible", "status": "fail", "summary": "Shadow decision differs from legacy",
             "details": {"legacy": "publish_pr", "relay": "noop"}},
        ],
    }


def _doctor_with_failure_details():
    return {
        "overall_status": "fail",
        "checks": [
            {"code": "active_execution_failures", "status": "fail", "summary": "1 unresolved failure",
             "details": {"failures": [
                 {"failure_id": "f-123", "failure_class": "subprocess_error",
                  "recommended_action": "retry", "confidence": "medium",
                  "recovery_state": "queued", "urgency": "high", "failure_age_seconds": 320}
             ]}},
        ],
    }


def test_doctor_panel_includes_overall_status():
    fmt = _fmt()
    out = fmt.format_doctor(_doctor_all_pass(), use_color=False)
    assert "Daedalus doctor" in out or "doctor" in out.lower()
    # Overall status visible
    assert "pass" in out.lower()


def test_doctor_panel_renders_each_check_with_glyph():
    fmt = _fmt()
    out = fmt.format_doctor(_doctor_one_fail(), use_color=False)
    assert "missing_lease" in out
    assert "shadow_compatible" in out
    # At least one ✓ and one ✗
    assert "✓" in out
    assert "✗" in out


def test_doctor_failure_details_rendered_inline():
    fmt = _fmt()
    out = fmt.format_doctor(_doctor_with_failure_details(), use_color=False)
    assert "f-123" in out
    assert "subprocess_error" in out
    assert "retry" in out


def test_doctor_no_raw_python_bools():
    fmt = _fmt()
    out = fmt.format_doctor(_doctor_all_pass(), use_color=False)
    assert " True" not in out
    assert " False" not in out
```

- [ ] **Step 2: Run failing tests + implement**

Append to `formatters.py`:

```python
# ─── /daedalus doctor ────────────────────────────────────────

def format_doctor(
    result: Mapping[str, Any],
    *,
    use_color: bool | None = None,
) -> str:
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
        rows.append(Row(
            label=check.get("code") or "check",
            value=check.get("summary") or "",
            status=row_status,
        ))
        # Inline failure details for active_execution_failures.
        if check.get("code") == "active_execution_failures":
            details = check.get("details") or {}
            for failure in (details.get("failures") or []):
                detail_text = (
                    f"  {failure.get('failure_id')} "
                    f"class={failure.get('failure_class')} "
                    f"action={failure.get('recommended_action')} "
                    f"confidence={failure.get('confidence')} "
                    f"recovery={failure.get('recovery_state')} "
                    f"age={failure.get('failure_age_seconds')}s"
                )
                rows.append(Row(label="", value=detail_text, status=None))

    overall_value = overall.upper() if overall in {"pass", "fail", "warn"} else overall
    summary_section = Section(
        name=None,
        rows=[Row(
            label="overall",
            value=overall_value,
            status=("pass" if overall == "pass" else ("fail" if overall == "fail" else "warn")),
        )],
    )
    checks_section = Section(name="checks", rows=rows)

    return format_panel(
        title="Daedalus doctor",
        sections=[summary_section, checks_section],
        use_color=use_color,
    )
```

In `tools.py`, replace the `if command == "doctor":` body:

```python
    if command == "doctor":
        try:
            from formatters import format_doctor as _fmt
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "daedalus_formatters_for_doctor", PLUGIN_DIR / "formatters.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _fmt = mod.format_doctor
        return _fmt(result)
```

- [ ] **Step 3: Tests pass + commit**

Run tests, then:

```bash
git add formatters.py tools.py tests/test_formatters_doctor.py
git commit -m "feat(formatters): structured panel for /daedalus doctor

Overall status row at top + per-check rows with PASS/FAIL/WARN
glyphs. active_execution_failures details inlined as indented
sub-rows showing failure_id / class / recommended action /
recovery state / age."
```

---

### Task 4.2: `format_shadow_report`

**Files:**
- Modify: `formatters.py`
- Modify: `tools.py`
- Test: `tests/test_formatters_shadow_report.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_formatters_shadow_report.py`:

```python
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name, relative_path):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt():
    return load_module("daedalus_formatters_shadow_test", "formatters.py")


def _example_shadow_report():
    return {
        "runtime": {"runtime_status": "running", "current_mode": "active",
                    "active_orchestrator_instance_id": "daedalus-active-yoyopod",
                    "latest_heartbeat_at": "2026-04-26T22:43:01Z"},
        "heartbeat": {"heartbeat_age_seconds": 17, "expires_at": "2026-04-26T22:44:00Z"},
        "service": {"service_mode": "active", "installed": True, "enabled": True, "active": True},
        "owner_summary": {"primary_owner": "daedalus", "active_execution_enabled": True, "gate_allowed": True},
        "active_lane": {"issue_number": 329, "lane_id": "lane-329",
                         "workflow_state": "under_review", "review_state": "pass",
                         "merge_state": "pending"},
        "legacy": {"next_action_type": "publish_pr", "reason": "head-clean"},
        "relay": {"derived_action_type": "publish_pr", "reason": "head-clean", "compatible": True},
        "warnings": [],
        "recent_shadow_actions": [],
        "recent_failures": [],
    }


def test_shadow_report_renders_runtime_and_lane_sections():
    fmt = _fmt()
    out = fmt.format_shadow_report(_example_shadow_report(), use_color=False)
    assert "Daedalus shadow-report" in out or "shadow" in out.lower()
    # Runtime + active-lane info present
    assert "running" in out
    assert "329" in out
    assert "publish_pr" in out


def test_shadow_report_warnings_appear_when_present():
    fmt = _fmt()
    rep = _example_shadow_report()
    rep["warnings"] = ["heartbeat-stale", "lease-near-expiry"]
    out = fmt.format_shadow_report(rep, use_color=False)
    assert "heartbeat-stale" in out
    assert "lease-near-expiry" in out


def test_shadow_report_no_warnings_section_when_empty():
    fmt = _fmt()
    out = fmt.format_shadow_report(_example_shadow_report(), use_color=False)
    assert "warnings" not in out.lower() or "(no warnings)" in out.lower()


def test_shadow_report_no_raw_python_bools():
    fmt = _fmt()
    out = fmt.format_shadow_report(_example_shadow_report(), use_color=False)
    assert " True" not in out
    assert " False" not in out
```

- [ ] **Step 2: Implement**

Append to `formatters.py`:

```python
# ─── /daedalus shadow-report ────────────────────────────────────────

def format_shadow_report(
    result: Mapping[str, Any],
    *,
    use_color: bool | None = None,
    now_iso: str | None = None,
) -> str:
    runtime = result.get("runtime") or {}
    heartbeat = result.get("heartbeat") or {}
    service = result.get("service") or {}
    owner_summary = result.get("owner_summary") or {}
    active_lane = result.get("active_lane") or {}
    legacy = result.get("legacy") or {}
    relay = result.get("relay") or {}
    warnings = result.get("warnings") or []
    recent_actions = result.get("recent_shadow_actions") or []
    recent_failures = result.get("recent_failures") or []

    sections: list[Section] = []

    # Runtime
    sections.append(Section(name="runtime", rows=[
        Row(label="state",     value=f"{runtime.get('runtime_status') or '?'} ({runtime.get('current_mode') or '?'} mode)"),
        Row(label="owner",     value=runtime.get("active_orchestrator_instance_id") or EMPTY_VALUE),
        Row(label="heartbeat", value=format_timestamp(runtime.get("latest_heartbeat_at") or "", now_iso=now_iso)),
    ]))

    # Service (when present)
    if service:
        sections.append(Section(name="service", rows=[
            Row(label="mode",      value=str(service.get("service_mode") or EMPTY_VALUE)),
            Row(label="installed", value=render_bool(service.get("installed"))),
            Row(label="enabled",   value=render_bool(service.get("enabled"))),
            Row(label="active",    value=render_bool(service.get("active"))),
        ]))

    # Active lane
    if active_lane.get("issue_number") is not None:
        sections.append(Section(name="active lane", rows=[
            Row(label="issue",   value=f"#{active_lane.get('issue_number')}"),
            Row(label="lane id", value=str(active_lane.get("lane_id") or EMPTY_VALUE)),
            Row(label="state",   value=f"{active_lane.get('workflow_state') or '?'} / "
                                          f"{active_lane.get('review_state') or '?'} / "
                                          f"{active_lane.get('merge_state') or '?'}"),
        ]))

    # Decisions: legacy vs relay
    sections.append(Section(name="next action", rows=[
        Row(label="legacy",     value=f"{legacy.get('next_action_type') or EMPTY_VALUE}",
            detail=legacy.get("reason") or None),
        Row(label="relay",      value=f"{relay.get('derived_action_type') or EMPTY_VALUE}",
            detail=relay.get("reason") or None),
        Row(label="compatible", value=render_bool(relay.get("compatible")),
            status=("pass" if relay.get("compatible") else "warn")),
    ]))

    # Warnings (if any)
    if warnings:
        sections.append(Section(name="warnings",
                                rows=[Row(label="", value=f"⚠ {w}", status="warn") for w in warnings]))

    # Recent actions (compact)
    if recent_actions:
        rows = []
        for action in recent_actions[:5]:
            rows.append(Row(
                label=str(action.get("requested_at") or "?")[:19],
                value=f"#{action.get('issue_number')} {action.get('action_type')} → {action.get('status')}",
            ))
        sections.append(Section(name="recent shadow actions", rows=rows))

    # Recent failures (compact)
    if recent_failures:
        rows = []
        for failure in recent_failures[:5]:
            rows.append(Row(
                label=str(failure.get("detected_at") or "?")[:19],
                value=f"#{failure.get('issue_number')} class={failure.get('failure_class')} "
                      f"recovery={failure.get('recovery_state')}",
                status="fail",
            ))
        sections.append(Section(name="recent failures", rows=rows))

    return format_panel(
        title="Daedalus shadow-report",
        sections=sections,
        use_color=use_color,
    )
```

In `tools.py`, replace the `if command == "shadow-report":` body:

```python
    if command == "shadow-report":
        try:
            from formatters import format_shadow_report as _fmt
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "daedalus_formatters_for_shadow", PLUGIN_DIR / "formatters.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _fmt = mod.format_shadow_report
        return _fmt(result)
```

- [ ] **Step 3: Tests pass + commit**

```bash
git add formatters.py tools.py tests/test_formatters_shadow_report.py
git commit -m "feat(formatters): structured panel for /daedalus shadow-report

Sectioned panel: runtime / service / active lane / next action
(legacy + relay + compatibility) / warnings / recent actions /
recent failures. Sections appear only when their data exists."
```

---

## Phase 5: service-status + get-observability

### Task 5.1: `format_service_status`

**Files:**
- Modify: `formatters.py`, `tools.py`
- Test: `tests/test_formatters_service_status.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_formatters_service_status.py`:

```python
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name, relative_path):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt():
    return load_module("daedalus_formatters_service_status_test", "formatters.py")


def _example_service_status():
    return {
        "service_name": "daedalus-active@yoyopod.service",
        "service_mode": "active",
        "installed": True,
        "enabled": True,
        "active": True,
        "unit_path": "/home/x/.config/systemd/user/daedalus-active@.service",
        "properties": {
            "ExecMainPID": "12345",
            "FragmentPath": "/home/x/.config/systemd/user/daedalus-active@.service",
        },
    }


def test_service_status_renders_identity_and_runtime():
    fmt = _fmt()
    out = fmt.format_service_status(_example_service_status(), use_color=False)
    assert "daedalus-active@yoyopod.service" in out
    assert "12345" in out
    # 3-state install row
    assert "installed" in out
    assert "enabled" in out
    assert "active" in out


def test_service_status_no_raw_python_bools():
    fmt = _fmt()
    out = fmt.format_service_status(_example_service_status(), use_color=False)
    assert " True" not in out
    assert " False" not in out


def test_service_status_handles_inactive_service():
    fmt = _fmt()
    inactive = _example_service_status()
    inactive["active"] = False
    inactive["properties"] = {}
    out = fmt.format_service_status(inactive, use_color=False)
    # Inactive should still render; pid empty becomes em-dash
    assert "—" in out
```

- [ ] **Step 2: Implement**

Append to `formatters.py`:

```python
# ─── /daedalus service-status ────────────────────────────────────────

def format_service_status(
    result: Mapping[str, Any],
    *,
    use_color: bool | None = None,
) -> str:
    name = result.get("service_name") or EMPTY_VALUE
    mode = result.get("service_mode") or "?"
    props = result.get("properties") or {}

    identity = Section(name=None, rows=[
        Row(label="service", value=name),
        Row(label="mode",    value=str(mode)),
    ])

    install = Section(name="install state", rows=[
        Row(label="installed", value=render_bool(result.get("installed")),
            status=("pass" if result.get("installed") else "warn")),
        Row(label="enabled",   value=render_bool(result.get("enabled")),
            status=("pass" if result.get("enabled") else "warn")),
        Row(label="active",    value=render_bool(result.get("active")),
            status=("pass" if result.get("active") else "warn")),
    ])

    runtime = Section(name="runtime", rows=[
        Row(label="pid", value=str(props.get("ExecMainPID") or "") or EMPTY_VALUE),
    ])

    paths = Section(name="paths", rows=[
        Row(label="unit", value=format_path(props.get("FragmentPath") or result.get("unit_path"))),
    ])

    return format_panel(
        title="Daedalus service",
        sections=[identity, install, runtime, paths],
        use_color=use_color,
    )
```

In `tools.py`, replace the `if command == "service-status":` body with the standard delegation pattern (mirror Task 4.2's `if command == "shadow-report":` shape, calling `format_service_status`).

- [ ] **Step 3: Tests pass + commit**

```bash
git add formatters.py tools.py tests/test_formatters_service_status.py
git commit -m "feat(formatters): structured panel for /daedalus service-status

Identity + install state (installed/enabled/active with PASS/WARN
glyphs) + runtime PID + unit path. Inactive services still render
clean; missing fields collapse to em-dash."
```

---

### Task 5.2: `format_get_observability` (panel form of existing 4-line text)

**Files:**
- Modify: `formatters.py`, `tools.py` (`cmd_get_observability` becomes a thin wrapper)
- Test: `tests/test_formatters_get_observability.py` (new)

- [ ] **Step 1: Inspect the existing handler**

Run: `grep -n "def cmd_get_observability" tools.py`

The handler currently returns a string directly (not a dict). For Issue #3 we want it to also produce a structured panel. Two paths:

(a) Make `cmd_get_observability` return a dict, route through `render_result` like other commands. Cleanest, but bigger change.
(b) Leave `cmd_get_observability` as-is but call `format_get_observability` to build the string instead of inline `\n`.join.

Choose (b) for minimal blast radius. The function still returns a string but routes through the formatter.

- [ ] **Step 2: Write the failing test**

Create `tests/test_formatters_get_observability.py`:

```python
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name, relative_path):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt():
    return load_module("daedalus_formatters_get_obs_test", "formatters.py")


def _example():
    return {
        "workflow": "code-review",
        "github_comments": {
            "enabled": True,
            "mode": "edit-in-place",
            "include_events": ["dispatch-implementation-turn", "merge-and-promote"],
        },
        "source": "yaml",
    }


def test_panel_includes_workflow_and_enabled():
    fmt = _fmt()
    out = fmt.format_get_observability(_example(), use_color=False)
    assert "code-review" in out
    assert "yaml" in out
    # enabled rendered as 'yes' not True
    assert " True" not in out


def test_firehose_warning_when_include_events_empty():
    fmt = _fmt()
    rec = _example()
    rec["github_comments"]["include_events"] = []
    out = fmt.format_get_observability(rec, use_color=False)
    assert "FIREHOSE" in out


def test_disabled_state_renders_with_fail_glyph():
    fmt = _fmt()
    rec = _example()
    rec["github_comments"]["enabled"] = False
    out = fmt.format_get_observability(rec, use_color=False)
    assert "no" in out.lower()  # rendered as 'no'
```

- [ ] **Step 3: Implement**

Append to `formatters.py`:

```python
# ─── /daedalus get-observability ────────────────────────────────────────

def format_get_observability(
    record: Mapping[str, Any],
    *,
    use_color: bool | None = None,
) -> str:
    workflow_name = record.get("workflow") or EMPTY_VALUE
    gh = record.get("github_comments") or {}
    source = record.get("source") or "default"

    enabled = bool(gh.get("enabled"))
    include_events = gh.get("include_events") or []

    if not include_events:
        events_value = "[] (FIREHOSE — every audit action)"
        events_status = "warn"
    else:
        events_value = ", ".join(include_events)
        events_status = None

    rows = [
        Row(label="workflow", value=str(workflow_name)),
        Row(label="enabled",  value=render_bool(enabled),
            status=("pass" if enabled else "fail"),
            detail=f"(source: {source})"),
        Row(label="mode",     value=str(gh.get("mode") or EMPTY_VALUE)),
        Row(label="include-events", value=events_value, status=events_status),
    ]

    return format_panel(
        title="Daedalus observability config",
        sections=[Section(name=None, rows=rows)],
        use_color=use_color,
    )
```

- [ ] **Step 4: Update `cmd_get_observability` to route through the formatter**

Find `cmd_get_observability` in `tools.py`. Replace the line-list/`\n`.join block with a call to `format_get_observability`. Build the input dict from the existing variables:

```python
    record = {
        "workflow": workflow_name,
        "github_comments": {
            "enabled": gh.get("enabled"),
            "mode": gh.get("mode"),
            "include_events": include_events or [],
        },
        "source": source,
    }
    fmt_choice = _resolve_format(getattr(args, "format", None), getattr(args, "json", False))
    if fmt_choice == "json":
        return json.dumps(record, indent=2, sort_keys=True)
    try:
        from formatters import format_get_observability as _fmt
    except ImportError:
        spec = importlib.util.spec_from_file_location(
            "daedalus_formatters_for_get_obs", PLUGIN_DIR / "formatters.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _fmt = mod.format_get_observability
    return _fmt(record)
```

(Adjust variable names to match what's already in `cmd_get_observability` — it builds the same data, just renders inline.)

- [ ] **Step 5: Tests pass + commit**

```bash
git add formatters.py tools.py tests/test_formatters_get_observability.py
git commit -m "feat(formatters): panel form for /daedalus get-observability

cmd_get_observability now routes through the panel renderer with
--format text|json honored. FIREHOSE warning surfaces with warn
glyph when include-events is an explicit empty list (every audit
action becomes a comment update). enabled flag uses pass/fail glyph."
```

---

## Phase 6: No-information-loss verification + docs + audit

### Task 6.1: No-information-loss tests across all upgraded commands

**Files:**
- Test: `tests/test_formatters_no_info_loss.py` (new)

- [ ] **Step 1: Write the test**

Create `tests/test_formatters_no_info_loss.py`:

```python
"""Every top-level field in the result dict appears in the rendered text panel.

This catches accidental field drops as new fields are added to result dicts.
"""
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name, relative_path):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt():
    return load_module("daedalus_formatters_no_info_loss_test", "formatters.py")


def _values_in_text(result, text):
    """Return list of (path, value) pairs whose value-string is missing from text.

    Treats nested dicts/lists by walking; ignores values that are themselves
    dicts/lists (only leaf values must be visible). Booleans rendered as
    yes/no/enabled/disabled are tolerated via render_bool semantics, so we
    only check non-bool primitives.
    """
    fmt = _fmt()
    missing = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [str(i)])
        else:
            if isinstance(node, bool) or node is None:
                return  # booleans are translated; None is em-dash
            value_str = str(node)
            if not value_str:
                return
            # Heuristic: values longer than 6 chars must appear verbatim.
            # Skip very short strings (likely common tokens like "1", "x").
            if len(value_str) >= 6 and value_str not in text:
                missing.append((".".join(path), value_str))

    walk(result, [])
    return missing


def test_status_no_info_loss():
    fmt = _fmt()
    result = {
        "runtime_status": "running", "current_mode": "active",
        "active_orchestrator_instance_id": "daedalus-active-yoyopod",
        "schema_version": 3, "lane_count": 14,
        "db_path": "/path/to/daedalus.db",
        "event_log_path": "/path/to/daedalus-events.jsonl",
        "latest_heartbeat_at": "2026-04-26T22:43:01Z",
    }
    out = fmt.format_status(result, use_color=False, now_iso="2026-04-26T22:43:18Z")
    missing = _values_in_text(result, out)
    assert not missing, f"Missing in status output: {missing}"


def test_active_gate_status_no_info_loss():
    fmt = _fmt()
    result = {
        "allowed": True, "reasons": [],
        "execution": {"active_execution_enabled": True},
        "primary_owner": "daedalus",
        "runtime": {"runtime_status": "running", "current_mode": "active"},
    }
    out = fmt.format_active_gate_status(result, use_color=False)
    missing = _values_in_text(result, out)
    assert not missing, f"Missing in active-gate output: {missing}"


def test_doctor_no_info_loss():
    fmt = _fmt()
    result = {
        "overall_status": "pass",
        "checks": [
            {"code": "missing_lease", "status": "pass", "summary": "Runtime lease present"},
        ],
    }
    out = fmt.format_doctor(result, use_color=False)
    missing = _values_in_text(result, out)
    assert not missing, f"Missing in doctor output: {missing}"


def test_service_status_no_info_loss():
    fmt = _fmt()
    result = {
        "service_name": "daedalus-active@yoyopod.service",
        "service_mode": "active",
        "installed": True, "enabled": True, "active": True,
        "unit_path": "/path/unit.service",
        "properties": {"ExecMainPID": "12345"},
    }
    out = fmt.format_service_status(result, use_color=False)
    missing = _values_in_text(result, out)
    assert not missing, f"Missing in service-status output: {missing}"
```

- [ ] **Step 2: Run + iterate**

Run: `/usr/bin/python3 -m pytest tests/test_formatters_no_info_loss.py -v`

If a missing-field is reported, decide whether (a) it's an oversight in the formatter — fix it, or (b) the missing field is genuinely redundant (e.g. nested lookup keys not worth surfacing) — adjust the heuristic. **Don't suppress a real omission** — always err toward surfacing the field.

- [ ] **Step 3: Commit**

```bash
git add tests/test_formatters_no_info_loss.py
git commit -m "test(formatters): no-information-loss across upgraded commands

Walks each result dict and asserts every leaf string of length >=6
appears in the rendered text. Catches accidental field drops as
new fields are added to result dicts."
```

---

### Task 6.2: Doc update — slash command catalog with rendered output

**Files:**
- Modify: `docs/slash-commands-catalog.md`

- [ ] **Step 1: Append "Output formats" section**

After the existing "Inspection (read-only)" subsection in `docs/slash-commands-catalog.md`, add:

```markdown
### Inspection output format

All inspection commands default to a structured human-readable panel.
Pass `--format json` (or the legacy `--json` alias) for machine-readable JSON.
ANSI color is auto-detected via `sys.stdout.isatty()` and respects the
`NO_COLOR` environment variable.

#### Example: `/daedalus status`

```
Daedalus runtime — yoyopod
  state    running (active mode)
  owner    daedalus-active-yoyopod
  schema   v3
  paths
    db          ~/.hermes/workflows/yoyopod/runtime/state/daedalus/daedalus.db
    events      ~/.hermes/workflows/yoyopod/runtime/memory/daedalus-events.jsonl
  heartbeat
    last        22:43:01 UTC (17s ago)
  lanes
    total       14
```

#### Example: `/daedalus active-gate-status`

```
Active execution gate
  ✓ ownership posture  primary_owner = daedalus
  ✓ active execution   enabled
  ✓ runtime mode       running in active
  ✓ legacy watchdog    retired (engine_owner = hermes)

→ gate is open: actions can dispatch
```

When blocked:

```
Active execution gate
  ✓ ownership posture  primary_owner = daedalus
  ✗ active execution   DISABLED  set via /daedalus set-active-execution --enabled true
  ✓ runtime mode       running in active
  ✓ legacy watchdog    retired (engine_owner = hermes)

→ gate is BLOCKED: no actions will dispatch
```

#### Example: `/daedalus doctor`

```
Daedalus doctor
  ✓ overall  PASS
  checks
    ✓ missing_lease       Runtime lease present
    ✓ shadow_compatible   Shadow decision matches legacy
    ✓ active_execution_failures  No active execution failures
```
```

- [ ] **Step 2: Commit**

```bash
git add docs/slash-commands-catalog.md
git commit -m "docs(catalog): rendered examples for status / active-gate-status / doctor"
```

---

### Task 6.3: Final audit

- [ ] **Step 1: Full test suite**

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -5`
Expected: 285 + (14 + 12 + 7 + 4 + 4 + 4 + 3 + 4) = 337+ passed, 0 failed.

- [ ] **Step 2: PAYLOAD_ITEMS includes formatters.py**

Run: `grep -A 20 "PAYLOAD_ITEMS\s*=" scripts/install.py | head -25`

If `formatters.py` is NOT listed, add it (mirror how `watch.py` is listed).

```bash
git add scripts/install.py
git commit -m "chore(install): add formatters.py to PAYLOAD_ITEMS" || echo "already there"
```

- [ ] **Step 3: Validate live YoYoPod runs without regression**

Run:
```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/output-formatting-issue-3
/usr/bin/python3 -c "
import sys
sys.path.insert(0, '.')
from formatters import format_status, format_active_gate_status, format_doctor
status_dict = {'runtime_status': 'running', 'current_mode': 'active',
               'active_orchestrator_instance_id': 'test',
               'schema_version': 3, 'lane_count': 0,
               'db_path': '/tmp/x.db', 'event_log_path': '/tmp/y.jsonl',
               'latest_heartbeat_at': '2026-04-26T22:43:01Z'}
print(format_status(status_dict, use_color=False, now_iso='2026-04-26T22:43:18Z'))
print('---')
gate_dict = {'allowed': True, 'reasons': [], 'execution': {'active_execution_enabled': True},
             'primary_owner': 'daedalus',
             'runtime': {'runtime_status': 'running', 'current_mode': 'active'}}
print(format_active_gate_status(gate_dict, use_color=False))
"
```
Expected: clean panel output for both commands with no Python errors.

- [ ] **Step 4: Branch summary**

Run: `git log --oneline main..HEAD`

Should show the spec commit, plan commit, and the per-task commits in chronological order.

If anything looks wrong, STOP and report.

---

## Acceptance criteria check (against spec §11)

- [ ] All upgraded commands render structured panel by default: Tasks 3.1, 3.2, 4.1, 4.2, 5.1, 5.2
- [ ] `--format text|json` works on every inspection command: Task 2.1
- [ ] `--json` continues to produce identical output: Task 2.1's resolution test
- [ ] ANSI color only when `isatty()` AND no `NO_COLOR`: Task 1.1's `_use_color` tests
- [ ] Single panel renderer used by all formatters: every per-command formatter calls `format_panel`
- [ ] Empty values render as `—`: Task 1.1's test
- [ ] Booleans never render as raw True/False: every per-command test asserts this
- [ ] No-information-loss across upgraded commands: Task 6.1
- [ ] Active-gate-status remediation hint when blocked: Task 3.2's test
- [ ] All existing tests pass: every task's full-suite step
- [ ] Doc update with examples: Task 6.2
