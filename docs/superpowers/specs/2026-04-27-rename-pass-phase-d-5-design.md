# Rename Pass Phase D-5 — Lane-State Field Migration

**Status:** Approved
**Date:** 2026-04-27
**Branch:** `claude/rename-pass-phase-d-5` from main `a24fd46`. Baseline 581 tests passing.

## Problem

Two `Claude*` fields persist in lane-state, nested at `ledger.implementation.laneState.review.<key>`:
- `lastClaudeReviewedHeadSha`
- `localClaudeReviewCount`

These are the last operator-visible legacy names in the persisted ledger. Phases D-1/D-3 migrated top-level keys (`reviews.*`, `claudeRepairHandoff`, etc.); D-5 finishes the cleanup at the lane-state nesting depth.

## Scope

### In scope
1. **Lane-state field renames** with auto-migration:
   - `lastClaudeReviewedHeadSha` → `lastInternalReviewedHeadSha`
   - `localClaudeReviewCount` → `localInternalReviewCount`

2. **Migration extension** in `workflows/code_review/migrations.py`:
   - New `LANE_STATE_REVIEW_KEY_RENAMES` dict
   - New `migrate_lane_state_review_keys(ledger)` helper that walks the nested path
   - `migrate_persisted_ledger` runs all three migrations (review keys, top-level keys, lane-state keys)
   - New `get_lane_state_review_field(state_review, new_key)` helper with one-release legacy fallback

3. **Source-code updates**:
   - `reviews.py:284-285`: read `localInternalReviewCount` / `lastInternalReviewedHeadSha` via `get_lane_state_review_field`
   - `sessions.py:130`: same pattern
   - `status.py:926, 928`: write new keys + `existing.get("review", {})` reads via the helper

### Out of scope (D-6)
- 73 cosmetic variable-name references (`claude_review`, `existing_claude_review`, `previous_claude_review`, `codex_review`, `codex_cloud_review`)

## Architecture

### Migration helper (nested-aware)
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
    LANE_STATE_REVIEW_KEY_RENAMES. Returns (ledger, was_changed).
    """
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
    """Read lane-state review field by new key with legacy fallback for one release."""
    state_review = state_review or {}
    if new_key in state_review:
        return state_review[new_key]
    legacy = _LEGACY_LANE_STATE_REVIEW_KEY_FOR.get(new_key)
    if legacy and legacy in state_review:
        return state_review[legacy]
    return None
```

### Read sites
- `reviews.py:284`: `int(state_review.get("localClaudeReviewCount") or 0)` → `int(get_lane_state_review_field(state_review, "localInternalReviewCount") or 0)`
- `reviews.py:285`: `state_review.get("lastClaudeReviewedHeadSha")` → `get_lane_state_review_field(state_review, "lastInternalReviewedHeadSha")`
- `sessions.py:130`: `int(review_state.get("localClaudeReviewCount") or 0)` → same pattern

### Write sites
- `status.py:926`: dict-literal output key `"lastClaudeReviewedHeadSha"` → `"lastInternalReviewedHeadSha"`. Inner `existing.get("review", {}).get("lastClaudeReviewedHeadSha")` → use `get_lane_state_review_field` for legacy fallback.
- `status.py:928`: dict-literal output key `"localClaudeReviewCount"` → `"localInternalReviewCount"`.

## Tests

New file `tests/test_rename_pass_phase_d_5.py`:
- `test_migrate_lane_state_review_keys_renames_legacy`
- `test_migrate_lane_state_review_keys_handles_missing_path` — ledger without `implementation` / `laneState` / `review`.
- `test_migrate_lane_state_review_keys_idempotent`
- `test_migrate_lane_state_review_keys_new_key_wins_when_both_present`
- `test_migrate_persisted_ledger_runs_all_three_migrations` — combined behavior on a ledger with all three legacy shapes.
- `test_get_lane_state_review_field_returns_new_when_present`
- `test_get_lane_state_review_field_falls_back_to_legacy`
- `test_get_lane_state_review_field_returns_none_for_unknown_key`
- `test_existing_yoyopod_ledger_lane_state_migration` — live ledger smoke test.

Target: 581 + 9 new = 590 passing.

## Risks
- Nested-path migration: if `implementation` or `laneState` is missing/wrong type, helper returns `(ledger, False)` cleanly. Standard pattern.
- Status output shape change: external tooling that parsed `lastClaudeReviewedHeadSha` / `localClaudeReviewCount` from `yoyopod-workflow-status.json` should switch to new names. Operator doc updated.
