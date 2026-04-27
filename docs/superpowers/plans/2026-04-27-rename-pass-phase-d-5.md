# Rename Pass Phase D-5 Implementation Plan

**Goal:** Migrate lane-state nested fields (`lastClaudeReviewedHeadSha`, `localClaudeReviewCount`) at `ledger.implementation.laneState.review.*`. Auto-migration on bootstrap; read-both/write-new for one release.

**Spec:** `docs/superpowers/specs/2026-04-27-rename-pass-phase-d-5-design.md`

**Worktree:** `/home/radxa/WS/hermes-relay/.claude/worktrees/rename-pass-phase-d-5` from main `a24fd46`. Baseline 581 passing. Use `/usr/bin/python3`.

---

## Task 1: Migration extension

**Files:**
- Modify: `workflows/code_review/migrations.py`
- Test: `tests/test_rename_pass_phase_d_5.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_rename_pass_phase_d_5.py`:

```python
"""Phase D-5 tests: lane-state nested field migration."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_migrate_lane_state_review_keys_renames_legacy():
    from workflows.code_review.migrations import migrate_lane_state_review_keys

    ledger = {
        "implementation": {
            "laneState": {
                "review": {
                    "lastClaudeReviewedHeadSha": "abc123",
                    "localClaudeReviewCount": 5,
                    "otherField": "preserved",
                },
            },
        },
    }
    out, changed = migrate_lane_state_review_keys(ledger)
    assert changed is True
    review = out["implementation"]["laneState"]["review"]
    assert review["lastInternalReviewedHeadSha"] == "abc123"
    assert review["localInternalReviewCount"] == 5
    assert review["otherField"] == "preserved"
    assert "lastClaudeReviewedHeadSha" not in review
    assert "localClaudeReviewCount" not in review


def test_migrate_lane_state_review_keys_handles_missing_path():
    from workflows.code_review.migrations import migrate_lane_state_review_keys

    # No implementation block
    out, changed = migrate_lane_state_review_keys({"reviews": {}})
    assert changed is False

    # No laneState
    out, changed = migrate_lane_state_review_keys({"implementation": {}})
    assert changed is False

    # No review
    out, changed = migrate_lane_state_review_keys({"implementation": {"laneState": {}}})
    assert changed is False


def test_migrate_lane_state_review_keys_idempotent():
    from workflows.code_review.migrations import migrate_lane_state_review_keys

    ledger = {
        "implementation": {
            "laneState": {
                "review": {
                    "lastInternalReviewedHeadSha": "abc",
                    "localInternalReviewCount": 1,
                },
            },
        },
    }
    out, changed = migrate_lane_state_review_keys(ledger)
    assert changed is False


def test_migrate_lane_state_review_keys_new_key_wins():
    from workflows.code_review.migrations import migrate_lane_state_review_keys

    ledger = {
        "implementation": {
            "laneState": {
                "review": {
                    "lastClaudeReviewedHeadSha": "old",
                    "lastInternalReviewedHeadSha": "new",
                },
            },
        },
    }
    out, changed = migrate_lane_state_review_keys(ledger)
    assert changed is True  # old key was dropped
    assert out["implementation"]["laneState"]["review"]["lastInternalReviewedHeadSha"] == "new"
    assert "lastClaudeReviewedHeadSha" not in out["implementation"]["laneState"]["review"]


def test_migrate_persisted_ledger_runs_all_three_migrations(tmp_path):
    from workflows.code_review.migrations import migrate_persisted_ledger

    p = tmp_path / "l.json"
    p.write_text(json.dumps({
        "reviews": {"claudeCode": {"v": 1}},
        "claudeRepairHandoff": {"v": 2},
        "implementation": {
            "laneState": {"review": {"lastClaudeReviewedHeadSha": "abc"}},
        },
    }, indent=2))
    migrate_persisted_ledger(p)
    out = json.loads(p.read_text())
    assert out["reviews"]["internalReview"] == {"v": 1}
    assert out["internalReviewRepairHandoff"] == {"v": 2}
    assert out["implementation"]["laneState"]["review"]["lastInternalReviewedHeadSha"] == "abc"


def test_get_lane_state_review_field_returns_new_when_present():
    from workflows.code_review.migrations import get_lane_state_review_field
    assert get_lane_state_review_field({"lastInternalReviewedHeadSha": "x"}, "lastInternalReviewedHeadSha") == "x"


def test_get_lane_state_review_field_falls_back_to_legacy():
    from workflows.code_review.migrations import get_lane_state_review_field
    assert get_lane_state_review_field({"lastClaudeReviewedHeadSha": "x"}, "lastInternalReviewedHeadSha") == "x"
    assert get_lane_state_review_field({"localClaudeReviewCount": 5}, "localInternalReviewCount") == 5


def test_get_lane_state_review_field_returns_none_for_unknown_key():
    from workflows.code_review.migrations import get_lane_state_review_field
    assert get_lane_state_review_field({"x": 1}, "made-up") is None


def test_existing_yoyopod_ledger_lane_state_migration(tmp_path):
    from workflows.code_review.migrations import migrate_persisted_ledger
    src = Path(os.path.expanduser("~/.hermes/workflows/yoyopod/memory/yoyopod-workflow-status.json"))
    if not src.exists():
        pytest.skip("yoyopod ledger not present")
    dst = tmp_path / "l.json"
    dst.write_text(src.read_text())
    migrate_persisted_ledger(dst)
    out = json.loads(dst.read_text())
    review = (out.get("implementation") or {}).get("laneState", {}).get("review") or {}
    assert "lastClaudeReviewedHeadSha" not in review
    assert "localClaudeReviewCount" not in review
```

