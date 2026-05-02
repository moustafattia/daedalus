# `change-delivery`

`change-delivery` is the bundled SDLC workflow for issue-to-PR delivery. It is
GitHub-first today for tracker and code-host operations, but the public workflow
contract models issues, actors, stages, and gates rather than GitHub-specific
role names.

## What it does

It takes a GitHub issue through:

1. lane selection
2. implementation by a configured actor
3. pre-publish gate review
4. PR publish
5. optional maintainer approval from PR comments/reactions
6. merge and promotion

Use `bootstrap --workflow change-delivery` when you want this lifecycle instead
of the default generic `issue-runner` workflow.

## Use it when

- GitHub is your tracker or you want the first-class GitHub PR system
- you want built-in review and merge gates
- you want the most complete Daedalus operator surface today

## Default template

- Public example: [`docs/examples/change-delivery.workflow.md`](../examples/change-delivery.workflow.md)
- Bundled payload template: [`daedalus/workflows/change_delivery/workflow.template.md`](/home/radxa/WS/daedalus/daedalus/workflows/change_delivery/workflow.template.md)

## Key config blocks

- `repository`: generic repo identity, checkout path, active-lane label
- `tracker`: issue source, issue state mapping, tracker feedback target
- `code-host`: PR, review, CI, and merge host
- `runtimes`: shared runtime backend profiles
- `actors`: named executors with model/runtime bindings
- `stages`: lifecycle steps that call actors or engine actions
- `gates`: review, approval, and code-host checks
- `triggers`: lane selector
- `lane-selection`: issue filtering/ranking
- `tracker-feedback`: tracker-facing lifecycle comments
- `webhooks` / `server`: outbound notifications and HTTP status

`change-delivery` composes shared `runtimes/`, `trackers/`, and `code_hosts/`
backends with workflow-specific prompts, stages, and gates.

Runtime-backed stages are dispatched through the same shared stage boundary used
by `issue-runner`. Each actor selects a runtime with `actors.<name>.runtime`;
the workflow owns prompts and gates, while the runtime profile owns execution.

See the detailed [change-delivery contract spec](change-delivery-contract.md)
for the actor/stage/gate mapping.

## Runtime Options

The default template binds every runtime-backed actor to the shared
`codex-app-server` profile. To run any stage through Hermes Agent instead,
change only `runtimes` and the matching `actors.*.runtime` binding in
`WORKFLOW.md`:

```yaml
runtimes:
  codex-app-server:
    kind: codex-app-server
    mode: external
    endpoint: ws://127.0.0.1:4500
    ephemeral: false
    keep_alive: true
    approval_policy: never
    thread_sandbox: workspace-write
    turn_sandbox_policy: workspace-write

  hermes-review:
    kind: hermes-agent
    mode: final

actors:
  implementer:
    name: Change_Implementer
    model: gpt-5.5
    runtime: codex-app-server
  reviewer:
    name: Change_Reviewer
    model: gpt-5.5
    runtime: hermes-review
```

When `codex-app-server` is selected, Daedalus stores
`lane:<issue-number> -> thread_id` plus token/rate-limit totals in
`runtime/state/daedalus/daedalus.db`, writes `memory/workflow-scheduler.json`
as a generated operator snapshot, and resumes that thread on later ticks.
During supervised active service runs, Daedalus also records the active `turn_id`.
If the active lane disappears, changes, the lease is lost, or the service is
interrupted, the runtime requests `turn/interrupt` and marks the scheduler
thread entry as `canceling`. Operators can see those entries in `/daedalus
watch` and the HTTP state payload under `codex_turns`.

## Operator path

Onboarding:

```bash
cd /path/to/repo
hermes daedalus bootstrap --workflow change-delivery
$EDITOR /path/to/repo/WORKFLOW.md
hermes daedalus codex-app-server up
hermes daedalus validate
hermes daedalus service-up
```

Common workflow commands:

- `/workflow change-delivery status`
- `/workflow change-delivery tick`
- `/workflow change-delivery show-active-lane`
- `/workflow change-delivery dispatch-implementation-turn`
- `/workflow change-delivery publish-ready-pr`
- `/workflow change-delivery merge-and-promote`

## Runtime behavior

Manual ticks remain synchronous: `/workflow change-delivery tick` and
`/daedalus iterate-active` run one action inline and return the final result.

The long-running active service path is supervised. `/daedalus run-active`
dispatches one active iteration into an in-process worker, keeps the Daedalus
lease fresh while the worker runs, reconciles completed workers before shutdown,
and persists action completion/failure in the runtime DB. This prevents a fast
completed action from being marked as synthetic restart failure after a bounded
or interrupted service run.

## Related docs

- [Architecture](../architecture.md)
- [Change-delivery contract spec](change-delivery-contract.md)
- [Lanes](../concepts/lanes.md)
- [Reviewers](../concepts/reviewers.md)
- [Failures](../concepts/failures.md)
- [Slash commands](../operator/slash-commands.md)
