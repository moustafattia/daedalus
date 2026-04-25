---
name: daedalus-hardening-slices
description: Follow-up workflow for Hermes Relay reliability hardening.
---

# Hermes Relay hardening slices

Use this when continuing Hermes Relay reliability work around stalled dispatches, recovery semantics, schema discipline, or alert state persistence.

## Workflow

1. **Inspect live state before editing anything**
   - Check repo status.
   - Inspect the live relay runtime, doctor, and alerts output for the active workflow root.
   - Confirm whether a parallel agent or prior change already handled part of the hardening slice.

2. **Locate the real implementation points**
   - `runtime.py` for schema, lane actions, recovery, reaping, and runtime status.
   - `tools.py` for doctor/report exposure.
   - `alerts.py` for alert decisioning and state persistence.
   - Tests in `tests/test_runtime_tools_alerts.py` or adjacent files.

3. **Implement slices in this order**
   - Dispatch reaper for stale `status='dispatched'` lane actions.
   - Separate same-action `retry_count` from recovery/restart attempt counting.
   - Scope stalled recovery queries by lane before applying `LIMIT`.
   - Bump schema version only with an explicit migration path and migration record.
   - Persist alert state only after an explicit successful-delivery contract.

4. **Expose the behavior in doctor**
   - Add a doctor check for any newly operational risk.
   - Include actionable counts and any synthetic failure class such as `dispatcher_lost`.

5. **Verify after edits**
   - Run targeted tests for runtime/tools/alerts.
   - Re-check the live command outputs if the workflow root is available.
   - Confirm the schema version and migration state match the code.

## Good patterns

- Use a synthetic failure class like `dispatcher_lost` for reaped dispatched actions.
- Keep `retry_count` semantically narrow: same-action retry series only.
- Use a separate recovery counter for restart/recovery series and recovery idempotency.
- Prefer lane-scoped SQL filters before `LIMIT`; filtering after `LIMIT` is a bug farm.
- Persist alert state with an explicit delivery result, not a blind local write.
- Treat schema version bumps as first-class migrations, not incidental constants.

## Pitfalls

- Do not trust a healthy runtime status alone; stale dispatched actions can still hide underneath.
- Do not reuse one counter for both retries and recovery attempts.
- Do not add a new operational condition without surfacing it in doctor.
- Do not write alert state just because a decision exists; require a successful delivery outcome.
- Do not bump schema version without writing the corresponding migration path and test.

## Verification checklist

- `runtime.py` schema version matches the database migration path.
- New dispatched-action reaper is exercised by a test.
- Doctor reports stuck dispatched actions when present.
- Recovery retry tests distinguish `retry_count` from `recovery_attempt_count`.
- Stalled recovery query is lane-scoped before `LIMIT`.
- Alert persistence is gated on delivery success.