- [ ] **Step 2: Verify failure**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/rename-pass-phase-d-5
/usr/bin/python3 -m pytest tests/test_rename_pass_phase_d_5.py -v
```

- [ ] **Step 3: Extend `migrations.py`**

Append to `workflows/code_review/migrations.py`:

```python
LANE_STATE_REVIEW_KEY_RENAMES: dict[str, str] = {
    "lastClaudeReviewedHeadSha": "lastInternalReviewedHeadSha",
    "localClaudeReviewCount": "localInternalReviewCount",
}

_LEGACY_LANE_STATE_REVIEW_KEY_FOR: dict[str, str] = {
    v: k for k, v in LANE_STATE_REVIEW_KEY_RENAMES.items()
}


def migrate_lane_state_review_keys(ledger: dict) -> tuple[dict, bool]:
    """Rewrite ledger.implementation.laneState.review.<key> per
    LANE_STATE_REVIEW_KEY_RENAMES. Returns (ledger, was_changed)."""
    impl = ledger.get("implementation")
    if not isinstance(impl, dict):
        return ledger, False
    lane_state = impl.get("laneState")
    if not isinstance(lane_state, dict):
        return ledger, False
    review = lane_state.get("review")
    if not isinstance(review, dict):
        return ledger, False

    changed = False
    for old, new in LANE_STATE_REVIEW_KEY_RENAMES.items():
        if old in review:
            if new not in review:
                review[new] = review[old]
            del review[old]
            changed = True
    return ledger, changed


def get_lane_state_review_field(state_review: dict | None, new_key: str):
    """Read lane-state review field by new key with legacy fallback (one release)."""
    state_review = state_review or {}
    if new_key in state_review:
        return state_review[new_key]
    legacy = _LEGACY_LANE_STATE_REVIEW_KEY_FOR.get(new_key)
    if legacy and legacy in state_review:
        return state_review[legacy]
    return None
```

- [ ] **Step 4: Update `migrate_persisted_ledger` to run all three**

Find the body (currently runs `migrate_review_keys` + `migrate_top_level_keys`). Add a third call:

```python
    _, c1 = migrate_review_keys(ledger)
    _, c2 = migrate_top_level_keys(ledger)
    _, c3 = migrate_lane_state_review_keys(ledger)
    if not (c1 or c2 or c3):
        return False
```

- [ ] **Step 5: Run target + full suite**

```bash
/usr/bin/python3 -m pytest tests/test_rename_pass_phase_d_5.py -v
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 9 in target, 590 total.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(migrations): add lane-state nested-path key migration

Extends migrate_persisted_ledger with migrate_lane_state_review_keys
and get_lane_state_review_field helpers. Renames
ledger.implementation.laneState.review.{lastClaudeReviewedHeadSha,
localClaudeReviewCount} to provider-neutral names.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Read sites + write sites

**Files:**
- Modify: `workflows/code_review/reviews.py` (lines 284-285)
- Modify: `workflows/code_review/sessions.py` (line 130)
- Modify: `workflows/code_review/status.py` (lines 926, 928)

- [ ] **Step 1: Update read sites**

