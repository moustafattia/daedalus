# Tracker Feedback

Tracker feedback publishes workflow lifecycle updates back to the issue source.
It is the shared replacement for workflow-specific GitHub comment settings.

## Why It Exists

- Operators get visible progress on the issue without opening Daedalus logs.
- GitHub, `local-json`, and future trackers use one workflow-facing contract.
- Workflow behavior stays repo-owned in `WORKFLOW.md`.
- `change-delivery` can keep issue feedback under `tracker` while PR/merge
  behavior stays under `code-host`.

## Configuration

```yaml
tracker-feedback:
  enabled: true
  comment-mode: append  # append | upsert
  include:
    - issue.selected
    - issue.running
    - issue.completed
  state-updates:
    enabled: true
    on-selected: in-progress
    on-completed: done
```

`include` is workflow-specific. `issue-runner` emits `issue.*` events.
`change-delivery` emits audit actions such as `dispatch-implementation-turn`,
`internal-review-completed`, `publish-ready-pr`, `push-pr-update`,
`merge-and-promote`, and operator-attention events.

`comment-mode: append` writes a new tracker comment for every included event.
`comment-mode: upsert` keeps one current Daedalus comment per workflow event
and updates it on later runs; the durable audit trail still lives in Daedalus
SQLite/events.

## Tracker Behavior

| Tracker | Feedback behavior |
|---|---|
| GitHub | Posts issue comments through `gh issue comment`, or updates marked comments when `comment-mode: upsert`; applies configured `open`/`closed` state updates and ignores other tracker states. |
| `local-json` | Appends or upserts `comments[]`, updates `updated_at`, and applies configured state changes. |
| Linear | Adapter exists for reads; feedback publishing is deferred. |

## Failure Handling

Feedback publishing is best-effort fanout after the local audit event is
written. A tracker API failure must not fail the workflow tick. Durable retry of
failed feedback publishes is a future hardening slice.

## Code Pointers

- Shared helper: `daedalus/trackers/feedback.py`
- Tracker adapters: `daedalus/trackers/github.py`, `daedalus/trackers/local_json.py`
- `issue-runner` wiring: `daedalus/workflows/issue_runner/workspace.py`
- `change-delivery` wiring: `daedalus/workflows/change_delivery/workspace.py`
