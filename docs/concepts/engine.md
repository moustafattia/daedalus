# Engine

`packages/core/src/sprints/engine/` owns durable state. It does not decide workflow policy.

The orchestrator and `WORKFLOW.md` decide what should happen. The engine stores
what happened, what is running, what needs retry, and what an operator can
inspect.

## Files

| File | Owns |
| --- | --- |
| `db.py` | SQLite connection and schema creation. |
| `store.py` | `EngineStore`, the workflow-scoped API. |
| `state.py` | SQL reads and writes. |
| `scheduler.py` | In-memory scheduler snapshot shape. |
| `lifecycle.py` | Pure transitions for running/retry entries. |
| `leases.py` | SQLite-backed leases. |
| `retention.py` | Event retention config. |
| `reports.py` | `runs` and `events` report builders. |
| `work.py` | Work/result dataclasses. |

## Database

Default path:

```text
<workflow-root>/runtime/state/sprints/sprints.db
```

The main tables are:

- `scheduler_state`
- `engine_runs`
- `engine_events`
- `leases`
- runtime session/thread state

## Rules

- Workflow code should use `EngineStore`.
- Only `db.py` creates schema.
- Only `state.py` should contain raw engine SQL.
- Run timelines come from `engine_events`.
- Work IDs stay tracker-neutral. Trackers may call them issues, tickets, PRs,
  or tasks.
