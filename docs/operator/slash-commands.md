# Slash Commands

Sprints exposes two Hermes command roots.

## `/sprints`

| Command | Purpose |
| --- | --- |
| `/sprints status` | Show workflow state and important paths. |
| `/sprints doctor` | Run config, state, runtime, and integration checks. |
| `/sprints doctor --fix` | Apply conservative local repairs and report each change. |
| `/sprints validate` | Validate the active `WORKFLOW.md`. |
| `/sprints runs` | Inspect durable engine runs. |
| `/sprints events` | Inspect durable engine events. |
| `/sprints watch` | Render the operator watch view. |
| `/sprints init` | Run the first-time setup wizard and write `WORKFLOW.md`. |
| `/sprints bootstrap` | Create workflow root and repo contract. |
| `/sprints scaffold-workflow` | Scaffold with explicit paths. |
| `/sprints configure-runtime` | Bind actors to runtime presets. |
| `/sprints runtime-matrix` | Show actor/runtime bindings. |

## `/sprints daemon`

| Command | Purpose |
| --- | --- |
| `run` | Run the workflow tick loop in the foreground. |
| `install` | Write the workflow daemon systemd user unit. |
| `up` | Install, enable, and start the workflow daemon. |
| `status` | Show unit and engine lease state. |
| `restart` | Restart the workflow daemon. |
| `logs` | Show recent logs. |
| `down` | Stop and disable the workflow daemon. |

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
| `/workflow change-delivery lanes` | List lane summaries. |
| `/workflow change-delivery lanes --attention` | List lanes blocked on operator attention. |
| `/workflow change-delivery lanes <lane-id>` | Show one full lane record. |
| `/workflow change-delivery retry <lane-id>` | Queue a retry after the operator fixed the blocker. |
| `/workflow change-delivery release <lane-id>` | Release a lane without completing it. |
| `/workflow change-delivery complete <lane-id>` | Complete a lane through normal completion cleanup. |
| `/workflow change-delivery validate` | Validate the contract. |
| `/workflow change-delivery tick` | Run one orchestrator tick. |

Most commands accept `--workflow-root <path>`. JSON-capable commands expose
`--json` or `--format json`.
