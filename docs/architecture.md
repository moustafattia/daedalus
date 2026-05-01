# Daedalus Architecture

<div align="center">

![Daedalus Architecture Diagram](assets/daedalus-architecture-diagram.svg)

> **Daedalus is a durable orchestration runtime that runs repo-owned SDLC workflows with leases, persisted state, action/scheduler queues, role handoffs, retries, and operator tooling so agentic work can run continuously without turning into invisible cron-driven chaos.**

</div>

---

## The 30-Second Read

| Question | Answer |
|---|---|
| **What is it?** | A plugin that turns fragile cron-loop automation into explicit, durable 24/7 workflow orchestration. |
| **What problem does it solve?** | Agentic SDLC breaks because policy is buried in prompts, state is scattered, failures are logged but not modeled, and handoffs are implicit. |
| **How?** | Leases. Workflow-specific durable state. JSON/JSONL audit history. Shadow/active execution where supported. Workflow packages with explicit contracts. |
| **Who owns what?** | The **workflow package** decides *what* should happen. **Daedalus** decides *how* to orchestrate it durably. |

---

## The Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL TRIGGERS                                   │
│   Tracker Issue        Operator (/daedalus)    WORKFLOW.md (hot-reload)     │
└─────────────────────────────────────────────────────────────────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────────────┐
│     DAEDALUS ENGINE                  │  │    WORKFLOW PACKAGE                  │
│  ─────────────────────────────────   │  │  ─────────────────────────────────   │
│  Runtime Loop                        │  │  Status / Read Model                 │
│    Tick → Ingest → Derive → Dispatch │◄─┤  Policy Engine                       │
│    → Record                          │  │  Roles / Hooks / Gates               │
│                                      │  │  Workflow State Machine              │
│  Leases (heartbeat · TTL · recovery) │  │  Handoffs (explicit, durable)        │
│                                      │  │                                      │
│  Durable State ─► SQLite source       │  │  Semantic Actions                    │
│                 JSON projections     │  │    select_issue                      │
│                                      │  │    render_prompt                     │
│  JSONL ───► turn_started ·           │  │    publish_ready_pr                  │
│             turn_completed · stall   │  │                                      │
│                                      │  │  ▼                                   │
│  Shadow Mode ──► observe · plan      │  │  Execution Actions                   │
│  Active Mode ──► execute · dispatch  │◄─┤    dispatch_turn                     │
│                                      │  │    publish_pr                        │
│  Operator Surfaces                   │  │    merge_pr                          │
│    /daedalus status · doctor · watch │  │    run_hooks                         │
│    shadow-report · active-gate       │  │                                      │
└──────────────────────────────────────┘  └──────────────────────────────────────┘
         │                                           │
         ▼                                           ▼
┌────────────────────────────┐              ┌────────────────────────────┐
│  SUPERVISION               │              │  EXTERNAL                  │
│  systemd service           │              │  GitHub API                │
│  /daedalus watch (TUI)     │              │  Webhooks (Slack / HTTP)   │
│  HTTP status :8765         │              │  Tracker feedback          │
└────────────────────────────┘              └────────────────────────────┘
```

---

## The Two Brains

Daedalus has **two brains** that speak different languages. The boundary between them is the most important design decision in the system.

### Brain 1: The Workflow Package (Semantic)

> *"What should happen next?"*

The workflow package is the **policy engine**. It knows about:
- the tracker and issue model
- workflow-specific states and transitions
- role and prompt structure
- review/publish/merge policy when the workflow has those concepts

It speaks **workflow semantics**:
```
select_issue
render_prompt
publish_ready_pr
merge_and_promote
```

Examples:
- `change-delivery` knows about issue lanes, PRs, reviewer gates, and merge
  policy. Its default production configuration uses GitHub for both `tracker`
  and `code-host`, but those are distinct config boundaries.
- `issue-runner` knows about tracker selection, isolated issue workspaces, lifecycle hooks, and one-agent execution.

### Brain 2: Daedalus Runtime (Execution)

> *"How do I orchestrate this durably?"*

Daedalus is the **execution engine**. It knows about:
- Leases and heartbeats
- workflow-specific durable state stores
- action queues / scheduler queues and idempotency keys
- Retry bookkeeping and failure tracking
- Shadow vs active execution modes

It speaks **execution semantics**:
```
request_internal_review
publish_pr
merge_pr
dispatch_implementation_turn
dispatch_repair_handoff
```

### Why two vocabularies?

Because **policy changes faster than orchestration**. A workflow package can change its issue lifecycle, gate structure, or prompt strategy. Daedalus still has to guarantee that dispatch happens durably, survives crashes, and retries correctly.

---

## The Five Guarantees

Daedalus exists to provide five guarantees that fragile cron-loop automation cannot:

### 1. Exactly-One Ownership (Leases)

```
┌─────────┐    acquire lease     ┌─────────┐
│ Runtime │ ───────────────────► │  Lane   │
│    A    │ ◄─────────────────── │  #42    │
└─────────┘    heartbeat (TTL)   └─────────┘
      │
      │  process dies
      ▼
