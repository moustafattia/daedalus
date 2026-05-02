# Bundled workflows

Daedalus ships more than one workflow. The engine, lease model, runtime
adapters, and `WORKFLOW.md` contract are shared; each workflow package defines
its own lifecycle, prompts, gates, and operator commands.

Shared workflow helpers live in the flat `daedalus/workflows/` support layer.
`agentic/` is the clean policy-driven workflow path. `change_delivery/` and
`issue_runner/` remain legacy workflow packages while their behavior is ported
into agentic templates.

## At a glance

| Workflow | Use it when... | Default template | Managed path |
|---|---|---|---|
| `agentic` | you want `WORKFLOW.md` to define stages, gates, actors, actions, and orchestrator policy while Python only executes mechanics | `daedalus/workflows/agentic/workflow.template.md` | no |
| [`issue-runner`](issue-runner.md) | you want a generic tracker-driven workflow that selects issues, creates workspaces, runs hooks, and invokes one agent | [`docs/examples/issue-runner.workflow.md`](../examples/issue-runner.workflow.md) | yes — default `bootstrap` + `service-up` |
| [`change-delivery`](change-delivery.md) | you want issue -> actor implementation -> gates -> PR -> merge delivery with GitHub as the first-class tracker/code-host path | [`docs/examples/change-delivery.workflow.md`](../examples/change-delivery.workflow.md) | yes — `bootstrap --workflow change-delivery` + `service-up` |

For the contract file itself, see the [`WORKFLOW.md` guide](workflow-contract.md).
Both bundled templates default runtime-backed stages to `codex-app-server`;
bind individual roles to Hermes Agent or another runtime profile when that is a
better fit for the stage.

## Agentic Workflow

`workflow: agentic` is the policy-driven workflow model. The front matter
defines mechanical bindings such as runtimes, actors, stages, gates, actions,
and storage. The Markdown body defines orchestrator and actor policies. Python
validates and executes those mechanics; production workflow policy belongs in
`WORKFLOW.md`.

## The boundary

- Generic docs such as [architecture](../architecture.md), [public contract](../public-contract.md), [security](../security.md), and the engine-level concept docs describe Daedalus itself.
- Workflow docs describe the lifecycle and contract details that belong to one workflow package.
- If a doc is mostly about PR publish/merge stages, actor/stage/gate policy, or code-host approvals, it belongs to `change-delivery`, not to the generic engine story.

## Repo Contract Naming

Daedalus uses `WORKFLOW.md` when a repository carries one workflow. When you
bootstrap a second workflow, Daedalus promotes the existing default contract to
`WORKFLOW-<existing-workflow>.md` and writes the new contract to
`WORKFLOW-<new-workflow>.md`.

Bootstrap does not overwrite existing named workflow contracts. If
`WORKFLOW.md` is a non-Daedalus file, rename it manually or choose a different
repo before running `hermes daedalus bootstrap`.
