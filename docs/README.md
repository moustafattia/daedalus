# Daedalus docs

Entry point for everything that won't fit on the [project landing page](../README.md).

## Start here

- **[architecture.md](architecture.md)** — the big picture. What Daedalus is, what it isn't, how the pieces fit together.
- **[operator/installation.md](operator/installation.md)** — the supported install, scaffold, verify, and supervise flow.
- **[examples/change-delivery.workflow.md](examples/change-delivery.workflow.md)** — copyable public baseline for `WORKFLOW.md`.
- **[public-contract.md](public-contract.md)** — the stability boundary for the first public release.
- **[symphony-conformance.md](symphony-conformance.md)** — where Daedalus matches the current Symphony draft, and where it still differs.
- **[security.md](security.md)** — the trust model, shell/network posture, and secret-handling expectations.

## Concepts

What each abstraction *means* — read these before reading code.

| | |
|---|---|
| [Lanes](concepts/lanes.md) | The unit of work. State machine, lifecycle, terminal states. |
| [Leases & heartbeats](concepts/leases.md) | How a single owner stays responsible for a lane. |
| [Runtimes](concepts/runtimes.md) | The `claude-cli` / `acpx-codex` / `hermes-agent` adapters. |
| [Events](concepts/events.md) | The append-only history. Symphony §10.4 taxonomy + `daedalus.*` namespace. |
| [Stalls](concepts/stalls.md) | `last_activity_ts()` + `stall.timeout_ms` (Symphony §8.5). |
| [Hot-reload & preflight](concepts/hot-reload.md) | Workflow-contract reload (`WORKFLOW.md` first, legacy `workflow.yaml` still loadable) + per-tick preflight (Symphony §6.2 + §6.3). |
| [Shadow → active](concepts/shadow-active.md) | The promotion gate from observation to execution. |

## Operator surface

Day-2 commands and observability.

- [Cheat sheet](operator/cheat-sheet.md) — quickest path to a useful answer
- [Slash commands](operator/slash-commands.md) — every `/daedalus` and `/workflow` form
- [HTTP status surface](operator/http-status.md) — `daedalus serve`, JSON + HTML endpoints

## History & decisions

- [Architectural decision records](adr/) — the *why* behind structural choices
- [Implementation specs](design/) — long-form design specs that became code
- [Superpowers archive](superpowers/) — brainstorm specs + execution plans, one folder per feature

## How these docs are organized

```
docs/
├── README.md                this file
├── architecture.md          big picture
├── public-contract.md       stable public surfaces for the first release
├── symphony-conformance.md  current spec alignment vs. remaining gaps
├── security.md              trust model + execution posture
│
├── concepts/                "what does X mean" — one file per abstraction
├── examples/                copyable config baselines
├── operator/                install + day-2 surface — cheat sheets, commands, endpoints
│
├── adr/                     architectural decisions (immutable record)
├── design/                  implementation specs that shipped
└── superpowers/             brainstorm specs + execution plans (history)
```