┌─────────┐    lease expires     ┌─────────┐
│ Runtime │ ◄─────────────────── │  Lane   │
│    B    │ ───────────────────► │  #42    │
└─────────┘   claim on next tick └─────────┘
```

- **Exclusivity:** One runtime owns a lane at a time.
- **Liveness:** Heartbeats prove the owner is alive.
- **Recovery:** Any instance can claim an expired lease. No coordinator needed.

### 2. Exactly-Once Actions (Idempotency)

Every active action has a composite key:
```
lane:<id>:<action_type>:<head_sha>
```

This prevents:
- Double-dispatching the same review on the same head
- Re-running `merge_pr` after the PR is already merged
- Spawning infinite coder sessions for a single issue

### 3. State Is Tracked, Not Guessed

| Layer | Storage | Purpose |
|---|---|---|
| **Runtime DB** | `runtime/state/daedalus/daedalus.db` | Engine work items, running work, retries, runtime sessions, token totals, plus `change-delivery` lanes/actions/reviews/failures |
| **Scheduler JSON** | `memory/workflow-scheduler.json` | Generated operator snapshot of scheduler state for file-oriented tooling |
| **Runtime JSONL** | `runtime/memory/daedalus-events.jsonl` | Daedalus orchestration events |
| **Workflow JSONL** | `memory/workflow-audit.jsonl` | workflow-specific audit trail |
| **Lane files** | `.lane-state.json` | `change-delivery` lane-local handoff artifacts |
| **Lane memos** | `.lane-memo.md` | human-readable handoff notes |

Never reconstruct current state by replaying events. Current engine execution state is in SQLite; status and scheduler JSON files are projections for operators and file-oriented tools.

### 4. Bad Edits Don't Crash the Loop

```mermaid
flowchart TD
    A[tick begins] --> B{workflow contract changed?}
    B -- no --> C[reuse last ConfigSnapshot]
    B -- yes --> D[parse + validate]
    D -- ok --> E[swap AtomicRef]
    D -- fail --> F[keep last good config]
    F --> G[emit config_reload_failed]
    C --> H[continue tick]
    E --> H
    G --> H
