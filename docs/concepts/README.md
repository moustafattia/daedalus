# Daedalus Concepts

> **The mental model of Daedalus, broken into bite-sized, interconnected ideas.**
>
> Each concept below is a self-contained document. Read them in any order — they cross-reference each other where it matters.

---

## Concept Map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DAEDALUS CONCEPT MAP                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐  │
│  │  CORE RUNTIME    │◄────►│  FAILURE &       │◄────►│  EXECUTION       │  │
│  │                  │      │  RECOVERY        │      │  MODEL           │  │
│  │  • Leases        │      │                  │      │                  │  │
│  │  • Lanes         │      │  • Failures      │      │  • Runtimes      │  │
│  │  • Actions       │      │  • Stalls        │      │  • Sessions      │  │
│  │  • Shadow/Active │      │  • Operator      │      │  • Reviewers     │  │
│  │  • Hot-reload    │      │    Attention     │      │                  │  │
│  └────────┬─────────┘      └────────┬─────────┘      └────────┬─────────┘  │
│           │                         │                         │             │
│           └─────────────────────────┼─────────────────────────┘             │
│                                     │                                       │
│                                     ▼                                       │
│  ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐  │
│  │  OBSERVABILITY   │◄────►│  OPERATIONS      │      │  (You are here)  │  │
│  │  & INTEGRATION   │      │                  │      │                  │  │
│  │                  │      │  • Migration     │      │                  │  │
│  │  • Events        │      │                  │      │                  │  │
│  │  • Observability │      │                  │      │                  │  │
│  │  • Webhooks      │      │                  │      │                  │  │
│  │  • Comments      │      │                  │      │                  │  │
│  └──────────────────┘      └──────────────────┘      └──────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Core Runtime

The beating heart of Daedalus. These five concepts explain how the engine keeps lanes alive, decides what to do, and survives restarts.

| Concept | One-Liner | Read This If... |
|:---|:---|:---|
| [**Leases**](./leases.md) | The thread Theseus carried into the labyrinth. Heartbeat-based ownership with automatic recovery. | ...you want to understand how Daedalus prevents split-brain and claims dead lanes. |
| [**Lanes**](./lanes.md) | The unit of work. One GitHub issue becomes one lane, carried from discovery to merge. | ...you want to see the full lifecycle of an automated issue. |
| [**Actions**](./actions.md) | The atomic unit of work. Queued, idempotent, tracked with composite keys. | ...you want to know how Daedalus guarantees exactly-once execution. |
| [**Shadow → Active**](./shadow-active.md) | Two execution modes: observe safely, then promote to real side effects. | ...you want to validate Daedalus parity before letting it touch real PRs. |
| [**Hot-reload**](./hot-reload.md) | Edit `workflow.yaml`, save, next tick picks it up. Bad edits are ignored, not fatal. | ...you want to change policy without restarting the service. |

**The narrative arc:** *Leases* give you ownership → *Lanes* give you work → *Actions* give you execution → *Shadow/Active* gives you safety → *Hot-reload* gives you agility.

---

## Failure & Recovery

Daedalus does not pretend failures don't happen. It models them as first-class state and recovers automatically.

| Concept | One-Liner | Read This If... |
|:---|:---|:---|
| [**Failures**](./failures.md) | First-class runtime state with retry budgets, recovery actions, and superseding logic. | ...you want to know what happens when a review or merge fails. |
| [**Stalls**](./stalls.md) | A wedged worker holding a lease but making no progress. Detected and terminated automatically. | ...you want to understand how Daedalus kills zombies. |
| [**Operator Attention**](./operator-attention.md) | The state a lane enters when the wrapper decides human judgment is required. | ...you want to know when and why Daedalus asks for help. |

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
| [**Observability**](./observability.md) | Three surfaces: watch TUI, HTTP status server, and GitHub comment audit trails. | ...you want to monitor health without SSHing into the box. |
| [**Webhooks**](./webhooks.md) | Pluggable outbound subscribers for audit events. Slack, HTTP JSON, with SSRF guard. | ...you want notifications in your team's chat. |
| [**Comments**](./comments.md) | Publish audit events as comments on the active GitHub issue or PR. | ...you want a public, timestamped record of what Daedalus did. |

**The narrative arc:** *Events* record → *Observability* surfaces → *Webhooks* notify → *Comments* document.

---

## Operations

The boring-but-critical stuff that keeps the lights on during transitions.

| Concept | One-Liner | Read This If... |
|:---|:---|:---|
| [**Migration & Cutover**](./migration.md) | Moving from hermes-relay to Daedalus. Filesystem renames, config paths, and the cutover dance. | ...you are upgrading an existing installation. |

---

## How These Connect

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
              [Comments] publish    [Webhooks] notify
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

1. [**Lanes**](./lanes.md) — understand the unit of work
2. [**Actions**](./actions.md) — understand what Daedalus actually does
3. [**Leases**](./leases.md) — understand how it stays alive
4. [**Shadow → Active**](./shadow-active.md) — understand how to deploy safely
5. [**Failures**](./failures.md) — understand how it handles bad days

**Operating Daedalus day-to-day?** Keep these open:

- [**Observability**](./observability.md) — for monitoring
- [**Operator Attention**](./operator-attention.md) — for knowing when to intervene
- [**Events**](./events.md) — for archaeology

**Extending Daedalus?** Read these:

- [**Runtimes**](./runtimes.md) — adding new backends
- [**Reviewers**](./reviewers.md) — changing review policy
- [**Webhooks**](./webhooks.md) — adding new integrations

---

## See Also

| Doc | What It Covers |
|---|---|
| [Architecture Overview](../architecture.md) | The big picture — how all concepts fit together |
| [Operator Cheat Sheet](../operator/cheat-sheet.md) | Day-to-day commands, SQL, debugging |
| [Slash Commands](../operator/slash-commands.md) | Every `/daedalus` command explained |
| [Contributing](../contributing.md) | How to contribute to Daedalus |
