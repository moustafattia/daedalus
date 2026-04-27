# Rename Pass Phase D-1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Migrate persisted ledger keys `reviews.claudeCode` → `reviews.internalReview` and `reviews.codexCloud` → `reviews.externalReview`. One-shot migration + read-both/write-new code paths. Action-type literal `run_claude_review` → `run_internal_review` with alias.

**Spec:** `docs/superpowers/specs/2026-04-26-rename-pass-phase-d-1-design.md`

**Worktree:** `/home/radxa/WS/hermes-relay/.claude/worktrees/rename-pass-phase-d-1` on branch `claude/rename-pass-phase-d-1` from main `47ae160`. Baseline 477 tests passing. Use `/usr/bin/python3`.

---

## File Structure

**New files:**
- `workflows/code_review/migrations.py` — `REVIEW_KEY_RENAMES`, `migrate_review_keys`, `get_review`, `migrate_persisted_ledger`
- `tests/test_rename_pass_phase_d_1.py`

**Modified files:**
- `workflows/code_review/actions.py` — write sites for `claudeCode`, action-type literal
- `workflows/code_review/orchestrator.py` — read sites for `claudeCode`
- `workflows/code_review/reviews.py` — read + write sites for `claudeCode` and `codexCloud`
- `workflows/code_review/workflow.py` — read sites for `claudeCode`
- `workflows/code_review/status.py` — read sites for `claudeCode`
- `workflows/code_review/workspace.py` — invoke `migrate_persisted_ledger` on bootstrap
- `skills/operator/SKILL.md` — note about migration

---

## Task 1: Migrations module

**Files:**
- Create: `workflows/code_review/migrations.py`
- Test: `tests/test_rename_pass_phase_d_1.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_rename_pass_phase_d_1.py`:

```python
"""Phase D-1 tests: persisted-state migration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_migrate_review_keys_renames_legacy_keys():
    from workflows.code_review.migrations import migrate_review_keys

    ledger = {"reviews": {"claudeCode": {"v": 1}, "codexCloud": {"v": 2}}}
    out, changed = migrate_review_keys(ledger)
    assert changed is True
    assert out["reviews"]["internalReview"] == {"v": 1}
    assert out["reviews"]["externalReview"] == {"v": 2}
    assert "claudeCode" not in out["reviews"]
    assert "codexCloud" not in out["reviews"]


def test_migrate_review_keys_idempotent():
    from workflows.code_review.migrations import migrate_review_keys

    ledger = {"reviews": {"internalReview": {"v": 1}, "externalReview": {"v": 2}}}
    out, changed = migrate_review_keys(ledger)
    assert changed is False
    assert out["reviews"]["internalReview"] == {"v": 1}


def test_migrate_review_keys_new_key_wins_when_both_present():
    from workflows.code_review.migrations import migrate_review_keys

    ledger = {"reviews": {
        "claudeCode": {"v": "old"}, "internalReview": {"v": "new"},
        "codexCloud": {"v": "old2"}, "externalReview": {"v": "new2"},
    }}
    out, changed = migrate_review_keys(ledger)
    assert changed is True  # old keys were dropped
    assert out["reviews"]["internalReview"] == {"v": "new"}
    assert out["reviews"]["externalReview"] == {"v": "new2"}
    assert "claudeCode" not in out["reviews"]
    assert "codexCloud" not in out["reviews"]


def test_migrate_review_keys_passes_through_unknown_keys():
    from workflows.code_review.migrations import migrate_review_keys

    ledger = {"reviews": {"claudeCode": {"v": 1}, "rockClaw": {"v": 9}}}
    out, _ = migrate_review_keys(ledger)
    assert out["reviews"]["rockClaw"] == {"v": 9}


def test_migrate_review_keys_handles_missing_reviews_block():
    from workflows.code_review.migrations import migrate_review_keys

    ledger = {"activeLane": {"number": 42}}
    out, changed = migrate_review_keys(ledger)
    assert changed is False
    assert out == ledger


def test_get_review_returns_new_when_present():
    from workflows.code_review.migrations import get_review

    reviews = {"internalReview": {"v": 1}}
    assert get_review(reviews, "internalReview") == {"v": 1}


def test_get_review_falls_back_to_legacy_when_only_legacy_present():
    from workflows.code_review.migrations import get_review

    reviews = {"claudeCode": {"v": 1}}
    assert get_review(reviews, "internalReview") == {"v": 1}

    reviews = {"codexCloud": {"v": 2}}
    assert get_review(reviews, "externalReview") == {"v": 2}


def test_get_review_prefers_new_when_both_present():
    from workflows.code_review.migrations import get_review

    reviews = {"claudeCode": {"v": "old"}, "internalReview": {"v": "new"}}
    assert get_review(reviews, "internalReview") == {"v": "new"}


def test_get_review_returns_empty_dict_for_unknown_key():
    from workflows.code_review.migrations import get_review

    assert get_review({"x": 1}, "made-up") == {}


def test_get_review_returns_empty_dict_when_value_is_none():
    from workflows.code_review.migrations import get_review

    assert get_review({"internalReview": None}, "internalReview") == {}


def test_migrate_persisted_ledger_rewrites_file_atomically(tmp_path):
    from workflows.code_review.migrations import migrate_persisted_ledger

    p = tmp_path / "ledger.json"
    p.write_text(json.dumps({"reviews": {"claudeCode": {"v": 1}}}, indent=2))
    migrate_persisted_ledger(p)
    out = json.loads(p.read_text())
    assert out["reviews"]["internalReview"] == {"v": 1}
    assert "claudeCode" not in out["reviews"]


def test_migrate_persisted_ledger_noop_on_already_migrated(tmp_path):
    from workflows.code_review.migrations import migrate_persisted_ledger

    p = tmp_path / "ledger.json"
    initial = {"reviews": {"internalReview": {"v": 1}}}
    p.write_text(json.dumps(initial, indent=2))
    mtime_before = p.stat().st_mtime_ns
    # Sleep just enough that any rewrite would change mtime
    import time
    time.sleep(0.01)
    migrate_persisted_ledger(p)
    mtime_after = p.stat().st_mtime_ns
    assert mtime_before == mtime_after  # file untouched


def test_migrate_persisted_ledger_handles_missing_file(tmp_path):
    from workflows.code_review.migrations import migrate_persisted_ledger

    p = tmp_path / "does-not-exist.json"
    # Should not raise
    migrate_persisted_ledger(p)
    assert not p.exists()


def test_migrate_persisted_ledger_preserves_indent(tmp_path):
    from workflows.code_review.migrations import migrate_persisted_ledger

    p = tmp_path / "ledger.json"
    p.write_text(json.dumps({"reviews": {"claudeCode": {"v": 1}}}, indent=2))
    migrate_persisted_ledger(p)
    text = p.read_text()
    assert "  " in text  # 2-space indent preserved


def test_existing_yoyopod_ledger_migrates_cleanly(tmp_path):
    """Smoke test: copy live yoyopod ledger to tmp, migrate, assert it works."""
    from workflows.code_review.migrations import migrate_persisted_ledger, get_review
    import os
    src = Path(os.path.expanduser("~/.hermes/workflows/yoyopod/memory/yoyopod-workflow-status.json"))
    if not src.exists():
        pytest.skip("yoyopod ledger not present on this host")

    dst = tmp_path / "ledger.json"
    dst.write_text(src.read_text())
    migrate_persisted_ledger(dst)

    out = json.loads(dst.read_text())
    reviews = out.get("reviews") or {}
    # Old keys gone (if they were present)
    assert "claudeCode" not in reviews
    assert "codexCloud" not in reviews
    # New keys readable via get_review (passes whether or not the source had old keys)
    _ = get_review(reviews, "internalReview")
    _ = get_review(reviews, "externalReview")
```

- [ ] **Step 2: Verify failure**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/rename-pass-phase-d-1
/usr/bin/python3 -m pytest tests/test_rename_pass_phase_d_1.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_review.migrations'`.

- [ ] **Step 3: Create migrations module**

Create `workflows/code_review/migrations.py`:

