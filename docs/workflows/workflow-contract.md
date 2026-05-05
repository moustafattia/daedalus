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
  terminal_states: [closed]
  required_labels: [active]
  exclude_labels: [blocked, needs-human, done]

intake:
  auto-activate:
    enabled: true
    add_label: active
    exclude_labels: [blocked, needs-human, done]
    max-per-tick: 1

code-host:
  kind: github
  github_slug: owner/repo

execution:
  actor-dispatch: auto

concurrency:
  max-lanes: 1
  actors:
    implementer: 1
    reviewer: 1
  per-lane-lock: true

recovery:
  running-stale-seconds: 1800
  auto-retry-interrupted: true

retry:
  max-attempts: 3
  initial-delay-seconds: 0
  backoff-multiplier: 2
  max-delay-seconds: 300

notifications:
  review-changes-requested:
    pull-request-review: true
    pull-request-comment: false
    issue-comment: true

completion:
  remove_labels: [active]
  add_labels: [done]
  auto-merge:
    enabled: true
    method: squash
    delete-branch: true

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
    skills: [pull, debug, commit, push]
  reviewer:
    runtime: codex
    skills: [review]

stages:
  deliver:
    actors: [implementer]
    gates: [delivery-ready]
    next: review

  review:
    actors: [reviewer]
    gates: [review-ready]
    next: done

gates:
  delivery-ready:
    type: orchestrator-evaluated
  review-ready:
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
may be considered by the orchestrator only after the operator or runner labels
it.

Tracker state is not engine ownership state.

The runner filters tracker candidates mechanically before dispatch:

- `active_states` must match the issue state.
- `terminal_states` closes blockers and terminal issue snapshots.
- `required_labels` must all be present.
- `exclude_labels` must be absent.

The orchestrator receives the filtered list under `facts.tracker.candidates`.
The runner claims eligible candidates into durable lanes before dispatch.
On each tick, the runner refreshes active lane issues and releases lanes that
are no longer tracker-eligible.

### `intake`

Intake config controls deterministic backlog promotion before a lane exists.

```yaml
intake:
  auto-activate:
    enabled: true
    add_label: active
    exclude_labels: [blocked, needs-human, done]
    max-per-tick: 1
```

When capacity is available and there are no eligible tracker candidates, the
runner can scan open tracker issues, skip issues that already have the active
label or any excluded label, add `add_label`, audit the mutation, then claim the
issue as a normal lane. This keeps backlog selection mechanical and observable.
The orchestrator still decides what to run after the lane is claimed.

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

Actors may also name bundled skills:

```yaml
actors:
  implementer:
    runtime: codex
    skills: [pull, debug, commit, push]
```

The runner injects those skill docs into that actor prompt. This is required for
runtime profiles that do not load Hermes plugin skills directly, including the
default `codex-app-server` profile.

### `stages`

Stages declare the actors, actions, gates, and next stage. `next: done` marks a
terminal transition. Change delivery uses broad actor-owned stages:

- `deliver`: implementer owns pull, edit, debug, commit, push, and PR creation.
- `review`: reviewer owns review of one lane and PR.

The runner stores progress in `state.lanes`, keyed by lane ID such as
`github#20`.

For `change-delivery`, the runner enforces two mechanical handoff contracts:

- `deliver -> review` requires implementer `status: done`,
  `pull_request.url`, and non-empty `verification`.
- `review -> done` requires reviewer `status: approved` and
  `pull_request.url`.

If either contract fails, the lane moves to `operator_attention` instead of
advancing.

### `concurrency`

Concurrency is explicit and enforced by the runner:

```yaml
execution:
  actor-dispatch: auto

concurrency:
  max-lanes: 1
  actors:
    implementer: 1
    reviewer: 1
  per-lane-lock: true
```

`max-lanes` is the top-level control. It limits non-terminal lanes across
`claimed`, `running`, `waiting`, `retry_queued`, and `operator_attention`.
Actor limits are optional caps inside lane capacity. If an actor limit is not
set, it derives from `max-lanes`; if it is higher than `max-lanes`, the runner
caps it to `max-lanes`.

Actor capacity is global for the workflow, not just per orchestrator response.
Before any actor is dispatched, the runner counts already-running lane status,
lane runtime sessions, engine runtime sessions, engine actor runs, and the
planned decisions in the current tick. If `actors.implementer: 1`, a second
implementer will not start while any other implementer run is still active,
even on a different lane.

`execution.actor-dispatch` controls how actor work leaves the tick loop:

| Value | Meaning |
| --- | --- |
| `auto` | Inline when `max-lanes: 1`; background subprocess workers when `max-lanes` is higher. |
| `inline` | The tick waits for the actor turn to finish. This is the default single-lane behavior. |
| `background` | The tick marks the lane `running`, starts an actor worker subprocess, saves state, and returns. |

