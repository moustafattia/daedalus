# Workflows

Daedalus now ships one workflow: `agentic`.

The engine, lease model, runtime adapters, and `WORKFLOW.md` contract are
shared. Workflow policy lives in the repo-owned Markdown contract instead of
hardcoded Python lifecycle modules.

## At A Glance

| Workflow | Use it when... | Default template | Managed path |
|---|---|---|---|
| `agentic` | you want `WORKFLOW.md` to define stages, gates, actors, actions, and orchestrator policy while Python only executes mechanics | `daedalus/workflows/workflow.template.md` | yes |

## Agentic Workflow

`workflow: agentic` is the policy-driven workflow model. The front matter
defines mechanical bindings such as runtimes, actors, stages, gates, actions,
and storage. The Markdown body defines orchestrator and actor policies.

Python validates and executes those mechanics; production workflow policy
belongs in `WORKFLOW.md`.

## Repo Contract Naming

Daedalus uses `WORKFLOW.md` as the repo-owned agentic workflow contract. If
`WORKFLOW.md` is a non-Daedalus file, rename it manually or choose a different
repo before running `hermes daedalus bootstrap`.
