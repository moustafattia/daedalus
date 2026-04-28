# HTTP status surface

Symphony §13.7. Optional localhost HTTP server that exposes lane state, recent events, and a manual refresh hook. Useful for dashboards, scripted health checks, and live debugging without grepping `daedalus-events.jsonl`.

## Enable it

Add `server.port` to `workflow.yaml`:

```yaml
server:
  port: 8765   # localhost only; bind 127.0.0.1
```

Then run the long-running CLI subcommand (separate from `tick`):

```bash
python3 -m workflows.code_review serve --workflow-root <root>
```

The server is `http.server.ThreadingHTTPServer`, stdlib-only, and reads SQLite via WAL read-only URI connections (`mode=ro`). It never writes — `POST /api/v1/refresh` shells out a tick subprocess instead.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/v1/state` | Snapshot — running + retrying lanes, totals, recent events. |
| `GET`  | `/api/v1/<identifier>` | Per-lane debug view. `<identifier>` = `#42`, `42`, or `lane_id`. |
| `POST` | `/api/v1/refresh` | Trigger an immediate tick subprocess. Returns `{queued: true, pid: …}`. |
| `GET`  | `/` | Minimal HTML dashboard reading the same JSON. |

### `GET /api/v1/state`

Conforms to Symphony §13.7 / Daedalus spec §6.4:

```json
{
  "generated_at": "2026-04-28T14:03:11Z",
  "counts":   { "running": 3, "retrying": 0 },
  "running":  [ { "issue_id": "01HF…", "issue_identifier": "#42", "state": "coding_dispatched", "session_id": "claude-coder-1", "turn_count": 0, "last_event": "turn_started", "started_at": "…", "last_event_at": "…", "tokens": { "input_tokens": 0, "output_tokens": 0, "total_tokens": 0 } } ],
  "retrying": [],
  "totals":   { "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "seconds_running": 0 },
  "rate_limits": null,
  "recent_events": [ /* up to 20, newest first */ ]
}
```

Token / time totals are zeros today — Daedalus doesn't track per-lane usage yet. The shape is reserved.

### `GET /api/v1/<identifier>`

Returns the same shape as a single `running` entry plus a `recent_events` array filtered to that lane. Returns `404` if no active lane matches.

### `POST /api/v1/refresh`

Shells out the workflow's CLI entry point (resolved via `workflow_cli_argv()` so it works in installed deployments, not just `-m` invocations). The tick runs in a subprocess; the response returns immediately with `{queued: true, pid: <int>}`. Failure modes (subprocess can't be spawned) return `503`.

## Security posture

- **Localhost only.** The server binds `127.0.0.1`. There is no auth — getting access to the port is the auth.
- **Read-only DB.** `mode=ro` URI; even a compromised process can't mutate the lanes table through the server.
- **Refresh is rate-limited at the OS level** by virtue of being a subprocess fork — no separate counter.

## Performance notes

- Each request opens a fresh sqlite connection (cheap, avoids cross-thread state hazards).
- The events tail uses an 8 KiB reverse-chunked seek so cost is bounded by `limit` regardless of total log size — the previous `readlines()` implementation was O(file size) and got expensive on long-lived logs.

## Where this lives in code

- Server entrypoint: `daedalus/workflows/code_review/server/__init__.py`
- Routes: `daedalus/workflows/code_review/server/routes.py`
- Read views: `daedalus/workflows/code_review/server/views.py`
- Refresh hook: `daedalus/workflows/code_review/server/refresh.py`
- HTML: `daedalus/workflows/code_review/server/html.py`
