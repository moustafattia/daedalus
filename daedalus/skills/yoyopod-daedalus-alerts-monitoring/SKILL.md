---
name: yoyopod-daedalus-alerts-monitoring
description: Run the YoYoPod Daedalus outage alert cron job, handle script/plugin breakage, and apply the job's strict send-and-dedupe contract.
---
# YoYoPod Daedalus alerts monitoring

Use this skill for the scheduled job that monitors YoYoPod Daedalus for runtime outages and sends deduplicated Telegram alerts.

## Goal
Run one decision tick, send exactly one Telegram message only when the decision demands it, and persist the new state only after delivery succeeds.

## Inputs
- Workflow root: `/home/radxa/.hermes/workflows/yoyopod`
- State file: `/home/radxa/.hermes/workflows/yoyopod/memory/daedalus-alert-state.json`
- Decision script: `/home/radxa/.hermes/workflows/yoyopod/scripts/daedalus_alerts.py`
- Telegram target: `telegram:YoYoPod Hermes Alerts (group)`

## Procedure
1. Run:
   ```bash
   python3 /home/radxa/.hermes/workflows/yoyopod/scripts/daedalus_alerts.py --json
   ```
2. Parse the JSON result and read:
   - `decision.should_alert`
   - `decision.should_resolve`
   - `state_path`
3. If `should_alert` is true:
   - send exactly one Telegram message to `telegram:YoYoPod Hermes Alerts (group)` using `decision.message`
   - only if delivery succeeds and is not skipped, write `decision.next_state_on_alert` as JSON to `state_path`
   - finish with a short local summary
4. Else if `should_resolve` is true:
   - send exactly one Telegram message to `telegram:YoYoPod Hermes Alerts (group)` using `decision.resolution_message`
   - only if delivery succeeds and is not skipped, write `decision.next_state_on_resolve` as JSON to `state_path`
   - finish with a short local summary
5. Else finish with exactly `NO_REPLY`.

## Dedupe and delivery rules
- Treat skipped duplicate delivery as not sent.
- Do not update the state file unless Telegram delivery actually succeeded and was not skipped.
- Do not invent new alert conditions; the script already decides whether the runtime is unhealthy or recovered.
- Do not send status chatter when nothing changed.
- Do not modify workflow code or cron jobs from this alert job.

## Failure handling
If the decision script fails because the Daedalus plugin path is missing or broken, inspect the live Daedalus state directly instead of guessing.

Suggested checks:
```bash
python3 - <<'PY'
import sqlite3, json
path='/home/radxa/.hermes/workflows/yoyopod/state/daedalus/daedalus.db'
conn=sqlite3.connect(path)
conn.row_factory=sqlite3.Row
cur=conn.cursor()
cur.execute('SELECT * FROM daedalus_runtime')
print(json.dumps([dict(r) for r in cur.fetchall()], indent=2))
cur.execute('SELECT * FROM leases ORDER BY lease_scope, lease_key')
print(json.dumps([dict(r) for r in cur.fetchall()], indent=2))
conn.close()
PY
```

Interpretation:
- `daedalus_runtime.runtime_status == "running"` plus a valid `primary-orchestrator` lease generally means no outage alert is warranted.
- If state is healthy and the script is broken, return `NO_REPLY` rather than inventing an alert.
- If Telegram delivery returns `Platform 'telegram' is not configured`, treat that as a runtime credential issue, not a sent alert. Retry only in a proper Hermes gateway runtime; do not advance notifier state on that failure.
- In cron/API environments where the direct `send_message` tool is absent, use the Hermes CLI messaging surface instead:
  - list targets with `hermes chat -Q -t messaging --query "Call send_message(action='list') and return only the matching Telegram targets."`
  - send exactly one message with `hermes chat -Q -t messaging --query "Send exactly one Telegram message to telegram:... with the exact text ..."`
  - treat the run as successful only if the CLI returns `sent`; a duplicate/skip is not sent and must not advance state.

## Verification
Before finishing, confirm:
- the script output was parsed correctly
- if a message was sent, the state file was updated only after successful delivery
- if nothing changed, the final output is exactly `NO_REPLY`
