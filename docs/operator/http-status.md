# HTTP status surface

Symphony §13.7. Optional localhost HTTP server that exposes workflow state,
recent events, and a manual refresh hook. Useful for dashboards, scripted
health checks, and live debugging without grepping `daedalus-events.jsonl`.

## Enable it

Add `server.port` to `WORKFLOW.md`:

```yaml
server:
  port: 8765   # localhost only; bind 127.0.0.1
```

Then run the workflow's `serve` subcommand (separate from `tick`):

```bash
/workflow change-delivery serve
/workflow issue-runner serve
```

If you are calling the Python entrypoint directly instead of going through
Hermes:

```bash
python3 -m workflows.change_delivery serve --workflow-root <root>
python3 -m workflows.issue_runner serve --workflow-root <root>
```

The server is `http.server.ThreadingHTTPServer`, stdlib-only, and reads the
workflow state read-only. `change-delivery` serves from SQLite + JSONL events;
`issue-runner` serves from its persisted status, scheduler, and audit files.
It never writes workflow state itself — `POST /api/v1/refresh` shells out a
tick subprocess instead.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/v1/state` | Snapshot — running + retrying work, totals, recent events. |
| `GET`  | `/api/v1/<identifier>` | Per-work-item debug view. `<identifier>` = `#42`, `42`, or `lane_id`. |
| `POST` | `/api/v1/refresh` | Trigger an immediate tick subprocess. Returns `{queued: true, pid: …}`. |
| `GET`  | `/` | Minimal HTML dashboard reading the same JSON. |

### `GET /api/v1/state`

Conforms to Symphony §13.7 / Daedalus spec §6.4:

```json
{
  "generated_at": "2026-04-28T14:03:11Z",
  "counts":   { "running": 3, "retrying": 0 },
  "running":  [ { "issue_id": "01HF…", "issue_identifier": "#42", "state": "coding_dispatched", "session_id": "claude-coder-1", "turn_count": 0, "last_event": "turn_started", "started_at": "…", "last_event_at": "…", "tokens": { "input_tokens": 1200, "output_tokens": 480, "total_tokens": 1680 } } ],
  "retrying": [],
  "codex_totals": { "input_tokens": 1200, "output_tokens": 480, "total_tokens": 1680, "seconds_running": 43 },
  "rate_limits": { "requests_remaining": 47 },
  "recent_events": [ /* up to 20, newest first */ ]
}
```

Both bundled workflows report aggregate Codex token totals and latest
rate-limit data from scheduler state when their active runtime is
`codex-app-server`. `change-delivery` still derives running lane rows from its
SQLite lane model; `issue-runner` derives running and retrying rows directly
from its scheduler file.

### `GET /api/v1/<identifier>`

Returns the same shape as a single `running` or `retrying` entry plus a
`recent_events` array filtered to that lane or issue. Returns `404` if nothing
active matches.

### `POST /api/v1/refresh`

Shells out the workflow's CLI entry point (resolved via `workflow_cli_argv()` so it works in installed deployments, not just `-m` invocations). The tick runs in a subprocess; the response returns immediately with `{queued: true, pid: <int>}`. Failure modes (subprocess can't be spawned) return `503`.

## Security posture

- **Localhost only.** The server binds `127.0.0.1`. There is no auth — getting access to the port is the auth.
- **Read-only state.** `change-delivery` uses `mode=ro` SQLite URIs; `issue-runner` reads persisted JSON/JSONL state files. The server itself never becomes a state writer.
- **Refresh is rate-limited at the OS level** by virtue of being a subprocess fork — no separate counter.

## Performance notes

- `change-delivery` requests open a fresh sqlite connection (cheap, avoids cross-thread state hazards).
- `issue-runner` requests read the current status/scheduler snapshots from disk, so reads remain bounded and restart-safe.
- The events tail uses an 8 KiB reverse-chunked seek so cost is bounded by `limit` regardless of total log size — the previous `readlines()` implementation was O(file size) and got expensive on long-lived logs.

## Where this lives in code

- Server entrypoint: `daedalus/workflows/change_delivery/server/__init__.py`
- Shared workflow-aware routes: `daedalus/workflows/change_delivery/server/routes.py`
- Shared workflow-aware read views: `daedalus/workflows/change_delivery/server/views.py`
- Refresh hook: `daedalus/workflows/change_delivery/server/refresh.py`
- HTML: `daedalus/workflows/change_delivery/server/html.py`
