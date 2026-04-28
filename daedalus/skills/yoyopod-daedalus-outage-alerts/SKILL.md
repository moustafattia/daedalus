---
name: yoyopod-daedalus-outage-alerts
description: Monitor YoYoPod Daedalus runtime outages, deduplicate Telegram alerts, and only advance alert state after confirmed delivery.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [yoyopod, daedalus, alerts, telegram, cron]
---

# YoyoPod Daedalus outage alerts

Use this skill for the Hermes-owned cron job that monitors YoYoPod Daedalus runtime outages and sends deduplicated Telegram alerts or recovery notices.

## Inputs
- Workflow root: `/home/radxa/.hermes/workflows/yoyopod`
- Alert script: `/home/radxa/.hermes/workflows/yoyopod/scripts/daedalus_alerts.py`
- State file: `/home/radxa/.hermes/workflows/yoyopod/memory/daedalus-alert-state.json`
- Telegram target: `telegram:YoYoPod Hermes Alerts (group)`

## Normal flow
1. Run:
   ```bash
   python3 /home/radxa/.hermes/workflows/yoyopod/scripts/daedalus_alerts.py --json
   ```
2. Parse the JSON result.
3. Read:
   - `decision.should_alert`
   - `decision.should_resolve`
   - `decision.message`
   - `decision.resolution_message`
   - `state_path`
4. If `should_alert` is true, send exactly one Telegram alert message.
5. If `should_resolve` is true, send exactly one Telegram recovery message.
6. Only write the next state after Telegram delivery succeeds and was not skipped.
7. If neither flag is true, return the required no-op response.

## State update rule
Never advance `/home/radxa/.hermes/workflows/yoyopod/memory/daedalus-alert-state.json` unless the Telegram message was actually delivered.
- A skipped duplicate counts as not sent.
- A failed send counts as not sent.
- Do not invent fallback state transitions.

## Alert semantics
Trust the decision script. Do not re-evaluate outage worthiness yourself.
The script already encodes dedupe and alert/recovery selection.

Critical paging semantics are stricter than they look:
- Page only when a doctor check is both `severity=critical` and `status=fail`.
- Do not page on `severity=critical` + `status=warn` alone.
- This keeps alert behavior aligned with doctor `overall_status` instead of escalating every critical-severity warning.

## Real-world failure mode
The alert script can fail because it tries to load a missing plugin file:

`/home/radxa/.hermes/workflows/yoyopod/.hermes/plugins/daedalus/daedalus_control.py`

Observed failure mode:
- `python3 /home/radxa/.hermes/workflows/yoyopod/scripts/daedalus_alerts.py --json`
- exits with `FileNotFoundError` for that path

When that happens, do not assume the workflow is down. Verify the live Daedalus runtime directly from the host.

Fallback inspection path:
1. Use the Daedalus runtime script directly, bypassing the missing alert wrapper import:
   - `python3 /home/radxa/.hermes/workflows/yoyopod/scripts/daedalus.py status --workflow-root /home/radxa/.hermes/workflows/yoyopod --json`
   - `python3 /home/radxa/.hermes/workflows/yoyopod/scripts/daedalus.py ownership-status --workflow-root /home/radxa/.hermes/workflows/yoyopod --json`
   These are the reliable source for current runtime health and ownership when the alert wrapper cannot import `daedalus_control.py`.
2. If the decision script says to alert or resolve, still honor the dedupe/state-write rules exactly; the fallback only replaces status collection, not alert semantics.
3. If the Telegram target name ever needs resolving, use the messaging directory first (`send_message(action='list')`) and then send exactly one message to the resolved target.
4. Cross-check the wrapper state files only as secondary evidence:
   - `/home/radxa/.hermes/workflows/yoyopod/memory/yoyopod-workflow-status.json`
   - `/home/radxa/.hermes/workflows/yoyopod/memory/yoyopod-workflow-health.json`
   - `/home/radxa/.hermes/workflows/yoyopod/memory/yoyopod-workflow-audit.jsonl`
5. Base the alert/no-alert decision on the script when it works; use the host-level checks to confirm the runtime is genuinely unhealthy before escalating a missing-plugin failure into an outage.

This is a troubleshooting fallback, not a replacement for the alert script.

## Guardrails
- Do not send extra chatter when nothing changed.
- Do not deliver more than one Telegram message per run.
- Do not update state on skipped duplicate sends.
- Do not modify workflow code or cron jobs from the alert job.
- Do not invent new alert conditions; the decision script is authoritative.

## Verification
Before finishing, confirm:
- `should_alert` / `should_resolve` matches the action taken
- exactly one Telegram message was sent when needed
- state changed only after confirmed delivery
- `NO_REPLY` or equivalent no-op was returned when nothing changed
