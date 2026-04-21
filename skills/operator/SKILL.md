---
name: operator
description: Operate the YoYoPod Relay project plugin control surface for status checks and shadow-runtime commands.
version: 0.1.0
author: Hermes Agent
license: MIT
---

# Hermes Relay Operator

Use this when the YoYoPod workflow repo-local `hermes-relay` plugin is enabled.

## Enable project plugin discovery

Run Hermes from the YoYoPod workflow root with:

```bash
export HERMES_ENABLE_PROJECT_PLUGINS=true
cd ~/.hermes/workflows/yoyopod
hermes
```

## Available slash command

Inside Hermes sessions:

```text
/relay status
/relay shadow-report
/relay doctor
/relay cutover-status
/relay cutover-switch --owner relay --instance-id relay-operator-1
/relay cutover-switch --owner legacy-watchdog --instance-id relay-operator-1
/relay service-install
/relay service-install --service-mode active
/relay service-status
/relay service-status --service-mode active
/relay service-start
/relay service-start --service-mode active
/relay service-stop
/relay service-stop --service-mode active
/relay service-restart
/relay service-logs --lines 50
/relay service-logs --service-mode active --lines 50
/relay start --instance-id relay-operator-1
/relay heartbeat --instance-id relay-operator-1
/relay iterate-shadow --instance-id relay-operator-1
/relay run-shadow --instance-id relay-operator-1 --max-iterations 1 --json
/relay iterate-active --instance-id relay-operator-1 --json
/relay run-active --instance-id relay-operator-1 --max-iterations 1 --json
```

## Notes

- Default workflow root is the current YoYoPod workflow repo.
- Use `--workflow-root` to point at a different test root.
- Service commands default to the shadow observer profile. Add `--service-mode active` to manage the guarded executor profile (`yoyopod-relay-active.service`).
- `service-install` resolves profile defaults automatically:
  - shadow: `yoyopod-relay-shadow.service` + `relay-shadow-service-1` + `run-shadow`
  - active: `yoyopod-relay-active.service` + `relay-active-service-1` + `run-active`
- `run-shadow` remains shadow-only: it derives and records actions but does not execute ownership side effects.
- `iterate-active` / `run-active` are guarded: they will only execute actions when Relay is marked as the desired primary owner, active execution is enabled, the runtime is in `active` mode, the legacy watchdog is disabled, and current Relay-vs-legacy parity is still compatible.
- `cutover-switch --owner relay` coordinates the wrapper-side pause plus Relay ownership arming; pair it with the supervised active service when you want a real executor instead of manual active runs. `cutover-switch --owner legacy-watchdog` disarms Relay active execution and resumes the legacy wrapper jobs.
- The plugin also registers a CLI command tree for future compatibility, but the reliable operator surface in the current Hermes build is the slash command.
