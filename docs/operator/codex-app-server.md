# Codex app-server Operations

Daedalus can use Codex app-server in two service shapes.

## Managed Mode

Managed mode means Daedalus owns a systemd user unit for the shared listener.
Use this when the workflow host should start the listener automatically.

```bash
hermes daedalus codex-app-server up
hermes daedalus codex-app-server doctor
```

The default listener is `ws://127.0.0.1:4500`. If the unit was installed with a
different `--listen` value, `doctor` reads it from the unit file.

Use logs when the service is installed but not active:

```bash
hermes daedalus codex-app-server logs --lines 100
```

## External Mode

External mode means another process owns the listener. Daedalus only connects
to its WebSocket endpoint.

```bash
hermes daedalus codex-app-server doctor \
  --mode external \
  --endpoint ws://127.0.0.1:4500
```

External mode skips systemd checks and validates endpoint shape, `GET /readyz`,
WebSocket auth posture, and durable thread mappings.

## Auth Checks

Loopback listeners do not require WebSocket auth. Non-loopback listeners should
declare one auth mode:

```bash
hermes daedalus codex-app-server up \
  --ws-token-file /absolute/path/to/codex-app-server.token
```

`doctor` fails if a non-loopback endpoint has no declared auth, or if the
configured token/shared-secret file is missing.

## Thread Mapping Checks

Daedalus persists Codex thread mappings in the workflow scheduler state:
`issue-runner` stores `issue_id -> thread_id`, and `change-delivery` stores
`lane:<issue-number> -> thread_id`. The default scheduler path is:

```text
<workflow-root>/memory/workflow-scheduler.json
```

`doctor --json` surfaces those mappings with issue id, identifier, session
name, thread id, turn id, status, cancellation state, and update time. Missing
thread ids are treated as broken state because future ticks cannot resume the
right Codex thread.