```python
"""Persisted-state migrations for the code-review workflow.

Phase D-1 rationale:
  reviews.claudeCode -> reviews.internalReview
  reviews.codexCloud -> reviews.externalReview

The old names tied the ledger to specific providers (Claude / Codex
Cloud). Phases A-C made runtimes/reviewers/webhooks pluggable; this
migration removes the last operator-visible coupling to provider names.

`migrate_persisted_ledger(path)` runs idempotently on workspace setup.
`get_review(reviews_dict, new_key)` reads new key with legacy fallback
so an unmigrated ledger still works for one release.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


REVIEW_KEY_RENAMES: dict[str, str] = {
    "claudeCode": "internalReview",
    "codexCloud": "externalReview",
}

_LEGACY_KEY_FOR: dict[str, str] = {v: k for k, v in REVIEW_KEY_RENAMES.items()}


def migrate_review_keys(ledger: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Rewrite legacy `reviews.<old>` keys to their new names.

    If both old and new keys are present, the new value wins and the
    old key is dropped. Returns ``(ledger, was_changed)``. The ``ledger``
    object is mutated in place AND returned for convenience.
    """
    reviews = ledger.get("reviews")
    if not isinstance(reviews, dict):
        return ledger, False

    changed = False
    for old_key, new_key in REVIEW_KEY_RENAMES.items():
        if old_key in reviews:
            if new_key not in reviews:
                reviews[new_key] = reviews[old_key]
            del reviews[old_key]
            changed = True
    return ledger, changed


def get_review(reviews: dict[str, Any] | None, new_key: str) -> dict[str, Any]:
    """Read a review by its new key; fall back to the legacy key."""
    reviews = reviews or {}
    value = reviews.get(new_key)
    if value:
        return value
    legacy_key = _LEGACY_KEY_FOR.get(new_key)
    if legacy_key:
        legacy_value = reviews.get(legacy_key)
        if legacy_value:
            return legacy_value
    return {}


def migrate_persisted_ledger(path: Path | str) -> bool:
    """Migrate the on-disk ledger at ``path``, atomically.

    Returns True if the file was rewritten, False otherwise. Missing
    files are silently no-op'd. Indent-2 JSON format is preserved.
    """
    p = Path(path)
    if not p.exists():
        return False
    try:
        ledger = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    _, changed = migrate_review_keys(ledger)
    if not changed:
        return False

    # Atomic temp-file + rename in the same directory.
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=p.name, suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return True
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_rename_pass_phase_d_1.py -v
```
Expected: 14 passed (or 13 + 1 skipped if yoyopod ledger absent).

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 491 passed (or 490 + 1 skipped).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(migrations): add persisted-state migration helpers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Rename claudeCode reads via get_review

**Files:**
- Modify: `workflows/code_review/orchestrator.py` (~6 read sites at lines 148, 185, 193, 364, 365)
- Modify: `workflows/code_review/reviews.py` (~5 read sites at lines 207, 1348, 1358, 1366, 1494)
- Modify: `workflows/code_review/workflow.py` (~2 read sites at lines 62, 177)
- Modify: `workflows/code_review/status.py` (~1 read site at line 440)

- [ ] **Step 1: Mechanical replacement of all read sites**

For each occurrence of `reviews.get("claudeCode")` or `(reviews or {}).get("claudeCode")` or `existing_reviews.get("claudeCode")`, replace with `get_review(reviews_dict, "internalReview")`.

Add `from workflows.code_review.migrations import get_review` to the top of each modified file (or to local scope where natural).

