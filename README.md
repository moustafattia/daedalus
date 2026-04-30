# Daedalus

<div align="center">

![Daedalus banner](assets/daedalus-banner.gif)

**Durable SDLC automation engine for Hermes Agent.**

</div>

Daedalus is a control plane for agentic software work. It turns issues into
supervised workflow runs, dispatches agents through runtime adapters, persists
state, reconciles failures, and gives operators a live surface for the loop.

During bootstrap, the Daedalus plugin generates a `WORKFLOW.md` file in the
repository you want Daedalus to operate on. That file is your repo-local
workflow contract: it defines policy and configuration, but it is not the
scheduler. The scheduler is the plugin, service loop, workflow package, state
store, leases, tracker clients, runtime adapters, and observability around it.

## What You Get

| Capability | What it means |
|---|---|
| Issue-based automation | Turns selected issues into supervised workflow runs with explicit lifecycle policy. |
| Repo-owned workflow contracts | Generates `WORKFLOW.md` into your target repo so config and policy live beside the code being automated. |
| Durable runtime state | Persists leases, running work, retries, thread mappings, audit history, status, and health in SQLite, JSON, and JSONL. |
| Supervised service loop | Runs under `systemd --user`, survives restarts, reconciles stalled work, and resumes eligible runs. |
| Runtime flexibility | Dispatches through runtime profiles for hosted agents, CLI agents, Codex app-server, or custom commands. |
| Operator surface | Exposes `/daedalus`, `/workflow`, watch output, service controls, and optional HTTP status. |
| Bundled workflow engine | Ships `issue-runner` and `change-delivery`, with shared tracker, runtime, config, and observability primitives. |

## Mental Model

| Term | Meaning |
|---|---|
| Target repo | The user repository where work should happen. Bootstrap writes `WORKFLOW.md` here. |
| Workflow contract | `WORKFLOW.md` or `WORKFLOW-<name>.md`; YAML front matter plus Markdown policy text. |
| Workflow root | Durable instance data under `~/.hermes/workflows/<owner>-<repo>-<workflow-type>`. |
| Workflow package | The installed Python implementation that decides the lifecycle for a selected issue. |
| Tracker | The system Daedalus reads issues from and writes status back to. |
| Issue | The unit of work selected from a tracker. Workflows should model issues, not one tracker vendor. |
| Runtime | The adapter that runs an agent or command against a workspace. |
| Workspace | The isolated checkout/path where the agent does work for an issue. |
| State store | SQLite, JSON, and JSONL files that preserve current state, history, retries, leases, and metrics. |
| Operator surface | Hermes commands, service controls, watch output, and optional HTTP status. |

## Bundled Workflows

| Workflow | What it automates | Use it when |
|---|---|---|
| `issue-runner` | issue -> workspace -> hooks -> prompt -> one agent run | You want a small generic issue workflow. |
| `change-delivery` | issue -> implementation -> internal review -> PR -> external review -> merge | You want the opinionated SDLC workflow with review and merge gates. |

`issue-runner` is the generic reference workflow and the closest surface to
Symphony-style issue execution. `change-delivery` is richer and more
opinionated.

## Quick Start

```bash
sudo apt install python3-yaml python3-jsonschema
hermes plugins install attmous/daedalus --enable

cd /path/to/your/repo
hermes daedalus bootstrap --workflow issue-runner
$EDITOR WORKFLOW.md
hermes daedalus service-up
hermes
```

Bootstrap creates the workflow root, writes the workflow contract into your
repo, commits it on a bootstrap branch, and stores a repo-local pointer so later
commands can resolve the workflow instance.

For the opinionated change-delivery workflow:

```bash
hermes daedalus bootstrap --workflow change-delivery
```

For manual scaffold paths, service modes, pip installs, and every lower-level command,
use the full install guide:
[docs/operator/installation.md](docs/operator/installation.md).

## Operate It

After installing the plugin, run Hermes from your target repo:

```bash
cd /path/to/your/repo
hermes
```

Inside Hermes Agent:

```text
# Daedalus engine and service commands
/daedalus status          # show runtime state, workflow root, and important paths
/daedalus doctor          # run health checks across config, service, state, and integrations
/daedalus watch           # render a live operator view
/daedalus service-status  # show the systemd user service state

# Workflow package commands
/workflow issue-runner status                         # show selected issues, runs, retries, and scheduler state
/workflow issue-runner run --max-iterations 1 --json  # run one bounded service-loop iteration
/workflow change-delivery status                      # show active issue/lane and next action
/workflow change-delivery tick                        # run one change-delivery workflow tick
```

The operator surfaces read the persisted state for you. You should not need to
inspect SQLite, scheduler JSON, JSONL logs, or systemd journals by hand during
normal operation.

## Configure The Workflow

Edit the generated contract in your target repo:

- `WORKFLOW.md` when the repo carries one workflow
- `WORKFLOW-issue-runner.md` / `WORKFLOW-change-delivery.md` when it carries more than one

Common knobs live in the YAML front matter:

- `tracker` / `repository`: issue source, repo checkout, labels, states
- `runtimes`: runtime profiles such as Codex app-server, CLI agents, or custom commands
- `agents`: model/runtime bindings for workflow roles
- `hooks` / `gates`: workflow-specific lifecycle policy
- `observability` / `server`: comments, webhooks, HTTP status

The Markdown body is the workflow policy prompt. The workflow package decides
how to use it. See the full [WORKFLOW.md guide](docs/workflows/workflow-contract.md).

## Supported Surfaces

| Area | Status | Notes |
|---|---|---|
| GitHub tracker | First-class tracker | Public supported path through authenticated `gh`. |
| `local-json` tracker | Development and fixtures | Useful for local tests and examples. |
| Linear tracker | Experimental | Deferred until after the GitHub path is hardened. |
| Supervision | Supported | `systemd --user`. |
| Runtime adapters | Supported | Codex app-server, ACPX Codex, Claude CLI, Hermes agent, custom commands. |

Stable public boundaries are tracked in [docs/public-contract.md](docs/public-contract.md).
Readiness and generic-surface guardrails are tracked in
[docs/harness-engineering.md](docs/harness-engineering.md).

## Documentation

| Doc | Purpose |
|---|---|
| [Installation](docs/operator/installation.md) | Full install, bootstrap, service, and troubleshooting path. |
| [WORKFLOW.md guide](docs/workflows/workflow-contract.md) | Workflow contract structure and examples. |
| [Bundled workflows](docs/workflows/README.md) | Workflow comparison and templates. |
| [Architecture](docs/architecture.md) | Engine/workflow boundary and durable runtime model. |
| [Operator cheat sheet](docs/operator/cheat-sheet.md) | Day-2 commands and debugging. |
| [Symphony conformance](docs/symphony-conformance.md) | Symphony alignment and remaining gaps. |
| [Security](docs/security.md) | Trust model, shell/runtime posture, and secrets. |

## Name

Daedalus built the labyrinth, kept the thread, and understood the risk of
unchecked flight. The project uses the name as a reminder: build the workflow
maze, keep recovery paths visible, and put limits around autonomy.

## License

MIT — see [LICENSE](LICENSE).