In `workflows/code_review/reviews.py:284-285`, replace:
```python
count = int(state_review.get("localClaudeReviewCount") or 0)
last_head = state_review.get("lastClaudeReviewedHeadSha")
```
with:
```python
count = int(get_lane_state_review_field(state_review, "localInternalReviewCount") or 0)
last_head = get_lane_state_review_field(state_review, "lastInternalReviewedHeadSha")
```

Add `get_lane_state_review_field` to the existing migrations import in reviews.py.

In `workflows/code_review/sessions.py:130`, replace:
```python
local_review_count = int(review_state.get("localClaudeReviewCount") or 0)
```
with:
```python
local_review_count = int(get_lane_state_review_field(review_state, "localInternalReviewCount") or 0)
```

Add `from workflows.code_review.migrations import get_lane_state_review_field` to sessions.py.

- [ ] **Step 2: Update write sites in `status.py`**

At lines 926, 928, the dict-literal builds the lane-state review block. Replace:
```python
"lastClaudeReviewedHeadSha": ((get_review(reviews, "internalReview")).get("reviewedHeadSha")) or ((existing.get("review") or {}).get("lastClaudeReviewedHeadSha")),
...
"localClaudeReviewCount": local_inter_review_agent_review_count((get_review(reviews, "internalReview") or None), existing),
```
with:
```python
"lastInternalReviewedHeadSha": ((get_review(reviews, "internalReview")).get("reviewedHeadSha")) or get_lane_state_review_field(existing.get("review"), "lastInternalReviewedHeadSha"),
...
"localInternalReviewCount": local_inter_review_agent_review_count((get_review(reviews, "internalReview") or None), existing),
```

Add `get_lane_state_review_field` import to status.py.

- [ ] **Step 3: Update `local_inter_review_agent_review_count` callees if needed**

The function (in `reviews.py:281-...`) takes `state_review` and reads `localClaudeReviewCount`. Already addressed in Step 1. But verify the function signature still matches what `status.py:928` passes — it might be passing the full `existing` dict expecting the function to dig.

```bash
grep -n "def local_inter_review_agent_review_count\|def local_claude_review_count" workflows/code_review/reviews.py
```

If the function name says `local_claude_*`, also rename to `local_internal_*`. Add an alias for one release.

- [ ] **Step 4: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: 590 passing. Existing tests asserting on `lastClaudeReviewedHeadSha` / `localClaudeReviewCount` keys in status output need updating — likely in `test_workflows_code_review_adapter_status.py`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: rename lane-state review keys to internal review names

reviews.py / sessions.py read via get_lane_state_review_field
(legacy fallback for one release). status.py writes the new keys
in its lane-state review block.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Operator docs

**Files:**
- Modify: `skills/operator/SKILL.md`

- [ ] **Step 1: Append section**

```markdown
## Lane-state migration (Phase D-5)

Two nested lane-state fields renamed for provider neutrality:
- `ledger.implementation.laneState.review.lastClaudeReviewedHeadSha` → `lastInternalReviewedHeadSha`
- `ledger.implementation.laneState.review.localClaudeReviewCount` → `localInternalReviewCount`

**Migration is automatic** on workspace bootstrap (extends D-1/D-3 mechanism).
**Read-both / write-new** for one release via `get_lane_state_review_field` helper.
**Status output keys also renamed** — external tooling reading `lastClaudeReviewedHeadSha` / `localClaudeReviewCount` from `yoyopod-workflow-status.json` should switch to the new names.
```

- [ ] **Step 2: Commit**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
git add -A
git commit -m "docs(operator): note Phase D-5 lane-state field migration

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/rename-pass-phase-d-5
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 590 passing.

Live yoyopod ledger smoke test:
```bash
/usr/bin/python3 -c "
import json, shutil, tempfile
from pathlib import Path
from workflows.code_review.migrations import migrate_persisted_ledger
src = Path.home() / '.hermes/workflows/yoyopod/memory/yoyopod-workflow-status.json'
with tempfile.TemporaryDirectory() as td:
    dst = Path(td) / 'l.json'
    shutil.copy2(src, dst)
    changed = migrate_persisted_ledger(dst)
    out = json.loads(dst.read_text())
    review = (out.get('implementation') or {}).get('laneState', {}).get('review') or {}
    print('lane-state migrated:', 'lastClaudeReviewedHeadSha' not in review and 'localClaudeReviewCount' not in review)
"
```
