# Rename Pass Phase D-4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Drop D-2 function aliases + D-3 read-time legacy fallbacks; rename per-thread `"source": "codexCloud"` → `"externalReview"`. Migration helpers stay intact.

**Spec:** `docs/superpowers/specs/2026-04-27-rename-pass-phase-d-4-design.md`

**Worktree:** `/home/radxa/WS/hermes-relay/.claude/worktrees/rename-pass-phase-d-4` from main `15056fb`. Baseline 583 passing. Use `/usr/bin/python3`.

---

## Task 1: Drop D-2 function aliases + per-thread source rename

**Files:**
- Modify: `workflows/code_review/reviews.py`
- Test: `tests/test_rename_pass_phase_d_4.py` (new)
- Update existing: `tests/test_rename_pass_phase_d_2.py` (remove 8 alias-equivalence tests)

- [ ] **Step 1: Write failing tests**

Create `tests/test_rename_pass_phase_d_4.py`:

```python
"""Phase D-4 tests: drop D-2/D-3 aliases + per-thread source rename."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("name", [
    "fetch_codex_cloud_review",
    "summarize_codex_cloud_review",
    "build_codex_cloud_thread",
    "should_dispatch_codex_cloud_repair_handoff",
    "codex_cloud_placeholder",
    "build_codex_cloud_repair_handoff_payload",
    "record_codex_cloud_repair_handoff",
    "fetch_codex_pr_body_signal",
])
def test_codex_cloud_alias_dropped(name):
    """All 8 Phase D-2 module-level aliases should be gone."""
    from workflows.code_review import reviews
    assert not hasattr(reviews, name), f"{name} alias should have been removed"


def test_build_external_review_thread_uses_externalReview_source():
    """Per-thread source label is provider-neutral after D-4."""
    from workflows.code_review.reviews import build_external_review_thread

    out = build_external_review_thread(
        node={"id": "T1", "isResolved": False, "isOutdated": False, "path": "a.py", "line": 1},
        comment={"body": "x", "url": "https://x", "createdAt": "2026-01-01T00:00:00Z"},
        severity="minor", summary="x",
        pr_signal=None, signal_epoch=None, comment_epoch=None,
    )
    assert out["source"] == "externalReview"
```

- [ ] **Step 2: Verify failure**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/rename-pass-phase-d-4
/usr/bin/python3 -m pytest tests/test_rename_pass_phase_d_4.py -v
```
Expected: aliases still present + source still "codexCloud" → tests fail.

- [ ] **Step 3: Drop the 8 D-2 aliases**

In `workflows/code_review/reviews.py`, delete lines 1721-1728 (the eight `<old> = <new>` lines and any preceding comment block referencing them).

- [ ] **Step 4: Rename per-thread source**

In `workflows/code_review/reviews.py:522`, change `"source": "codexCloud",` to `"source": "externalReview",`.

- [ ] **Step 5: Remove now-redundant tests in `test_rename_pass_phase_d_2.py`**

Delete the 8 alias-equivalence tests (`test_fetch_external_review_aliased`, `test_summarize_external_review_aliased`, etc.) — they assert the aliases exist, which is no longer true.

If the file becomes empty after the deletions, leave a single sentinel test or delete the file entirely. Pragmatic choice: delete the file.

- [ ] **Step 6: Update other tests asserting on `"source": "codexCloud"`**

```bash
grep -rn '"source": "codexCloud"\|t\.get("source") == "codexCloud"\|source == "codexCloud"' tests/
```
Update each to `"externalReview"`. The Phase D-2 dropped-alias tests at `test_synthesize_repair_brief_no_longer_routes_codex_cloud_key` use the codexCloud source as a fixture to test that the production path doesn't route legacy data — UPDATE that test (or its fixture) to also reflect the new source label, OR remove the test if its scenario is no longer reproducible.

- [ ] **Step 7: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: ~575 passing (583 baseline + 9 new tests − 8 dropped tests − ~9 alias-equivalence tests already in test_rename_pass_phase_d_2.py).

If the count is far off, debug — don't loosen assertions.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(reviews): drop D-2 function aliases + rename per-thread source

Drops the 8 module-level aliases added in Phase D-2:
  fetch_codex_cloud_review, summarize_codex_cloud_review,
  build_codex_cloud_thread, should_dispatch_codex_cloud_repair_handoff,
  codex_cloud_placeholder, build_codex_cloud_repair_handoff_payload,
  record_codex_cloud_repair_handoff, fetch_codex_pr_body_signal.

The one-release back-compat window has elapsed. All in-tree callers
already use the external_review names.

Also renames the per-thread \"source\" field from \"codexCloud\" to
\"externalReview\" — threads are rebuilt from GitHub PR data each
tick, so old labels self-heal on the next fetch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Drop D-3 read-time legacy fallbacks

**Files:**
- Modify: `workflows/code_review/migrations.py`
- Modify: `workflows/code_review/reviews.py` (line 308)
- Modify: `workflows/code_review/workspace.py` (line ~504)
- Test: `tests/test_rename_pass_phase_d_4.py` (extend)

- [ ] **Step 1: Append failing tests**

```python
def test_get_ledger_field_no_legacy_fallback():
    """D-3 fallback to legacy keys is dropped after D-4."""
    from workflows.code_review.migrations import get_ledger_field
    assert get_ledger_field({"interReviewAgentModel": "x"}, "internalReviewerModel") is None
    assert get_ledger_field({"claudeRepairHandoff": {"v": 1}}, "internalReviewRepairHandoff") is None


