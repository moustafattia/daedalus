# Daedalus Concepts

> **The mental model of Daedalus, broken into bite-sized, interconnected ideas.**
>
> Each concept below is a self-contained document. Some pages are engine-level; some use `change-delivery` as the concrete workflow because that is where the richer lane/action model lives.

---

## Concept Map

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         DAEDALUS CONCEPT MAP                               │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐  │
│  │  CORE RUNTIME    │◄────►│  FAILURE &       │◄────►│  EXECUTION       │  │
│  │                  │      │  RECOVERY        │      │  MODEL           │  │
│  │  • Leases        │      │                  │      │                  │  │
│  │  • Hot-reload    │      │  • Failures      │      │  • Runtimes      │  │
│  │  • Service loops │      │  • Stalls        │      │  • Sessions      │  │
│  │  • State stores  │      │  • Operator      │      │  • Trackers      │  │
│  │  • Contracts     │      │    Attention     │      │                  │  │
│  └────────┬─────────┘      └────────┬─────────┘      └────────┬─────────┘  │
│           │                         │                         │            │
│           └─────────────────────────┼─────────────────────────┘            │
│                                     │                                      │
│                                     ▼                                      │
│  ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐  │
│  │  WORKFLOW MODELS │◄────►│  OBSERVABILITY   │      │  OPERATIONS      │  │
│  │                  │      │  & INTEGRATION   │      │                  │  │
│  │                  │      │                  │      │                  │  │
│  │  • Lanes         │      │  • Events        │      │  • Migration     │  │
│  │  • Actions       │      │  • Observability │      │                  │  │
│  │  • Reviewers     │      │  • Webhooks      │      │                  │  │
│  │  • Shadow/Active │      │  • Tracker Feed  │      │                  │  │
│  └──────────────────┘      └──────────────────┘      └──────────────────┘  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## Engine concepts

The beating heart of Daedalus. These concepts explain how the engine keeps work owned, decides what to do, and survives restarts.

| Concept | One-Liner | Read This If... |
|:---|:---|:---|
| [**Engine**](./engine.md) | Shared durable mechanics: tick, service loop, state stores, scheduler, audit, SQLite. | ...you want the boundary between Daedalus runtime and workflow packages. |
| [**Leases**](./leases.md) | The thread Theseus carried into the labyrinth. Heartbeat-based ownership with automatic recovery. | ...you want to understand how Daedalus prevents split-brain and claims dead lanes. |
| [**Actions**](./actions.md) | The `change-delivery` active/shadow action queue. Queued, idempotent, tracked with composite keys. | ...you want to know how the opinionated workflow guarantees exactly-once execution. |
| [**Shadow → Active**](./shadow-active.md) | Two execution modes: observe safely, then promote to real side effects. | ...you want to validate Daedalus parity before letting it touch real PRs. |
| [**Hot-reload**](./hot-reload.md) | Edit `WORKFLOW.md`, save, next tick picks it up. Bad edits are ignored, not fatal. | ...you want to change policy without restarting the service. |

**The narrative arc:** *Leases* give you ownership → workflow state gives you continuity → *Shadow/Active* gives `change-delivery` safety → *Hot-reload* gives you agility.

## Workflow-specific concepts

These docs use the opinionated `change-delivery` workflow as their concrete
example. They are useful, but they are not the generic engine contract.

| Concept | One-Liner | Read This If... |
|:---|:---|:---|
| [**Lanes**](./lanes.md) | The `change-delivery` unit of work. One selected tracker issue becomes one lane carried through code/review/merge. | ...you want to see the full lifecycle of the opinionated SDLC workflow. |
| [**Reviewers**](./reviewers.md) | The multi-stage review pipeline used by `change-delivery`. | ...you want to see how publish/merge gates are structured. |
| [**Failures**](./failures.md) | `change-delivery` failure/action state. `issue-runner` retry state is documented in its workflow page. | ...you want to know what happens when a review or merge step fails. |
| [**Operator Attention**](./operator-attention.md) | How `change-delivery` escalates when automation reaches its limit. | ...you want to know when Daedalus asks for help. |

---

## Failure & Recovery

Daedalus does not pretend failures don't happen. It models them as first-class state and recovers automatically.

| Concept | One-Liner | Read This If... |
|:---|:---|:---|
| [**Failures**](./failures.md) | First-class `change-delivery` runtime state with retry budgets, recovery actions, and superseding logic. | ...you want to know what happens when a review or merge fails. |
| [**Stalls**](./stalls.md) | A wedged worker holding a lease but making no progress. Detected and terminated automatically. | ...you want to understand how Daedalus kills zombies. |
| [**Operator Attention**](./operator-attention.md) | The state a `change-delivery` lane enters when human judgment is required. | ...you want to know when and why Daedalus asks for help. |

