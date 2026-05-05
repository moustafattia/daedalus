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

Every workflow tick is a durable run. The runner starts an `engine_runs` row with
`mode=tick`, records ordered `workflow.tick.*` events in `engine_events`, and
finishes the run as `completed` or `failed`. This makes partial ticks visible:
operators can see whether a tick reached policy load, state load, reconcile,
intake, orchestrator execution, decision parsing, decision application, or only
the failure boundary.

Runner-owned side effects use stable idempotency keys and record their lifecycle
in `engine_events`: `started`, `succeeded`, `failed`, or `skipped`. The rich
lane JSON keeps the current side-effect ledger for orchestrator/operator
context; the engine event IDs make successful side effects detectable after a
process restart.

The engine stores neutral work IDs. Trackers may call them issues, tickets, PRs,
or tasks, but the engine should stay tracker-neutral.

`engine_work_items` is the current engine projection of lane lifecycle state.
Workflow lanes still own the rich lane JSON, but every lane status transition
goes through one transition boundary: mutate lane JSON, build a transition
record with previous/current state, then write the work-item projection and
engine event in one SQLite transaction. Operators can inspect lane state from
the engine DB and reconstruct transitions from `engine_events`.

Status reads are engine-first. `/sprints status` and `/sprints watch` read
`engine_work_items` as the primary lane list and enrich the projection from lane
JSON when available. This keeps operator status available even when the JSON
state file is stale or incomplete.

`engine_runtime_sessions` is the durable projection of actor runtime/session
state. Each actor dispatch also creates an `engine_runs` row with `mode=actor`.
Workflow lanes still keep runtime metadata for orchestrator context, but runtime
start/progress/result hooks upsert the engine session row directly and link it
to the actor run ID.

Workflow lanes also keep an actor dispatch journal. It records the launch
boundary before a runtime session exists: `planned`, `started`, `running`, then a
terminal status. Engine rows still hold the durable run/session projection; the
journal protects the smaller runner crash window between "we decided to launch"
and "the runtime has an active session".

`EngineStore.running_runs(mode="actor")` is the dispatch guard input. Workflow
code uses it to refuse duplicate actor work when a prior actor run is still
marked `running`.

`engine_retry_queue` is owned through `EngineStore.schedule_retry()` and
`EngineStore.clear_retry()`. Workflows decide that a lane should retry and pass
workflow context such as stage, target, reason, and inputs. The engine computes
the next attempt, checks the retry limit, computes backoff, persists the due
retry projection, and builds the normalized retry record used by workflow lane
JSON. Workflow lane JSON may keep `pending_retry` for actor handoff, but retry
attempt math, due-time parsing, and retry projection shape live in `engine/`.
Scheduler snapshots do not rewrite retry rows.

Retry visibility is required, not optional. Status and audit projections must
show retry history, due time, max attempts, backoff delay, and failure reason.
Engine events record retry scheduling and retry-limit exhaustion so unattended
runs can be reconstructed from the database.

Retry wakeups are engine-owned. The daemon does not inspect lane JSON to decide
when to wake for retries; it reads the `engine_retry_queue` projection through
`EngineStore.retry_wakeup()`. That projection reports queued retry count, due
retry count, next due time, and next retry metadata.

## Deferred

The current engine layer is a durable projection, not the only source of truth.
Workflow lane JSON still owns rich lane state and policy context.

Later engine ownership waves:

- make lane lifecycle transitions engine-owned instead of `set_lane_status()`
  mutating JSON first and recording a projection second
- make actor dispatch/run/session updates transactional around engine run
  records
- reduce or remove scheduler snapshot rebuilds once direct engine tables cover
  status, running work, and sessions
- keep workflow policy, stages, gates, and actor contracts outside the engine
