# External Reviewer Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Make the external reviewer pluggable via config: introduce a `Reviewer` Protocol + registry, generalize the Codex Cloud fetcher into a `github-comments` provider, add a `disabled` provider, and migrate the repair-handoff prompt to a bundled file.

**Architecture:** New `workflows/code_review/reviewers/` package mirrors `workflows/code_review/runtimes/`. `Reviewer` Protocol with `fetch_review(...)`, `fetch_pr_body_signal(...)`, `placeholder(...)` methods. Workspace builds one reviewer instance during setup; existing `_fetch_codex_cloud_review` shims delegate to it. Helper renames in `reviews.py` stay deferred to Phase D.

**Tech Stack:** Python 3.11, JSON Schema (jsonschema), pyyaml, pytest.

**Spec:** `docs/superpowers/specs/2026-04-26-external-reviewer-phase-b-design.md`

**Worktree:** `/home/radxa/WS/hermes-relay/.claude/worktrees/external-reviewer-phase-b` on branch `claude/external-reviewer-phase-b` from main `47ae160`. Baseline 477 tests passing. Use `/usr/bin/python3`.

---

## File Structure

**New files:**
- `workflows/code_review/reviewers/__init__.py` — Protocol, ReviewerContext, registry, build_reviewer
- `workflows/code_review/reviewers/github_comments.py` — `GithubCommentsReviewer`
- `workflows/code_review/reviewers/disabled.py` — `DisabledReviewer`
- `workflows/code_review/prompts/external-reviewer-repair-handoff.md` — extracted from inline lines
- `tests/test_external_reviewer_phase_b.py`
- `tests/test_external_reviewer_schema.py`
- `tests/test_external_reviewer_repair_handoff_prompt.py`

**Modified files:**
- `workflows/code_review/schema.yaml` — `kind:` enum, `logins`/`clean-reactions`/`pending-reactions`/`repo-slug` inside reviewer block
- `workflows/code_review/workspace.py` — build reviewer once, route shims through it
- `workflows/code_review/prompts.py` — extract template, add new function name + back-compat alias
- `skills/operator/SKILL.md` — document the new reviewer config surface

---

## Task 1: Reviewer Protocol + registry skeleton

**Files:**
- Create: `workflows/code_review/reviewers/__init__.py`
- Test: `tests/test_external_reviewer_phase_b.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/test_external_reviewer_phase_b.py`:

```python
"""Phase B tests: external reviewer pluggability."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_reviewer_module_exposes_protocol_and_registry():
    from workflows.code_review.reviewers import Reviewer, ReviewerContext, register, build_reviewer, _REVIEWER_KINDS
    assert callable(register)
    assert callable(build_reviewer)
    assert isinstance(_REVIEWER_KINDS, dict)


def test_build_reviewer_unknown_kind_raises():
    from workflows.code_review.reviewers import build_reviewer

    with pytest.raises(ValueError, match="unknown"):
        build_reviewer({"kind": "made-up"}, ws_context=MagicMock())
```

- [ ] **Step 2: Verify failure**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/external-reviewer-phase-b
/usr/bin/python3 -m pytest tests/test_external_reviewer_phase_b.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_review.reviewers'`.

- [ ] **Step 3: Create the package skeleton**

Create `workflows/code_review/reviewers/__init__.py`:

