# Architecture

Sprints is a Hermes-Agent plugin for running repo-owned supervised workflows.

The core rule is simple: `WORKFLOW.md` decides policy; Sprints executes the
mechanics.

## Boundary

| Layer | Owns |
| --- | --- |
| `WORKFLOW.md` | Workflow config, orchestrator policy, actor policy, output shape. |
| `workflows/` | Loading contracts, typing config, daemon loop, rendering prompts, applying decisions. |
| `runtimes/` | Running one actor turn through coding agents like Codex, Hermes-Agent, Claude, Kimi, or a command-backed adapter. |
| `engine/` | SQLite state, leases, runs, retries, events, and reports. |
| `trackers/` | External work sources such as GitHub and Linear. |
| `observe/` | Read-only operator views. |
| `cli/` | Hermes command surface. |

## Workflow Shape

Every workflow has:

- one orchestrator actor
- one or more stages
- one or more gates
- actors and actions attached to stages

Minimum shape:

```text
entry -> stage -> gate
              |-> actor
              |-> action
```

Longer workflows repeat `stage -> gate`.

The orchestrator is just an actor with authority over transitions. It reads the
state and stage outputs, then returns a JSON decision:

- `run_actor`
- `run_action`
- `advance`
- `retry`
- `complete`
- `operator_attention`

## Runtime Execution

Actors do not run locally inside the workflow layer. Each actor names a runtime
profile from `runtimes:`. The workflow runner sends the rendered prompt to that
runtime through `runtimes/turns.py`.

This keeps one execution path:

```text
WORKFLOW.md -> workflows.runner -> workflows.actors -> runtimes.turns -> runtime adapter
```

Command-backed stages are still runtime execution. They are explicit
`command:` overrides on a runtime or actor config.

## State

SQLite is the durable source of truth for engine state:

```text
<workflow-root>/runtime/state/sprints/sprints.db
```

Workflow-local JSON/JSONL files are audit/status artifacts. They should not be
treated as the primary state model when SQLite has the data.

## Package Shape

```text
sprints/
|-- cli/
|-- engine/
|-- observe/
|-- runtimes/
|-- trackers/
`-- workflows/
    |-- loader.py
    |-- contracts.py
|-- config.py
    |-- daemon.py
    |-- runner.py
    |-- orchestrator.py
    |-- actors.py
    |-- actions.py
    `-- templates/
```
