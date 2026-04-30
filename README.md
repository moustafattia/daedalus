<div align="center">

![Daedalus banner](assets/daedalus-banner.gif)

<br>

**A Hermes Agent plugin 💠 Reads issues, writes PRs**

*Daedalus built the Labyrinth, gave Theseus the thread, and warned Icarus not to fly too close to the sun.*

*This one does all three.*

</div>

---

## What it is

Daedalus automates your **SDLC** with agents — driven by your GitHub issues. Label an issue and Daedalus walks it through your workflow: picks the right agent for each stage, tracks state, survives crashes, ships when done. The first workflow we ship is **Change-Delivery** (`Issue → Code → Review → Merge`). More are coming.

## Three myths, one engine

<table>
<tr>
<td width="33%" valign="top">

### 🧵 The thread

One owner per issue. A heartbeat keeps the thread taut. If the holder dies mid-flight, another instance picks it up on the next tick — work never gets dropped or duplicated.

→ [Leases & heartbeats](docs/concepts/leases.md)

</td>
<td width="33%" valign="top">

### 🌀 The labyrinth

Every issue walks a clear path through the workflow — picked, coded, reviewed, shipped. State is tracked, not guessed. You always know where each issue is and how it got there.

→ [Lanes](docs/concepts/lanes.md) · [Events](docs/concepts/events.md)

</td>
<td width="33%" valign="top">

### 🪶 The wings

Daedalus warned Icarus, then flew home. Edits take effect on the next tick. A bad edit doesn't crash the loop — it gets ignored until you fix it. Wedged workers clean up automatically.

→ [Hot-reload](docs/concepts/hot-reload.md) · [Stalls](docs/concepts/stalls.md)

</td>
</tr>
</table>

## What's in the box

- **Configurable agent per role.** Pick which agent and model handles each role in your workflow — Codex for review, Claude for code, your own agent for merge. Set in `WORKFLOW.md`.
- **Hot-reload.** Edit `WORKFLOW.md` and the next tick picks it up. Bad edits don't crash the loop; they get ignored until you fix them.
- **Stall detection.** Wedged agents get terminated automatically and the lane retries. No zombie workers.
- **Symphony-aligned event vocabulary** — events follow the [openai/symphony](https://github.com/openai/symphony) taxonomy, so observability tools work across systems.
- **Operator commands** — `/daedalus status`, `/daedalus doctor`, `/workflow change-delivery status`, `/workflow change-delivery tick`.
- **Live status dashboard** — ships separately as a Hermes-Agent watch plugin.

## Supported path

Daedalus is ready to publish on one explicit path:

- **Platform:** Linux
- **Install path:** `hermes plugins install attmous/daedalus --enable`
- **Plugin home after install:** `~/.hermes/plugins/daedalus`
- **Workflow root:** `~/.hermes/workflows/<owner>-<repo>-<workflow-type>`
- **Host Python:** `python3` with `yaml` and `jsonschema` available
- **24/7 supervision:** `systemd --user`
- **Runtime adapters:** whatever `WORKFLOW.md` names must exist on the host (`acpx-codex`, `claude-cli`, `hermes-agent`, ...).

If you want the exact operator contract we support, read [docs/public-contract.md](docs/public-contract.md).

## Install & quick start

```bash
sudo apt install python3-yaml python3-jsonschema
hermes plugins install attmous/daedalus --enable
cd /path/to/your/repo
hermes daedalus bootstrap
```

Edit the generated workflow contract:

```bash
$EDITOR ~/.hermes/workflows/your-org-your-repo-change-delivery/WORKFLOW.md
```

Then bring it up:

```bash
hermes daedalus service-up
```

Start Hermes in your repo:

```bash
cd /path/to/your/repo
hermes
```

`bootstrap` infers the repo root and GitHub slug, creates
`~/.hermes/workflows/<owner>-<repo>-<workflow-type>/WORKFLOW.md`, and wires the
repo checkout to that workflow root. The YAML front matter is the machine
config; the Markdown body is the shared workflow policy.

Inside Hermes:

```text
/daedalus status
/daedalus doctor
/workflow change-delivery status
```

For manual scaffold paths, pip installs, local-dev installation, service modes,
and every lower-level command, read
[docs/operator/installation.md](docs/operator/installation.md).

The full operator surface is in the [cheat sheet](docs/operator/cheat-sheet.md); every slash command is catalogued in [slash-commands.md](docs/operator/slash-commands.md).

## How it fits together

```mermaid
flowchart LR
  ISSUE["GitHub issue<br/>active-lane label"]

  subgraph DAEDALUS["Daedalus engine"]
    direction TB
    WF["WORKFLOW.md<br/>stages, roles, gates"]
    LANE["Lane<br/>one run per active issue"]
    WF -.-> LANE
  end

  subgraph AGENTS["Agents per role"]
    direction TB
    A1["Coder &middot; Claude"]
    A2["Reviewer &middot; Codex"]
    A3["Merger &middot; ..."]
  end

  PR["Merged PR"]

  ISSUE ==> LANE
  LANE ==> AGENTS
  AGENTS ==> PR
```

A **labeled issue** is the trigger. The **engine** ticks; for every active issue, it spins up a **lane** — one run of the workflow defined in `WORKFLOW.md` — and dispatches to the **agent** configured for the current stage. Agents write commits, post review comments, and eventually merge. When the workflow's last gate clears, the PR closes the loop.

## Philosophy

- **State is tracked, not guessed.** The workflow always knows where each issue stands.
- **A bad edit in `WORKFLOW.md` doesn't crash anything.** It just gets ignored until you fix it.
- **Recovery is automatic.** Lost workers never block forward motion.
- **No packaging theater.** This is a plugin payload — flat top level, on purpose.
- **`--json` is the default operator dialect.** Humans read formatters, scripts read JSON.

## Documentation

- **[docs/architecture.md](docs/architecture.md)** — the big picture, end to end.
- **[docs/operator/installation.md](docs/operator/installation.md)** — the supported install, scaffold, verify, and supervise path.
- **[docs/public-contract.md](docs/public-contract.md)** — the stability boundary for the first public release.
- **[docs/symphony-conformance.md](docs/symphony-conformance.md)** — what is already Symphony-aligned, what is only partial, and what is still missing.
- **[docs/security.md](docs/security.md)** — the trust model, shell/runtime posture, and secret-handling expectations.
- **[docs/concepts/](docs/concepts/)** — short explainers for each moving part: lanes, leases, runtimes, events, hot-reload, stalls.
- **[docs/operator/](docs/operator/)** — day-to-day commands, the operator cheat sheet, the full slash-command catalogue.

## License

MIT — see [LICENSE](LICENSE).

<div align="center">
<sub>Daedalus is a Hermes plugin. Hermes is the messenger; Daedalus is the loom.</sub>
</div>