```python
"""Pluggable external-reviewer abstraction.

Mirrors the runtime layer: Protocol + @register decorator + factory.
Each kind wraps a way of fetching post-publish review threads (today:
GitHub PR comments from configured bots; future: webhook payloads,
HTTP polling, etc.) and normalizes them into the provider-neutral
output shape that `reviews.normalize_review` already enforces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class ReviewerContext:
    """Workspace-scoped primitives a reviewer needs at fetch time."""

    run_json: Callable[..., Any]
    repo_path: Path
    repo_slug: str
    iso_to_epoch: Callable[[Any], int | None]
    now_epoch: Callable[[], float]
    extract_severity: Callable[[str], str]
    extract_summary: Callable[[str], str]
    agent_name: str
    agent_role: str = "external_reviewer_agent"


@runtime_checkable
class Reviewer(Protocol):
    """Protocol every external reviewer kind implements."""

    def fetch_review(
        self,
        *,
        pr_number: int | None,
        current_head_sha: str | None,
        cached_review: dict | None,
    ) -> dict[str, Any]: ...

    def fetch_pr_body_signal(self, pr_number: int | None) -> dict | None: ...

    def placeholder(
        self,
        *,
        required: bool,
        status: str,
        summary: str,
    ) -> dict[str, Any]: ...


_REVIEWER_KINDS: dict[str, type] = {}


def register(kind: str):
    """Decorator: registers a class as the implementation for a reviewer kind."""

    def _register(cls):
        _REVIEWER_KINDS[kind] = cls
        return cls

    return _register


def build_reviewer(reviewer_cfg: dict, *, ws_context: ReviewerContext) -> Reviewer:
    """Instantiate the configured reviewer.

    Selection rules:
      - If reviewer_cfg.get('enabled') is False -> 'disabled'.
      - Else use reviewer_cfg.get('kind') (default 'github-comments').
    """
    # Trigger registration side-effects via lazy import.
    from workflows.code_review.reviewers import github_comments  # noqa: F401
    from workflows.code_review.reviewers import disabled as _disabled  # noqa: F401

    if reviewer_cfg.get("enabled") is False:
        kind = "disabled"
    else:
        kind = reviewer_cfg.get("kind") or "github-comments"

    if kind not in _REVIEWER_KINDS:
        raise ValueError(
            f"unknown external reviewer kind={kind!r}; "
            f"registered kinds: {sorted(_REVIEWER_KINDS)}"
        )
    cls = _REVIEWER_KINDS[kind]
    return cls(reviewer_cfg, ws_context=ws_context)
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_external_reviewer_phase_b.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 479 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(reviewers): add Reviewer Protocol + registry skeleton

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: github-comments provider

**Files:**
- Create: `workflows/code_review/reviewers/github_comments.py`
- Test: `tests/test_external_reviewer_phase_b.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/test_external_reviewer_phase_b.py`:

```python
def _ws_context():
    from workflows.code_review.reviewers import ReviewerContext

    return ReviewerContext(
        run_json=MagicMock(return_value={"data": {"repository": {"pullRequest": {
            "state": "OPEN", "headRefOid": "abc123",
            "reviewThreads": {"nodes": []},
        }}}}),
        repo_path=Path("/tmp"),
        repo_slug="acme/widget",
        iso_to_epoch=lambda x: None,
        now_epoch=lambda: 1000.0,
        extract_severity=lambda body: "minor",
        extract_summary=lambda body: body,
        agent_name="External_Reviewer_Agent",
    )


def test_github_comments_reviewer_registered():
    from workflows.code_review.reviewers import _REVIEWER_KINDS, github_comments  # noqa: F401

    assert "github-comments" in _REVIEWER_KINDS


def test_github_comments_reviewer_uses_configured_repo_slug():
    """Regression: repo slug comes from reviewer config, not from workspace.py hardcode."""
    from workflows.code_review.reviewers import build_reviewer

    ctx = _ws_context()
    cfg = {
        "enabled": True,
        "name": "X",
        "kind": "github-comments",
        "logins": ["bot[bot]"],
        "repo-slug": "different/repo",
    }
    rv = build_reviewer(cfg, ws_context=ctx)
    rv.fetch_review(pr_number=42, current_head_sha="abc123", cached_review=None)
    # The GraphQL query string passed to gh api graphql contains the configured slug
    args, _ = ctx.run_json.call_args
    cmd_argv = args[0]
    flat = " ".join(cmd_argv)
    assert "different/repo" in flat
    assert "acme/widget" not in flat


def test_github_comments_reviewer_uses_configured_logins():
    """Bot logins come from reviewer config."""
    from workflows.code_review.reviewers import build_reviewer

    ctx = _ws_context()
    # Inject one matching review-thread comment from a custom bot login.
    ctx.run_json.return_value = {"data": {"repository": {"pullRequest": {
        "state": "OPEN", "headRefOid": "abc123",
        "reviewThreads": {"nodes": [{
            "id": "T1", "isResolved": False, "isOutdated": False,
            "path": "a.py", "line": 10,
            "comments": {"nodes": [{
                "author": {"login": "my-bot[bot]"},
                "body": "issue", "url": "https://x", "createdAt": "2026-01-01T00:00:00Z",
            }]},
        }]},
    }}}}
    cfg = {
        "enabled": True,
        "name": "X",
        "kind": "github-comments",
        "logins": ["my-bot[bot]"],
        "repo-slug": "acme/widget",
    }
    rv = build_reviewer(cfg, ws_context=ctx)
    out = rv.fetch_review(pr_number=42, current_head_sha="abc123", cached_review=None)
    assert any(t.get("source") == "codexCloud" for t in out.get("threads", []))


def test_github_comments_reviewer_ignores_non_matching_logins():
    """Comments from non-configured logins are filtered out."""
    from workflows.code_review.reviewers import build_reviewer

    ctx = _ws_context()
    ctx.run_json.return_value = {"data": {"repository": {"pullRequest": {
        "state": "OPEN", "headRefOid": "abc123",
        "reviewThreads": {"nodes": [{
            "id": "T1", "isResolved": False, "isOutdated": False,
            "path": "a.py", "line": 10,
            "comments": {"nodes": [{
                "author": {"login": "human-user"},
                "body": "issue", "url": "https://x", "createdAt": "2026-01-01T00:00:00Z",
            }]},
        }]},
    }}}}
    cfg = {
        "enabled": True, "name": "X", "kind": "github-comments",
        "logins": ["my-bot[bot]"], "repo-slug": "acme/widget",
    }
    rv = build_reviewer(cfg, ws_context=ctx)
    out = rv.fetch_review(pr_number=42, current_head_sha="abc123", cached_review=None)
    assert out.get("threads") == []


