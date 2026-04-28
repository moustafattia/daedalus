<div align="center">

![Daedalus banner](assets/daedalus-banner.gif)

<br>

**The durable thread for your agents' workflows.**

*Daedalus the craftsman built the Labyrinth, gave Theseus the thread, and warned Icarus not to fly too close to the sun.*

*This Daedalus does the orchestration version of all three.*

<br>

[Architecture](docs/architecture.md) · [Concepts](docs/concepts/) · [Operator](docs/operator/cheat-sheet.md) · [ADRs](docs/adr/)

</div>

---

## What it is

Daedalus runs your agent workflows reliably, 24/7. You describe the work — its stages, hand-offs, and definition of "done". Daedalus turns it into a system that ships. The first workflow we ship and dogfood is **Code-Review** (`Issue → Code → Review → Merge`). More are coming.

## Three myths, three guarantees

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

- **Configurable agent per role.** Pick which agent and model handles each role in your workflow — Codex for review, Claude for code, your own agent for merge. Set in `workflow.yaml`.
- **Hot-reload.** Edit `workflow.yaml` and the next tick picks it up. Bad edits don't crash the loop; they get ignored until you fix them.
- **Stall detection.** Wedged agents get terminated automatically and the lane retries. No zombie workers.
- **Symphony-aligned event vocabulary** — events follow the [openai/symphony](https://github.com/openai/symphony) taxonomy, so observability tools work across systems.
- **Operator commands** — `/daedalus status`, `/daedalus doctor`, `/workflow code-review status`, `/workflow code-review tick`.
- **Live status dashboard** — ships separately as a Hermes-Agent watch plugin.

## Install & quick start

```bash
# 1. Get the code
git clone https://github.com/attmous/daedalus.git
cd daedalus

# 2. Install into your Hermes home
./scripts/install.sh

# 3. Launch Hermes with project plugins enabled
export HERMES_ENABLE_PROJECT_PLUGINS=true
cd <project-root>
hermes
```

Inside Hermes:

```text
/daedalus status
/daedalus doctor
/workflow code-review status
```

**Need a non-default install location?**

```bash
./scripts/install.sh --hermes-home /path/to/hermes-home    # custom Hermes home
./scripts/install.sh --destination /tmp/daedalus           # arbitrary destination
```

The full operator surface is in the [cheat sheet](docs/operator/cheat-sheet.md); every slash command is catalogued in [slash-commands.md](docs/operator/slash-commands.md).

## Philosophy

- **State is tracked, not guessed.** The workflow always knows where each issue stands.
- **A bad edit doesn't crash anything.** It just gets ignored until you fix it.
- **Recovery is automatic.** Lost workers never block forward motion.
- **`--json` is the default operator dialect.** Humans read formatters, scripts read JSON.
- **No packaging theater.** This is a plugin payload — flat top level, on purpose.

## Documentation

- **[docs/architecture.md](docs/architecture.md)** — the big picture, end to end.
- **[docs/concepts/](docs/concepts/)** — short explainers for each moving part: lanes, leases, runtimes, events, hot-reload, stalls.
- **[docs/operator/](docs/operator/)** — day-to-day commands, the operator cheat sheet, the full slash-command catalogue.
- **[docs/adr/](docs/adr/)** — architectural decision records.

## License

MIT — see [LICENSE](LICENSE).

<div align="center">
<sub>Daedalus is a Hermes plugin. Hermes is the messenger; Daedalus is the loom.</sub>
</div>
