# Slash Commands

Sprints exposes two Hermes command roots.

## `/sprints`

| Command | Purpose |
| --- | --- |
| `/sprints status` | Show workflow state and important paths. |
| `/sprints doctor` | Run config, state, runtime, and integration checks. |
| `/sprints validate` | Validate the active `WORKFLOW.md`. |
| `/sprints runs` | Inspect durable engine runs. |
| `/sprints events` | Inspect durable engine events. |
| `/sprints watch` | Render the operator watch view. |
| `/sprints bootstrap` | Create workflow root and repo contract. |
| `/sprints scaffold-workflow` | Scaffold with explicit paths. |
| `/sprints configure-runtime` | Bind actors to runtime presets. |
| `/sprints runtime-matrix` | Show actor/runtime bindings. |

## `/sprints codex-app-server`

| Command | Purpose |
| --- | --- |
| `install` | Write the systemd user unit. |
| `up` | Install, enable, and start the listener. |
| `status` | Show unit and readiness state. |
| `doctor` | Diagnose listener config and auth. |
| `restart` | Restart the listener. |
| `logs` | Show recent logs. |
| `down` | Stop and disable the listener. |

## `/workflow`

| Command | Purpose |
| --- | --- |
| `/workflow` | List installed workflows. |
| `/workflow change-delivery status` | Show change-delivery workflow state. |
| `/workflow change-delivery validate` | Validate the contract. |
| `/workflow change-delivery tick` | Run one orchestrator tick. |

Most commands accept `--workflow-root <path>`. JSON-capable commands expose
`--json` or `--format json`.
