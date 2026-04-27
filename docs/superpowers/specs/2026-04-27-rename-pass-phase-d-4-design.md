# Rename Pass Phase D-4 — Drop D-2 / D-3 Back-Compat Aliases

**Status:** Approved
**Date:** 2026-04-27
**Branch:** `claude/rename-pass-phase-d-4` from main `15056fb`. Baseline 583 tests passing.

## Problem

Phase D-2 added 8 module-level function aliases in `reviews.py` (`fetch_codex_cloud_review = fetch_external_review`, etc.) for one-release back-compat. Phase D-3 added read-time legacy fallbacks in `get_ledger_field` and at `reviews.py:308` (`state_review.get("lastInternalVerdict") or state_review.get("lastClaudeVerdict")`) and at `workspace.py:504` (`review_policy.get("interReviewAgentModel")` fallback). Plus `pop(legacy_key, None)` defensive cleanup calls scattered across actions.py / orchestrator.py / reviews.py / status.py.

The one-release window has elapsed. Drop them. The workflow is single-deployment; live yoyopod is migrated; tests use the new names.

Also: the per-thread `"source": "codexCloud"` label inside `build_external_review_thread` is the last `codex_cloud` string in code-review live data flow. Rename it to `"externalReview"`. Threads are rebuilt from PR data each tick, so old labels self-heal.

## Scope

### In scope (this PR)
1. **Drop 8 D-2 function aliases** (reviews.py lines 1721-1728).
2. **Drop D-3 read-time legacy fallbacks**:
   - `migrations.get_ledger_field` legacy fallback (drop `_LEGACY_LEDGER_KEY_FOR` map and the legacy lookup branch).
   - `reviews.py:308` `state_review.get("lastClaudeVerdict")` fallback.
   - `workspace.py:504` `review_policy.get("interReviewAgentModel")` fallback.
3. **Drop `pop(legacy_key, None)` cleanup calls** added during D-1 / D-3 (defensive only after migration; now redundant).
4. **Rename per-thread `"source": "codexCloud"` to `"externalReview"`** in `build_external_review_thread` (reviews.py:522).
5. **Tests**: alias-removed tests, fallback-removed tests, `"source": "externalReview"` assertion.
6. **Operator docs note** in `skills/operator/SKILL.md`.

### Explicitly KEPT
- `migrations.LEDGER_KEY_RENAMES` and `migrate_top_level_keys` migration logic — runs idempotently on bootstrap; useful for restored backups.
- `migrations.REVIEW_KEY_RENAMES` (D-1) — same reason.
- `migrations.LEDGER_KEYS_TO_DROP = {"claudeModel"}` — same reason.

### Out of scope (D-5, later)
- Lane-state nested field renames (`lastClaudeReviewedHeadSha` → `lastInternalReviewedHeadSha`, `localClaudeReviewCount` → `localInternalReviewCount`). Requires lane-state migration parallel to the ledger migration.
- Cosmetic variable renames (`claude_review`, `codex_review`, etc.).

## Architecture

### Drop function aliases
Delete lines 1721-1728 of `reviews.py`. External callers that imported via the old name now hit `ImportError` — should not exist in this codebase (verified; only consumers were workspace shims, all renamed in D-2/D-3).

### Drop migrations.get_ledger_field fallback
Simplify to:
```python
def get_ledger_field(ledger: dict | None, new_key: str):
    return (ledger or {}).get(new_key)
```
Drop `_LEGACY_LEDGER_KEY_FOR` constant.

### Drop reviews.py:308 fallback
```python
latest_verdict = state_review.get("lastInternalVerdict")
```
(Drop the `or state_review.get("lastClaudeVerdict")` tail.)

### Drop workspace.py:504 fallback
```python
review_policy.get("internalReviewerModel")
or "claude-sonnet-4-6"
```
(Drop the `interReviewAgentModel` fallback in the chain.)

### Drop pop calls
Remove all of:
- `actions.py`: `ledger.pop('claudeModel', None)`, `ledger.pop('interReviewAgentModel', None)` (3 occurrences each)
- `orchestrator.py`: `ledger.pop("codexCloudAutoResolved", None)` (2 occurrences)
- `reviews.py`: `ledger.pop("claudeRepairHandoff", None)`, `ledger.pop("codexCloudRepairHandoff", None)`
- `status.py`: `ledger.pop("claudeModel", None)`, `ledger.pop("interReviewAgentModel", None)`

### Rename per-thread source
`reviews.py:522`: `"source": "codexCloud"` → `"source": "externalReview"`.

Tests that assert on `t.get("source") == "codexCloud"` (e.g., in `synthesize_repair_brief` thread iteration) need to be updated to `"externalReview"`.

## Migration safety
- D-1 / D-3 migrations have run on the live yoyopod ledger (verified). Old keys are gone.
- A restored-backup scenario where a ledger has old keys: `migrate_persisted_ledger` rewrites them on bootstrap before any read. Read-time fallbacks were defense-in-depth; the migration is the actual mechanism.
- Per-thread `source` rename: threads are rebuilt from GitHub PR data each tick (`fetch_external_review` rebuilds the threads array from scratch — verified at reviews.py:842). No stale `"codexCloud"` source values persist past one tick.

## Tests

New file `tests/test_rename_pass_phase_d_4.py`:

**Aliases dropped:**
- `test_fetch_codex_cloud_review_alias_dropped` — `from workflows.code_review.reviews import fetch_codex_cloud_review` raises `ImportError`.
- Same for the other 7 D-2 aliases.

**Fallbacks dropped:**
- `test_get_ledger_field_no_legacy_fallback` — `get_ledger_field({"interReviewAgentModel": "x"}, "internalReviewerModel")` returns None.
- `test_reviews_py_no_lastClaudeVerdict_fallback` — structural source-read assertion.

**Per-thread source:**
- `test_build_external_review_thread_uses_external_review_source` — `build_external_review_thread(...)["source"] == "externalReview"`.

Existing tests that asserted on the dropped aliases or per-thread `"codexCloud"` source need to be updated/removed:
- `tests/test_rename_pass_phase_d_2.py` — 8 alias-equivalence tests must REMOVE.
- `tests/test_workflows_code_review_reviews.py` — any assertion on `t.get("source") == "codexCloud"` must update to `"externalReview"`.

Target: 583 + ~10 new − 8 alias-equivalence tests = ~585 passing.

## Risks
- Test churn from removing alias-equivalence tests + updating source-label assertions. Mechanical.
- No runtime behavior change for live yoyopod (already-migrated state).
