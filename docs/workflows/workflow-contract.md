# WORKFLOW.md Contract

`WORKFLOW.md` is the repo-owned contract for a Sprints workflow.

It has two parts:

1. YAML front matter for typed config.
2. Markdown policy sections for the orchestrator and actors.

## Front Matter

Minimal shape:

```yaml
---
workflow: change-delivery
schema-version: 1

repository:
  local-path: /absolute/path/to/repo

tracker:
  kind: github
  github_slug: owner/repo
  active_states: [open]
  required_labels: [active]
  exclude_labels: [blocked, needs-human, done]

code-host:
  kind: github
  github_slug: owner/repo

orchestrator:
  actor: orchestrator

runtimes:
  codex:
    kind: codex-app-server
    mode: external
    endpoint: ws://127.0.0.1:4500
    ephemeral: false
    keep_alive: true

actors:
  orchestrator:
    runtime: codex
  implementer:
    runtime: codex

stages:
  entry:
    actors: [implementer]
    actions: []
    gates: [entry-complete]
    next: done

gates:
  entry-complete:
    type: orchestrator-evaluated

actions: {}

storage:
  state: .sprints/change-delivery-state.json
  audit-log: .sprints/change-delivery-audit.jsonl
---
```

## Required Sections

### `repository`

`repository.local-path` must point to an existing checkout. Runtime turns use it
as the worktree.

### `tracker`

Tracker config defines the external work-item source and the mechanical
eligibility filter. For GitHub, `required_labels: [active]` means an issue
may be considered by the orchestrator only after the operator labels it.

Tracker state is not engine ownership state.

### `code-host`

Code-host config defines where branches, pull requests, reviews, and merge
operations live. GitHub may be both tracker and code host, but they are separate
roles in the contract.

### `runtimes`

Named runtime profiles. Supported `kind` values:

- `codex-app-server`
- `hermes-agent`
- `claude-cli`
- `acpx-codex`

### `actors`

Each actor names a runtime profile. There is no implicit runtime.

### `stages`

Stages declare the actors, actions, gates, and next stage. `next: done` marks a
terminal transition.

### `gates`

Current engine gate type:

```yaml
type: orchestrator-evaluated
```

The orchestrator decides whether the gate passes by returning a JSON decision.

### `actions`

Actions are deterministic mechanics run by the workflow runner. Current bundled
action types include:

- `noop`
- `command`
- `comment`
- `code-host.create-pull-request`

`code-host.create-pull-request` uses the `code-host` section and the
implementation output or orchestrator inputs to create a pull request.

## Policy Sections

### Orchestrator

```md
# Orchestrator Policy

Decide the next transition from the current workflow state.

Return JSON only:

{
  "decision": "run_actor",
  "stage": "entry",
  "target": "implementer",
  "reason": "why this transition is valid",
  "inputs": {},
  "operator_message": null
}
```

Allowed decisions:

- `run_actor`
- `run_action`
- `advance`
- `retry`
- `complete`
- `operator_attention`

For `retry`, `stage` names the stage to retry and `target` names the actor or
action to run again. The runner stores the retry request in workflow state and
dispatches it on the next tick with `retry.reason`, `retry.attempt`, and any
`inputs` from the decision.

### Actor

```md
# Actor: implementer

## Input

Issue:
{{ issue }}

Workflow state:
{{ workflow }}

## Policy

Do the work described by the orchestrator input.

## Output

Return JSON only:

{
  "status": "done",
  "summary": "what changed or why no change was needed",
  "artifacts": [],
  "validation": [],
  "blockers": [],
  "next_recommendation": "complete"
}
```

Actor output is handed back to the orchestrator through workflow state.

## Multiple Workflows

One repo can carry multiple contracts by naming them:

```text
WORKFLOW-release.md
WORKFLOW-triage.md
```

Each contract declares its own `workflow:` value. The file name selects which
contract to load.
