# Observability

Daedalus exposes three operator-facing observability surfaces: the **TUI watch
frame**, the **HTTP status server**, and **tracker feedback**. All three read
from durable workflow state, but they serve different consumption patterns.

## Surfaces

| Surface | Use case | Live? | Writable? |
|---|---|---|---|
| `/daedalus watch` | Human operator in terminal | Yes | No |
| HTTP status server | Dashboard and scripted health checks | On request | No |
| `tracker-feedback` | Public issue timeline updates | Event-driven | Yes |

## TUI Watch

`/daedalus watch` renders active work, alerts, and recent engine events. The
frame is assembled from workflow-aware projections:

- `change-delivery`: active lane state from the engine DB and workflow ledger.
- `issue-runner`: shared engine `running`, `retry_queue`, and tracker state.
- `recent_events`: latest SQLite `engine_events`, with JSONL fallback.

If one source is unreadable, the frame marks that section stale and keeps
rendering the rest.

## HTTP Status Server

See [http-status.md](../operator/http-status.md) for endpoints. The server is a
localhost-only read surface over SQLite state plus generated JSON/JSONL
projections.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/state` | Running + retrying workflow work, totals, recent events |
| `GET /api/v1/events` | Filterable engine event ledger |
| `GET /api/v1/runs` | Durable engine run history |
| `GET /api/v1/runs/<run_id>` | One run plus correlated event timeline |
| `GET /api/v1/<identifier>` | Per-lane or per-issue debug view |
| `POST /api/v1/refresh` | Trigger an immediate tick subprocess |

## Tracker Feedback

Tracker feedback is configured in `WORKFLOW.md` with `tracker-feedback`, not via
runtime override commands. GitHub receives issue comments; `local-json` appends
comment objects and can update local issue state.

```yaml
tracker-feedback:
  enabled: true
  comment-mode: append  # use upsert to keep one current comment per event
  include:
    - dispatch-implementation-turn
    - internal-review-completed
    - merge-and-promote
  state-updates:
    enabled: false
```

See [tracker-feedback.md](tracker-feedback.md) for workflow examples and event
selection rules.

## Where This Lives

- TUI frame renderer: `daedalus/watch.py`
- Watch source aggregation: `daedalus/watch_sources.py`
- HTTP status server: `daedalus/workflows/change_delivery/server/`
- Tracker clients and feedback helper: `daedalus/trackers/`
- Event writer/indexer: `daedalus/engine/`
