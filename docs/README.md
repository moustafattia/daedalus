# Sprints Docs

Current docs for Hermes Sprints.

Sprints has one default template: `change-delivery`. Policy lives in repo-owned
`WORKFLOW.md`; Python owns loading, validation, runtime dispatch, state, and
operator commands.

## Read First

| Doc | Use it for |
| --- | --- |
| [Architecture](architecture.md) | Package shape and ownership boundaries. |
| [Workflow Contract](workflows/workflow-contract.md) | `WORKFLOW.md` front matter and policy sections. |
| [Runtimes](concepts/runtimes.md) | Runtime profiles, actors, and turn execution. |
| [Engine](concepts/engine.md) | SQLite-backed state, leases, runs, and events. |
| [Installation](operator/installation.md) | Install, bootstrap, validate, and run. |
| [Slash Commands](operator/slash-commands.md) | `/sprints` and `/workflow change-delivery` commands. |
| [Codex App-Server](operator/codex-app-server.md) | Shared Codex listener setup and checks. |
| [Public Contract](public-contract.md) | Compatibility-sensitive surfaces. |
| [Security](security.md) | Trust model and execution risk. |

## Source Layout

```text
sprints/
|-- cli/          # command parsing and rendering
|-- engine/       # durable SQLite mechanics
|-- observe/      # watch/status read side
|-- runtimes/     # Codex, Hermes Agent, Claude, ACPX adapters
|-- trackers/     # GitHub and Linear tracker clients
`-- workflows/    # WORKFLOW.md loader, runner, actors, actions
```

## Workflow Templates

Bundled policy templates live in `sprints/workflows/templates/`:

- `issue-runner.md`
- `change-delivery.md`
- `release.md`
- `triage.md`

They use the same Python implementation. The selected template defines the
workflow name and policy.
