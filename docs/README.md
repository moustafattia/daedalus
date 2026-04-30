# Daedalus docs

Entry point for everything that won't fit on the [project landing page](../README.md).

## Start here

- **[architecture.md](architecture.md)** — the big picture. What Daedalus is, what it isn't, how the pieces fit together.
- **[operator/installation.md](operator/installation.md)** — the supported install, scaffold, verify, and supervise flow.
- **[workflows/README.md](workflows/README.md)** — the two bundled workflows, when to use each, and where their templates live.
- **[public-contract.md](public-contract.md)** — the stability boundary for the first public release.
- **[symphony-conformance.md](symphony-conformance.md)** — where Daedalus matches the current Symphony draft, and where it still differs.
- **[harness-engineering.md](harness-engineering.md)** — repo-level checks that keep the public surface generic, GitHub-first, and template-safe.
- **[release-readiness.md](release-readiness.md)** — public-beta scorecard, launch gates, and next hardening slice.
- **[security.md](security.md)** — the trust model, shell/network posture, and secret-handling expectations.

## How to read these docs

- Generic docs describe the plugin engine: contracts, state stores, runtimes, trackers, service supervision, and observability.
- Workflow docs describe lifecycle policy. `change-delivery` is the opinionated GitHub issue-to-merge path; `issue-runner` is the smaller generic tracker-driven path.
- Operator docs describe installed deployments. SQL examples usually apply to `change-delivery`; `issue-runner` uses persisted status, scheduler, and audit files instead.

## Concepts

What each abstraction *means* — read these before reading code.

| | |
|---|---|
| [Lanes](concepts/lanes.md) | The unit of work. State machine, lifecycle, terminal states. |
| [Leases & heartbeats](concepts/leases.md) | How a single owner stays responsible for a lane. |
| [Runtimes](concepts/runtimes.md) | The shared execution backends: `claude-cli`, `acpx-codex`, `hermes-agent`, `codex-app-server`. |
| [Events](concepts/events.md) | Runtime JSONL events plus workflow audit files. Symphony §10.4 taxonomy + `daedalus.*` namespace. |
| [Stalls](concepts/stalls.md) | `last_activity_ts()` + `stall.timeout_ms` (Symphony §8.5). |
| [Hot-reload & preflight](concepts/hot-reload.md) | Workflow-contract reload (`WORKFLOW.md` first, legacy `workflow.yaml` still loadable) + per-tick preflight (Symphony §6.2 + §6.3). |
| [Shadow → active](concepts/shadow-active.md) | The promotion gate from observation to execution. |

## Operator surface

Day-2 commands and observability.

- [Cheat sheet](operator/cheat-sheet.md) — quickest path to a useful answer
- [Slash commands](operator/slash-commands.md) — every `/daedalus` and `/workflow` form
- [Codex app-server operations](operator/codex-app-server.md) — managed/external listener diagnostics
- [HTTP status surface](operator/http-status.md) — workflow-scoped JSON + HTML endpoints
- [GitHub smoke test](operator/github-smoke.md) — skipped-by-default live test for the supported tracker path
- [Codex app-server smoke tests](operator/codex-app-server-smoke.md) — fake CI harness and opt-in real runtime smoke

## Workflow docs

- [Bundled workflows](workflows/README.md) — overview of `change-delivery` and `issue-runner`
- [WORKFLOW.md guide](workflows/workflow-contract.md) — repo-owned contract location, front matter, and Markdown body
- [change-delivery](workflows/change-delivery.md) — opinionated GitHub SDLC workflow
- [issue-runner](workflows/issue-runner.md) — generic tracker-driven reference workflow
- [examples/change-delivery.workflow.md](examples/change-delivery.workflow.md) — copyable default contract
- [examples/issue-runner.workflow.md](examples/issue-runner.workflow.md) — copyable generic tracker-driven contract

## History & decisions

- [Architectural decision records](adr/) — the *why* behind structural choices

## How these docs are organized

```
docs/
├── README.md                this file
├── architecture.md          big picture
├── public-contract.md       stable public surfaces for the first release
├── symphony-conformance.md  current spec alignment vs. remaining gaps
├── harness-engineering.md   public-readiness checks and guardrails
├── release-readiness.md     launch scorecard and hardening gates
├── security.md              trust model + execution posture
│
├── concepts/                "what does X mean" — one file per abstraction
├── examples/                copyable config baselines
├── workflows/               bundled workflow-specific docs and templates
├── operator/                install + day-2 surface — cheat sheets, commands, endpoints
│
└── adr/                     architectural decisions
```
