<div align="center">

![Daedalus banner](assets/daedalus-banner.gif)

<br>

**The durable thread for agent workflows.**

*Daedalus the craftsman built the Labyrinth, gave Theseus the thread, and warned Icarus not to fly too close to the sun.*

*This Daedalus does the orchestration version of all three.*

<br>

[Architecture](docs/architecture.md) · [Concepts](docs/concepts/) · [Operator](docs/operator/cheat-sheet.md) · [HTTP status](docs/operator/http-status.md) · [ADRs](docs/adr/)

</div>

---

## What it is

Daedalus is the runtime layer underneath your agentic workflow. Your workflow wrapper is still the brain — it decides *what* should happen next. Daedalus is the loom underneath: it owns the loop, the state, the leases, the retries, the recovery. It's the part you don't want to rewrite for every project.

## Three myths, three guarantees

<table>
<tr>
<td width="33%" valign="top">

### 🧵 The thread

One owner per lane. A heartbeat keeps the thread taut. If the holder dies, the thread is found again on the next tick and another instance takes over — no coordinator, no split-brain.

→ [Leases & heartbeats](docs/concepts/leases.md)

</td>
<td width="33%" valign="top">

### 🌀 The labyrinth

Lanes move through an explicit state machine. SQLite is current truth, JSONL is append-only history. Nothing is inferred from prompt context, nothing is reconstructed by replay.

→ [Lanes](docs/concepts/lanes.md) · [Events](docs/concepts/events.md)

</td>
<td width="33%" valign="top">

### 🪶 The wings

Daedalus warned Icarus, then flew home. Hot-reload picks up config changes per-tick; bad edits keep the last good config alive; stalls terminate wedged workers without crashing the loop.

→ [Hot-reload](docs/concepts/hot-reload.md) · [Stalls](docs/concepts/stalls.md)

</td>
</tr>
</table>

## What you get out of the box

- A **shadow → active** promotion gate so you can watch a new instance for a day before letting it write
- Multiple **runtime adapters** — Claude one-shot, Codex persistent-session, generic Hermes agent
- A **localhost HTTP status surface** with `/api/v1/state`, per-lane debug views, and a manual refresh
- An **operator surface** — `/daedalus status`, `shadow-report`, `doctor`, `active-gate-status`, `iterate-active`
- A **Symphony-aligned** event taxonomy with a one-release alias window for prefixed event names
- ~700 tests so you can refactor without flinching

## Install

```bash
./scripts/install.sh                                  # default Hermes home
./scripts/install.sh --hermes-home /path/to/hermes-home
./scripts/install.sh --destination /tmp/daedalus      # explicit destination
```

The installer copies the plugin payload only — no packaging theater.

## Quick start

```bash
/usr/bin/python3 -m pytest          # 1. run the tests
./scripts/install.sh --destination /tmp/daedalus    # 2. drop into a scratch Hermes home
export HERMES_ENABLE_PROJECT_PLUGINS=true
cd <project-root>
hermes                              # 3. launch
```

Inside Hermes:

```text
/daedalus status
/daedalus shadow-report
/daedalus doctor
```

The full operator surface is documented in the [operator cheat sheet](docs/operator/cheat-sheet.md). Direct `runtime.py` invocations (for debugging without the Hermes shell) live in the [slash commands catalog](docs/operator/slash-commands.md).

## Philosophy

- **The thread, not the loom.** Daedalus runs the loop. Your wrapper picks the next thread.
- **SQLite is now, JSONL is history.** Never reconstruct current state by replaying events.
- **Crash is a bug, not a strategy.** Bad config skips dispatch; reconciliation never stops.
- **`--json` is the default operator dialect.** Humans read formatters, scripts read JSON.
- **No packaging theater.** This is a plugin payload. Flat top level, on purpose.

## Where to read next

| Audience | Start here |
|---|---|
| New operator | [docs/operator/cheat-sheet.md](docs/operator/cheat-sheet.md) |
| New contributor | [docs/architecture.md](docs/architecture.md) → [docs/concepts/](docs/concepts/) |
| Integrator (HTTP) | [docs/operator/http-status.md](docs/operator/http-status.md) |
| Plugin author | [docs/concepts/runtimes.md](docs/concepts/runtimes.md) |
| Decision archaeologist | [docs/adr/](docs/adr/) |

## License

MIT — see [LICENSE](LICENSE).

<div align="center">
<sub>Daedalus is a Hermes plugin. Hermes is the messenger; Daedalus is the loom.</sub>
</div>