def test_github_comments_reviewer_placeholder():
    """Placeholder shape matches reviews.codex_cloud_placeholder for back-compat."""
    from workflows.code_review.reviewers import build_reviewer

    cfg = {"enabled": True, "name": "X", "kind": "github-comments", "repo-slug": "x/y"}
    rv = build_reviewer(cfg, ws_context=_ws_context())
    p = rv.placeholder(required=True, status="pending", summary="waiting")
    assert p["status"] == "pending"
    assert p["summary"] == "waiting"
    assert p["agentRole"] == "external_reviewer_agent"
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_external_reviewer_phase_b.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_review.reviewers.github_comments'`.

- [ ] **Step 3: Create the github-comments provider**

Create `workflows/code_review/reviewers/github_comments.py`:

```python
"""GitHub PR-comments external reviewer.

Generalizes the Codex Cloud fetcher: configurable bot logins,
clean/pending reactions, repo slug, cache TTL. Today this still
delegates to ``reviews.fetch_codex_cloud_review`` /
``reviews.fetch_codex_pr_body_signal`` for the actual work — Phase D
will rename those helpers.
"""
from __future__ import annotations

import time
from typing import Any

from workflows.code_review.reviewers import (
    Reviewer,
    ReviewerContext,
    register,
)


_DEFAULT_LOGINS = ("chatgpt-codex-connector[bot]",)
_DEFAULT_CLEAN_REACTIONS = ("+1", "rocket", "heart", "hooray")
_DEFAULT_PENDING_REACTIONS = ("eyes",)
_DEFAULT_CACHE_SECONDS = 300


@register("github-comments")
class GithubCommentsReviewer:
    """Reads PR review threads from GitHub via ``gh api graphql``.

    Config shape (YAML, inside ``agents.external-reviewer:``):
        kind: github-comments
        logins: ["chatgpt-codex-connector[bot]"]
        clean-reactions: ["+1", "rocket"]
        pending-reactions: ["eyes"]
        cache-seconds: 300
        repo-slug: "owner/repo"
    """

    def __init__(self, cfg: dict, *, ws_context: ReviewerContext):
        self._cfg = cfg
        self._ctx = ws_context
        self._logins = set(cfg.get("logins") or _DEFAULT_LOGINS)
        self._clean_reactions = list(cfg.get("clean-reactions") or _DEFAULT_CLEAN_REACTIONS)
        self._pending_reactions = list(cfg.get("pending-reactions") or _DEFAULT_PENDING_REACTIONS)
        self._cache_seconds = int(cfg.get("cache-seconds") or _DEFAULT_CACHE_SECONDS)
        self._repo_slug = cfg.get("repo-slug") or ws_context.repo_slug

    def fetch_review(
        self,
        *,
        pr_number: int | None,
        current_head_sha: str | None,
        cached_review: dict | None,
    ) -> dict[str, Any]:
        from workflows.code_review.reviews import fetch_codex_cloud_review

        return fetch_codex_cloud_review(
            pr_number,
            current_head_sha=current_head_sha,
            cached_review=cached_review,
            fetch_pr_body_signal_fn=self.fetch_pr_body_signal,
            run_json_fn=self._ctx.run_json,
            cwd=self._ctx.repo_path,
            repo_slug=self._repo_slug,
            codex_bot_logins=self._logins,
            cache_seconds=self._cache_seconds,
            iso_to_epoch_fn=self._ctx.iso_to_epoch,
            now_epoch_fn=self._ctx.now_epoch,
            extract_severity_fn=self._ctx.extract_severity,
            extract_summary_fn=self._ctx.extract_summary,
            agent_name=self._ctx.agent_name,
        )

    def fetch_pr_body_signal(self, pr_number: int | None) -> dict | None:
        from workflows.code_review.reviews import fetch_codex_pr_body_signal

        return fetch_codex_pr_body_signal(
            pr_number,
            run_json_fn=self._ctx.run_json,
            cwd=self._ctx.repo_path,
            codex_bot_logins=self._logins,
            clean_reactions=self._clean_reactions,
            pending_reactions=self._pending_reactions,
            repo_slug=self._repo_slug,
        )

    def placeholder(
        self,
        *,
        required: bool,
        status: str,
        summary: str,
    ) -> dict[str, Any]:
        from workflows.code_review.reviews import codex_cloud_placeholder

        return codex_cloud_placeholder(
            required=required,
            status=status,
            summary=summary,
            agent_name=self._ctx.agent_name,
            agent_role=self._ctx.agent_role,
        )
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_external_reviewer_phase_b.py -v
```
Expected: 7 passed. The placeholder test depends on `codex_cloud_placeholder` returning a dict with `agentRole`; if the test fails because of a normalize-review key, inspect the output and adjust the assertion to match actual structure (the field is named `agentRole` in `reviews.py:466`).

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 484 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(reviewers): add github-comments provider

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: disabled provider

