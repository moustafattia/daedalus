# Inspection Output Formatting — Design Spec

**Issue:** [moustafattia/daedalus #3](https://github.com/moustafattia/daedalus/issues/3)
**Date:** 2026-04-26
**Status:** Approved (auto-mode)

## 1. Problem

`/daedalus` inspection commands return terse single-line `key=value` strings. From a real session:

```
/daedalus status
runtime=running mode=active owner=daedalus-active-yoyopod lanes=14

/daedalus active-gate-status
allowed=True active_execution_enabled=True reasons=
```

This is unfriendly for an operator reading at a glance: no structure, no alignment, no grouping, empty fields render as bare trailing `=`, booleans stringify as Python literals, no inline "what to do next" hints.

## 2. Goals (in scope)

1. Default output of inspection commands renders as a **structured human-readable panel** — title, sections, key/value pairs aligned within section, status glyphs (`✓` / `✗`), color when interactive.
2. **Single panel renderer** in a new `formatters.py` module reused across all inspection commands so visual style stays consistent.
3. **`--format text|json` flag** added to every inspection command for explicit selection. **`--json` retained as alias** for back-compat with existing scripts and the slash-command operator habit.
4. **Color auto-detected** via `sys.stdout.isatty()` — never appears in piped or captured output.
5. **No information loss** — every field shown in the current terse output is shown in the new panel output (just better grouped). `--json` retains the exact machine-readable shape.
6. **Inline next-action hints** for blocked-state cases (active-gate `DISABLED → set via /daedalus set-active-execution`) — bounded scope; only when the next step is unambiguous.
7. Doc update with a real rendered output example for each upgraded command.

## 3. Non-goals

- **Operational commands** (`init`, `start`, `iterate-*`, `run-*`, `execute-action`, `set-*`, `service-{install,uninstall,start,stop,restart,enable,disable}`, `migrate-*`, `heartbeat`, `analyze-failure`) — their current confirmation-string output is fine; they do a thing and report it. Keep as-is.
- New formats beyond `text` and `json` (yaml, markdown, etc.) — defer until requested.
- Width-aware re-wrapping. Let the terminal handle wrapping.
- Themable colors. The default scheme stays fixed.
- TUI / interactive elements. This issue is *snapshot* output; live tracking is Issue #1's `/daedalus watch`.

## 4. Architecture decisions

### 4.1 New module `formatters.py` at repo root (Daedalus core)

Lives next to `tools.py`, `watch.py`. Shipped via `scripts/install.py` `PAYLOAD_ITEMS`. Daedalus core surface (not workflow-specific) since the inspection commands belong to `/daedalus`, not `/workflow`.

### 4.2 Output flow stays the same

```
execute_namespace(args)  →  result: dict
render_result(command, result, *, format)  →  str
```

`render_result` continues to dispatch by command name. For inspection commands it now calls `formatters.format_panel(...)` (text mode) or returns `json.dumps(result, indent=2, sort_keys=True)` (json mode). For operational commands it falls through to the existing terse-string branch.

### 4.3 `--format` is the canonical flag; `--json` is an alias

```
/daedalus status                  → text (default)
/daedalus status --format text    → text (explicit)
/daedalus status --format json    → json
/daedalus status --json           → json (alias, back-compat)
```

`--format` is a string with choices `[text, json]`, default `text`. The dispatcher resolves to json when EITHER `--json` is set OR `--format json` is set:

```python
fmt = "json" if (getattr(args, "json", False) or getattr(args, "format", "text") == "json") else "text"
```

### 4.4 Color is opt-in by capability, not by config

`_use_color()` returns `True` only when `sys.stdout.isatty()` AND `os.environ.get("NO_COLOR")` is unset (per the [no-color.org](https://no-color.org/) convention). No `--no-color` flag — environment is the right surface.

### 4.5 Unicode glyphs assumed

Modern terminals all handle UTF-8. We emit `✓` `✗` `→` `⚠` directly. Locale-fallback to ASCII is over-engineering for the realistic deployment surface (Linux/macOS terminals). If we ever see real complaints we add a `DAEDALUS_ASCII=1` env override.

### 4.6 Single panel primitive: `format_panel`

```python
format_panel(
    title: str,
    sections: list[Section],
    *,
    use_color: bool = True,
    footer: str | None = None,
) -> str
```

Where `Section` is a dataclass:

```python
@dataclass
class Section:
    name: str | None                 # None = no header (top-level rows)
    rows: list[Row]

@dataclass
class Row:
    label: str                       # left column (key)
    value: str                       # right column (value)
    status: Literal["pass", "fail", "warn", "info"] | None = None
                                     # adds glyph + color
    detail: str | None = None        # optional hint after value
```

The renderer auto-aligns labels within each section by computing the max label width, indents sections (2 spaces) and rows under sections (4 spaces).

### 4.7 Per-command formatters live in `formatters.py`

Each upgraded command gets a small `format_<command>(result)` function in `formatters.py` that takes the result dict and returns a `format_panel(...)` call. Keeping per-command formatters in the same file means a future contributor adding a new inspection command finds the same pattern for visual style.

## 5. Commands upgraded

| Command | Today | After |
|---|---|---|
| `status` | `runtime=running mode=active owner=... lanes=14` | Multi-section panel: runtime / paths / heartbeat / lanes |
| `doctor` | `daedalus-doctor / overall: PASS / - PASS code: summary` | Aligned check rows with PASS/FAIL glyphs |
| `active-gate-status` | `allowed=True active_execution_enabled=True reasons=` | Per-gate-condition rows + summary line + remediation hint when blocked |
| `shadow-report` | Multi-line but still terse | Sectioned panel: runtime / heartbeat / service / live lane / decisions / warnings / recent actions |
| `service-status` | Single line with PID + path | Sectioned: identity / install state / runtime state / paths |
| `get-observability` | Already 4-line text | Promote to panel for consistency |

Operational commands (`set-*`, `init`, `start`, etc.) keep their existing confirmation-string output. Adding `--format json` to all of them is out of scope (issue says "every inspection command" — operational ones already mostly have `--json` and that surface stays).

## 6. Output style guide

- **Title**: one line, format `Daedalus <thing> — <context>` (e.g. `Daedalus runtime — yoyopod`). Bold when color enabled.
- **Sections**: indented 2 spaces from title. Section header on its own line in dim color.
- **Rows**: indented 4 spaces (or 2 if no section header). Label left-aligned, padded to max-label-width within the section + 2 spaces. Value follows.
- **Glyphs**: `✓` (green) for pass, `✗` (red) for fail, `⚠` (yellow) for warn, `→` (cyan) for hint/next-action. Always followed by a space.
- **Footer**: optional one-line summary `→ gate is open` / `→ gate is BLOCKED`. Always shown for active-gate-status; rare for others.
- **Empty values**: render as `—` (em-dash) instead of empty string. Tells operator "this exists but has no current value" vs "missing field".
- **Paths**: shown verbatim, not abbreviated (the operator might paste them into a shell). Use `~/` only if the path begins with `$HOME`.
- **Timestamps**: shown as ISO-8601 UTC with parenthetical relative age (`22:43:01 UTC (17s ago)`).

## 7. Color palette (ANSI 16-color, fixed)

| Use | ANSI |
|---|---|
| Title bold | `\033[1m` |
| Section header dim | `\033[2m` |
| Pass `✓` green | `\033[32m` |
| Fail `✗` red | `\033[31m` |
| Warn `⚠` yellow | `\033[33m` |
| Hint `→` cyan | `\033[36m` |
| Reset | `\033[0m` |

When `_use_color()` returns False, all wrapping is no-op.

## 8. Module layout

```
formatters.py              # NEW — color helpers + Section/Row dataclasses + format_panel + per-command formatters
tools.py                   # MODIFIED — render_result delegates to formatters; --format flag added everywhere

tests/
  test_formatters.py                          # NEW — panel renderer + color gating + per-command snapshot-ish tests
  test_tools_render_result_format_flag.py     # NEW — --format text|json + --json alias resolution
```

## 9. Test strategy

### 9.1 Unit tests (pure rendering)

- `format_panel` produces expected layout for: empty sections, single section, multi-section, missing values, all status types
- Color is stripped when `use_color=False`
- `_use_color()` returns False when `NO_COLOR` env is set
- `_use_color()` returns False when stdout is not a TTY (mock `sys.stdout.isatty`)
- Empty value renders as `—`
- Path rendering: `$HOME` prefix collapses to `~/`
- Timestamp rendering: ISO + relative age suffix

### 9.2 Per-command snapshot-ish tests

For each upgraded command, give the formatter a representative result dict and assert:
- Expected section headers present
- Key fields are present somewhere in the output
- No raw `True/False` Python literals leak through (booleans render as `enabled/disabled` or via glyphs)

Don't pin exact whitespace — that's brittle. Test for *presence and grouping*, not pixel-perfect formatting.

### 9.3 Format-flag resolution tests

- `--format text` and absent flag both produce text
- `--format json` produces json
- `--json` (legacy) produces json
- `--json --format text` → still json (json wins; preserve script back-compat)
- `--format text --json` → still json (same reason)

### 9.4 No-information-loss tests

For each upgraded command, parse the JSON result and assert every top-level key appears somewhere in the text output (by name or by value). Catches accidental field drops.

## 10. Backwards compatibility

- All existing `--json` flags continue to work and produce identical machine-readable output (`json.dumps(result, indent=2, sort_keys=True)` is unchanged).
- Existing scripts that grep stdout for terse `key=value` patterns will break. **This is the intentional change** — the issue's premise is that those scripts should switch to `--json`. Operational commands' confirmation strings (which scripts may depend on) are NOT touched.
- The dict shape returned by `execute_namespace` is unchanged — only the text rendering is modified. Any consumer using the Python API directly is unaffected.

## 11. Acceptance criteria

- [ ] All listed inspection commands (§5) render a structured panel by default
- [ ] `--format text|json` works on every inspection command
- [ ] `--json` continues to work and produces identical output to `--format json`
- [ ] ANSI color appears only when `sys.stdout.isatty()` and `NO_COLOR` is unset
- [ ] Single panel renderer (`format_panel`) used by all per-command formatters — no duplicated layout code
- [ ] Empty values render as `—` not bare blank
- [ ] Booleans never render as raw `True`/`False` in text mode
- [ ] No information from current text output is dropped (verified per §9.4)
- [ ] Active-gate-status shows remediation hint when blocked
- [ ] All existing tests pass (baseline 285)
- [ ] Doc update: `docs/slash-commands-catalog.md` shows rendered examples
