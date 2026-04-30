# `change-delivery`

`change-delivery` is the opinionated bundled SDLC workflow. It is the current
managed/default Daedalus path.

## What it does

It takes a GitHub issue through:

1. lane selection
2. implementation
3. internal review
4. PR publish
5. external review
6. merge and promotion

This is the workflow behind the default `bootstrap` and `service-up` operator
flow.

## Use it when

- GitHub is your tracker and PR system
- you want built-in review and merge gates
- you want the most complete Daedalus operator surface today

## Default template

- Public example: [`docs/examples/change-delivery.workflow.md`](../examples/change-delivery.workflow.md)
- Bundled payload template: [`daedalus/workflows/change_delivery/workflow.template.md`](/home/radxa/WS/daedalus/daedalus/workflows/change_delivery/workflow.template.md)

## Key config blocks

- `repository`: repo checkout, GitHub slug, active-lane label
- `runtimes`: shared runtime backend profiles used by the workflow roles
- `agents`: the workflow roles and their runtime/model bindings
- `gates`: publish/merge policy
- `triggers`: lane selector
- `lane-selection`: issue filtering/ranking
- `observability`: comments/webhooks integration

`change-delivery` composes the shared `runtimes/` backends with workflow-specific
prompts, reviewers, GitHub behavior, and merge policy.

## Operator path

Default onboarding:

```bash
cd /path/to/repo
hermes daedalus bootstrap
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