Specific transformations:
- `orchestrator.py:148`: `existing_claude_review = existing_reviews.get("claudeCode")` → `existing_claude_review = get_review(existing_reviews, "internalReview")` (note: the variable name `existing_claude_review` is preserved; renaming the local var is Phase D-2's problem to keep this PR small).
- `orchestrator.py:185`: `inter_review_agent_review=reviews["claudeCode"]` → `inter_review_agent_review=get_review(reviews, "internalReview")` — but `reviews["claudeCode"]` is a write-target-style read inside an indexable dict. Inspect the surrounding code: if it's actually being passed as a value (not assigned), the `get_review` call is fine. If it's expected to mutate the dict, this needs different handling. Read the surrounding 5 lines and decide.
- `orchestrator.py:193`, `:364`, `:365`: same pattern; use `get_review(reviews, "internalReview")` for read-only access.
- `orchestrator.py:340`: `previous_claude_review = ((ledger.get("reviews") or {}).get("claudeCode") or {}).copy()` → `previous_claude_review = get_review(ledger.get("reviews"), "internalReview").copy()`
- `reviews.py:207`: `claude_review = reviews.get("claudeCode") or {}` → `claude_review = get_review(reviews, "internalReview")`
- `reviews.py:1348`, `:1358`, `:1366`: `claude_review=reviews.get("claudeCode")` → `claude_review=get_review(reviews, "internalReview")` — note these pass to function kwargs; the parameter rename `claude_review` → `internal_review` is Phase D-2.
- `reviews.py:1494`: `existing_claude_review = existing_reviews.get("claudeCode")` → `existing_claude_review = get_review(existing_reviews, "internalReview")`
- `workflow.py:62`: `claude_review = (reviews or {}).get("claudeCode")` → `claude_review = get_review(reviews, "internalReview")` (note `or {}` is no longer needed because `get_review` handles None)
- `workflow.py:177`: `claude_review=(reviews or {}).get("claudeCode")` → `claude_review=get_review(reviews, "internalReview")`
- `status.py:440`: this one is special — it's part of a fallback chain `ledger.get("claudeModel") or ledger.get("interReviewAgentModel") or ((reviews.get("claudeCode") or {}).get("model"))`. Replace the `reviews.get("claudeCode")` part: `or get_review(reviews, "internalReview").get("model")`.

- [ ] **Step 2: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: 491 passed. Tests that mock `reviews["claudeCode"]` directly still work because `get_review` falls back to the legacy key.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: read reviews.internalReview via get_review (with legacy fallback)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Rename codexCloud reads via get_review

**Files:**
- Modify: same files as Task 2, plus any `codexCloud` read sites

- [ ] **Step 1: Find all codexCloud reads**

```bash
grep -rn 'reviews.*get("codexCloud")\|reviews\[.codexCloud.\]' workflows/code_review/*.py | grep -v test_
```

Apply the same `get_review(reviews, "externalReview")` pattern to each.

- [ ] **Step 2: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: 491 passed.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: read reviews.externalReview via get_review (with legacy fallback)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Rename write sites

**Files:**
- Modify: `workflows/code_review/actions.py` (lines 369, 401, 435 for `claudeCode`; similar for `codexCloud`)
- Modify: `workflows/code_review/reviews.py` (lines 1504, 1531 for `claudeCode`)

- [ ] **Step 1: Replace assignment targets**

`actions.py:369`: `ledger['reviews']['claudeCode'] = build_inter_review_agent_running_review(...)` → `ledger['reviews']['internalReview'] = build_inter_review_agent_running_review(...)`. Also delete any stale `claudeCode` key in the same dict to avoid both keys being live during the read-both window: `ledger['reviews'].pop('claudeCode', None)`.

Same pattern for lines 401, 435 in actions.py.

In reviews.py:1504 and :1531: `"claudeCode": normalize_review(...)` (inside dict literals constructing `reviews`) → `"internalReview": normalize_review(...)`.

For codexCloud writes (search `grep -n '"codexCloud"' workflows/code_review/*.py`): same pattern — replace key in dict literal with `"externalReview"`.

- [ ] **Step 2: Verify reads of the same data still work**

After the write-site rename, code paths that read the same dict in the same tick must read via `get_review` (Tasks 2 + 3 already did this) so the new key is found. Sanity check:

```bash
grep -rn '"claudeCode"\|"codexCloud"' workflows/code_review/*.py | grep -v test_ | grep -v migrations.py
```
Expected: only the in-comment / docstring references remain (no live code).

- [ ] **Step 3: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: 491 passed.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: write reviews.internalReview / reviews.externalReview (drop legacy keys)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Action-type rename + workspace integration

**Files:**
- Modify: `workflows/code_review/actions.py:473`
- Modify: `workflows/code_review/workflow.py` (any `pick_workflow_action` site that emits the literal)
- Modify: `workflows/code_review/workspace.py` — invoke `migrate_persisted_ledger` at bootstrap
- Test: `tests/test_rename_pass_phase_d_1.py` (extend)

- [ ] **Step 1: Add failing tests for the action-type alias**

Append to `tests/test_rename_pass_phase_d_1.py`:

```python
def test_action_dispatcher_accepts_run_internal_review():
    """The dispatcher matches the new literal."""
    # This test is structural — actions.py has a single dispatch site at line 473.
    # We test by reading the source and asserting both literals are matched.
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "workflows/code_review/actions.py"
    text = src.read_text()
    # Expected dispatcher form: action_type in ('run_internal_review', 'run_claude_review')
    assert "run_internal_review" in text
    assert "run_claude_review" in text  # back-compat alias retained


def test_action_dispatcher_accepts_run_claude_review_alias():
    """Same as above, framed from the alias side."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "workflows/code_review/actions.py"
    text = src.read_text()
    # Both literals should appear in the same expression
    assert "'run_internal_review'" in text or '"run_internal_review"' in text
    assert "'run_claude_review'" in text or '"run_claude_review"' in text
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_rename_pass_phase_d_1.py::test_action_dispatcher_accepts_run_internal_review -v
```
Expected: FAIL — `run_internal_review` not in source.

- [ ] **Step 3: Update actions.py:473**

Change:
```python
if action_type == 'run_claude_review':
    return run_inter_review_agent_review_action(...)
```
to:
```python
if action_type in ('run_internal_review', 'run_claude_review'):
    return run_inter_review_agent_review_action(...)
```

- [ ] **Step 4: Update producers**

Search for sites that emit `'run_claude_review'` (likely in `workflow.py` or `pick_workflow_action`):

```bash
grep -n "run_claude_review" workflows/code_review/*.py
```

Producers should now emit `'run_internal_review'`. Consumers continue to accept the alias.

- [ ] **Step 5: Wire migrate_persisted_ledger into workspace bootstrap**

In `workflows/code_review/workspace.py`, after `ledger_path` is determined (around line 583) but before any code reads the ledger, add:

```python
from workflows.code_review.migrations import migrate_persisted_ledger
migrate_persisted_ledger(ledger_path)
```

(Place this immediately after the `ledger_path = ...` assignment.)

- [ ] **Step 6: Run tests**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: 493 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(actions): rename run_claude_review -> run_internal_review (alias retained)

Wire migrate_persisted_ledger into workspace bootstrap so existing
ledgers migrate in-place on first startup. Idempotent on subsequent
runs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Operator docs

**Files:**
- Modify: `skills/operator/SKILL.md`

- [ ] **Step 1: Append a section**

Append to `skills/operator/SKILL.md`:

````markdown
## Persisted-state migration (Phase D-1)

The workflow ledger renames two `reviews.*` keys for provider neutrality:
- `reviews.claudeCode` → `reviews.internalReview`
- `reviews.codexCloud` → `reviews.externalReview`

**Migration is automatic.** On workspace bootstrap, the engine rewrites the persisted ledger in place (atomic temp-file + rename). Idempotent: subsequent boots are no-ops.

**Back-compat reads.** For one release, code paths use a `get_review(reviews, new_key)` helper that falls back to the legacy key if the migration hasn't run yet (e.g., a stale process wrote an old key after migration).

**Action-type literal.** The transient action `run_claude_review` is renamed to `run_internal_review`. The dispatcher accepts both for one release.

**What this means for you:** nothing — the rename is transparent. If you write external tooling that reads the ledger directly (e.g., a dashboard parsing `yoyopod-workflow-status.json`), update it to use `reviews.internalReview` / `reviews.externalReview`.
````

- [ ] **Step 2: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 493 passed.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "docs(operator): document phase D-1 ledger field migration

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Run full suite**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/rename-pass-phase-d-1
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: 493 passed (or 492 + 1 skipped).

- [ ] **Sanity-check: live yoyopod ledger migrates cleanly**

```bash
/usr/bin/python3 -c "
import json, shutil, tempfile
from pathlib import Path
from workflows.code_review.migrations import migrate_persisted_ledger, get_review

src = Path.home() / '.hermes/workflows/yoyopod/memory/yoyopod-workflow-status.json'
with tempfile.TemporaryDirectory() as td:
    dst = Path(td) / 'l.json'
    shutil.copy2(src, dst)
    changed = migrate_persisted_ledger(dst)
    out = json.loads(dst.read_text())
    reviews = out.get('reviews') or {}
    assert 'claudeCode' not in reviews
    assert 'codexCloud' not in reviews
    print('migration:', 'rewrote' if changed else 'no-op')
    print('internalReview present:', 'internalReview' in reviews)
    print('externalReview present:', 'externalReview' in reviews)
    print('rockClaw preserved:', 'rockClaw' in reviews)
"
```

- [ ] **Sanity-check: schema unchanged for live yoyopod**

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

- [ ] **Use superpowers:finishing-a-development-branch.**
