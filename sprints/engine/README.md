# Engine

`engine/` owns durable workflow state.

It does not decide workflow policy. `WORKFLOW.md` and the orchestrator decide what
should happen. The engine stores state, leases, runs, events, retries, runtime
sessions, and exposes a workflow-scoped API for that state.

## Layout

| File | Owns |
| --- | --- |
| `db.py` | SQLite connection, schema, table checks |
| `store.py` | `EngineStore`, the workflow-scoped public API |
| `state.py` | SQL reads and writes for scheduler state, runs, and events |
| `scheduler.py` | In-memory scheduler snapshot shape |
| `lifecycle.py` | Pure transitions for running and retry entries |
| `retries.py` | Retry policy, attempt limits, backoff, and due-time planning |
| `leases.py` | SQLite-backed leases |
| `retention.py` | Event retention config normalization |
| `reports.py` | CLI report builders for runs and events |
| `work.py` | Work/result dataclasses and tracker adapters |

## State

SQLite is the source of truth.

Default DB path comes from `workflows.paths.runtime_paths()`:

```text
<workflow_root>/runtime/state/sprints/sprints.db
```

## Rules

Workflow code should use `EngineStore`.

Only `state.py` should contain raw SQL for engine state operations. Only `db.py`
should create schema.

Durable events live in `engine_events`. Run timelines come from `engine_events`,
not JSONL audit files.

The engine stores neutral work IDs. Trackers may call them issues, tickets, PRs,
or tasks, but the engine should stay tracker-neutral.

`engine_work_items` is the current engine projection of lane lifecycle state.
Workflow lanes still own the rich lane JSON, but every lane status transition
records a tracker-neutral work item row so operators can inspect lane state from
the engine DB.

`engine_runtime_sessions` is the durable projection of actor runtime/session
state. Each actor dispatch also creates an `engine_runs` row with `mode=actor`.
Workflow lanes still keep runtime metadata for orchestrator context, but runtime
start/progress/result hooks upsert the engine session row directly and link it
to the actor run ID.

`EngineStore.running_runs(mode="actor")` is the dispatch guard input. Workflow
code uses it to refuse duplicate actor work when a prior actor run is still
marked `running`.

`engine_retry_queue` is owned through `EngineStore.schedule_retry()`. Workflows
decide that a lane should retry; the engine computes the next attempt, checks
the retry limit, computes backoff, and persists the due retry projection.

## Deferred

The current engine layer is a durable projection, not the only source of truth.
Workflow lane JSON still owns rich lane state and policy context.

Later engine ownership waves:

- let due retries actively wake workflow ticks instead of only shortening daemon
  sleep on the next loop
- make lane lifecycle transitions engine-owned instead of `set_lane_status()`
  mutating JSON first and recording a projection second
- make actor dispatch/run/session updates transactional around engine run
  records
- reduce or remove scheduler snapshot rebuilds once direct engine tables cover
  status, retries, running work, and sessions
- keep workflow policy, stages, gates, and actor contracts outside the engine