**Files:**
- Create: `workflows/code_review/reviewers/disabled.py`
- Test: `tests/test_external_reviewer_phase_b.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/test_external_reviewer_phase_b.py`:

```python
def test_disabled_reviewer_registered():
    from workflows.code_review.reviewers import _REVIEWER_KINDS, disabled  # noqa: F401

    assert "disabled" in _REVIEWER_KINDS


def test_disabled_reviewer_returns_skipped_placeholder():
    from workflows.code_review.reviewers import build_reviewer

    cfg = {"enabled": False, "name": "X"}
    rv = build_reviewer(cfg, ws_context=_ws_context())
    out = rv.fetch_review(pr_number=42, current_head_sha="abc", cached_review=None)
    assert out["status"] == "skipped"
    assert out["required"] is False


def test_disabled_reviewer_does_not_call_run_json():
    from workflows.code_review.reviewers import build_reviewer

    ctx = _ws_context()
    cfg = {"enabled": False, "name": "X"}
    rv = build_reviewer(cfg, ws_context=ctx)
    rv.fetch_review(pr_number=42, current_head_sha="abc", cached_review=None)
    rv.fetch_pr_body_signal(42)
    ctx.run_json.assert_not_called()


def test_build_reviewer_defaults_to_disabled_when_enabled_false():
    """enabled: false wins over an explicit kind."""
    from workflows.code_review.reviewers import build_reviewer
    from workflows.code_review.reviewers.disabled import DisabledReviewer

    rv = build_reviewer({"enabled": False, "kind": "github-comments"}, ws_context=_ws_context())
    assert isinstance(rv, DisabledReviewer)


def test_build_reviewer_defaults_to_github_comments_when_enabled():
    """No explicit kind + enabled: true -> github-comments."""
    from workflows.code_review.reviewers import build_reviewer
    from workflows.code_review.reviewers.github_comments import GithubCommentsReviewer

    rv = build_reviewer({"enabled": True, "name": "X"}, ws_context=_ws_context())
    assert isinstance(rv, GithubCommentsReviewer)
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_external_reviewer_phase_b.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_review.reviewers.disabled'`.

- [ ] **Step 3: Create the disabled provider**

Create `workflows/code_review/reviewers/disabled.py`:

```python
"""Disabled external reviewer — used when ``enabled: false`` or
``kind: disabled``. All operations short-circuit with a skipped
placeholder; no GitHub API calls."""
from __future__ import annotations

from typing import Any

from workflows.code_review.reviewers import (
    Reviewer,
    ReviewerContext,
    register,
)


@register("disabled")
class DisabledReviewer:
    def __init__(self, cfg: dict, *, ws_context: ReviewerContext):
        self._cfg = cfg
        self._ctx = ws_context

    def fetch_review(
        self,
        *,
        pr_number: int | None,
        current_head_sha: str | None,
        cached_review: dict | None,
    ) -> dict[str, Any]:
        return self.placeholder(
            required=False,
            status="skipped",
            summary="External review disabled.",
        )

    def fetch_pr_body_signal(self, pr_number: int | None) -> dict | None:
        return None

    def placeholder(
        self,
        *,
        required: bool,
        status: str,
        summary: str,
    ) -> dict[str, Any]:
        from workflows.code_review.reviews import codex_cloud_placeholder

        return codex_cloud_placeholder(
            required=required,
            status=status,
            summary=summary,
            agent_name=self._ctx.agent_name,
            agent_role=self._ctx.agent_role,
        )
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_external_reviewer_phase_b.py -v
```
Expected: 12 passed.

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 489 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(reviewers): add disabled external reviewer

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Schema extensions

**Files:**
- Modify: `workflows/code_review/schema.yaml`
- Test: `tests/test_external_reviewer_schema.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_external_reviewer_schema.py`:

