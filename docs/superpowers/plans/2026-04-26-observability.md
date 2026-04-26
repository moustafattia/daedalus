# Operator Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Daedalus issue #1 — workflow-CLI–driven GitHub ticket comments, an operator override surface, and a `/daedalus watch` live TUI.

**Architecture:** Three layers landing in three phases. Phase 1: per-workflow comment publisher invoked from the existing `audit_fn` hook in `workflows/code_review/workspace.py`. Phase 2: Daedalus-core `/daedalus watch` TUI reading existing event sources read-only. Phase 3: operator override commands writing `runtime/state/daedalus/observability-overrides.json` consumed by Phase 1's resolver.

**Tech Stack:** Python 3.11 stdlib + pyyaml + jsonschema (already deps). Phase 2 adds `rich` (already a Hermes dep — verify in install.py before coding). `gh` CLI for GitHub interactions (already used by `actions.py`).

**Spec:** `docs/superpowers/specs/2026-04-26-observability-design.md`

**Tests baseline:** 244 tests passing + 1 pre-existing `test_runtime_tools_alerts.py` failure (unrelated). Final state: 244 + N new tests passing, same one pre-existing failure unchanged. Always run with `/usr/bin/python3` (system 3.11 has pyyaml + jsonschema; homebrew python3 does not).

**Repo:** `/home/radxa/WS/hermes-relay`. **Worktree for this work:** `.claude/worktrees/observability-issue-1` on branch `claude/observability-issue-1`. All commits land on that branch.

---

## Phase 0: Preflight

### Task 0.1: Verify rich availability + worktree state

**Files:**
- Read: `scripts/install.py`, `pyproject.toml` (if exists), system site-packages

- [ ] **Step 1: Confirm rich is importable from /usr/bin/python3**

Run: `/usr/bin/python3 -c "import rich; print(rich.__version__)"`
Expected: prints version, exits 0. If it fails, STOP and report — Phase 2 needs rich.

- [ ] **Step 2: Confirm worktree is on the right branch**

Run: `cd /home/radxa/WS/hermes-relay/.claude/worktrees/observability-issue-1 && git branch --show-current`
Expected: `claude/observability-issue-1`

- [ ] **Step 3: Confirm baseline tests pass**

Run: `cd /home/radxa/WS/hermes-relay/.claude/worktrees/observability-issue-1 && /usr/bin/python3 -m pytest -q 2>&1 | tail -5`
Expected: `244 passed, 1 failed in <X>s` with the one failure being `tests/test_runtime_tools_alerts.py`. Nothing else.

No commit for this task.

---

## Phase 1: Comment publisher (workflow CLI)

### Task 1.1: Schema entry for `observability` block in `workflows/code_review/schema.yaml`

**Files:**
- Modify: `workflows/code_review/schema.yaml`
- Test: `tests/test_workflow_code_review_schema.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflow_code_review_schema.py`:

```python
"""Schema validation for the observability block in workflow.yaml."""
import importlib.util
from pathlib import Path

import jsonschema
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "workflows" / "code_review" / "schema.yaml"


def _load_schema() -> dict:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _minimal_valid_config() -> dict:
    """Smallest workflow.yaml dict that satisfies the existing required fields."""
    return {
        "workflow": "code-review",
        "schema-version": 1,
        "instance": {"name": "test", "engine-owner": "hermes"},
        "repository": {
            "local-path": "/tmp/x",
            "github-slug": "owner/repo",
            "active-lane-label": "active-lane",
        },
        "runtimes": {
            "acpx-codex": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": 1,
                "session-idle-grace-seconds": 1,
                "session-nudge-cooldown-seconds": 1,
            }
        },
        "agents": {
            "coder": {
                "default": {"name": "x", "model": "y", "runtime": "acpx-codex"}
            },
            "internal-reviewer": {"name": "x", "model": "y", "runtime": "acpx-codex"},
            "external-reviewer": {"enabled": True, "name": "x"},
        },
        "gates": {
            "internal-review": {},
            "external-review": {},
            "merge": {},
        },
        "triggers": {"lane-selector": {"type": "github-label", "label": "active-lane"}},
        "storage": {
            "ledger": "memory/ledger.json",
            "health": "memory/health.json",
            "audit-log": "memory/audit.jsonl",
        },
    }


def test_schema_accepts_config_without_observability_block():
    """Back-compat: existing workflow.yaml files without observability still validate."""
    schema = _load_schema()
    config = _minimal_valid_config()
    jsonschema.validate(config, schema)  # must not raise


def test_schema_accepts_observability_with_github_comments_disabled():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["observability"] = {
        "github-comments": {"enabled": False}
    }
    jsonschema.validate(config, schema)


def test_schema_accepts_observability_full_block():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["observability"] = {
        "github-comments": {
            "enabled": True,
            "mode": "edit-in-place",
            "include-events": ["dispatch-implementation-turn", "merge-and-promote"],
            "suppress-transient-failures": True,
        }
    }
    jsonschema.validate(config, schema)


def test_schema_rejects_invalid_mode():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["observability"] = {
        "github-comments": {"enabled": True, "mode": "append-thread"}
    }
    try:
        jsonschema.validate(config, schema)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected ValidationError for invalid mode 'append-thread'")


def test_schema_rejects_github_comments_missing_enabled():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["observability"] = {"github-comments": {"mode": "edit-in-place"}}
    try:
        jsonschema.validate(config, schema)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected ValidationError when 'enabled' missing")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_schema.py -v`
Expected: 5 tests, the 3 "accept" tests fail with `additionalProperties` violation, the 2 "reject" tests fail with `"expected ValidationError"` (because the schema currently allows anything under unknown top-level keys).

Wait — actually the existing schema's top level uses `required` + `properties` without `additionalProperties: false`, so unknown top-level keys are accepted. Verify by running the test. If the "accept" tests pass already (because unknown keys are silently allowed), only the "reject" tests fail. Either way, proceed to Step 3 to add explicit schema rules so behavior is intentional.

- [ ] **Step 3: Add observability block to schema**

