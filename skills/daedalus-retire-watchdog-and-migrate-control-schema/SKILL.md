---
name: daedalus-retire-watchdog-and-migrate-control-schema
description: Remove legacy watchdog/cutover concepts from Hermes Relay, switch to relay-only active execution control, and migrate the live SQLite control table cleanly.
---

Use this when working on the Hermes Relay repo at `~/WS/hermes-relay` and the goal is to fully retire legacy watchdog/cutover ownership logic.

When this applies
- The repo still mentions `watchdog`, `cutover`, `legacy-watchdog`, `desired_owner`, or `require_watchdog_paused`.
- The live Relay DB still has the old `ownership_controls` schema.
- You want the codebase and live system to use relay-only active execution control.

Steps
1. Search the repo first.
   - Search `.py` and `.md` for:
     - `watchdog`
     - `cutover`
     - `legacy-watchdog`
     - `desired_owner`
     - `require_watchdog_paused`

2. Simplify runtime control semantics in `runtime.py`.
   - Remove watchdog/legacy-owner constants and logic.
   - Keep Relay as the only primary owner.
   - Replace ownership-switch semantics with a simple execution gate:
     - `active_execution_enabled`
     - `primary_owner = relay`
   - Replace CLI/runtime commands:
     - `ownership-status` -> `active-gate-status`
     - `set-ownership` -> `set-active-execution --enabled true|false`

3. Migrate the legacy control table to the clean canonical table.
   - Desired final canonical table name:
     - `execution_controls`
   - Desired final schema:
     - `control_id`
     - `active_execution_enabled`
     - `updated_at`
     - `metadata_json`
   - Important: keep `control_id = "primary"` so existing data migrates in place instead of creating a second row.
   - Implement a real migration in `init_relay_db()`:
     - if `execution_controls` already exists with the expected schema, use it and drop leftover `ownership_controls` if present
     - if only `ownership_controls` exists, copy `control_id`, `active_execution_enabled`, `updated_at`, and `metadata_json` into `execution_controls`, then drop `ownership_controls`
     - if neither exists, create `execution_controls`
   - Do not leave compatibility shims around once the migration exists.

4. Update operator surface in `tools.py`.
   - Replace cutover commands with:
     - `/relay active-gate-status`
     - `/relay set-active-execution --enabled true|false`
   - Update summaries/rendering to stop mentioning watchdog/cutover/desired owner.

5. Update `alerts.py`.
   - Replace `cutover` snapshot key with `active_gate`.
   - Replace issue code `cutover_gate` with `active_execution_gate`.
   - Remove watchdog text from alert and recovery messages.

6. Update docs and local operator notes.
   - Update:
     - `README.md`
     - `docs/architecture.md`
     - `docs/operator-cheat-sheet.md`
     - `skills/operator/SKILL.md`
   - Remove watchdog/cutover language completely.

7. Add tests.
   - Add a regression test that starts with the old SQLite `ownership_controls` table and verifies migration to the clean schema.
   - Update tests for the renamed command surface and alert snapshot key.

8. Verify in this order.
   - `pytest -q` in `~/WS/hermes-relay`
   - Run live gate check:
     - `python3 runtime.py active-gate-status --workflow-root ~/.hermes/workflows/yoyopod --json`
   - Inspect live DB schema and tables:
     - `SELECT name FROM sqlite_master WHERE type='table' ORDER BY name`
     - `PRAGMA table_info(execution_controls)`
   - Optionally rewrite the live control row through the runtime command after migration:
     - `python3 runtime.py set-active-execution --workflow-root ~/.hermes/workflows/yoyopod --enabled true --json`
   - Verify repo is clean of legacy language:
     - search `.py` and `.md` again for the legacy terms above

Expected live result
- `active-gate-status` should report:
  - `allowed: true`
  - `primary_owner: relay`
  - `execution.active_execution_enabled: true`
- The live DB should contain `execution_controls` and no `ownership_controls` table.
- `execution_controls` should have exactly these columns:
  - `control_id`
  - `active_execution_enabled`
  - `updated_at`
  - `metadata_json`

Pitfalls
- If you change the control id from `primary`, you can accidentally leave two control rows and create a fake clean state.
- If you only hide old columns in code without migrating the DB, the structure is still dirty.
- If you keep compatibility shims after the migration exists, the codebase stays conceptually polluted.
- Update docs and tests in the same pass, or the repo will lie about the operator surface.
