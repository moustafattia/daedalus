# Operator Docs

Use these when installing or running Sprints.

| Doc | Purpose |
| --- | --- |
| [Installation](installation.md) | Install, initialize, validate, and run. |
| [Slash Commands](slash-commands.md) | `/sprints` and `/workflow change-delivery` commands. |
| [Codex App-Server](codex-app-server.md) | Shared Codex listener operations. |
| [Workflow Daemon](workflow-daemon.md) | Orchestrator loop service operations. |

Normal loop:

```bash
hermes sprints init
hermes sprints codex-app-server up
hermes sprints validate
hermes sprints doctor
hermes sprints daemon up
hermes
```