**The narrative arc:** *Failures* are tracked → *Stalls* are detected → *Operator Attention* is the graceful off-ramp when automation hits its limit.

---

## Execution Model

How code gets written, reviewed, and shipped by explicit actors with defined roles.

| Concept | One-Liner | Read This If... |
|:---|:---|:---|
| [**Runtimes**](./runtimes.md) | The thing Daedalus shells out to. Claude CLI, Codex, or any subprocess that speaks the session protocol. | ...you want to add a new AI backend or local tool. |
| [**Sessions**](./sessions.md) | The runtime's handle to a persistent or one-shot execution context. | ...you want to understand how Daedalus manages long-lived coder sessions. |
| [**Reviewers**](./reviewers.md) | Multi-stage review pipeline: internal (Claude), external (Codex Cloud), advisory (optional). | ...you want to see how review gates are structured and enforced. |

**The narrative arc:** *Runtimes* execute → *Sessions* persist state → *Reviewers* gate quality.

---

## Observability & Integration

How Daedalus talks to the outside world and lets operators see what's happening.

| Concept | One-Liner | Read This If... |
|:---|:---|:---|
| [**Events**](./events.md) | Append-only JSONL history of everything that happened. Replayable, auditable, immutable. | ...you want to debug what the system did last Tuesday. |
| [**Observability**](./observability.md) | Watch TUI, HTTP status server, and tracker feedback surfaces. | ...you want to monitor health without SSHing into the box. |
| [**Webhooks**](./webhooks.md) | Pluggable outbound subscribers for audit events. Slack, HTTP JSON, with SSRF guard. | ...you want notifications in your team's chat. |
| [**Tracker Feedback**](./tracker-feedback.md) | Publish workflow updates back to the tracker issue. | ...you want a public, timestamped record of what Daedalus did. |

**The narrative arc:** *Events* record → *Observability* surfaces → *Webhooks* notify → *Tracker Feedback* documents.

---

## Operations

The boring-but-critical stuff that keeps the lights on during transitions.

| Concept | One-Liner | Read This If... |
|:---|:---|:---|
| [**Migration & Cutover**](./migration.md) | Moving from hermes-relay to Daedalus. Filesystem renames, config paths, and the cutover dance. | ...you are upgrading an existing installation. |

---

## How These Connect In `change-delivery`

```
GitHub Issue ──► [Lanes] ──► [Leases] claim ownership
                    │
                    ▼
              [Actions] queued (shadow first)
                    │
                    ▼
              [Runtimes] execute via [Sessions]
                    │
                    ▼
              [Reviewers] gate (internal → external)
                    │
                    ▼
              [Events] record ──► [Observability] surface
                    │                    │
                    ▼                    ▼
              [Tracker Feedback]    [Webhooks] notify
                    │
                    ▼
              [Failures] tracked ──► [Stalls] detected
                    │                    │
                    ▼                    ▼
              [Operator Attention] ◄── recovery
                    │
                    ▼
              [Hot-reload] policy updated
                    │
                    ▼
              [Migration] when upgrading
```

---

## Start Here

**New to Daedalus?** Read in this order:

1. [**Architecture**](../architecture.md) — understand the big picture
2. [**Engine**](./engine.md) — understand shared durable mechanics
3. [**Leases**](./leases.md) — understand how Daedalus stays alive
4. [**Runtimes**](./runtimes.md) — understand how turns execute
5. [**Hot-reload**](./hot-reload.md) — understand how policy changes land
6. [**Workflow docs**](../workflows/README.md) — choose the bundled workflow that matches your use case
7. [**Actions**](./actions.md) — read this when operating `change-delivery`

**Operating Daedalus day-to-day?** Keep these open:

- [**Observability**](./observability.md) — for monitoring
- [**Operator Attention**](./operator-attention.md) — for knowing when to intervene
- [**Events**](./events.md) — for archaeology

**Extending Daedalus?** Read these:

- [**Runtimes**](./runtimes.md) — adding new backends
- [**Reviewers**](./reviewers.md) — changing review policy
- [**Webhooks**](./webhooks.md) — adding new integrations
- [**Workflow docs**](../workflows/README.md) — deciding whether you are extending `change-delivery`, `issue-runner`, or adding a new workflow package

---

## See Also

| Doc | What It Covers |
|---|---|
| [Architecture Overview](../architecture.md) | The big picture — how all concepts fit together |
| [Engine](./engine.md) | The shared runtime mechanisms below workflow packages |
| [Bundled Workflows](../workflows/README.md) | Workflow-specific docs for `change-delivery` and `issue-runner` |
| [Operator Cheat Sheet](../operator/cheat-sheet.md) | Day-to-day commands, SQL, debugging |
| [Slash Commands](../operator/slash-commands.md) | Every `/daedalus` command explained |
| [Contributing](../contributing.md) | How to contribute to Daedalus |
