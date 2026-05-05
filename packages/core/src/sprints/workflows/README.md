# Sprints Workflows

`sprints/workflows/` is the flat implementation shared by bundled workflow
templates.

Workflow intent lives in `WORKFLOW.md`. Python owns the mechanics: loading the
contract, typing front matter, dispatching actors/actions, applying
orchestrator decisions, and writing state.

## Layout

```text
workflows/
|-- __init__.py              # public workflow exports
|-- __main__.py              # `python -m workflows --workflow-root <path> ...`
|-- loader.py                # WORKFLOW.md contract and policy loader
|-- contracts.py             # WORKFLOW.md loading, rendering, and policy sections
|-- registry.py              # workflow object registry and CLI dispatch
|-- config.py                # typed front matter config
|-- bindings.py              # actor/runtime binding and runtime checks
|-- validation.py            # contract validation and readiness recommendations
|-- bootstrap.py             # repo bootstrap and scaffold mechanics
|-- daemon.py                # workflow tick loop and service controls
|-- orchestrator.py          # orchestrator prompt + decision schema
|-- prompt_context.py        # compact state/facts for runtime prompts
|-- runner.py                # CLI command router
|-- inspection.py            # validate, show, status, and lanes commands
|-- ticks.py                 # tick lifecycle and orchestrator invocation
|-- tick_journal.py          # engine run/events for workflow.tick.*
|-- state_io.py              # WorkflowState, state IO, audit, and state lock
|-- dispatch.py              # actor dispatch, background worker, heartbeats
|-- operator.py              # operator retry, release, and complete commands
|-- variables.py             # prompt variable builders
|-- lanes.py                 # lane facade used by workflow mechanics
|-- lane_state.py            # lane ledger state, config parsing, engine projections
|-- intake.py                # tracker intake, auto-activation, lane claiming
|-- reconcile.py             # runtime, tracker, and pull request reconciliation
|-- transitions.py           # lane decisions, transitions, actor output handling
|-- retries.py               # workflow adapter for engine-owned retry mechanics
|-- notifications.py         # review feedback notifications
|-- effects.py               # idempotency keys for external side effects
|-- status.py                # engine-first workflow and lane status projections
|-- sessions.py              # actor dispatch journal, sessions, heartbeats, scheduler projections
|-- teardown.py              # merge, tracker cleanup, and cleanup retry mechanics
|-- actors.py                # actor runtime dispatch
|-- actions.py               # deterministic action execution
|-- paths.py                 # workflow root and runtime path helpers
|-- schema.yaml              # workflow config schema
`-- templates/               # bundled WORKFLOW.md policy templates
    |-- issue-runner.md
    |-- change-delivery.md
    |-- release.md
    `-- triage.md
```

## Contract Shape

`WORKFLOW.md` has:

- YAML front matter for runtimes, actors, stages, gates, actions, and storage.
- `# Orchestrator Policy` for transition authority.
- `# Actor: <name>` sections for actor-specific policy and output shape.

The orchestrator decides whether to run an actor, run an action, advance,
retry, complete, or raise operator attention. `ticks.py` validates and applies
that decision.

The orchestrator does not receive raw workflow state. `prompt_context.py`
builds a compact prompt payload:

- active and decision-ready lanes keep the fields needed for validation and
  handoff
- terminal lanes are reduced to counts and recent summaries
- runtime sessions, dispatch journals, transition history, and side-effect
  details stay in lane state, audit logs, and engine history
- prompt size is measured before runtime dispatch and aggressively compacted
  before the Codex app-server input limit can be hit

## Tick Journal

Each runner tick is journaled in the engine:

```text
engine_runs(mode=tick)
  `-- engine_events(workflow.tick.*)
```

The journal starts before policy loading and ends after state save or failure
handling. It records the main mechanical checkpoints: policy loaded, state
loaded, reconciled, intake completed, readiness evaluated, orchestrator
started/completed or output override, decisions parsed, decisions applied, and
the terminal event. `/sprints status` exposes the latest tick run and recent
tick journal events.

## Retry Wakeups

`workflows/retries.py` is only the workflow adapter around engine retry
mechanics. It asks the engine to schedule or clear retry rows, then keeps
`lane.pending_retry` as actor/orchestrator context.

The daemon does not derive wake timing from `lane.pending_retry`. It reads
`EngineStore.retry_wakeup()`, which is built from `engine_retry_queue`, and
uses that to shorten the next sleep when a retry is due or nearly due.

## Actor Dispatch Journal

Actor dispatch is journaled before the runtime is launched:

```text
planned -> started -> running -> completed | failed | interrupted | blocked
```

`planned` is saved immediately after the runner decides to launch an actor.
`started` links the journal entry to the engine actor run and runtime session.
`running` records progress metadata such as thread, turn, heartbeat, and log
paths. Terminal states preserve the final runtime result.

The journal is lane-scoped and blocks duplicate dispatch for that lane. If a
tick dies after `planned` but before a runtime session starts, reconciliation
marks the dispatch `interrupted` after `recovery.running-stale-seconds` and
queues a retry to the same actor/stage when configured.