```

A bad `WORKFLOW.md` edit is **ignored**, not fatal. The next valid save picks up automatically.

### 5. Recovery Is Automatic

When an action fails:
1. Failed row is persisted with `retry_count`
2. Next tick checks if retry budget remains
3. If yes: requeue with incremented `retry_count`
4. If no: transition to `operator_attention_required`
5. Human intervenes, or the lane is archived

Lost workers never block forward motion.

---

## Bundled Workflows

Daedalus does not ship one universal lifecycle. It ships a generic engine plus
bundled workflow packages.

| Workflow | Shape | Best for | Docs |
|---|---|---|---|
| `change-delivery` | issue -> code -> internal review -> PR -> external review -> merge | opinionated SDLC automation | [`workflows/change-delivery.md`](workflows/change-delivery.md) |
| `issue-runner` | tracker issue -> workspace -> hooks -> prompt -> one agent run | generic tracker-driven automation | [`workflows/issue-runner.md`](workflows/issue-runner.md) |

The workflow package owns the lifecycle. Daedalus owns the durable execution
machinery around it.

That means:
- `change-delivery` can define reviewer roles, PR publish, and merge gates.
- `issue-runner` can stay smaller and focus on issue selection plus isolated execution.
- both reuse the same workflow contract loader, runtime adapters, hot-reload primitives, and stall detection.

If you are looking for workflow-specific states, prompts, or operator commands,
read the workflow docs rather than treating the generic architecture as if it
described one universal lane state machine.

---

## Execution Modes

### Shadow Mode: "What would I do?"

- Reads workflow state
- Derives next action
- Writes **shadow** action rows (no idempotency check)
- Emits comparison reports
- **No side effects**

Use shadow mode to validate parity safely before promoting to active.

### Active Mode: "What actually happens."

- Reads workflow state
- Derives next action
- Writes **active** action rows (idempotency enforced)
- Dispatches to real runtimes
- Records success / failure / retry state

Promotion from shadow to active is gated by `active-gate-status` — an explicit operator step, not a config edit.

---

## The Data Flow (One Tick)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   TICK      │────►│    LOAD     │────►│   DERIVE    │────►│   DISPATCH  │
│  (cron/     │     │ workflow +  │     │ next step   │     │  to runtime │
│   manual)   │     │ runtime     │     │ from state  │     │  (active)   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                                                                  │
                                                                  ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   NEXT      │◄────│   RECORD    │◄────│   RESULT    │◄────│   RUNTIME   │
│   TICK      │     │  success/   │     │  success/   │     │  executes   │
│             │     │  failure    │     │  failure    │     │  turn       │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

Each tick:
1. **Load** — Read the workflow contract plus the workflow package's current state
2. **Derive** — Ask the workflow package what operation should happen next
3. **Dispatch** — If the derived action is new and its idempotency key is free, dispatch to runtime
4. **Record** — Write result (success/failure/retry) to the workflow's state store plus JSONL audit events
5. **Heartbeat** — Refresh lease to prove liveness

---

## Operator Surfaces

Daedalus exposes tooling instead of forcing DB archaeology.

| Surface | Command | What It Answers |
|---|---|---|
| **Status** | `/daedalus status` | Runtime row, lane count, paths, freshness |
| **Doctor** | `/daedalus doctor` | Full health check across all subsystems |
| **Watch** | `/daedalus watch` | Live TUI: lanes + alerts + events |
| **Shadow Report** | `/daedalus shadow-report` | Diff shadow plan vs active reality |
| **Active Gate** | `/daedalus active-gate-status` | What's blocking promotion to active |
| **Service** | `/daedalus service-status` | systemd health snapshot |
| **HTTP** | `GET localhost:8765/api/v1/state` | JSON snapshot for dashboards |

---

## Repository Anatomy

```
daedalus/
├── __init__.py              # Plugin registration
├── plugin.yaml              # Plugin manifest
├── schemas.py               # CLI/slash parser schema
├── daedalus_cli.py          # Operator surface + systemd helpers
├── runtime.py               # Durable engine (the heart)
│   ├── database schema
│   ├── leases + heartbeats
│   ├── ingestion
│   ├── action derivation
│   ├── active execution
│   ├── retries + failure tracking
│   └── runtime loops
├── alerts.py                # Outage alert logic
├── watch.py                 # TUI frame renderer
├── watch_sources.py         # Lane + alert + event aggregation
├── formatters.py            # Human-readable inspection output
├── migration.py             # historical filesystem migration helpers
├── runtimes/                # Shared execution backends (Codex, Claude, Hermes)
├── trackers/                # Shared tracker clients (GitHub, local-json, Linear experimental, ...)
├── code_hosts/              # Shared PR/review/merge clients (GitHub)
└── workflows/
    ├── __init__.py          # Workflow loader + CLI dispatcher
    ├── shared/              # Shared paths, config snapshots, stalls
    ├── change_delivery/
        ├── workflow.py      # Semantic policy engine
        ├── dispatch.py      # Action dispatch
        ├── actions.py       # Action primitives
        ├── reviews.py       # Review policy + findings
        ├── sessions.py      # Session protocol
        ├── runtimes/        # Workflow-local compatibility re-exports
        ├── reviewers/       # Reviewer implementations
        ├── webhooks/        # Outbound webhook subscribers
        ├── server/          # HTTP status surface
        └── workspace.py     # Audit fanout + tracker feedback wiring
    └── issue_runner/
        ├── tracker.py       # Issue selection + workflow-specific tracker policy
        ├── workspace.py     # Issue workspace lifecycle + hooks
        ├── cli.py           # status / doctor / tick
        ├── preflight.py     # Dispatch-gated config checks
        └── schema.yaml      # Workflow contract shape