```python
"""Phase B schema validation."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator, ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "workflows/code_review/schema.yaml"


def _schema():
    return yaml.safe_load(SCHEMA_PATH.read_text())


def _base_config():
    return {
        "workflow": "code-review",
        "schema-version": 1,
        "instance": {"name": "test", "engine-owner": "hermes"},
        "repository": {
            "local-path": "/tmp/x",
            "github-slug": "x/y",
            "active-lane-label": "active",
        },
        "runtimes": {
            "codex-acpx": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": 900,
                "session-idle-grace-seconds": 1800,
                "session-nudge-cooldown-seconds": 600,
            },
        },
        "agents": {
            "coder": {"default": {"name": "c", "model": "m", "runtime": "codex-acpx"}},
            "internal-reviewer": {"name": "ir", "model": "m", "runtime": "codex-acpx"},
            "external-reviewer": {"enabled": True, "name": "er"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "label", "label": "active"}},
        "storage": {"ledger": "x", "health": "x", "audit-log": "x"},
    }


def test_schema_accepts_kind_github_comments():
    cfg = _base_config()
    cfg["agents"]["external-reviewer"]["kind"] = "github-comments"
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_kind_disabled():
    cfg = _base_config()
    cfg["agents"]["external-reviewer"]["kind"] = "disabled"
    Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_unknown_kind():
    cfg = _base_config()
    cfg["agents"]["external-reviewer"]["kind"] = "made-up"
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_repo_slug_override():
    cfg = _base_config()
    cfg["agents"]["external-reviewer"]["repo-slug"] = "acme/widget"
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_logins_inside_reviewer_block():
    cfg = _base_config()
    cfg["agents"]["external-reviewer"]["logins"] = ["bot[bot]"]
    cfg["agents"]["external-reviewer"]["clean-reactions"] = ["+1"]
    cfg["agents"]["external-reviewer"]["pending-reactions"] = ["eyes"]
    Draft7Validator(_schema()).validate(cfg)


def test_existing_yoyopod_workflow_yaml_still_validates():
    yoyopod = Path(os.path.expanduser("~/.hermes/workflows/yoyopod/config/workflow.yaml"))
    if not yoyopod.exists():
        pytest.skip("yoyopod workspace not present on this host")
    cfg = yaml.safe_load(yoyopod.read_text())
    Draft7Validator(_schema()).validate(cfg)
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_external_reviewer_schema.py -v
```
Expected: FAIL on `kind` enum tests.

- [ ] **Step 3: Edit schema.yaml**

In `workflows/code_review/schema.yaml`, replace the `external-reviewer:` block (currently lines 61-68) with:

```yaml
      external-reviewer:
        type: object
        required: [enabled, name]
        properties:
          enabled: {type: boolean}
          name: {type: string}
          kind:
            type: string
            enum: [github-comments, disabled]
          provider: {type: string}
          cache-seconds: {type: integer}
          repo-slug: {type: string}
          logins:
            type: array
            items: {type: string}
          clean-reactions:
            type: array
            items: {type: string}
          pending-reactions:
            type: array
            items: {type: string}
```

