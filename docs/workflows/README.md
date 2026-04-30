# Bundled workflows

Daedalus ships more than one workflow. The engine, lease model, runtime
adapters, and `WORKFLOW.md` contract are shared; each workflow package defines
its own lifecycle, prompts, gates, and operator commands.

## At a glance

| Workflow | Use it when... | Default template | Managed path |
|---|---|---|---|
| [`issue-runner`](issue-runner.md) | you want a generic issue workflow that creates workspaces, runs hooks, and invokes one agent | [`docs/examples/issue-runner.workflow.md`](../examples/issue-runner.workflow.md) | yes — `bootstrap --workflow issue-runner` or explicit `scaffold-workflow` + `service-up` |
| [`change-delivery`](change-delivery.md) | you want the opinionated SDLC workflow: issue -> code -> review -> PR -> merge | [`docs/examples/change-delivery.workflow.md`](../examples/change-delivery.workflow.md) | yes — `bootstrap --workflow change-delivery` + `service-up` |

For the contract file itself, see the [`WORKFLOW.md` guide](workflow-contract.md).

## The boundary

- Generic docs such as [architecture](../architecture.md), [public contract](../public-contract.md), [security](../security.md), and the engine-level concept docs describe Daedalus itself.
- Workflow docs describe the lifecycle and contract details that belong to one workflow package.
- If a doc is mostly about GitHub review gates, PR publish/merge stages, or reviewer roles, it belongs to `change-delivery`, not to the generic engine story.

## Repo Contract Naming

Daedalus uses `WORKFLOW.md` when a repository carries one workflow. When you
bootstrap a second workflow, Daedalus promotes the existing default contract to
`WORKFLOW-<existing-workflow>.md` and writes the new contract to
`WORKFLOW-<new-workflow>.md`.

Bootstrap does not overwrite existing named workflow contracts. If
`WORKFLOW.md` is a non-Daedalus file, rename it manually or choose a different
repo before running `hermes daedalus bootstrap`.