def test_reviews_no_lastClaudeVerdict_fallback():
    """reviews.py:308 should read only the new key after D-4."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "workflows/code_review/reviews.py").read_text()
    # The fallback `or state_review.get("lastClaudeVerdict")` should be gone.
    assert 'state_review.get("lastClaudeVerdict")' not in src


def test_workspace_no_interReviewAgentModel_fallback():
    """workspace.py review_policy fallback should not include the legacy key after D-4."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "workflows/code_review/workspace.py").read_text()
    # The fallback `or review_policy.get("interReviewAgentModel")` should be gone.
    assert 'review_policy.get("interReviewAgentModel")' not in src
```

- [ ] **Step 2: Drop the migrations.get_ledger_field fallback**

In `workflows/code_review/migrations.py`, simplify `get_ledger_field`:

```python
def get_ledger_field(ledger: dict | None, new_key: str):
    """Read a top-level ledger field. Returns None if absent."""
    return (ledger or {}).get(new_key)
```

The `_LEGACY_LEDGER_KEY_FOR` constant can stay (unused but harmless) OR be removed. Suggest removing for clarity.

- [ ] **Step 3: Drop the reviews.py:308 fallback**

Find:
```python
latest_verdict = state_review.get("lastInternalVerdict") or state_review.get("lastClaudeVerdict")
```
Change to:
```python
latest_verdict = state_review.get("lastInternalVerdict")
```

- [ ] **Step 4: Drop the workspace.py:504 fallback**

Find the chain:
```python
review_policy.get("internalReviewerModel")
or review_policy.get("interReviewAgentModel")
or "claude-sonnet-4-6"
```
Change to:
```python
review_policy.get("internalReviewerModel")
or "claude-sonnet-4-6"
```

- [ ] **Step 5: Run target + full suite**

```bash
/usr/bin/python3 -m pytest tests/test_rename_pass_phase_d_4.py -v
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```

If tests fail because they fed legacy keys that previously fell back through `get_ledger_field`, update them to use new keys.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: drop D-3 read-time legacy-key fallbacks

The one-release window has elapsed and migrate_persisted_ledger has
already rewritten the live ledger to new keys. Read sites no longer
fall back to the legacy keys in three places:

- migrations.get_ledger_field: drops legacy lookup branch
- reviews.py:308: lane-state lastInternalVerdict only
- workspace.py:504: review_policy.internalReviewerModel only

migration logic (LEDGER_KEY_RENAMES, migrate_top_level_keys) stays
intact — runs idempotently on bootstrap, useful for restored backups.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Drop redundant pop(legacy_key, None) cleanup calls

**Files:**
- Modify: `workflows/code_review/actions.py`
- Modify: `workflows/code_review/orchestrator.py`
- Modify: `workflows/code_review/reviews.py`
- Modify: `workflows/code_review/status.py`

- [ ] **Step 1: Find all pop calls**

```bash
grep -rn 'pop("claudeRepairHandoff"\|pop("codexCloudRepairHandoff"\|pop("codexCloudAutoResolved"\|pop("interReviewAgentModel"\|pop("claudeModel"\|pop(.claudeCode.\|pop(.codexCloud.\|pop(.claudeModel.\|pop(.interReviewAgentModel.' workflows/code_review/*.py
```

- [ ] **Step 2: Delete each pop line**

For each match, delete the line. These were defensive cleanup after the new-key write; with migration in place, they're no-ops at best, and clutter at worst.

Specifically:
- `actions.py:383, 420, 444`: `ledger.pop('interReviewAgentModel', None)` — DELETE.
- `actions.py:382, 419, 443`: `ledger.pop('claudeModel', None)` — DELETE.
- `orchestrator.py:496, 515`: `ledger.pop("codexCloudAutoResolved", None)` — DELETE.
- `reviews.py:1391`: `ledger.pop("claudeRepairHandoff", None)` — DELETE.
- `reviews.py:1453`: `ledger.pop("codexCloudRepairHandoff", None)` — DELETE.
- `status.py:551`: `ledger.pop("interReviewAgentModel", None)` — DELETE.
- `status.py` (similar `ledger.pop("claudeModel", None)` if present) — DELETE.
- Any remaining `pop("claudeCode", None)` / `pop("codexCloud", None)` in actions.py for the reviews dict — DELETE.

- [ ] **Step 3: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```
Expected: same passing count.

- [ ] **Step 4: Sanity grep**

```bash
grep -rn '"claudeCode"\|"codexCloud"\|"claudeModel"\|"interReviewAgentModel"\|"claudeRepairHandoff"\|"codexCloudRepairHandoff"\|"codexCloudAutoResolved"\|"lastClaudeVerdict"' workflows/code_review/*.py | grep -v test_ | grep -v migrations.py | grep -v 'class.*\:'
```
Expected: no live code matches (only docstrings/comments at most).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: drop redundant pop(legacy_key) cleanup calls

These were defensive cleanups after new-key writes during the D-1 /
D-3 transition windows. With migration in place and the live ledger
already rewritten, they're no-ops. Removing for clarity.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Operator docs

**Files:**
- Modify: `skills/operator/SKILL.md`

- [ ] **Step 1: Append section**

```markdown
## Deprecation cleanup round 2 (Phase D-4)

The Phase D-2 / D-3 one-release back-compat aliases have been removed:
- 8 D-2 module-level function aliases in `workflows/code_review/reviews.py` (`fetch_codex_cloud_review`, etc.) — gone. Use the `external_review` names.
- D-3 read-time legacy-key fallbacks in `get_ledger_field`, `reviews.py:308`, `workspace.py:504` — gone. Live ledgers were migrated by the D-3 bootstrap; restored backups still get migrated automatically before any read.
- Per-thread `"source": "codexCloud"` review-thread label is now `"externalReview"`. Threads are rebuilt from GitHub data each tick, so old labels self-heal.

Migration helpers (`migrate_review_keys`, `migrate_top_level_keys`, `migrate_persisted_ledger`) remain — they run idempotently on bootstrap and protect against stale state from backups.
```

- [ ] **Step 2: Run full suite + commit**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
git add -A
git commit -m "docs(operator): note Phase D-4 deprecation cleanup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/rename-pass-phase-d-4
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```

Live yoyopod ledger smoke test (should be no-op since migrations already ran):
```bash
/usr/bin/python3 -c "
from pathlib import Path
from workflows.code_review.migrations import migrate_persisted_ledger
src = Path.home() / '.hermes/workflows/yoyopod/memory/yoyopod-workflow-status.json'
print('migration result:', migrate_persisted_ledger(src))  # False = no-op
"
```
