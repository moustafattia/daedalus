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
workflow state read-only. Both workflows serve engine execution state from
SQLite plus JSONL/status projections. It never writes workflow state itself —
`POST /api/v1/refresh` shells out a tick subprocess instead.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/v1/state` | Snapshot — running + retrying work, totals, recent events. |
| `GET`  | `/api/v1/events` | Filterable engine event ledger. Query params: `run_id`, `work_id`, `type`, `severity`, `limit`. |
| `GET`  | `/api/v1/runs` | Durable engine run history. |
| `GET`  | `/api/v1/runs/<run_id>` | One engine run plus correlated event timeline. |
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
rate-limit data from shared engine state when their active runtime is
`codex-app-server`. `change-delivery` still derives running lane rows from its
SQLite lane model; `issue-runner` derives running and retrying rows from the
shared engine tables. `change-delivery` also includes `codex_turns` so
operators can inspect active or canceling Codex `thread_id` / `turn_id` pairs.

### `GET /api/v1/events`

Reads SQLite `engine_events` and returns newest events first:

```json
{
  "workflow": "issue-runner",
  "filters": { "work_id": "ISSUE-123" },
  "counts": { "shown": 1 },
  "events": [ { "event_type": "issue_runner.tick.completed", "work_id": "ISSUE-123" } ]
}
```

### `GET /api/v1/<identifier>`

Returns the same shape as a single `running` or `retrying` entry plus a
`recent_events` array filtered to that lane or issue. Returns `404` if nothing
active matches.

### `POST /api/v1/refresh`

Shells out the workflow's CLI entry point (resolved via `workflow_cli_argv()` so it works in installed deployments, not just `-m` invocations). The tick runs in a subprocess; the response returns immediately with `{queued: true, pid: <int>}`. Failure modes (subprocess can't be spawned) return `503`.

## Security posture

- **Localhost only.** The server binds `127.0.0.1`. There is no auth — getting access to the port is the auth.
- **Read-only state.** The server reads SQLite and JSON/JSONL projections; it never becomes a state writer.
- **Refresh is rate-limited at the OS level** by virtue of being a subprocess fork — no separate counter.

## Performance notes

- Requests open fresh SQLite connections (cheap, avoids cross-thread state hazards) and read bounded projections.
- Recent events come from `engine_events`; JSONL tailing remains as a bounded fallback when the ledger is empty or unavailable.

## Where this lives in code

- Server entrypoint: `daedalus/workflows/change_delivery/server/__init__.py`
- Shared workflow-aware routes: `daedalus/workflows/change_delivery/server/routes.py`
- Shared workflow-aware read views: `daedalus/workflows/change_delivery/server/views.py`
- Refresh hook: `daedalus/workflows/change_delivery/server/refresh.py`
- HTML: `daedalus/workflows/change_delivery/server/html.py`
