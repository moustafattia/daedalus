# Observability

Daedalus exposes three operator-facing observability surfaces: the **TUI watch
frame**, the **HTTP status server**, and **GitHub comments publishing**. All
three read from the workflow's canonical state but serve different
consumption patterns.

---

## Three surfaces

| Surface | Use case | Live? | Writable? |
|---|---|---|---|
| `/daedalus watch` | Human operator in terminal | Yes (1s refresh) | No |
| HTTP status server | Dashboard, scripted health checks | Yes (on request) | No (read-only DB) |
| GitHub comments | Audit trail on the PR/issue | No (event-driven) | Yes (publishes to GitHub) |

---

## TUI watch (`/daedalus watch`)

### What it shows

```
┌─ Daedalus active lanes ──────────────────────────────────────────────┐
│ Active lanes                                                         │
│  Lane          State                GH Issue                           │
│  lane:220      under_review         #220                             │
│  lane:221      implementing_local     #221                             │
│                                                                      │
│ ⚠️  Active alerts                                                    │
│  primary=daedalus watchdog=retired issues=active_execution_failures  │
│                                                                      │
│ Recent events                                                        │
│  Time               Source     Event              Detail             │
│  2026-04-28T14:03   daedalus   turn_completed     coder-claude-1     │
│  2026-04-28T14:01   daedalus   stall_detected     lane:221           │
```

### Frame composition

The watch frame is assembled from three sources:

1. **`active_lanes`** — workflow-aware projection of active work
   `change-delivery`: `SELECT * FROM lanes WHERE lane_status NOT IN ('merged', 'closed', 'archived')`
   `issue-runner`: shared engine `running` + `retry_queue`
2. **`alert_state`** — parsed from `daedalus/alerts.py` output
3. **`recent_events`** — latest rows from SQLite `engine_events`, with JSONL tail fallback

### Modes

- **Live mode** (`/daedalus watch`): Refreshes every second until Ctrl-C.
- **One-shot mode** (`/daedalus watch --once`): Renders one frame and exits. Works in pipes and tests.

### Stale handling

If a source is unreadable, the frame prints `[stale]` in that section but keeps rendering the rest. The watch loop never crashes because one source is down.

---

## HTTP status server

See [http-status.md](http-status.md) for full endpoint documentation. Summary:

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/state` | Snapshot — running + retrying workflow work, totals, recent events |
| `GET /api/v1/events` | Filterable engine event ledger (`run_id`, `work_id`, `type`, `severity`) |
| `GET /api/v1/runs` | Durable engine run history |
| `GET /api/v1/runs/<run_id>` | One run plus correlated event timeline |
| `GET /api/v1/<identifier>` | Per-lane or per-issue debug view (`#42`, `42`, or `lane_id`) |
| `POST /api/v1/refresh` | Trigger immediate tick subprocess |
| `GET /` | Minimal HTML dashboard |

### Security

- **Localhost only** (`127.0.0.1`). No auth — port access is the auth.
- **Read-only state** (SQLite source state plus JSON/JSONL projections).
- **Refresh is rate-limited by OS** (subprocess fork).

---

## GitHub comments publishing

Daedalus can publish audit events as comments on the active PR (or issue, if no PR exists). This is **off by default**.

### Enable it

```yaml
observability:
  github-comments:
    enabled: true
    mode: edit-in-place   # or "append"
    include-events:
      - dispatch-implementation-turn
      - internal-review-completed
      - publish-ready-pr
      - push-pr-update
      - merge-and-promote
      - operator-attention-transition
      - operator-attention-recovered
```

### Resolution precedence

1. **Override file** (`observability-overrides.json`) — set via `/daedalus set-observability`
2. **`WORKFLOW.md`** `observability:` block
3. **Hardcoded defaults** (everything off)

### Modes

| Mode | Behavior |
|---|---|
| `edit-in-place` | One comment per lane; edits it as events arrive. |
| `append` | New comment for every event. |

### Operator commands

```text
/daedalus set-observability --workflow change-delivery --github-comments on
/daedalus set-observability --workflow change-delivery --github-comments off
/daedalus get-observability --workflow change-delivery
```

---

## Event log (`daedalus-events.jsonl`)

All three surfaces consume the same append-only JSONL event log. Typical events:

```json
{"type": "daedalus.turn_completed", "lane_id": "lane:220", "actor_id": "coder-claude-1", "at": "2026-04-28T14:03:11Z", "payload": {"model": "opus", "input_tokens": 1342, "output_tokens": 506}}
```

See [events.md](events.md) for the full taxonomy.

---

## Where this lives in code

- TUI frame renderer: `daedalus/watch.py`
- Watch source aggregation: `daedalus/watch_sources.py`
- Shared workflow-aware HTTP server: `daedalus/workflows/change_delivery/server/`
- GitHub comments: `daedalus/workflows/change_delivery/comments.py`, `comments_publisher.py`
- Observability config: `daedalus/workflows/change_delivery/observability.py`
- Override surface: `daedalus/observability_overrides.py`
- Event writer: `daedalus/runtime.py::append_daedalus_event`
- Tests: `tests/test_daedalus_watch_render.py`, `tests/test_daedalus_watch_sources.py`, `tests/test_status_server.py`, `tests/test_workflow_change_delivery_comments_*.py`