(Note: do not add `additionalProperties: false` to this one — the live yoyopod config or others may carry extra workspace-internal fields.)

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_external_reviewer_schema.py -v
```
Expected: 6 passed (or 5 + 1 skipped).

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 495 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(schema): add reviewer kind enum + reviewer-scoped logins/reactions/repo-slug

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Repair-handoff prompt to file

**Files:**
- Create: `workflows/code_review/prompts/external-reviewer-repair-handoff.md`
- Modify: `workflows/code_review/prompts.py`
- Test: `tests/test_external_reviewer_repair_handoff_prompt.py` (new)

- [ ] **Step 1: Capture the legacy output**

Run a quick script to print the current `render_codex_cloud_repair_handoff_prompt` output for a fixed input, save to a string for the regression test (use this verbatim in Step 4):

```bash
/usr/bin/python3 -c "
from pathlib import Path
from workflows.code_review.prompts import render_codex_cloud_repair_handoff_prompt
out = render_codex_cloud_repair_handoff_prompt(
    issue={'number': 42, 'title': 'Bug X'},
    codex_review={'reviewedHeadSha': 'abc123', 'summary': 'Found issue.'},
    repair_brief={'mustFix': [{'summary': 'Fix A'}], 'shouldFix': [{'summary': 'Improve B'}]},
    lane_memo_path=Path('/tmp/memo.md'),
    lane_state_path=Path('/tmp/state.json'),
    pr_url='https://x/1',
    external_reviewer_agent_name='External_Reviewer_Agent',
)
print(out)
"
```
Save the captured output for use in the regression test.

- [ ] **Step 2: Write failing tests**

Create `tests/test_external_reviewer_repair_handoff_prompt.py`:

```python
"""Phase B: external-reviewer repair-handoff prompt template."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_repair_handoff_template_file_exists():
    bundled = Path(__file__).resolve().parent.parent / "workflows" / "code_review" / "prompts" / "external-reviewer-repair-handoff.md"
    assert bundled.is_file()


def test_render_external_reviewer_repair_handoff_prompt_callable():
    from workflows.code_review.prompts import render_external_reviewer_repair_handoff_prompt
    assert callable(render_external_reviewer_repair_handoff_prompt)


def test_codex_cloud_alias_still_callable():
    from workflows.code_review.prompts import render_codex_cloud_repair_handoff_prompt
    assert callable(render_codex_cloud_repair_handoff_prompt)


def test_aliases_produce_identical_output():
    from workflows.code_review.prompts import (
        render_external_reviewer_repair_handoff_prompt,
        render_codex_cloud_repair_handoff_prompt,
    )

    kwargs = dict(
        issue={"number": 42, "title": "Bug X"},
        codex_review={"reviewedHeadSha": "abc123", "summary": "Found issue."},
        repair_brief={"mustFix": [{"summary": "Fix A"}], "shouldFix": [{"summary": "Improve B"}]},
        lane_memo_path=Path("/tmp/memo.md"),
        lane_state_path=Path("/tmp/state.json"),
        pr_url="https://x/1",
        external_reviewer_agent_name="External_Reviewer_Agent",
    )
    new = render_external_reviewer_repair_handoff_prompt(**kwargs)
    legacy = render_codex_cloud_repair_handoff_prompt(**kwargs)
    assert new == legacy


def test_repair_handoff_includes_required_fields():
    from workflows.code_review.prompts import render_external_reviewer_repair_handoff_prompt

    out = render_external_reviewer_repair_handoff_prompt(
        issue={"number": 42, "title": "Bug X"},
        codex_review={"reviewedHeadSha": "abc123", "summary": "Found issue."},
        repair_brief={"mustFix": [{"summary": "Fix A"}], "shouldFix": []},
        lane_memo_path=Path("/tmp/memo.md"),
        lane_state_path=Path("/tmp/state.json"),
        pr_url="https://x/1",
        external_reviewer_agent_name="My_External_Reviewer",
    )
    assert "issue #42" in out
    assert "abc123" in out
    assert "Fix A" in out
    assert "My_External_Reviewer" in out
    assert "https://x/1" in out
```

- [ ] **Step 3: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_external_reviewer_repair_handoff_prompt.py -v
```
Expected: FAIL on missing template file + missing `render_external_reviewer_repair_handoff_prompt`.

- [ ] **Step 4: Create the bundled prompt template**

The current inline output (from Step 1) has this shape (paraphrased — capture the exact bytes from Step 1):

```
{external_reviewer_agent_name} review found follow-up work for issue #{issue_number} on published head {reviewed_head_sha}.
Issue: #{issue_number} {issue_title}
PR: {pr_url}
{lane_memo_line}
{lane_state_line}
Read .lane-memo.md and .lane-state.json first; they are authoritative.
Stay on the same branch and fix the current Codex Cloud review findings on the published head.
After fixes, run focused validation, update the branch head, and stop so the normal review loop can re-evaluate.

Codex Cloud summary:
{review_summary}

Current must-fix items:
{must_fix_lines}

Current should-fix items:
{should_fix_lines}

Guardrails:
- Do not touch data/test_messages/messages.json.
- Do not publish .codex artifacts.
- Keep scope narrow to the active Codex Cloud repair brief.
- Report exactly what changed, what validation ran, and the new HEAD SHA.
```

Notes:
- Two of the lines say "Codex Cloud" (`fix the current Codex Cloud review findings`, `Codex Cloud summary:`, `active Codex Cloud repair brief`). Replace these with `{external_reviewer_agent_name}` to make the template provider-neutral. The regression test (Step 2) uses `external_reviewer_agent_name="External_Reviewer_Agent"`, so both old and new output will have `External_Reviewer_Agent` instead of `Codex Cloud` — UPDATE the legacy `render_codex_cloud_repair_handoff_prompt` to also use `{external_reviewer_agent_name}` for these strings (it's already a passed-in kwarg). The regression-equality test then holds.

Create `workflows/code_review/prompts/external-reviewer-repair-handoff.md`:

```
{external_reviewer_agent_name} review found follow-up work for issue #{issue_number} on published head {reviewed_head_sha}.
Issue: #{issue_number} {issue_title}
PR: {pr_url}
{lane_memo_line}
{lane_state_line}
Read .lane-memo.md and .lane-state.json first; they are authoritative.
Stay on the same branch and fix the current {external_reviewer_agent_name} review findings on the published head.
After fixes, run focused validation, update the branch head, and stop so the normal review loop can re-evaluate.

{external_reviewer_agent_name} summary:
{review_summary}

Current must-fix items:
{must_fix_lines}

Current should-fix items:
{should_fix_lines}

Guardrails:
- Do not touch data/test_messages/messages.json.
- Do not publish .codex artifacts.
- Keep scope narrow to the active {external_reviewer_agent_name} repair brief.
- Report exactly what changed, what validation ran, and the new HEAD SHA.
```

- [ ] **Step 5: Update prompts.py**

In `workflows/code_review/prompts.py`, REPLACE the existing `render_codex_cloud_repair_handoff_prompt` (lines 153-192) with a new `render_external_reviewer_repair_handoff_prompt` that loads the template, plus a back-compat alias:

```python
def render_external_reviewer_repair_handoff_prompt(
    *,
    issue: dict[str, Any] | None,
    codex_review: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    lane_memo_path: Path | None,
    lane_state_path: Path | None,
    pr_url: str | None,
    external_reviewer_agent_name: str,
) -> str:
    review = codex_review or {}
    must_fix = [item.get("summary", "") for item in (repair_brief or {}).get("mustFix", []) if item.get("summary")][:8]
    should_fix = [item.get("summary", "") for item in (repair_brief or {}).get("shouldFix", []) if item.get("summary")][:8]
    must_fix_lines = "\n".join([f"- {item}" for item in must_fix] or ["- none recorded"])
    should_fix_lines = "\n".join([f"- {item}" for item in should_fix] or ["- none recorded"])
    return _load_template("external-reviewer-repair-handoff").format(
        external_reviewer_agent_name=external_reviewer_agent_name,
        issue_number=(issue or {}).get("number"),
        issue_title=(issue or {}).get("title"),
        reviewed_head_sha=review.get("reviewedHeadSha") or "unknown",
        lane_memo_line=f"Lane memo: {lane_memo_path}" if lane_memo_path else "Lane memo: none",
        lane_state_line=f"Lane state: {lane_state_path}" if lane_state_path else "Lane state: none",
        review_summary=review.get("summary") or f"No {external_reviewer_agent_name} summary recorded.",
        must_fix_lines=must_fix_lines,
        should_fix_lines=should_fix_lines,
    )


# Back-compat alias — Phase D will remove all callers.
render_codex_cloud_repair_handoff_prompt = render_external_reviewer_repair_handoff_prompt
```

- [ ] **Step 6: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_external_reviewer_repair_handoff_prompt.py -v
```
Expected: 5 passed.

- [ ] **Step 7: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 500 passed. If any pre-existing test asserted the old "Codex Cloud" wording in the prompt output, update those assertions to use `{external_reviewer_agent_name}` substitution (the agent name passed in). Search before fixing:

```bash
grep -rn "Codex Cloud summary\|fix the current Codex Cloud review\|active Codex Cloud repair" tests/
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(prompts): extract external-reviewer repair-handoff to bundled template

Replaces inline string-building with a .format() template at
prompts/external-reviewer-repair-handoff.md. Strings 'Codex Cloud'
become {external_reviewer_agent_name} substitutions so the template
is provider-neutral. render_codex_cloud_repair_handoff_prompt
remains as a back-compat alias.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Workspace integration

**Files:**
- Modify: `workflows/code_review/workspace.py`

- [ ] **Step 1: Build the reviewer in workspace setup**

Locate `workspace.py:64` (`ext_reviewer = agents.get("external-reviewer", {}) or {}`). After the existing setup that derives `EXTERNAL_REVIEWER_AGENT_NAME` (around line 528-638), add reviewer construction. Look for the section where `ns` is being assembled with attributes; right after `EXTERNAL_REVIEWER_AGENT_NAME=external_reviewer_agent_name,` is set on `ns`, append a reviewer build:

```python
# Build the external reviewer once; downstream shims delegate to it.
from workflows.code_review.reviewers import ReviewerContext, build_reviewer

# Resolve config: agents.external-reviewer first, codex-bot as deprecated fallback.
ext_reviewer_cfg = dict(agents.get("external-reviewer") or {})
codex_bot_block = config.get("codex-bot") or {}
for legacy_key, modern_key in (
    ("logins", "logins"),
    ("clean-reactions", "clean-reactions"),
    ("pending-reactions", "pending-reactions"),
):
    if modern_key not in ext_reviewer_cfg and legacy_key in codex_bot_block:
        ext_reviewer_cfg[modern_key] = codex_bot_block[legacy_key]

# Default repo-slug preserves current hardcoded behavior for unmodified configs.
if "repo-slug" not in ext_reviewer_cfg:
    ext_reviewer_cfg["repo-slug"] = "moustafattia/YoyoPod_Core"

reviewer_ctx = ReviewerContext(
    run_json=ns._run_json,
    repo_path=ns.REPO_PATH,
    repo_slug=ext_reviewer_cfg["repo-slug"],
    iso_to_epoch=ns._iso_to_epoch,
    now_epoch=time.time,
    extract_severity=ns._extract_severity,
    extract_summary=ns._extract_summary,
    agent_name=ns.EXTERNAL_REVIEWER_AGENT_NAME,
)
ns.reviewer = build_reviewer(ext_reviewer_cfg, ws_context=reviewer_ctx)
```

(Exact placement depends on where `ns` exists and `_run_json` etc. are bound — search for `EXTERNAL_REVIEWER_AGENT_NAME=external_reviewer_agent_name,` and place this block AFTER `ns` has all the underscored callables. Probably after line 638 in the existing namespace assembly.)

- [ ] **Step 2: Route the existing shims through the reviewer**

In `workspace.py:1375-1411`, replace the bodies of `_fetch_codex_pr_body_signal`, `_fetch_codex_cloud_review`, and `_codex_cloud_placeholder` to delegate to `ns.reviewer`:

```python
def _fetch_codex_pr_body_signal(pr_number):
    return ns.reviewer.fetch_pr_body_signal(pr_number)


def _fetch_codex_cloud_review(pr_number, current_head_sha, cached_review=None):
    return ns.reviewer.fetch_review(
        pr_number=pr_number,
        current_head_sha=current_head_sha,
        cached_review=cached_review,
    )


def _codex_cloud_placeholder(*, required, status, summary):
    return ns.reviewer.placeholder(required=required, status=status, summary=summary)
```

- [ ] **Step 3: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: 500 passed. If any tests fail because of mock expectations on the inner `fetch_codex_cloud_review`, the test mocks need to point at `ns.reviewer.fetch_review` instead — fix as needed and document the change in the commit message.

- [ ] **Step 4: Verify yoyopod still validates and behaves**

```bash
/usr/bin/python3 -c "
import yaml
from pathlib import Path
from jsonschema import Draft7Validator
schema = yaml.safe_load(Path('workflows/code_review/schema.yaml').read_text())
cfg = yaml.safe_load(Path('/home/radxa/.hermes/workflows/yoyopod/config/workflow.yaml').read_text())
Draft7Validator(schema).validate(cfg)
print('yoyopod config valid')
"
```
Expected: `yoyopod config valid`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(workspace): route external review through ws.reviewer

Workspace builds one Reviewer instance during setup; the legacy
_fetch_codex_cloud_review / _fetch_codex_pr_body_signal /
_codex_cloud_placeholder shims now delegate to it. Top-level
codex-bot block is read as a deprecated fallback for one release;
operators should move logins/reactions inside agents.external-reviewer.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Operator docs

**Files:**
- Modify: `skills/operator/SKILL.md`

- [ ] **Step 1: Append new section**

Append to `skills/operator/SKILL.md`:

````markdown
## External reviewer config (Phase B — pluggable)

Pick a reviewer kind via `agents.external-reviewer.kind`:

```yaml
agents:
  external-reviewer:
    enabled: true
    name: ChatGPT_Codex_Cloud
    kind: github-comments         # default; reads PR review threads
    repo-slug: owner/repo         # optional; falls back to legacy hardcode
    cache-seconds: 300
    logins:
      - chatgpt-codex-connector[bot]
    clean-reactions: ["+1", "rocket", "heart", "hooray"]
    pending-reactions: ["eyes"]
```

**Kinds:**
- `github-comments` — reads PR review threads via `gh api graphql`. Configurable bot logins, clean/pending reactions, repo slug, cache TTL.
- `disabled` — no external review; placeholder review with `status: skipped`.

**`enabled: false`** is equivalent to `kind: disabled` regardless of any other field.

**Deprecated:** the top-level `codex-bot:` block (`logins`/`clean-reactions`/`pending-reactions`) is still honored as a fallback for one release. Move those keys inside `agents.external-reviewer:` to silence the deprecation path.

**Prompt overrides:** the repair-handoff prompt now lives at `workflows/code_review/prompts/external-reviewer-repair-handoff.md`. Drop a file at `<workspace>/config/prompts/external-reviewer-repair-handoff.md` to override it (Phase A resolution chain).
````

- [ ] **Step 2: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 500 passed (no test impact).

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "docs(operator): document external reviewer config surface

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Run full suite once more**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/external-reviewer-phase-b
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: 500 passed.

- [ ] **Sanity-check live yoyopod config still validates**

```bash
/usr/bin/python3 -c "
import yaml
from pathlib import Path
from jsonschema import Draft7Validator
schema = yaml.safe_load(Path('workflows/code_review/schema.yaml').read_text())
cfg = yaml.safe_load(Path('/home/radxa/.hermes/workflows/yoyopod/config/workflow.yaml').read_text())
Draft7Validator(schema).validate(cfg)
print('yoyopod config valid')
"
```
Expected: `yoyopod config valid`.

- [ ] **Use superpowers:finishing-a-development-branch** to wrap up.