In `workflows/code_review/schema.yaml`, find the `properties:` block (top-level) and add the following entry alphabetically (after `agents:`, before `gates:` is fine — order doesn't matter for jsonschema, but place it after `codex-bot:` near the bottom for readability):

```yaml
  observability:
    type: object
    properties:
      github-comments:
        type: object
        required: [enabled]
        additionalProperties: false
        properties:
          enabled: {type: boolean}
          mode: {type: string, enum: [edit-in-place]}
          include-events:
            type: array
            items: {type: string}
          suppress-transient-failures: {type: boolean}
```

- [ ] **Step 4: Run tests to verify all 5 pass**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_schema.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/schema.yaml tests/test_workflow_code_review_schema.py
git commit -m "feat(schema): add observability.github-comments block to code-review workflow

Schema validates 'enabled' as required, 'mode' constrained to edit-in-place,
'include-events' as string array, 'suppress-transient-failures' as boolean.
Block is optional — existing workflow.yaml files without it continue to validate."
```

---

### Task 1.2: Observability config resolver — `workflows/code_review/observability.py`

**Files:**
- Create: `workflows/code_review/observability.py`
- Test: `tests/test_workflow_code_review_observability.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflow_code_review_observability.py`:

```python
"""Resolution of effective observability config (override > yaml > default)."""
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return load_module(
        "daedalus_workflow_code_review_observability_test",
        "workflows/code_review/observability.py",
    )


def test_default_when_yaml_block_absent_and_no_override(tmp_path):
    obs = _module()
    cfg = obs.resolve_effective_config(workflow_yaml={}, override_dir=tmp_path, workflow_name="code-review")
    assert cfg["github-comments"]["enabled"] is False
    assert cfg["github-comments"]["mode"] == "edit-in-place"
    assert cfg["github-comments"]["include-events"] == []
    assert cfg["github-comments"]["suppress-transient-failures"] is True
    assert cfg["source"]["github-comments"] == "default"


def test_yaml_block_picked_up_when_no_override(tmp_path):
    obs = _module()
    yaml_block = {
        "observability": {
            "github-comments": {
                "enabled": True,
                "mode": "edit-in-place",
                "include-events": ["merge-and-promote"],
                "suppress-transient-failures": False,
            }
        }
    }
    cfg = obs.resolve_effective_config(
        workflow_yaml=yaml_block, override_dir=tmp_path, workflow_name="code-review"
    )
    assert cfg["github-comments"]["enabled"] is True
    assert cfg["github-comments"]["include-events"] == ["merge-and-promote"]
    assert cfg["github-comments"]["suppress-transient-failures"] is False
    assert cfg["source"]["github-comments"] == "yaml"


def test_override_file_wins_over_yaml(tmp_path):
    obs = _module()
    yaml_block = {"observability": {"github-comments": {"enabled": True}}}
    override_file = tmp_path / "observability-overrides.json"
    override_file.write_text(json.dumps({
        "code-review": {"github-comments": {"enabled": False, "set-at": "2026-04-26T00:00:00Z"}}
    }))
    cfg = obs.resolve_effective_config(
        workflow_yaml=yaml_block, override_dir=tmp_path, workflow_name="code-review"
    )
    assert cfg["github-comments"]["enabled"] is False
    assert cfg["source"]["github-comments"] == "override"


def test_override_for_other_workflow_is_ignored(tmp_path):
    obs = _module()
    yaml_block = {"observability": {"github-comments": {"enabled": True}}}
    override_file = tmp_path / "observability-overrides.json"
    override_file.write_text(json.dumps({
        "other-workflow": {"github-comments": {"enabled": False}}
    }))
    cfg = obs.resolve_effective_config(
        workflow_yaml=yaml_block, override_dir=tmp_path, workflow_name="code-review"
    )
    assert cfg["github-comments"]["enabled"] is True
    assert cfg["source"]["github-comments"] == "yaml"


def test_override_file_corrupt_falls_through_to_yaml(tmp_path):
    obs = _module()
    yaml_block = {"observability": {"github-comments": {"enabled": True}}}
    (tmp_path / "observability-overrides.json").write_text("not json{")
    cfg = obs.resolve_effective_config(
        workflow_yaml=yaml_block, override_dir=tmp_path, workflow_name="code-review"
    )
    # Corrupt override is ignored, yaml wins, source reflects fallback.
    assert cfg["github-comments"]["enabled"] is True
    assert cfg["source"]["github-comments"] == "yaml"


def test_event_is_included_respects_include_events_list(tmp_path):
    obs = _module()
    cfg = {"github-comments": {"enabled": True, "include-events": ["merge-and-promote"]}}
    assert obs.event_is_included(cfg, "merge-and-promote") is True
    assert obs.event_is_included(cfg, "dispatch-implementation-turn") is False


def test_event_is_included_empty_list_means_all_events(tmp_path):
    """An empty include-events list = include every audit action (operator can opt out per-event later)."""
    obs = _module()
    cfg = {"github-comments": {"enabled": True, "include-events": []}}
    assert obs.event_is_included(cfg, "anything") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_observability.py -v`
Expected: All fail — `workflows/code_review/observability.py` does not exist yet.

- [ ] **Step 3: Implement the resolver**

Create `workflows/code_review/observability.py`:

```python
"""Effective observability config resolution.

Resolution precedence (highest first):

1. Override file at ``<override_dir>/observability-overrides.json`` (per-workflow,
   set by the operator via the ``/daedalus set-observability`` slash command).
2. ``observability:`` block in ``workflow.yaml``.
3. Hardcoded defaults (everything off).

The override file is canonical for "right now this workflow's observability is X"
without forcing an edit-and-redeploy cycle on workflow.yaml.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


OVERRIDE_FILENAME = "observability-overrides.json"

_DEFAULT_GITHUB_COMMENTS = {
    "enabled": False,
    "mode": "edit-in-place",
    "include-events": [],          # empty list = include every audit event
    "suppress-transient-failures": True,
}


def _read_override_file(override_dir: Path) -> dict[str, Any]:
    path = override_dir / OVERRIDE_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable override file — pretend it does not exist.
        # The operator override surface is best-effort observability config;
        # never block real workflow execution on a malformed override.
        return {}


def resolve_effective_config(
    *,
    workflow_yaml: Mapping[str, Any],
    override_dir: Path,
    workflow_name: str,
) -> dict[str, Any]:
    """Return the effective observability config for ``workflow_name``.

    The result has the shape::

        {
            "github-comments": {"enabled": bool, "mode": str, ...},
            "source": {"github-comments": "default" | "yaml" | "override"},
        }

    ``source`` is informational — used by ``/daedalus get-observability`` to
    explain *why* the current value is what it is.
    """
    yaml_block = (workflow_yaml or {}).get("observability") or {}
    yaml_gh = yaml_block.get("github-comments")

    overrides = _read_override_file(override_dir)
    override_for_wf = (overrides.get(workflow_name) or {}).get("github-comments")

    if override_for_wf is not None:
        merged = {**_DEFAULT_GITHUB_COMMENTS, **(yaml_gh or {}), **override_for_wf}
        # Strip the bookkeeping fields that the override file may carry.
        merged.pop("set-at", None)
        merged.pop("set-by", None)
        source = "override"
    elif yaml_gh is not None:
        merged = {**_DEFAULT_GITHUB_COMMENTS, **yaml_gh}
        source = "yaml"
    else:
        merged = dict(_DEFAULT_GITHUB_COMMENTS)
        source = "default"

    return {
        "github-comments": merged,
        "source": {"github-comments": source},
    }


def event_is_included(effective_config: Mapping[str, Any], audit_action: str) -> bool:
    """Whether ``audit_action`` should produce a comment update under ``effective_config``."""
    gh = (effective_config or {}).get("github-comments") or {}
    if not gh.get("enabled"):
        return False
    include = gh.get("include-events") or []
    if not include:
        # Empty list = include every event (caller's whitelist is "everything").
        return True
    return audit_action in include
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_observability.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/observability.py tests/test_workflow_code_review_observability.py
git commit -m "feat(observability): config resolver with override > yaml > default precedence

Pure-function resolver returns effective github-comments config plus a
'source' field explaining which layer won. Corrupt override files fall
through to yaml without raising — observability is best-effort and must
never block workflow execution."
```

---

### Task 1.3: Comment formatter — `workflows/code_review/comments.py` (render only)

**Files:**
- Create: `workflows/code_review/comments.py`
- Test: `tests/test_workflow_code_review_comments_format.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflow_code_review_comments_format.py`:

```python
"""Audit event → bot-comment rendering. Pure-function, no I/O."""
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


def _module():
    return load_module(
        "daedalus_workflow_code_review_comments_test",
        "workflows/code_review/comments.py",
    )


def test_render_row_for_dispatch_implementation_turn():
    comments = _module()
    row = comments.render_row({
        "at": "2026-04-26T22:30:34Z",
        "action": "dispatch-implementation-turn",
        "summary": "Dispatched coder",
        "model": "gpt-5.3-codex-spark/high",
        "sessionName": "lane-329",
    })
    assert "🔄" in row
    assert "Codex coder dispatched" in row
    assert "gpt-5.3-codex-spark/high" in row
    assert "22:30:34" in row


def test_render_row_for_merge_and_promote():
    comments = _module()
    row = comments.render_row({
        "at": "2026-04-26T22:31:00Z",
        "action": "merge-and-promote",
        "summary": "Merged",
        "mergedPrNumber": 382,
    })
    assert "✅" in row or "🚀" in row
    assert "382" in row


def test_render_row_falls_back_for_unknown_action():
    comments = _module()
    row = comments.render_row({
        "at": "2026-04-26T22:31:00Z",
        "action": "some-unknown-action",
        "summary": "Did the thing",
    })
    # Unknown actions still render — generic format keeps the comment honest
    # rather than silently dropping events.
    assert "some-unknown-action" in row
    assert "Did the thing" in row


def test_render_full_comment_includes_header_and_table():
    comments = _module()
    body = comments.render_comment(
        issue_number=329,
        workflow_state="under_review",
        rows=[
            "| 22:30:34 | 🔄 Codex coder dispatched | gpt-5.3-codex-spark/high |",
            "| 22:31:00 | 🚀 PR published | #382 |",
        ],
        is_operator_attention=False,
    )
    assert "Daedalus lane status" in body
    assert "lane #329" in body
    assert "under_review" in body
    assert "| Time (UTC) | Event | Detail |" in body
    assert "22:30:34" in body
    assert "Last update" in body


def test_render_full_comment_with_operator_attention_sets_sticky_header():
    comments = _module()
    body = comments.render_comment(
        issue_number=329,
        workflow_state="operator_attention_required",
        rows=["| 22:31:00 | ⚠️ Operator attention required | retry budget exhausted |"],
        is_operator_attention=True,
    )
    assert "⚠️" in body
    assert "operator-attention" in body or "operator_attention" in body


def test_render_truncates_to_max_rows():
    comments = _module()
    rows = [f"| 22:00:0{i} | x | y |" for i in range(60)]
    body = comments.render_comment(
        issue_number=329,
        workflow_state="under_review",
        rows=rows,
        is_operator_attention=False,
    )
    # Older rows truncated when count exceeds MAX_COMMENT_ROWS (50).
    assert body.count("| 22:00:") <= 51  # 50 rows + 1 header row pattern match


def test_append_row_keeps_chronological_order_newest_first():
    comments = _module()
    existing_rows = ["| 22:00:01 | ev1 | d1 |"]
    new_row = "| 22:00:05 | ev2 | d2 |"
    out = comments.append_row(existing_rows, new_row)
    assert out[0] == new_row  # newest at top
    assert out[1] == existing_rows[0]


def test_append_row_caps_at_max_rows():
    comments = _module()
    existing_rows = [f"| 22:00:{i:02d} | x | y |" for i in range(50)]
    new_row = "| 22:01:00 | new | new |"
    out = comments.append_row(existing_rows, new_row)
    assert len(out) == 50  # MAX_COMMENT_ROWS
    assert out[0] == new_row
    # Oldest row dropped.
    assert "22:00:00" not in out[-1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_comments_format.py -v`
Expected: All fail — module does not exist.

- [ ] **Step 3: Implement the formatter**

Create `workflows/code_review/comments.py`:

```python
"""Render workflow audit events as bot-comment markdown.

This module is pure rendering — no I/O, no GitHub API calls. The publisher
in ``comments_publisher.py`` consumes ``render_row``/``render_comment`` and
calls ``gh``.

Per-workflow design (issue #1, design doc 2026-04-26): each workflow owns
its own ``comments.py``. The code-review workflow's audit-event vocabulary
is what's mapped here; a future testing workflow would have its own.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

MAX_COMMENT_ROWS = 50

# Map audit-action → (emoji, headline). Unknown actions fall back to a generic
# render so we never silently drop a fired event.
_ACTION_RENDERERS: dict[str, tuple[str, str]] = {
    "dispatch-implementation-turn": ("🔄", "Codex coder dispatched"),
    "codex-committed": ("✅", "Codex committed"),
    "internal-review-started": ("🔍", "Internal review started"),
    "internal-review-completed": ("🔍", "Internal review completed"),
    "request-internal-review": ("🔍", "Internal review requested"),
    "publish-ready-pr": ("🚀", "PR published"),
    "push-pr-update": ("📤", "PR updated"),
    "merge-and-promote": ("✅", "Merged + promoted"),
    "operator-attention-transition": ("⚠️", "Operator attention required"),
    "operator-attention-recovered": ("✅", "Recovered — resuming"),
}


def _format_clock(at_iso: str) -> str:
    """ISO-8601 UTC → HH:MM:SS for table display."""
    try:
        # Tolerate trailing 'Z' as well as full ISO with offset.
        cleaned = at_iso.replace("Z", "+00:00") if at_iso.endswith("Z") else at_iso
        return datetime.fromisoformat(cleaned).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return at_iso  # let it through; better than crashing the publisher


def _detail_for(event: Mapping[str, Any]) -> str:
    action = event.get("action") or ""
    if action == "dispatch-implementation-turn":
        return event.get("model") or event.get("sessionName") or ""
    if action == "codex-committed":
        sha = event.get("commitSha") or event.get("headSha") or ""
        return f"`{sha[:7]}`" if sha else ""
    if action == "internal-review-completed":
        verdict = event.get("verdict") or event.get("review", {}).get("verdict", "")
        findings = event.get("findingsCount") or event.get("findings") or ""
        if findings:
            return f"{verdict} — {findings} findings" if verdict else str(findings)
        return verdict or ""
    if action == "publish-ready-pr":
        pr = event.get("prNumber") or event.get("number")
        return f"#{pr}" if pr else ""
    if action == "push-pr-update":
        sha = event.get("headSha") or ""
        return f"`{sha[:7]}`" if sha else ""
    if action == "merge-and-promote":
        return f"PR #{event.get('mergedPrNumber') or ''}"
    if action == "operator-attention-transition":
        return event.get("reason") or ""
    return event.get("summary") or ""


def render_row(event: Mapping[str, Any]) -> str:
    """Render one audit event as a markdown table row."""
    action = event.get("action") or "unknown"
    emoji, headline = _ACTION_RENDERERS.get(action, ("•", action))
    detail = _detail_for(event)
    clock = _format_clock(event.get("at") or "")
    return f"| {clock} | {emoji} {headline} | {detail} |"


def append_row(existing_rows: list[str], new_row: str) -> list[str]:
    """Insert ``new_row`` at the top, cap the list at MAX_COMMENT_ROWS."""
    out = [new_row, *existing_rows]
    return out[:MAX_COMMENT_ROWS]


def render_comment(
    *,
    issue_number: int,
    workflow_state: str,
    rows: list[str],
    is_operator_attention: bool,
) -> str:
    """Render the full bot-comment markdown body."""
    if is_operator_attention:
        header_state = "⚠️ operator-attention"
    else:
        header_state = workflow_state
    rows = rows[:MAX_COMMENT_ROWS]
    table_header = "| Time (UTC) | Event | Detail |\n|---|---|---|"
    body_rows = "\n".join(rows) if rows else "_(no events yet)_"
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        f"🤖 **Daedalus lane status** — lane #{issue_number} · `{header_state}`\n\n"
        f"{table_header}\n{body_rows}\n\n"
        f"_Last update: {now} · auto-generated by Daedalus_"
    )
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_comments_format.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/comments.py tests/test_workflow_code_review_comments_format.py
git commit -m "feat(comments): code-review audit event → bot-comment markdown formatter

Pure rendering, no I/O. Maps known audit actions to (emoji, headline) pairs;
unknown actions fall back to a generic render so fired events are never
silently dropped. Caps comment row count at 50 to stay under GitHub's 65535
byte limit safely."
```

---

### Task 1.4: Lane-comments state file helpers — extend `comments.py`

**Files:**
- Modify: `workflows/code_review/comments.py`
- Test: `tests/test_workflow_code_review_comments_state.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflow_code_review_comments_state.py`:

```python
"""State file: per-issue {comment_id, last_rendered_text, last_action}."""
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return load_module(
        "daedalus_workflow_code_review_comments_state_test",
        "workflows/code_review/comments.py",
    )


def test_state_path_for_issue(tmp_path):
    comments = _module()
    p = comments.state_path_for_issue(state_dir=tmp_path, issue_number=329)
    assert p == tmp_path / "329.json"


def test_load_returns_empty_state_when_file_absent(tmp_path):
    comments = _module()
    state = comments.load_state(tmp_path, 329)
    assert state == {"comment_id": None, "last_rendered_text": None, "rows": [], "last_action": None}


def test_save_then_load_roundtrip(tmp_path):
    comments = _module()
    state = {
        "comment_id": "12345",
        "last_rendered_text": "hello",
        "rows": ["| 22:00:01 | ev | d |"],
        "last_action": "dispatch-implementation-turn",
    }
    comments.save_state(tmp_path, 329, state)
    loaded = comments.load_state(tmp_path, 329)
    assert loaded == state


def test_save_writes_atomically(tmp_path):
    """Save should never leave a half-written file."""
    comments = _module()
    state = {"comment_id": "1", "last_rendered_text": "x", "rows": [], "last_action": None}
    comments.save_state(tmp_path, 329, state)
    # No leftover .tmp
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_save_creates_directory_if_missing(tmp_path):
    comments = _module()
    nested = tmp_path / "deeply" / "nested"
    state = {"comment_id": None, "last_rendered_text": None, "rows": [], "last_action": None}
    comments.save_state(nested, 329, state)
    assert (nested / "329.json").exists()


def test_load_corrupt_state_returns_empty(tmp_path):
    comments = _module()
    p = comments.state_path_for_issue(tmp_path, 329)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json{")
    state = comments.load_state(tmp_path, 329)
    assert state["comment_id"] is None
    assert state["rows"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_comments_state.py -v`
Expected: All fail — `state_path_for_issue`/`load_state`/`save_state` not defined.

- [ ] **Step 3: Add state helpers to `workflows/code_review/comments.py`**

Append at the end of `workflows/code_review/comments.py`:

```python
# ─── Lane-comments state file ────────────────────────────────────────────
# Persisted at <workflow_root>/runtime/state/lane-comments/<issue>.json
# Schema: {comment_id, last_rendered_text, rows, last_action}
#   - comment_id: GitHub comment id (str) or None when no comment created yet
#   - last_rendered_text: full body of the last successful PATCH/POST (str|None)
#   - rows: chronological-newest-first list of rendered table rows (list[str])
#   - last_action: name of the last audit action that fired (for dedupe / debug)

import json as _json
import os as _os


def state_path_for_issue(state_dir: Path, issue_number: int) -> Path:
    return Path(state_dir) / f"{issue_number}.json"


def _empty_state() -> dict[str, Any]:
    return {"comment_id": None, "last_rendered_text": None, "rows": [], "last_action": None}


def load_state(state_dir: Path, issue_number: int) -> dict[str, Any]:
    path = state_path_for_issue(state_dir, issue_number)
    if not path.exists():
        return _empty_state()
    try:
        return _json.loads(path.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError):
        return _empty_state()


def save_state(state_dir: Path, issue_number: int, state: dict[str, Any]) -> None:
    path = state_path_for_issue(state_dir, issue_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_json.dumps(state), encoding="utf-8")
    _os.replace(tmp, path)
```

Add `from pathlib import Path` (if not already imported) and ensure `from typing import Any` is present at the top of the file.

- [ ] **Step 4: Run tests to verify all pass**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_comments_state.py -v`
Expected: 6 passed.

Also run the prior test file to confirm no regression:
Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_comments_format.py tests/test_workflow_code_review_comments_state.py -v`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/comments.py tests/test_workflow_code_review_comments_state.py
git commit -m "feat(comments): per-issue state file (atomic write + corrupt-tolerant read)

State stored at runtime/state/lane-comments/<issue>.json with comment_id,
last rendered body, rendered rows, and last action. Atomic write via
temp-file + os.replace; corrupt JSON falls through to empty state so a
bad write never breaks the publisher."
```

---

### Task 1.5: Comment publisher — `workflows/code_review/comments_publisher.py`

> **Plan amendment 2026-04-26:** dedupe gate moved from rendered-text to row-equality (the rendered body changes on every render due to `Last update: now()`, making text dedupe unreliable). Test name kept; result reason kept (`rendered-unchanged`).

**Files:**
- Create: `workflows/code_review/comments_publisher.py`
- Test: `tests/test_workflow_code_review_comments_publisher.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflow_code_review_comments_publisher.py`:

```python
"""Comment publisher: orchestrates state load → render → gh CLI → state save.

We mock subprocess.run to capture every gh invocation; no live GitHub calls.
"""
import importlib.util
import json
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


def _publisher():
    return load_module(
        "daedalus_workflow_code_review_comments_publisher_test",
        "workflows/code_review/comments_publisher.py",
    )


class _FakeRun:
    """Capturable subprocess.run replacement."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError(f"FakeRun ran out of responses; argv={argv}")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        result = mock.Mock()
        result.returncode = resp.get("returncode", 0)
        result.stdout = resp.get("stdout", "")
        result.stderr = resp.get("stderr", "")
        if result.returncode != 0:
            import subprocess
            raise subprocess.CalledProcessError(result.returncode, argv, output=result.stdout, stderr=result.stderr)
        return result


def test_publisher_disabled_when_event_not_included(tmp_path):
    pub = _publisher()
    fake_run = _FakeRun([])  # no calls expected
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event={"at": "2026-04-26T22:00:00Z", "action": "reconcile", "summary": "x"},
        effective_config={"github-comments": {"enabled": True, "include-events": ["merge-and-promote"], "suppress-transient-failures": True}},
        state_dir=tmp_path,
        run_fn=fake_run,
    )
    assert result == {"published": False, "reason": "event-not-in-include-events"}
    assert fake_run.calls == []


def test_publisher_disabled_when_globally_off(tmp_path):
    pub = _publisher()
    fake_run = _FakeRun([])
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event={"at": "2026-04-26T22:00:00Z", "action": "merge-and-promote", "summary": "x"},
        effective_config={"github-comments": {"enabled": False, "include-events": []}},
        state_dir=tmp_path,
        run_fn=fake_run,
    )
    assert result["published"] is False
    assert result["reason"] == "github-comments-disabled"
    assert fake_run.calls == []


def test_first_event_creates_comment_via_gh(tmp_path):
    pub = _publisher()
    # gh issue comment returns the URL of the new comment
    fake_run = _FakeRun([
        {"returncode": 0, "stdout": "https://github.com/owner/repo/issues/329#issuecomment-12345\n"},
    ])
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event={
            "at": "2026-04-26T22:30:00Z",
            "action": "dispatch-implementation-turn",
            "summary": "Dispatched coder",
            "model": "gpt-5.3-codex-spark",
            "sessionName": "lane-329",
        },
        effective_config={"github-comments": {"enabled": True, "include-events": [], "suppress-transient-failures": True}},
        state_dir=tmp_path,
        run_fn=fake_run,
    )
    assert result["published"] is True
    assert result["comment_id"] == "12345"
    assert len(fake_run.calls) == 1
    argv = fake_run.calls[0]["argv"]
    assert argv[0] == "gh"
    assert "issue" in argv and "comment" in argv
    assert "329" in argv
    assert "--repo" in argv and "owner/repo" in argv
    # State persisted
    state_file = tmp_path / "329.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state["comment_id"] == "12345"
    assert "Daedalus lane status" in state["last_rendered_text"]


def test_subsequent_event_edits_existing_comment(tmp_path):
    pub = _publisher()
    # Pre-seed state as if a prior event created comment 12345
    (tmp_path / "329.json").write_text(json.dumps({
        "comment_id": "12345",
        "last_rendered_text": "old body",
        "rows": ["| 22:00:00 | 🔄 Codex coder dispatched | x |"],
        "last_action": "dispatch-implementation-turn",
    }))
    fake_run = _FakeRun([
        {"returncode": 0, "stdout": ""},  # gh api PATCH returns empty
    ])
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event={
            "at": "2026-04-26T22:31:00Z",
            "action": "merge-and-promote",
            "summary": "Merged",
            "mergedPrNumber": 382,
        },
        effective_config={"github-comments": {"enabled": True, "include-events": [], "suppress-transient-failures": True}},
        state_dir=tmp_path,
        run_fn=fake_run,
    )
    assert result["published"] is True
    assert result["comment_id"] == "12345"
    assert len(fake_run.calls) == 1
    argv = fake_run.calls[0]["argv"]
    # PATCH path uses gh api
    assert argv[0] == "gh"
    assert argv[1] == "api"
    assert any("12345" in part for part in argv)


def test_skip_publish_when_rendered_body_unchanged(tmp_path):
    pub = _publisher()
    # The comment publisher dedupes when rendered body matches last_rendered_text.
    # Pre-seed a state with a known body, then re-fire the same event.
    pre_event = {
        "at": "2026-04-26T22:30:00Z",
        "action": "dispatch-implementation-turn",
        "summary": "x",
        "model": "gpt-5.3-codex-spark",
        "sessionName": "lane-329",
    }
    fake_run_first = _FakeRun([
        {"returncode": 0, "stdout": "https://github.com/owner/repo/issues/329#issuecomment-12345\n"},
    ])
    pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event=pre_event,
        effective_config={"github-comments": {"enabled": True, "include-events": [], "suppress-transient-failures": True}},
        state_dir=tmp_path,
        run_fn=fake_run_first,
    )
    # Now re-fire the same event. With the same rendered output, no second gh call.
    fake_run_second = _FakeRun([])
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event=pre_event,
        effective_config={"github-comments": {"enabled": True, "include-events": [], "suppress-transient-failures": True}},
        state_dir=tmp_path,
        run_fn=fake_run_second,
    )
    assert result["published"] is False
    assert result["reason"] == "rendered-unchanged"
    assert fake_run_second.calls == []


def test_gh_failure_does_not_raise_returns_failure_result(tmp_path):
    pub = _publisher()
    import subprocess
    fake_run = _FakeRun([
        subprocess.CalledProcessError(1, ["gh"], output="", stderr="rate limited"),
    ])
    result = pub.publish_event(
        repo_slug="owner/repo",
        issue_number=329,
        workflow_state="under_review",
        is_operator_attention=False,
        audit_event={"at": "2026-04-26T22:30:00Z", "action": "merge-and-promote", "summary": "x", "mergedPrNumber": 382},
        effective_config={"github-comments": {"enabled": True, "include-events": [], "suppress-transient-failures": True}},
        state_dir=tmp_path,
        run_fn=fake_run,
    )
    # Publisher swallows the error — observability never blocks workflow execution.
    assert result["published"] is False
    assert "error" in result
    assert "rate limited" in result["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_comments_publisher.py -v`
Expected: All fail — `comments_publisher.py` does not exist.

- [ ] **Step 3: Implement the publisher**

Create `workflows/code_review/comments_publisher.py`:

```python
"""GitHub bot-comment publisher for the code-review workflow.

This is the only module that actually shells out to ``gh``. It composes:

  - ``observability.event_is_included`` (gate)
  - ``comments.render_row`` / ``render_comment`` (markdown body)
  - ``comments.load_state`` / ``save_state`` (per-issue persistence)
  - ``gh issue comment`` (create) / ``gh api PATCH`` (edit-in-place)

Failures NEVER raise. The workflow tick must continue even if observability
is broken — this is read-the-tea-leaves scaffolding, not a correctness layer.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

# Sibling-import boilerplate: dual-import (package vs script).
try:
    from . import comments as _comments_module
    from . import observability as _observability_module
except ImportError:
    _here = Path(__file__).resolve().parent

    def _load(name: str):
        spec = importlib.util.spec_from_file_location(
            f"daedalus_workflow_code_review_{name}", _here / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    _comments_module = _load("comments")
    _observability_module = _load("observability")


_COMMENT_URL_RE = re.compile(r"#issuecomment-(\d+)")


def _parse_comment_id_from_gh_output(stdout: str) -> str | None:
    """``gh issue comment`` prints the URL of the created comment on success."""
    if not stdout:
        return None
    m = _COMMENT_URL_RE.search(stdout)
    return m.group(1) if m else None


def publish_event(
    *,
    repo_slug: str,
    issue_number: int,
    workflow_state: str,
    is_operator_attention: bool,
    audit_event: Mapping[str, Any],
    effective_config: Mapping[str, Any],
    state_dir: Path,
    run_fn: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Publish (or skip) a comment update for one audit event.

    Returns a result dict. Never raises.
    """
    gh_cfg = (effective_config or {}).get("github-comments") or {}
    if not gh_cfg.get("enabled"):
        return {"published": False, "reason": "github-comments-disabled"}

    action = audit_event.get("action") or ""
    if not _observability_module.event_is_included(effective_config, action):
        return {"published": False, "reason": "event-not-in-include-events"}

    state = _comments_module.load_state(state_dir, issue_number)
    new_row = _comments_module.render_row(audit_event)
    existing_rows = state.get("rows") or []

    # Row-based dedupe: re-firing an event that produces the same row as
    # the current top row is a no-op tick — skip the API call. This is
    # more reliable than rendered-text dedupe because the rendered body
    # includes a `Last update: now()` timestamp that always changes.
    if existing_rows and new_row == existing_rows[0]:
        return {"published": False, "reason": "rendered-unchanged"}

    new_rows = _comments_module.append_row(existing_rows, new_row)
    rendered = _comments_module.render_comment(
        issue_number=issue_number,
        workflow_state=workflow_state,
        rows=new_rows,
        is_operator_attention=is_operator_attention,
    )

    comment_id = state.get("comment_id")

    try:
        if comment_id is None:
            # Create the bot-comment.
            argv = [
                "gh", "issue", "comment", str(issue_number),
                "--repo", repo_slug,
                "--body", rendered,
            ]
            result = run_fn(argv, check=True, capture_output=True, text=True)
            stdout = getattr(result, "stdout", "") or ""
            new_comment_id = _parse_comment_id_from_gh_output(stdout)
            if new_comment_id is None:
                return {"published": False, "error": f"could-not-parse-comment-id-from: {stdout!r}"}
            comment_id = new_comment_id
        else:
            # Edit-in-place via the comments API.
            api_path = f"/repos/{repo_slug}/issues/comments/{comment_id}"
            argv = [
                "gh", "api", "-X", "PATCH", api_path,
                "-f", f"body={rendered}",
            ]
            run_fn(argv, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (getattr(exc, "stderr", "") or "").strip()
        stdout = (getattr(exc, "stdout", "") or "").strip()
        return {
            "published": False,
            "error": stderr or stdout or str(exc),
        }
    except (FileNotFoundError, OSError) as exc:
        return {"published": False, "error": f"gh-cli-unavailable: {exc}"}

    new_state = {
        "comment_id": comment_id,
        "last_rendered_text": rendered,
        "rows": new_rows,
        "last_action": action,
    }
    try:
        _comments_module.save_state(state_dir, issue_number, new_state)
    except OSError as exc:
        return {"published": True, "comment_id": comment_id, "warning": f"state-save-failed: {exc}"}

    return {"published": True, "comment_id": comment_id}
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_comments_publisher.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/comments_publisher.py tests/test_workflow_code_review_comments_publisher.py
git commit -m "feat(comments): publisher orchestrates render -> gh CLI -> state save

Single entry point publish_event: gates on effective config, dedupes
rendered-unchanged ticks, creates-or-edits the per-issue bot-comment,
persists comment_id + last_rendered_text. CalledProcessError is
swallowed and returned as a result dict — observability must never
block workflow execution."
```

---

### Task 1.6: Wire publisher into `workspace.audit`

**Files:**
- Modify: `workflows/code_review/workspace.py:412` (the `audit` closure)
- Test: `tests/test_workflow_code_review_workspace_audit_hook.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflow_code_review_workspace_audit_hook.py`:

```python
"""The audit() closure should invoke the comment publisher when one is wired in."""
import importlib.util
import json
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


def test_audit_calls_publisher_when_hook_provided(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_test",
        "workflows/code_review/workspace.py",
    )

    audit_log_path = tmp_path / "audit.jsonl"
    captured_calls = []

    def fake_publisher(*, action, summary, extra):
        captured_calls.append({"action": action, "summary": summary, "extra": extra})

    audit_fn = workspace_module._make_audit_fn(
        audit_log_path=audit_log_path,
        publisher=fake_publisher,
    )

    audit_fn("merge-and-promote", "Merged", mergedPrNumber=382)

    # Audit log was still written
    lines = audit_log_path.read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action"] == "merge-and-promote"
    assert entry["mergedPrNumber"] == 382

    # Publisher was called
    assert len(captured_calls) == 1
    assert captured_calls[0]["action"] == "merge-and-promote"
    assert captured_calls[0]["extra"]["mergedPrNumber"] == 382


def test_audit_does_not_raise_if_publisher_throws(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_test",
        "workflows/code_review/workspace.py",
    )
    audit_log_path = tmp_path / "audit.jsonl"

    def bad_publisher(**kwargs):
        raise RuntimeError("publisher exploded")

    audit_fn = workspace_module._make_audit_fn(
        audit_log_path=audit_log_path,
        publisher=bad_publisher,
    )
    # Must not raise
    audit_fn("merge-and-promote", "Merged", mergedPrNumber=382)
    # Audit log still written
    assert audit_log_path.exists()


def test_audit_works_with_no_publisher(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_test",
        "workflows/code_review/workspace.py",
    )
    audit_log_path = tmp_path / "audit.jsonl"

    audit_fn = workspace_module._make_audit_fn(
        audit_log_path=audit_log_path,
        publisher=None,
    )
    audit_fn("dispatch-implementation-turn", "ok", model="x")
    assert audit_log_path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_workspace_audit_hook.py -v`
Expected: All fail — `_make_audit_fn` does not exist.

- [ ] **Step 3: Refactor `audit` into a module-level `_make_audit_fn` and add the publisher hook**

In `workflows/code_review/workspace.py`, find the existing `audit` closure at line 412 and refactor:

(a) Add a module-level helper near the top of the file (after the existing imports and before the `build_workspace` function — search for `def build_workspace` to find the right spot):

```python
def _make_audit_fn(
    *,
    audit_log_path,
    publisher=None,
):
    """Build an ``audit(action, summary, **extra)`` closure that:

      1. Always appends a JSONL row to ``audit_log_path``.
      2. If ``publisher`` is provided, calls ``publisher(action=..., summary=..., extra=...)``
         after the write. Publisher exceptions are swallowed — observability
         must never break workflow execution.
    """
    def audit(action, summary, **extra):
        _append_jsonl(
            audit_log_path,
            {
                "at": _now_iso(),
                "action": action,
                "summary": summary,
                **extra,
            },
        )
        if publisher is not None:
            try:
                publisher(action=action, summary=summary, extra=dict(extra))
            except Exception:
                # Best-effort observability hook; never raise into the caller.
                pass

    return audit
```

(b) In the existing `build_workspace` function, replace the inline `def audit(...)` closure (around line 412) with:

```python
    audit = _make_audit_fn(audit_log_path=audit_log_path, publisher=None)
```

(For Task 1.6 we just refactor — the publisher stays None. Wiring in the actual publisher happens in Task 1.7.)

- [ ] **Step 4: Run tests to verify all pass**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_workspace_audit_hook.py -v`
Expected: 3 passed.

Run the full workspace-related test suite to confirm no regression:
Run: `/usr/bin/python3 -m pytest tests/ -k "workspace or workflow" -v 2>&1 | tail -10`
Expected: All previously passing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add workflows/code_review/workspace.py tests/test_workflow_code_review_workspace_audit_hook.py
git commit -m "refactor(workspace): extract audit() to _make_audit_fn with publisher hook

Closure refactored to a module-level factory accepting an optional
publisher. Publisher exceptions are swallowed so observability can
never break the workflow tick. Behavior unchanged when publisher=None
(the default for now)."
```

---

### Task 1.7: Wire the actual publisher into `build_workspace`

**Files:**
- Modify: `workflows/code_review/workspace.py` (the `build_workspace` function — wire publisher)
- Test: `tests/test_workflow_code_review_workspace_publisher_wire.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflow_code_review_workspace_publisher_wire.py`:

```python
"""build_workspace should wire the comment publisher when observability is on."""
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


def _make_minimal_config(workspace_root: Path) -> dict:
    return {
        "workflow": "code-review",
        "schemaVersion": 1,
        "instance": {"name": "test", "engineOwner": "hermes"},
        "repository": {
            "localPath": str(workspace_root),
            "githubSlug": "owner/repo",
            "activeLaneLabel": "active-lane",
        },
        "auditLogPath": str(workspace_root / "memory" / "audit.jsonl"),
        "ledgerPath": str(workspace_root / "memory" / "ledger.json"),
        "healthPath": str(workspace_root / "memory" / "health.json"),
        "cronJobsPath": str(workspace_root / "cron-jobs.json"),
        "hermesCronJobsPath": str(workspace_root / "hermes-cron-jobs.json"),
        "sessionsStatePath": str(workspace_root / "state" / "sessions"),
        # … minimal stubs for the rest. Do NOT fully replicate workflow.yaml here;
        # only fields that build_workspace dereferences before the audit wiring.
    }


def test_make_publisher_returns_none_when_disabled(tmp_path):
    """When github-comments.enabled=false, no publisher is wired."""
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_publisher_wire_test",
        "workflows/code_review/workspace.py",
    )
    publisher = workspace_module._make_comment_publisher(
        workflow_root=tmp_path,
        repo_slug="owner/repo",
        workflow_yaml={"observability": {"github-comments": {"enabled": False}}},
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
    )
    assert publisher is None


def test_make_publisher_returns_callable_when_enabled(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_publisher_wire_test",
        "workflows/code_review/workspace.py",
    )
    publisher = workspace_module._make_comment_publisher(
        workflow_root=tmp_path,
        repo_slug="owner/repo",
        workflow_yaml={"observability": {"github-comments": {"enabled": True}}},
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
    )
    assert callable(publisher)


def test_publisher_skips_when_no_active_issue(tmp_path):
    """When no active lane exists, the publisher silently skips."""
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_publisher_wire_test",
        "workflows/code_review/workspace.py",
    )
    fake_run_calls = []

    def fake_run(*args, **kwargs):
        fake_run_calls.append(args)
        raise AssertionError("publisher should not have called gh when issue=None")

    publisher = workspace_module._make_comment_publisher(
        workflow_root=tmp_path,
        repo_slug="owner/repo",
        workflow_yaml={"observability": {"github-comments": {"enabled": True}}},
        get_active_issue_number=lambda: None,
        get_workflow_state=lambda: "no_active_lane",
        get_is_operator_attention=lambda: False,
        run_fn=fake_run,
    )
    publisher(action="merge-and-promote", summary="x", extra={"mergedPrNumber": 1})
    assert fake_run_calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_workspace_publisher_wire.py -v`
Expected: All fail — `_make_comment_publisher` does not exist.

- [ ] **Step 3: Add `_make_comment_publisher` to `workspace.py`**

Add (also as a module-level helper, near `_make_audit_fn`):

```python
def _make_comment_publisher(
    *,
    workflow_root,
    repo_slug,
    workflow_yaml,
    get_active_issue_number,
    get_workflow_state,
    get_is_operator_attention,
    run_fn=None,
):
    """Build the ``publisher`` callable consumed by ``_make_audit_fn``.

    Returns ``None`` when github-comments is disabled — the caller
    (``build_workspace``) wires that None into ``_make_audit_fn`` so
    nothing happens at the audit hook.
    """
    # Lazy import to avoid hard-coupling workspace.py to comments_publisher
    # before the rest of the workspace bootstrap is happy.
    try:
        from . import observability as _obs
        from . import comments_publisher as _pub
    except ImportError:
        _here = Path(__file__).resolve().parent
        import importlib.util as _ilu

        def _load(name):
            spec = _ilu.spec_from_file_location(
                f"daedalus_workflow_code_review_{name}", _here / f"{name}.py"
            )
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        _obs = _load("observability")
        _pub = _load("comments_publisher")

    workflow_root = Path(workflow_root)
    override_dir = workflow_root / "runtime" / "state" / "daedalus"
    state_dir = workflow_root / "runtime" / "state" / "lane-comments"

    effective = _obs.resolve_effective_config(
        workflow_yaml=workflow_yaml or {},
        override_dir=override_dir,
        workflow_name="code-review",
    )
    if not effective["github-comments"].get("enabled"):
        return None

    def publisher(*, action, summary, extra):
        # Re-resolve the config every call so a /daedalus set-observability
        # toggle takes effect immediately, without restarting the service.
        eff = _obs.resolve_effective_config(
            workflow_yaml=workflow_yaml or {},
            override_dir=override_dir,
            workflow_name="code-review",
        )
        if not eff["github-comments"].get("enabled"):
            return
        issue_number = get_active_issue_number()
        if issue_number is None:
            return
        audit_event = {
            "at": _now_iso(),
            "action": action,
            "summary": summary,
            **(extra or {}),
        }
        _pub.publish_event(
            repo_slug=repo_slug,
            issue_number=issue_number,
            workflow_state=get_workflow_state(),
            is_operator_attention=get_is_operator_attention(),
            audit_event=audit_event,
            effective_config=eff,
            state_dir=state_dir,
            **({"run_fn": run_fn} if run_fn is not None else {}),
        )

    return publisher
```

(b) Add `Path` import at the top of `workspace.py` if not already present. (`_now_iso` is already defined in this file.)

- [ ] **Step 4: Run tests to verify all pass**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_workspace_publisher_wire.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
file workflows/code_review/workspace.py tests/test_workflow_code_review_workspace_publisher_wire.py
git add workflows/code_review/workspace.py tests/test_workflow_code_review_workspace_publisher_wire.py
git commit -m "feat(workspace): _make_comment_publisher resolves config + builds hook

Returns None when github-comments disabled (zero-cost path). When
enabled, returns a publisher that re-resolves the override file on
every call so /daedalus set-observability takes effect immediately
without a service restart."
```

---

### Task 1.8: Final integration — `build_workspace` calls `_make_comment_publisher`

**Files:**
- Modify: `workflows/code_review/workspace.py` (in `build_workspace`)

- [ ] **Step 1: Wire publisher in `build_workspace`**

In `workflows/code_review/workspace.py`'s `build_workspace` function, locate the `audit = _make_audit_fn(...)` line you added in Task 1.6 and replace it with:

```python
    # Wire the comment publisher (returns None when observability is disabled —
    # the audit hook then becomes a pure log-write with no GitHub I/O).
    _publisher = _make_comment_publisher(
        workflow_root=workspace_root,
        repo_slug=config["repository"]["githubSlug"],
        workflow_yaml=config.get("rawWorkflowYaml") or {},
        get_active_issue_number=lambda: (
            (ns.load_ledger().get("activeLane") or {}).get("number")
            if hasattr(ns, "load_ledger") else None
        ),
        get_workflow_state=lambda: (
            (ns.load_ledger().get("workflowState") or "unknown")
            if hasattr(ns, "load_ledger") else "unknown"
        ),
        get_is_operator_attention=lambda: (
            (ns.load_ledger().get("workflowState") == "operator_attention_required")
            if hasattr(ns, "load_ledger") else False
        ),
    )
    audit = _make_audit_fn(audit_log_path=audit_log_path, publisher=_publisher)
```

(Note: `ns.load_ledger` is the workspace's existing ledger reader. The `hasattr` guard is defensive — early-bootstrap calls before `ns` is populated.)

- [ ] **Step 2: Verify config carries the raw yaml**

Search `workflows/code_review/workspace.py` for the function that reads workflow.yaml and returns the in-memory config dict (likely `_load_workflow_config` or near `auditLogPath` mapping at line 71). The returned dict needs a `rawWorkflowYaml` field so the publisher can read the `observability:` block.

If the loader doesn't already include the raw block, add it. Look for the function that does `yaml.safe_load(...)` on the config file — at the return point, add:

```python
    config["rawWorkflowYaml"] = raw  # raw is the yaml.safe_load() result
```

If the loader name is different, adjust accordingly. The objective: `config.get("rawWorkflowYaml")` returns the original (unflattened) yaml dict so `observability:` is reachable.

- [ ] **Step 3: Smoke test — full pytest suite passes**

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -5`
Expected: previous test count + new tests, the one pre-existing failure unchanged. No new failures introduced.

If any existing test fails because `build_workspace` now calls into `_make_comment_publisher` and the test config doesn't include enough of the workflow.yaml to satisfy the resolver: extend that test's config to add `rawWorkflowYaml: {}` or `rawWorkflowYaml: {"observability": {"github-comments": {"enabled": False}}}`.

- [ ] **Step 4: Commit**

```bash
git add workflows/code_review/workspace.py
git commit -m "feat(workspace): wire comment publisher into audit hook in build_workspace

audit() now invokes the publisher after every JSONL write. Default-off
config means existing deployments see no behavioral change. Workflow
yaml is preserved as config['rawWorkflowYaml'] so the resolver can
read the observability: block."
```

---

### Task 1.9: Operator-attention transition tracking

**Files:**
- Modify: `workflows/code_review/orchestrator.py` (around line 388 where `operator_attention_needed` is computed)
- Test: `tests/test_workflow_code_review_operator_attention_audit.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_workflow_code_review_operator_attention_audit.py`:

```python
"""Operator-attention transitions emit semantic audit events."""
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


def test_emit_operator_attention_transition_on_entering_state():
    orch = load_module(
        "daedalus_workflow_code_review_orchestrator_test",
        "workflows/code_review/orchestrator.py",
    )
    captured = []

    def fake_audit(action, summary, **extra):
        captured.append({"action": action, "summary": summary, "extra": extra})

    orch.emit_operator_attention_transition(
        previous_state="under_review",
        new_state="operator_attention_required",
        reasons=["operator-attention-required:failure-retry-count=5"],
        audit_fn=fake_audit,
    )
    assert len(captured) == 1
    assert captured[0]["action"] == "operator-attention-transition"
    assert "failure-retry-count=5" in captured[0]["extra"]["reason"]


def test_emit_operator_attention_recovered_on_leaving_state():
    orch = load_module(
        "daedalus_workflow_code_review_orchestrator_test",
        "workflows/code_review/orchestrator.py",
    )
    captured = []

    def fake_audit(action, summary, **extra):
        captured.append({"action": action, "summary": summary})

    orch.emit_operator_attention_transition(
        previous_state="operator_attention_required",
        new_state="under_review",
        reasons=[],
        audit_fn=fake_audit,
    )
    assert len(captured) == 1
    assert captured[0]["action"] == "operator-attention-recovered"


def test_no_emit_when_state_unchanged():
    orch = load_module(
        "daedalus_workflow_code_review_orchestrator_test",
        "workflows/code_review/orchestrator.py",
    )
    captured = []

    def fake_audit(action, summary, **extra):
        captured.append(action)

    orch.emit_operator_attention_transition(
        previous_state="under_review",
        new_state="under_review",
        reasons=[],
        audit_fn=fake_audit,
    )
    orch.emit_operator_attention_transition(
        previous_state="operator_attention_required",
        new_state="operator_attention_required",
        reasons=["x"],
        audit_fn=fake_audit,
    )
    assert captured == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_operator_attention_audit.py -v`
Expected: All fail — `emit_operator_attention_transition` does not exist.

- [ ] **Step 3: Add helper to `orchestrator.py`**

Add this module-level function near the top of `workflows/code_review/orchestrator.py` (after imports, before the first existing function):

```python
def emit_operator_attention_transition(
    *,
    previous_state,
    new_state,
    reasons,
    audit_fn,
):
    """Emit a semantic audit event when a lane crosses the operator-attention
    boundary. No-op when the state did not change.

    The comment publisher (Task 1.7) listens for ``operator-attention-transition``
    and ``operator-attention-recovered`` to render the sticky ⚠️ header (and to
    clear it on recovery).
    """
    OAS = "operator_attention_required"
    if previous_state == new_state:
        return
    if new_state == OAS:
        reason = "; ".join(reasons) if reasons else "operator-attention-required"
        audit_fn(
            "operator-attention-transition",
            "Lane entered operator-attention state",
            reason=reason,
            previousState=previous_state,
        )
    elif previous_state == OAS:
        audit_fn(
            "operator-attention-recovered",
            "Lane recovered from operator-attention state",
            newState=new_state,
        )
```

- [ ] **Step 4: Wire into the orchestrator's tick logic**

Find where `operator_attention_needed` is computed (around `orchestrator.py:388`) and where `workflowState` is updated. After the new state is computed and the previous state is read, insert a call:

```python
emit_operator_attention_transition(
    previous_state=previous_workflow_state,
    new_state=new_workflow_state,
    reasons=stale_lane_reasons,    # already collected nearby — verify the variable name
    audit_fn=ws.audit,
)
```

If `previous_workflow_state` and `new_workflow_state` aren't yet captured at that point, hoist them:

```python
previous_workflow_state = (status.get("ledger") or {}).get("workflowState") or "unknown"
# ... existing logic that computes new state ...
new_workflow_state = (after_status.get("ledger") or {}).get("workflowState") or "unknown"
emit_operator_attention_transition(...)
```

(Exact variable names depend on the surrounding context — read the function and bind correctly.)

- [ ] **Step 5: Run unit tests + integration**

```bash
/usr/bin/python3 -m pytest tests/test_workflow_code_review_operator_attention_audit.py -v
/usr/bin/python3 -m pytest -q 2>&1 | tail -5
```
Expected: All new tests pass; baseline suite still 244 passing + 1 pre-existing failure unchanged.

- [ ] **Step 6: Commit**

```bash
git add workflows/code_review/orchestrator.py tests/test_workflow_code_review_operator_attention_audit.py
git commit -m "feat(orchestrator): emit operator-attention-transition audit events

Fires operator-attention-transition (entering) and operator-attention-recovered
(leaving) once per state change. No-op on unchanged states. The comment
publisher renders these as ⚠️/✅ rows with sticky headers."
```

---

### Task 1.10: End-to-end integration test (mocked GitHub)

**Files:**
- Test: `tests/test_workflow_code_review_observability_e2e.py` (new)

- [ ] **Step 1: Write the integration test**

Create `tests/test_workflow_code_review_observability_e2e.py`:

```python
"""End-to-end: enabled → audit fires → publisher runs → state updated.

Mocks subprocess.run so no live GitHub calls. Verifies the wiring works
across all the modules added in Task 1.1–1.9.
"""
import importlib.util
import json
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


def test_end_to_end_audit_creates_then_edits_bot_comment(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_e2e",
        "workflows/code_review/workspace.py",
    )
    state_dir = tmp_path / "lane-comments"
    audit_log_path = tmp_path / "audit.jsonl"

    fake_run_responses = [
        # First call: gh issue comment → returns URL with comment id 99
        mock.Mock(returncode=0, stdout="https://github.com/owner/repo/issues/329#issuecomment-99\n", stderr=""),
        # Second call: gh api PATCH (no stdout, success)
        mock.Mock(returncode=0, stdout="", stderr=""),
    ]
    fake_run_calls = []

    def fake_run(argv, **kwargs):
        fake_run_calls.append(argv)
        return fake_run_responses.pop(0)

    publisher = workspace_module._make_comment_publisher(
        workflow_root=tmp_path,
        repo_slug="owner/repo",
        workflow_yaml={
            "observability": {
                "github-comments": {
                    "enabled": True,
                    "include-events": [],   # empty = include all
                    "suppress-transient-failures": True,
                }
            }
        },
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
        run_fn=fake_run,
    )
    audit = workspace_module._make_audit_fn(audit_log_path=audit_log_path, publisher=publisher)

    # The state_dir the publisher writes into is workflow_root/runtime/state/lane-comments.
    # Our tmp_path is workflow_root.
    expected_state_dir = tmp_path / "runtime" / "state" / "lane-comments"

    audit("dispatch-implementation-turn", "ok", model="gpt-5.3-codex-spark", sessionName="lane-329")
    assert len(fake_run_calls) == 1
    assert fake_run_calls[0][0] == "gh"
    assert "issue" in fake_run_calls[0]
    state_path = expected_state_dir / "329.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["comment_id"] == "99"

    audit("merge-and-promote", "merged", mergedPrNumber=382)
    assert len(fake_run_calls) == 2
    assert fake_run_calls[1][0] == "gh"
    assert fake_run_calls[1][1] == "api"
```

- [ ] **Step 2: Run integration test**

Run: `/usr/bin/python3 -m pytest tests/test_workflow_code_review_observability_e2e.py -v`
Expected: 1 passed.

- [ ] **Step 3: Run full suite**

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -5`
Expected: 244 + N passing, 1 pre-existing failure unchanged.

- [ ] **Step 4: Commit**

```bash
git add tests/test_workflow_code_review_observability_e2e.py
git commit -m "test(observability): end-to-end audit -> publisher -> gh -> state

Mocks subprocess.run; verifies first event creates a bot-comment via
gh issue comment, parses comment_id from the returned URL, and
subsequent events PATCH the same comment via gh api."
```

---

## Phase 2: `/daedalus watch` TUI

### Task 2.1: Data source aggregator — `watch_sources.py`

**Files:**
- Create: `watch_sources.py` (Daedalus core, sibling of `tools.py`)
- Test: `tests/test_daedalus_watch_sources.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_daedalus_watch_sources.py`:

```python
"""Read-only aggregation of state from existing event sources."""
import importlib.util
import json
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return load_module("daedalus_watch_sources_test", "watch_sources.py")


def _make_workflow_root(tmp_path):
    """Build a workflow_root tree that runtime_paths recognizes (has runtime/, config/)."""
    root = tmp_path / "yoyopod_core"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()
    return root


def test_read_recent_daedalus_events_returns_last_n_lines_newest_first(tmp_path):
    sources = _module()
    root = _make_workflow_root(tmp_path)
    log_path = root / "runtime" / "memory" / "daedalus-events.jsonl"
    log_path.write_text("\n".join([
        json.dumps({"at": "2026-04-26T22:00:01Z", "event": "a"}),
        json.dumps({"at": "2026-04-26T22:00:02Z", "event": "b"}),
        json.dumps({"at": "2026-04-26T22:00:03Z", "event": "c"}),
    ]) + "\n")
    events = sources.recent_daedalus_events(root, limit=2)
    assert [e["event"] for e in events] == ["c", "b"]


def test_read_recent_workflow_audit_handles_missing_file(tmp_path):
    sources = _module()
    root = _make_workflow_root(tmp_path)
    out = sources.recent_workflow_audit(root, limit=10)
    assert out == []


def test_read_active_lanes_from_db(tmp_path):
    sources = _module()
    root = _make_workflow_root(tmp_path)
    db_path = root / "runtime" / "state" / "daedalus" / "daedalus.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE lanes (project_key TEXT, lane_id TEXT, state TEXT, github_issue_number INTEGER)")
    conn.execute("INSERT INTO lanes VALUES ('yoyopod', '329', 'under_review', 329)")
    conn.execute("INSERT INTO lanes VALUES ('yoyopod', '330', 'merged', 330)")
    conn.commit()
    conn.close()
    lanes = sources.active_lanes(root)
    assert len(lanes) == 1
    assert lanes[0]["lane_id"] == "329"
    assert lanes[0]["state"] == "under_review"


def test_read_alert_state_returns_empty_dict_when_absent(tmp_path):
    sources = _module()
    root = _make_workflow_root(tmp_path)
    state = sources.alert_state(root)
    assert state == {}


def test_read_alert_state_when_present(tmp_path):
    sources = _module()
    root = _make_workflow_root(tmp_path)
    alert_path = root / "runtime" / "memory" / "daedalus-alert-state.json"
    alert_path.write_text(json.dumps({"fingerprint": "abc", "active": True}))
    state = sources.alert_state(root)
    assert state["active"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_daedalus_watch_sources.py -v`
Expected: All fail — `watch_sources.py` does not exist.

- [ ] **Step 3: Implement aggregator**

Create `watch_sources.py` (at the repo root, sibling of `tools.py`):

```python
"""Read-only aggregation of state from Daedalus event sources for /daedalus watch.

This module never writes — it only reads from:

  - ``<workflow_root>/runtime/memory/daedalus-events.jsonl``
  - ``<workflow_root>/runtime/memory/workflow-audit.jsonl``
  - ``<workflow_root>/runtime/state/daedalus/daedalus.db`` (lanes table)
  - ``<workflow_root>/runtime/memory/daedalus-alert-state.json``

Each function tolerates the source being absent / corrupt and returns an
empty result rather than raising. The TUI must keep rendering even if
one source is unavailable.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

# Sibling-import boilerplate.
try:
    from workflows.code_review.paths import runtime_paths
except ImportError:
    import importlib.util as _ilu
    _here = Path(__file__).resolve().parent
    _spec = _ilu.spec_from_file_location(
        "daedalus_workflows_code_review_paths_for_watch",
        _here / "workflows" / "code_review" / "paths.py",
    )
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    runtime_paths = _mod.runtime_paths


def _read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    parsed: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    parsed.reverse()  # newest first
    return parsed


def recent_daedalus_events(workflow_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    paths = runtime_paths(Path(workflow_root))
    return _read_jsonl_tail(paths["event_log_path"], limit)


def recent_workflow_audit(workflow_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    base = Path(workflow_root)
    # workflow-audit.jsonl lives under <root>/runtime/memory/ in the project layout
    # and under <root>/memory/ in the legacy layout — match runtime_paths logic.
    runtime_event_log = runtime_paths(base)["event_log_path"]
    audit_path = runtime_event_log.parent / "workflow-audit.jsonl"
    return _read_jsonl_tail(audit_path, limit)


def active_lanes(workflow_root: Path) -> list[dict[str, Any]]:
    paths = runtime_paths(Path(workflow_root))
    db_path = paths["db_path"]
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return []
    try:
        cur = conn.execute(
            "SELECT lane_id, state, github_issue_number FROM lanes WHERE state NOT IN ('merged', 'closed')"
        )
        out = [
            {"lane_id": row[0], "state": row[1], "github_issue_number": row[2]}
            for row in cur.fetchall()
        ]
    except sqlite3.OperationalError:
        out = []
    finally:
        conn.close()
    return out


def alert_state(workflow_root: Path) -> dict[str, Any]:
    paths = runtime_paths(Path(workflow_root))
    alert_path = paths["alert_state_path"]
    if not alert_path.exists():
        return {}
    try:
        return json.loads(alert_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
```

- [ ] **Step 4: Add `watch_sources.py` to `PAYLOAD_ITEMS` in `scripts/install.py`**

In `scripts/install.py`, find the `PAYLOAD_ITEMS` list (the installer ships only files in this list). Add `"watch_sources.py"` and (anticipating Task 2.4) `"watch.py"` to the list. Verify the file is picked up:

Run: `grep -n "PAYLOAD_ITEMS" scripts/install.py`
Expected: list found, includes the new entries.

- [ ] **Step 5: Run tests + verify all pass**

Run: `/usr/bin/python3 -m pytest tests/test_daedalus_watch_sources.py -v`
Expected: 5 passed.

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -5`
Expected: full suite green except the one pre-existing failure.

- [ ] **Step 6: Commit**

```bash
git add watch_sources.py tests/test_daedalus_watch_sources.py scripts/install.py
git commit -m "feat(watch): read-only data aggregator for /daedalus watch

Reads daedalus-events.jsonl, workflow-audit.jsonl, daedalus.db (lanes
table), and daedalus-alert-state.json. Every function tolerates missing
or corrupt sources by returning empty — TUI degrades gracefully rather
than crashing on any one source."
```

---

### Task 2.2: Frame renderer — `watch.py` (rich-based, no live loop yet)

**Files:**
- Create: `watch.py`
- Test: `tests/test_daedalus_watch_render.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_daedalus_watch_render.py`:

```python
"""Frame rendering: aggregator output → rich-renderable frame string.

We render to a string (capture mode) and snapshot-test the output structure.
"""
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


def _module():
    return load_module("daedalus_watch_test", "watch.py")


def test_render_frame_with_no_active_lanes():
    watch = _module()
    out = watch.render_frame_to_string({
        "active_lanes": [],
        "alert_state": {},
        "recent_events": [],
    })
    assert "Daedalus active lanes" in out
    assert "(no active lanes)" in out


def test_render_frame_with_one_lane():
    watch = _module()
    out = watch.render_frame_to_string({
        "active_lanes": [
            {"lane_id": "329", "state": "under_review", "github_issue_number": 329}
        ],
        "alert_state": {},
        "recent_events": [
            {"at": "2026-04-26T22:30:34Z", "source": "workflow", "event": "dispatch_implementation_turn", "detail": "committed"},
        ],
    })
    assert "329" in out
    assert "under_review" in out
    assert "dispatch_implementation_turn" in out


def test_render_frame_includes_alert_banner_when_alert_active():
    watch = _module()
    out = watch.render_frame_to_string({
        "active_lanes": [],
        "alert_state": {"active": True, "fingerprint": "abc", "message": "stale heartbeat"},
        "recent_events": [],
    })
    assert "Active alerts" in out or "alert" in out.lower()


def test_render_frame_handles_stale_source():
    """Source-level [stale] markers when an aggregator returned an error sentinel."""
    watch = _module()
    out = watch.render_frame_to_string({
        "active_lanes": [{"_stale": True}],
        "alert_state": {"_stale": True},
        "recent_events": [],
    })
    # No crash; "[stale]" appears somewhere
    assert "stale" in out.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_daedalus_watch_render.py -v`
Expected: All fail — `watch.py` does not exist.

- [ ] **Step 3: Implement renderer**

Create `watch.py` at repo root:

```python
"""TUI frame rendering for /daedalus watch.

Phase 2 (this file) implements the frame renderer. The live loop is wired
in later — this module exposes ``render_frame_to_string(snapshot)`` so the
CLI handler and tests can both produce frame text without spinning up a
real TTY.
"""
from __future__ import annotations

from typing import Any, Mapping

from rich.console import Console
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
            t.add_row("[stale]", "[stale]", "[stale]")
            continue
        t.add_row(
            str(lane.get("lane_id") or ""),
            str(lane.get("state") or ""),
            str(lane.get("github_issue_number") or ""),
        )
    return t


def _alerts_panel(alert_state: Mapping[str, Any]) -> Panel | None:
    if alert_state.get("_stale"):
        return Panel("[stale] alert source unreadable", title="⚠️  Active alerts")
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
```

- [ ] **Step 4: Run tests + verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_daedalus_watch_render.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add watch.py tests/test_daedalus_watch_render.py
git commit -m "feat(watch): rich-based frame renderer

render_frame_to_string returns a single TUI frame as plain text.
Lanes panel + optional alerts panel + recent events panel. Stale
source sentinels render as '[stale]' instead of crashing the frame."
```

---

### Task 2.3: Snapshot builder — combines sources into one frame snapshot

**Files:**
- Modify: `watch.py` (add `build_snapshot`)
- Test: extend `tests/test_daedalus_watch_render.py`

- [ ] **Step 1: Write the failing test (append to existing file)**

Append to `tests/test_daedalus_watch_render.py`:

```python
import json
import sqlite3


def _make_workflow_root(tmp_path):
    root = tmp_path / "yoyopod_core"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()
    return root


def test_build_snapshot_combines_all_sources(tmp_path):
    watch = _module()
    root = _make_workflow_root(tmp_path)

    # Seed daedalus-events
    (root / "runtime" / "memory" / "daedalus-events.jsonl").write_text(
        json.dumps({"at": "2026-04-26T22:00:01Z", "event": "lane_action_dispatched"}) + "\n"
    )
    # Seed workflow-audit
    (root / "runtime" / "memory" / "workflow-audit.jsonl").write_text(
        json.dumps({"at": "2026-04-26T22:00:02Z", "action": "merge-and-promote"}) + "\n"
    )
    # Seed lanes table
    db = root / "runtime" / "state" / "daedalus" / "daedalus.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE lanes (project_key TEXT, lane_id TEXT, state TEXT, github_issue_number INTEGER)")
    conn.execute("INSERT INTO lanes VALUES ('yoyopod', '329', 'under_review', 329)")
    conn.commit()
    conn.close()
    # Seed alert state
    (root / "runtime" / "memory" / "daedalus-alert-state.json").write_text(
        json.dumps({"active": True, "message": "stale dispatch"})
    )

    snap = watch.build_snapshot(root)
    assert len(snap["active_lanes"]) == 1
    assert snap["active_lanes"][0]["lane_id"] == "329"
    # interleaved + sorted recent events
    assert any(e.get("source") == "daedalus" for e in snap["recent_events"])
    assert any(e.get("source") == "workflow" for e in snap["recent_events"])
    assert snap["alert_state"]["active"] is True
```

- [ ] **Step 2: Run failing test**

Run: `/usr/bin/python3 -m pytest tests/test_daedalus_watch_render.py::test_build_snapshot_combines_all_sources -v`
Expected: fails — `build_snapshot` not defined.

- [ ] **Step 3: Add `build_snapshot` to `watch.py`**

Append to `watch.py`:

```python
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
```

- [ ] **Step 4: Run tests + verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_daedalus_watch_render.py -v`
Expected: 5 passed (4 existing + 1 new).

- [ ] **Step 5: Commit**

```bash
git add watch.py tests/test_daedalus_watch_render.py
git commit -m "feat(watch): build_snapshot interleaves event sources

Tags daedalus-events and workflow-audit rows with their source,
sorts newest-first by 'at' timestamp, caps at 50 rows. Each call
re-reads the sources, so polling produces a fresh snapshot."
```

---

### Task 2.4: `watch` subcommand handler + no-TTY fallback

**Files:**
- Modify: `watch.py` (add `cmd_watch`)
- Modify: `tools.py` (register `watch` subcommand)
- Test: `tests/test_daedalus_watch_cli.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_daedalus_watch_cli.py`:

```python
"""watch CLI handler in non-TTY mode renders one frame and exits."""
import importlib.util
import io
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


def test_cmd_watch_one_shot_when_not_tty(tmp_path, capsys):
    watch = load_module("daedalus_watch_cli_test", "watch.py")
    root = tmp_path / "yoyopod_core"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()

    args = mock.Mock()
    args.workflow_root = root
    args.once = False  # don't force one-shot via flag

    # Force is_tty to False
    with mock.patch.object(watch, "_stdout_is_tty", return_value=False):
        result = watch.cmd_watch(args, parser=None)

    assert "Daedalus active lanes" in result
    # No live loop entered (would block test)


def test_cmd_watch_with_once_flag_renders_one_frame(tmp_path):
    watch = load_module("daedalus_watch_cli_test", "watch.py")
    root = tmp_path / "yoyopod_core"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()

    args = mock.Mock()
    args.workflow_root = root
    args.once = True

    # Even with TTY, --once should bypass live loop
    with mock.patch.object(watch, "_stdout_is_tty", return_value=True):
        result = watch.cmd_watch(args, parser=None)

    assert "Daedalus active lanes" in result
```

- [ ] **Step 2: Run failing tests**

Run: `/usr/bin/python3 -m pytest tests/test_daedalus_watch_cli.py -v`
Expected: All fail — `cmd_watch` not defined.

- [ ] **Step 3: Add CLI handler to `watch.py`**

Append to `watch.py`:

```python
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
```

Add `from pathlib import Path` at the top of `watch.py` if not already present.

- [ ] **Step 4: Register the subcommand in `tools.py`**

In `tools.py`'s `configure_subcommands`, add (anywhere in the existing chain, e.g. after `migrate_systemd_cmd`):

```python
    watch_cmd = sub.add_parser(
        "watch",
        help="Live operator TUI: lanes, alerts, recent events.",
    )
    watch_cmd.add_argument("--workflow-root", type=Path, default=DEFAULT_WORKFLOW_ROOT)
    watch_cmd.add_argument("--once", action="store_true", help="Render one frame and exit (default when stdout is not a TTY).")
    watch_cmd.add_argument("--interval", type=float, default=2.0, help="Poll interval in live mode.")
    watch_cmd.set_defaults(handler=_lazy_cmd_watch, func=run_cli_command)
```

And add the lazy loader near `cmd_migrate_filesystem`:

```python
def _lazy_cmd_watch(args, parser):
    """Lazy import so importing tools.py doesn't pull rich into every CLI invocation."""
    try:
        from watch import cmd_watch
    except ImportError:
        path = PLUGIN_DIR / "watch.py"
        spec = importlib.util.spec_from_file_location("daedalus_watch_for_cli", path)
        if spec is None or spec.loader is None:
            raise DaedalusCommandError(f"unable to load watch module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cmd_watch = module.cmd_watch
    return cmd_watch(args, parser)
```

- [ ] **Step 5: Run tests + full suite**

Run: `/usr/bin/python3 -m pytest tests/test_daedalus_watch_cli.py -v`
Expected: 2 passed.

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -5`
Expected: full suite green except 1 pre-existing failure.

- [ ] **Step 6: Commit**

```bash
git add watch.py tools.py tests/test_daedalus_watch_cli.py
git commit -m "feat(watch): /daedalus watch subcommand with rich.live + no-TTY fallback

Wired through tools.py with lazy import (rich pulled in only when watch
runs). One-shot render when stdout is not a TTY or --once is passed;
otherwise enters rich.live loop polling at 2s."
```

---

## Phase 3: Operator override commands

### Task 3.1: Override file read/write — `observability_overrides.py`

**Files:**
- Create: `observability_overrides.py` (Daedalus core)
- Test: `tests/test_observability_overrides.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_observability_overrides.py`:

```python
"""Read/write the observability-overrides.json file."""
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return load_module("daedalus_observability_overrides_test", "observability_overrides.py")


def test_set_creates_file_when_absent(tmp_path):
    over = _module()
    state_dir = tmp_path / "state"
    over.set_override(state_dir, workflow_name="code-review", github_comments_enabled=True, set_by="operator-cli")
    file = state_dir / "observability-overrides.json"
    assert file.exists()
    data = json.loads(file.read_text())
    assert data["code-review"]["github-comments"]["enabled"] is True
    assert data["code-review"]["github-comments"]["set-by"] == "operator-cli"
    assert "set-at" in data["code-review"]["github-comments"]


def test_set_updates_existing_file_preserving_other_workflows(tmp_path):
    over = _module()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "observability-overrides.json").write_text(json.dumps({
        "other-workflow": {"github-comments": {"enabled": True}}
    }))
    over.set_override(state_dir, workflow_name="code-review", github_comments_enabled=False)
    data = json.loads((state_dir / "observability-overrides.json").read_text())
    assert data["other-workflow"]["github-comments"]["enabled"] is True  # preserved
    assert data["code-review"]["github-comments"]["enabled"] is False


def test_unset_removes_only_the_targeted_workflow_block(tmp_path):
    over = _module()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "observability-overrides.json").write_text(json.dumps({
        "code-review": {"github-comments": {"enabled": True}},
        "other-workflow": {"github-comments": {"enabled": True}},
    }))
    over.unset_override(state_dir, workflow_name="code-review")
    data = json.loads((state_dir / "observability-overrides.json").read_text())
    assert "code-review" not in data
    assert data["other-workflow"]["github-comments"]["enabled"] is True


def test_get_returns_empty_dict_when_file_absent(tmp_path):
    over = _module()
    state_dir = tmp_path / "state"
    out = over.get_override(state_dir, workflow_name="code-review")
    assert out == {}


def test_get_returns_workflow_block_when_present(tmp_path):
    over = _module()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "observability-overrides.json").write_text(json.dumps({
        "code-review": {"github-comments": {"enabled": True, "set-at": "2026-04-26T00:00:00Z"}}
    }))
    out = over.get_override(state_dir, workflow_name="code-review")
    assert out["github-comments"]["enabled"] is True
```

- [ ] **Step 2: Run failing tests**

Run: `/usr/bin/python3 -m pytest tests/test_observability_overrides.py -v`
Expected: All fail — module not defined.

- [ ] **Step 3: Implement `observability_overrides.py`**

Create `observability_overrides.py` at repo root:

```python
"""Read/write of observability override file.

Stored at ``<workflow_root>/runtime/state/daedalus/observability-overrides.json``.
Schema::

    {
        "<workflow_name>": {
            "github-comments": {
                "enabled": <bool>,
                "set-at": "<iso8601>",
                "set-by": "<operator label>"
            }
        }
    }

Override is per-workflow and overrides the workflow.yaml value at
resolution time. Used by ``/daedalus set-observability``.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OVERRIDE_FILENAME = "observability-overrides.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _override_path(state_dir: Path) -> Path:
    return Path(state_dir) / OVERRIDE_FILENAME


def _load(state_dir: Path) -> dict[str, Any]:
    p = _override_path(state_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_atomic(state_dir: Path, data: dict[str, Any]) -> None:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    p = _override_path(state_dir)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def set_override(
    state_dir: Path,
    *,
    workflow_name: str,
    github_comments_enabled: bool,
    set_by: str = "operator-cli",
) -> None:
    data = _load(state_dir)
    workflow_block = data.get(workflow_name) or {}
    workflow_block["github-comments"] = {
        "enabled": bool(github_comments_enabled),
        "set-at": _now_iso(),
        "set-by": set_by,
    }
    data[workflow_name] = workflow_block
    _save_atomic(state_dir, data)


def unset_override(state_dir: Path, *, workflow_name: str) -> None:
    data = _load(state_dir)
    if workflow_name in data:
        del data[workflow_name]
        _save_atomic(state_dir, data)


def get_override(state_dir: Path, *, workflow_name: str) -> dict[str, Any]:
    data = _load(state_dir)
    return data.get(workflow_name) or {}
```

- [ ] **Step 4: Run tests + verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_observability_overrides.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add observability_overrides.py tests/test_observability_overrides.py
git commit -m "feat(observability): runtime override file read/write helpers

Atomic writes, per-workflow blocks. The override file overrides the
workflow.yaml observability block at resolution time so operators can
mute/unmute comment publishing without editing config + redeploy."
```

---

### Task 3.2: `/daedalus set-observability` + `/daedalus get-observability` subcommands

**Files:**
- Modify: `tools.py` (add handlers + subcommand registry)
- Test: `tests/test_daedalus_observability_cli.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_daedalus_observability_cli.py`:

```python
"""set-observability + get-observability CLI handlers."""
import importlib.util
import json
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


def _make_workflow_root(tmp_path):
    root = tmp_path / "yoyopod_core"
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()
    return root


def test_set_observability_writes_override(tmp_path):
    tools = load_module("daedalus_tools_set_obs_test", "tools.py")
    root = _make_workflow_root(tmp_path)

    args = mock.Mock()
    args.workflow_root = root
    args.workflow = "code-review"
    args.github_comments = "off"

    out = tools.cmd_set_observability(args, parser=None)
    assert "code-review" in out
    assert "off" in out.lower() or "False" in out

    override_file = root / "runtime" / "state" / "daedalus" / "observability-overrides.json"
    assert override_file.exists()
    data = json.loads(override_file.read_text())
    assert data["code-review"]["github-comments"]["enabled"] is False


def test_set_observability_unset_removes_block(tmp_path):
    tools = load_module("daedalus_tools_set_obs_test", "tools.py")
    root = _make_workflow_root(tmp_path)

    # First set
    args1 = mock.Mock()
    args1.workflow_root = root
    args1.workflow = "code-review"
    args1.github_comments = "on"
    tools.cmd_set_observability(args1, parser=None)

    # Then unset
    args2 = mock.Mock()
    args2.workflow_root = root
    args2.workflow = "code-review"
    args2.github_comments = "unset"
    out = tools.cmd_set_observability(args2, parser=None)
    assert "unset" in out.lower() or "removed" in out.lower()

    override_file = root / "runtime" / "state" / "daedalus" / "observability-overrides.json"
    data = json.loads(override_file.read_text())
    assert "code-review" not in data


def test_get_observability_shows_default_source_when_no_yaml_no_override(tmp_path):
    tools = load_module("daedalus_tools_get_obs_test", "tools.py")
    root = _make_workflow_root(tmp_path)

    # Create a workflow.yaml without an observability block
    (root / "config" / "workflow.yaml").write_text("""\
workflow: code-review
schema-version: 1
instance: {name: yoyopod, engine-owner: hermes}
repository: {local-path: /tmp, github-slug: o/r, active-lane-label: active-lane}
runtimes:
  acpx-codex:
    kind: acpx-codex
    session-idle-freshness-seconds: 1
    session-idle-grace-seconds: 1
    session-nudge-cooldown-seconds: 1
agents:
  coder: {default: {name: x, model: y, runtime: acpx-codex}}
  internal-reviewer: {name: x, model: y, runtime: acpx-codex}
  external-reviewer: {enabled: true, name: x}
gates: {internal-review: {}, external-review: {}, merge: {}}
triggers: {lane-selector: {type: github-label, label: active-lane}}
storage: {ledger: l, health: h, audit-log: a}
""")
    args = mock.Mock()
    args.workflow_root = root
    args.workflow = "code-review"

    out = tools.cmd_get_observability(args, parser=None)
    assert "default" in out.lower() or "false" in out.lower()


def test_get_observability_shows_override_source_when_overridden(tmp_path):
    tools = load_module("daedalus_tools_get_obs_test", "tools.py")
    root = _make_workflow_root(tmp_path)

    (root / "config" / "workflow.yaml").write_text("""\
workflow: code-review
schema-version: 1
instance: {name: yoyopod, engine-owner: hermes}
repository: {local-path: /tmp, github-slug: o/r, active-lane-label: active-lane}
runtimes:
  acpx-codex:
    kind: acpx-codex
    session-idle-freshness-seconds: 1
    session-idle-grace-seconds: 1
    session-nudge-cooldown-seconds: 1
agents:
  coder: {default: {name: x, model: y, runtime: acpx-codex}}
  internal-reviewer: {name: x, model: y, runtime: acpx-codex}
  external-reviewer: {enabled: true, name: x}
gates: {internal-review: {}, external-review: {}, merge: {}}
triggers: {lane-selector: {type: github-label, label: active-lane}}
storage: {ledger: l, health: h, audit-log: a}
""")
    # Pre-write override
    override_dir = root / "runtime" / "state" / "daedalus"
    (override_dir / "observability-overrides.json").write_text(json.dumps({
        "code-review": {"github-comments": {"enabled": True, "set-at": "2026-04-26T00:00:00Z"}}
    }))

    args = mock.Mock()
    args.workflow_root = root
    args.workflow = "code-review"
    out = tools.cmd_get_observability(args, parser=None)
    assert "override" in out.lower()
    assert "true" in out.lower() or "on" in out.lower()
```

- [ ] **Step 2: Run failing tests**

Run: `/usr/bin/python3 -m pytest tests/test_daedalus_observability_cli.py -v`
Expected: All fail — `cmd_set_observability` / `cmd_get_observability` not defined.

- [ ] **Step 3: Add CLI handlers + subcommands to `tools.py`**

In `tools.py`, add the handlers near the bottom of the file (before `configure_subcommands`):

```python
def cmd_set_observability(args, parser) -> str:
    """``/daedalus set-observability --workflow X --github-comments on|off|unset``."""
    try:
        from observability_overrides import set_override, unset_override
    except ImportError:
        path = PLUGIN_DIR / "observability_overrides.py"
        spec = importlib.util.spec_from_file_location("daedalus_observability_overrides_for_cli", path)
        if spec is None or spec.loader is None:
            raise DaedalusCommandError(f"unable to load observability_overrides from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        set_override = module.set_override
        unset_override = module.unset_override

    workflow_root = Path(args.workflow_root).expanduser().resolve()
    state_dir = workflow_root / "runtime" / "state" / "daedalus"
    workflow_name = args.workflow
    setting = (args.github_comments or "").strip().lower()

    if setting == "on":
        set_override(state_dir, workflow_name=workflow_name, github_comments_enabled=True)
        return f"observability override set: {workflow_name}.github-comments = on"
    if setting == "off":
        set_override(state_dir, workflow_name=workflow_name, github_comments_enabled=False)
        return f"observability override set: {workflow_name}.github-comments = off"
    if setting == "unset":
        unset_override(state_dir, workflow_name=workflow_name)
        return f"observability override removed for {workflow_name}"
    raise DaedalusCommandError(
        f"--github-comments must be one of: on, off, unset (got {args.github_comments!r})"
    )


def cmd_get_observability(args, parser) -> str:
    """``/daedalus get-observability --workflow X``: show effective config + source."""
    try:
        from workflows.code_review.observability import resolve_effective_config
    except ImportError:
        path = PLUGIN_DIR / "workflows" / "code_review" / "observability.py"
        spec = importlib.util.spec_from_file_location("daedalus_observability_for_cli", path)
        if spec is None or spec.loader is None:
            raise DaedalusCommandError(f"unable to load observability resolver from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        resolve_effective_config = module.resolve_effective_config

    workflow_root = Path(args.workflow_root).expanduser().resolve()
    workflow_name = args.workflow
    config_yaml_path = workflow_root / "config" / "workflow.yaml"
    workflow_yaml = {}
    if config_yaml_path.exists():
        try:
            import yaml as _yaml
            workflow_yaml = _yaml.safe_load(config_yaml_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return f"error reading workflow.yaml: {exc}"

    eff = resolve_effective_config(
        workflow_yaml=workflow_yaml,
        override_dir=workflow_root / "runtime" / "state" / "daedalus",
        workflow_name=workflow_name,
    )
    gh = eff["github-comments"]
    source = eff["source"]["github-comments"]
    lines = [
        f"workflow: {workflow_name}",
        f"github-comments.enabled: {gh.get('enabled')} (source: {source})",
        f"github-comments.mode: {gh.get('mode')}",
        f"github-comments.include-events: {gh.get('include-events') or '(all)'}",
        f"github-comments.suppress-transient-failures: {gh.get('suppress-transient-failures')}",
    ]
    return "\n".join(lines)
```

Then in `configure_subcommands`, add:

```python
    set_obs_cmd = sub.add_parser(
        "set-observability",
        help="Override observability config for a workflow (writes runtime override file).",
    )
    set_obs_cmd.add_argument("--workflow-root", type=Path, default=DEFAULT_WORKFLOW_ROOT)
    set_obs_cmd.add_argument("--workflow", required=True, help="Workflow name (e.g. code-review)")
    set_obs_cmd.add_argument("--github-comments", choices=["on", "off", "unset"], required=True)
    set_obs_cmd.set_defaults(handler=cmd_set_observability, func=run_cli_command)

    get_obs_cmd = sub.add_parser(
        "get-observability",
        help="Show effective observability config + which layer (default/yaml/override) won.",
    )
    get_obs_cmd.add_argument("--workflow-root", type=Path, default=DEFAULT_WORKFLOW_ROOT)
    get_obs_cmd.add_argument("--workflow", required=True)
    get_obs_cmd.set_defaults(handler=cmd_get_observability, func=run_cli_command)
```

- [ ] **Step 4: Add to `PAYLOAD_ITEMS` in `scripts/install.py`**

Add `"observability_overrides.py"` to the install payload.

- [ ] **Step 5: Run tests + full suite**

Run: `/usr/bin/python3 -m pytest tests/test_daedalus_observability_cli.py -v`
Expected: 4 passed.

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -5`
Expected: full suite green except 1 pre-existing failure.

- [ ] **Step 6: Commit**

```bash
git add tools.py observability_overrides.py scripts/install.py tests/test_daedalus_observability_cli.py
git commit -m "feat(daedalus): /daedalus set-observability + get-observability CLI

set-observability writes the runtime override file (per-workflow,
github-comments on|off|unset). get-observability shows the effective
config and which layer won (default/yaml/override). Wired through
tools.configure_subcommands; observability_overrides.py added to
the install payload."
```

---

## Phase 4: Cleanup + docs

### Task 4.1: Slash-command catalog update

**Files:**
- Modify: `docs/slash-commands-catalog.md`

- [ ] **Step 1: Add new commands to the catalog**

In `docs/slash-commands-catalog.md`, add a new section after `Cutover / migration`:

```markdown
### Observability

| Command | What it does |
|---|---|
| `/daedalus watch` | Live operator TUI (lanes + alerts + recent events) |
| `/daedalus watch --once` | Render one frame and exit (works in pipes) |
| `/daedalus set-observability --workflow X --github-comments on|off|unset` | Set/clear runtime override for a workflow's GitHub-comment publishing |
| `/daedalus get-observability --workflow X` | Show effective observability config + which layer (default/yaml/override) won |
```

Also update the "Most useful day-to-day, in order" section by inserting `/daedalus watch` near the top.

- [ ] **Step 2: Commit**

```bash
git add docs/slash-commands-catalog.md
git commit -m "docs(catalog): document /daedalus watch + set/get-observability"
```

---

### Task 4.2: Final integration check + grep audit

- [ ] **Step 1: Full test suite**

Run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -10`
Expected: all green except the one pre-existing failure (`test_runtime_tools_alerts.py`). Total tests = 244 + N new (count them and confirm).

- [ ] **Step 2: Grep for missed wiring**

Run:
```bash
grep -rn "TODO\|FIXME\|XXX" workflows/code_review/comments.py workflows/code_review/comments_publisher.py workflows/code_review/observability.py watch.py watch_sources.py observability_overrides.py 2>&1 | grep -v ".pyc:"
```
Expected: empty (or only commented-out exposition).

- [ ] **Step 3: Verify install payload completeness**

Run:
```bash
grep -E "PAYLOAD_ITEMS" scripts/install.py -A 30 | head -40
```
Expected: includes `comments.py`, `comments_publisher.py`, `observability.py`, `watch.py`, `watch_sources.py`, `observability_overrides.py`. Add anything missing.

- [ ] **Step 4: Verify schema accepts the live workflow.yaml**

Run:
```bash
/usr/bin/python3 -c "
import yaml, jsonschema
schema = yaml.safe_load(open('workflows/code_review/schema.yaml'))
config = yaml.safe_load(open('/home/radxa/.hermes/workflows/yoyopod/config/workflow.yaml'))
jsonschema.validate(config, schema)
print('OK — live workflow.yaml validates against new schema')
"
```
Expected: prints OK. (The live yaml doesn't have an `observability:` block, so the optional-block design holds back-compat.)

- [ ] **Step 5: Commit any cleanup**

```bash
git add -A
git diff --cached --stat
git commit -m "chore: install payload + grep audit clean for observability feature" || echo "nothing to commit"
```

---

## Acceptance criteria check (against spec §13)

- [ ] `observability:` schema validates: tested in Task 1.1
- [ ] `enabled: false` → no `gh` calls, no state writes: tested in Task 1.5 (`test_publisher_disabled_when_globally_off`) and Task 1.10
- [ ] First event creates comment, subsequent events PATCH it: Task 1.5 (`test_first_event_creates...`, `test_subsequent_event_edits...`) and Task 1.10
- [ ] Operator-attention sticky header: Task 1.3 (`test_render_full_comment_with_operator_attention...`)
- [ ] `/daedalus set-observability` mutes a yaml-enabled workflow: Task 3.2 (`test_set_observability_writes_override`)
- [ ] `/daedalus get-observability` shows effective config + source: Task 3.2 (`test_get_observability_shows_*_source_*`)
- [ ] `/daedalus watch` renders live frame; degrades on missing data: Task 2.4 + Task 2.1's tolerance tests
- [ ] No-TTY fallback exits 0: Task 2.4 (`test_cmd_watch_one_shot_when_not_tty`)
- [ ] All existing tests still pass: verified in every task's Step 4/5 (full pytest run)
- [ ] No behavioral change for live YoYoPod workspace: live workflow.yaml has no `observability:` block → resolves to default `enabled: false`. Verified by Task 4.2 schema-validation step.

## Final handoff

After all tasks complete:

1. Final full pytest run: `/usr/bin/python3 -m pytest -q 2>&1 | tail -10`
2. Final commit log: `git log --oneline main..HEAD`
3. Push the worktree branch and (optionally) open a PR.
