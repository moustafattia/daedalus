# Events

Append-only history of everything that happened. Events are written to `daedalus-events.jsonl` (one JSON object per line) and consumed by:

- the operator dashboard (recent-event tail)
- the alerting layer
- post-hoc auditing
- regression tests that snapshot lifecycles

State is in SQLite. **History is in events.** Never reconstruct current state by replaying events — that's what the lanes table is for.

## Anatomy of an event

```json
{
  "type": "daedalus.turn_completed",
  "lane_id": "01HF3Q…",
  "issue_number": 42,
  "actor_id": "coder-claude-1",
  "at": "2026-04-28T14:03:11Z",
  "payload": { "model": "opus", "input_tokens": 1342, "output_tokens": 506 }
}
```

## Taxonomy (Symphony §10.4)

Daedalus follows the Symphony event taxonomy with a `daedalus.*` prefix on orchestration events. The bare names are defined as Symphony-compatible aliases (`workflows.code_review.event_taxonomy.EVENT_ALIASES`) and run for one release before the prefixed form becomes the only canonical type.

```mermaid
flowchart LR
  subgraph "session lifecycle"
    SS[session_started]
    SC[session_closed]
  end
  subgraph "turn lifecycle"
    TS[turn_started]
    TC[turn_completed]
    TF[turn_failed]
    TX[turn_cancelled]
    TI[turn_input_required]
  end
  subgraph "daedalus.* orchestration"
    LD[daedalus.lane_discovered]
    LP[daedalus.lane_promoted]
    SD[daedalus.stall_detected]
    ST[daedalus.stall_terminated]
    DR[daedalus.dispatch_skipped]
    HR[daedalus.config_reloaded]
  end

  SS --> TS --> TC
  TS --> TF
  TS --> TX
  TS --> TI
  LD --> LP --> SS
  TS -.timeout.-> SD --> ST
```

## Where bare-name vs prefixed applies

| Layer | Bare name | Prefixed |
|---|---|---|
| Turn-level (model, runtime) | ✅ canonical | also accepted |
| Lane-level (Daedalus orchestration) | accepted (alias window) | ✅ canonical |
| Session-level | ✅ canonical | also accepted |

`workflows.code_review.event_taxonomy.canonicalize(event_type)` resolves either form to the current canonical name.

## Event writer

Events are appended by `daedalus/runtime.py::append_daedalus_event`. The function:

1. Builds the event dict with `type`, `lane_id`, `at`, and `payload`
2. Atomically appends one JSON line to `daedalus-events.jsonl`
3. Never blocks on a full disk — if the write fails, the event is dropped and a warning is emitted

### File rotation

The JSONL file is **not rotated automatically**. For long-lived deployments:

- Archive old logs: `mv daedalus-events.jsonl daedalus-events-$(date +%Y%m%d).jsonl`
- The next event write creates a fresh `daedalus-events.jsonl`
- No data is lost if you archive while the process is running (append is atomic)

### Event schema

All events share a common envelope:

```json
{
  "type": "daedalus.turn_completed",
  "lane_id": "lane:220",
  "issue_number": 42,
  "actor_id": "coder-claude-1",
  "at": "2026-04-28T14:03:11Z",
  "payload": { ... }
}
```

| Field | Required | Notes |
|---|---|---|
| `type` | ✅ | Canonical event type. |
| `lane_id` | ❌ | Present for lane-scoped events. |
| `issue_number` | ❌ | Present for lane-scoped events. |
| `actor_id` | ❌ | Present for actor-scoped events. |
| `at` | ✅ | ISO-8601 UTC timestamp. |
| `payload` | ✅ | Event-specific data. |

## Reading events efficiently

The dashboard tails the last 20 events on every HTTP hit. Naïve `readlines()` is O(file size); the implementation in `daedalus/workflows/code_review/server/views.py::_read_events_tail` uses an 8 KiB reverse-chunked seek so request cost is bounded regardless of how big the log gets. Same algorithm if you write your own consumer.

## Where this lives in code

- Taxonomy constants: `daedalus/workflows/code_review/event_taxonomy.py`
- Writer: `daedalus/runtime.py::append_daedalus_event`
- Reader (tail): `daedalus/workflows/code_review/server/views.py::_read_events_tail`
- AST regression test: `tests/test_event_taxonomy.py` ensures `daedalus/runtime.py` only emits known event types
