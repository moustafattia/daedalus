# `change-delivery`

`change-delivery` is the opinionated bundled SDLC workflow for GitHub-backed
issue-to-PR delivery.

## What it does

It takes a GitHub issue through:

1. lane selection
2. implementation
3. internal review
4. PR publish
5. external review
6. merge and promotion

Use `bootstrap --workflow change-delivery` when you want this lifecycle instead
of the default generic `issue-runner` workflow.

## Use it when

- GitHub is your tracker and PR system
- you want built-in review and merge gates
- you want the most complete Daedalus operator surface today

## Default template

- Public example: [`docs/examples/change-delivery.workflow.md`](../examples/change-delivery.workflow.md)
- Bundled payload template: [`daedalus/workflows/change_delivery/workflow.template.md`](/home/radxa/WS/daedalus/daedalus/workflows/change_delivery/workflow.template.md)

## Key config blocks

- `repository`: repo checkout, repo slug, GitHub slug, active-lane label
- `tracker`: GitHub-backed issue source and issue state mapping
- `runtimes`: shared runtime backend profiles used by the workflow roles
- `agents`: the workflow roles and their runtime/model bindings
- `gates`: publish/merge policy
- `triggers`: lane selector
- `lane-selection`: issue filtering/ranking
- `observability`: comments/webhooks integration

`change-delivery` composes the shared `runtimes/` backends with workflow-specific
prompts, reviewers, GitHub behavior, and merge policy.

## Codex Runtime Options

The default template uses `acpx-codex` for the coder role. To run the coder
through Codex app-server instead, change only `runtimes` and
`agents.coder.*.runtime` in `WORKFLOW.md`:

```yaml
runtimes:
  coder-runtime:
    kind: codex-app-server
    mode: external
    endpoint: ws://127.0.0.1:4500
    ephemeral: false
    keep_alive: true
    approval_policy: never
    thread_sandbox: workspace-write
    turn_sandbox_policy: workspace-write

agents:
  coder:
    default:
      name: Internal_Coder_Agent
      model: gpt-5.5
      runtime: coder-runtime
```

When `codex-app-server` is selected, Daedalus stores
`lane:<issue-number> -> thread_id` plus token/rate-limit totals in
`memory/workflow-scheduler.json` and resumes that thread on later ticks. During
supervised active service runs, Daedalus also records the active `turn_id`.
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
- [Lanes](../concepts/lanes.md)
- [Reviewers](../concepts/reviewers.md)
- [Failures](../concepts/failures.md)
- [Slash commands](../operator/slash-commands.md)