Background workers merge their final result through the same workflow state lock
used by the tick. That keeps lane state serialized while allowing another tick
to inspect capacity and dispatch other lanes.

The runner calls the orchestrator only when at least one lane is
decision-ready: `claimed`, `waiting`, or `retry_queued` with a due retry. If all
active lanes are `running`, waiting for retry time, or blocked on
`operator_attention`, the tick saves a `no_decision_ready` event and returns
without spending an orchestrator turn.

### `recovery`

The runner persists actor runtime metadata on each lane before dispatch and
during progress callbacks:

- runtime profile and kind
- session name
- session/thread/turn IDs when the runtime exposes them
- latest runtime event and message
- token and rate-limit snapshots
- background worker process ID, log path, dispatch file, and heartbeat path

Background actor workers refresh a heartbeat artifact while the runtime turn is
alive. Reconciliation uses that heartbeat as the latest running timestamp, so a
long actor turn is not treated as interrupted just because the lane JSON has not
changed yet.

If a later tick finds a lane still marked `running` beyond
`running-stale-seconds` and the heartbeat has also gone stale, the runtime
session row and actor run are marked `interrupted`. When
`auto-retry-interrupted` is true, the runner queues a durable retry to the same
stage and actor with the runtime session artifacts in
`pending_retry.inputs.recovery`. The next dispatch resumes the same actor/stage
session when the runtime exposes a session ID. If recovery is disabled, missing
actor/stage context, or retry limits are exhausted, the lane moves to
`operator_attention` with recovery artifacts.

Before dispatching an actor, the runner also checks for an already-running lane,
runtime session, or actor run for the same lane/actor/stage. A conflict moves
the lane to `operator_attention` with the active run/session artifacts instead
of starting duplicate work.

### `retry`

Retry config is mechanical lane control:

```yaml
retry:
  max-attempts: 3
  initial-delay-seconds: 0
  backoff-multiplier: 2
  max-delay-seconds: 300
```

When the orchestrator returns `retry`, the engine computes the next attempt,
checks `max-attempts`, applies backoff, and persists the due retry row. The lane
keeps `pending_retry` as the actor handoff projection with target stage, target
actor, feedback inputs, attempt, due time, and retry history. The next actor
dispatch receives that state as `retry`. The runner rejects dispatch before
`pending_retry.due_at` and moves the lane to `operator_attention` when the engine
reports that the retry limit is exhausted.

`pending_retry` mirrors the engine schedule; it does not own the retry queue.
Scheduler snapshots do not rewrite retry rows, so retry state is created only by
`EngineStore.schedule_retry()` and cleared only by `EngineStore.clear_retry()`.

### `notifications`

Notifications are deterministic code-host side effects.

```yaml
notifications:
  review-changes-requested:
    pull-request-review: true
    pull-request-comment: false
    issue-comment: true
```

When the reviewer returns `changes_requested` or `needs_changes`, the runner can
post the reviewer summary, findings, required fixes, and verification gaps to
the pull request as a formal change request, optionally as a PR comment, and to
the source issue. Notifications are fingerprinted by lane, issue, PR, and review
content, so repeated ticks do not repost the same review side effect.
Notification failures are recorded on the lane and engine event stream; they do
not start duplicate actor work.

### `completion`

Completion is mechanical. Before a lane is marked complete, the runner can
optionally merge the reviewed pull request, applies configured label changes,
and only then releases the lane lock:

```yaml
completion:
  remove_labels: [active]
  add_labels: [done]
  auto-merge:
    enabled: true
    method: squash
    delete-branch: true
```

The bundled `change-delivery` template enables auto-merge. Set
`auto-merge.enabled: false` when the workflow should stop after review approval.
When enabled, the runner merges after review approval and before tracker label
cleanup. Before calling merge, the GitHub code-host checks PR state,
draft/mergeability, merge state, review decision, status checks, and unresolved
review threads. Supported methods are `squash`, `merge`, and `rebase`. If merge
readiness or merge fails, the lane moves to `operator_attention` instead of
being released silently.

Tracker cleanup is idempotent and runner-owned. If one cleanup step succeeds and
another fails, for example `active` was removed but `done` was not added, the
lane stays claimed and the engine schedules a `completion-cleanup` retry. Actors
are not rerun for that failure. The lane moves to `operator_attention` only
after the retry limit is exhausted.

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

Return at most one decision per lane in one tick. The runner validates the full
decision batch before applying it, so duplicate lane decisions, non-due retry
targets, duplicate dispatch, and actor concurrency violations fail before actors
are dispatched.

For `retry`, `stage` names the stage to retry and `target` names the actor or
action to run again. The runner stores the retry request in workflow state and
dispatches it when due with `retry.reason`, `retry.attempt`, `retry.due_at`, and
any `inputs` from the decision.

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