```

---

## Current Deployment Shape

The supported community shape keeps code, policy, and state separated:

| Layer | Owner | Role |
|---|---|---|
| **Plugin** | `~/.hermes/plugins/daedalus` | engine, workflow packages, shared runtimes/trackers/code hosts |
| **Repo contract** | `WORKFLOW.md` / `WORKFLOW-<workflow>.md` | workflow policy and operator config |
| **Workflow root** | `~/.hermes/workflows/<owner>-<repo>-<workflow-type>` | durable runtime data and workspace-local state |
| **Daedalus service** | systemd user unit | recurring dispatcher/supervisor |
| **Operator surfaces** | Hermes slash/CLI, watch, HTTP | inspection, diagnosis, manual override |

Manual ticks remain useful for debugging, but the service loop is the supported long-running path.

---

## Long-Term Vision

> Full agentic SDLC lanes that run continuously, respect policy and review gates, survive failures, and let humans stay passive by default while stepping in only when judgment or escalation is truly needed.

That means:
- Each lane is durable
- Coding and reviewing are explicit roles
- State transitions are auditable
- Failures are recoverable
- Humans observe or intervene without becoming the scheduler
- The system runs 24/7 without degrading into prompt spaghetti

**Daedalus is the control plane for that future.**

---

## See Also

| Doc | What It Covers |
|---|---|
| [`workflows/README.md`](workflows/README.md) | Which bundled workflow to use and where its template lives |
| [`workflows/change-delivery.md`](workflows/change-delivery.md) | The opinionated issue-to-PR SDLC workflow |
| [`workflows/issue-runner.md`](workflows/issue-runner.md) | The generic tracker-driven bundled workflow |
| [`concepts/lanes.md`](concepts/lanes.md) | Lane state machine, selection, workspace binding |
| [`concepts/actions.md`](concepts/actions.md) | Action types, idempotency, shadow vs active |
| [`concepts/failures.md`](concepts/failures.md) | Failure lifecycle, retry policy, lane-220 fixes |
| [`concepts/leases.md`](concepts/leases.md) | Lease acquisition, heartbeat, recovery, split-brain |
| [`concepts/reviewers.md`](concepts/reviewers.md) | Internal/external/advisory review pipeline |
| [`concepts/observability.md`](concepts/observability.md) | Watch TUI, HTTP server, tracker feedback |
| [`concepts/operator-attention.md`](concepts/operator-attention.md) | When attention triggers, thresholds, recovery |
| [`operator/cheat-sheet.md`](operator/cheat-sheet.md) | Day-to-day commands, debugging, SQL cheats |

---

## Architecture in One Sentence

**Daedalus is a durable orchestration runtime that runs repo-owned SDLC workflows with leases, persisted state, action/scheduler queues, role handoffs, retries, and operator tooling so agentic work can run continuously without turning into invisible cron-driven chaos.**
