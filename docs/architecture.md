# Daedalus Architecture

## Executive summary

Daedalus is a **workflow-oriented orchestration layer** for agentic software delivery. It does **not** replace the workflow brain. It wraps that brain in durable runtime mechanics: leases, canonical state, action queues, retries, failures, service supervision, and operator tooling.

In the current YoYoPod deployment:
- the **workflow wrapper** is still the semantic policy engine
- the **Daedalus runtime** is the durable orchestrator
- the **active systemd service** keeps Daedalus running continuously
- the **coder / reviewer actors** are explicit roles, not ad-hoc prompt invocations

The design goal is simple:

> Turn fragile cron-loop automation into explicit, durable, role-based 24/7 workflow orchestration.

---

## 1. Problem statement

Classic agent automation breaks down for the same reasons every time:
- policy is buried in prompts or cron jobs
- state is spread across files, GitHub, and half-finished sessions
- actions are inferred, not queued
- failures are logged but not modeled
- retries are accidental instead of explicit
- handoffs between coder, reviewer, and merge logic are implicit and brittle

That works for toy demos. It fails for long-running SDLC lanes.

Daedalus exists to solve that by introducing:
- a durable runtime
- explicit actions and actors
- canonical current state
- append-only event history
- active/shadow execution modes
- supervised long-running process ownership

---

## 2. Architecture principles

### 2.1 Wrapper is the semantic policy brain
The workflow wrapper decides:
- current read model
- semantic workflow state
- next action
- review/publish/merge policy

### 2.2 Daedalus is the orchestration runtime
Daedalus owns:
- lease / heartbeat
- durable runtime tables
- shadow vs active action rows
- failure tracking
- retry bookkeeping
- operator surfaces

### 2.3 SQLite is current truth, JSONL is history
- **SQLite** stores canonical runtime state now
- **JSONL** stores append-only event and audit history

### 2.4 Actors are explicit
The system is modeled around named roles:
- `Workflow_Orchestrator`
- `Internal_Coder_Agent`
- `Internal_Reviewer_Agent`
- `External_Reviewer_Agent`
- `Advisory_Reviewer_Agent`

### 2.5 Handoffs must be durable
Every meaningful handoff should survive:
- service restarts
- session staleness
- transient CLI failures
- GitHub drift

---

## 3. Repository anatomy

## 3.1 Core files
- `__init__.py` — plugin registration
- `plugin.yaml` — plugin manifest
- `daedalus/schemas.py` — CLI/slash parser schema
- `daedalus/tools.py` — operator surface and service helpers
- `daedalus/runtime.py` — durable Daedalus engine
- `daedalus/alerts.py` — outage alert logic
- `daedalus/watch.py` — TUI frame renderer for `/daedalus watch`
- `daedalus/watch_sources.py` — watch source aggregation (lanes + alerts + events)
- `daedalus/formatters.py` — inspection output formatting (status, doctor, shadow-report)
- `daedalus/migration.py` — relay→daedalus filesystem migration
- `daedalus/observability_overrides.py` — operator observability config overrides
- `scripts/install.py` / `scripts/install.sh` — plugin installation
- `scripts/migrate_config.py` — config path migration helper

## 3.2 Responsibility split

### `__init__.py`
Registers the plugin surfaces:
- slash/session command
- CLI command
- optional operator skill

### `daedalus/tools.py`
Provides the human/operator interface:
- status surfaces
- doctoring
- shadow reports
- active gate checks
- systemd install/start/restart helpers

### `daedalus/runtime.py`
Implements the real orchestration model:
- database schema
- leases
- ingestion
- action derivation
- active action execution
- retries
- failure analysis state
- runtime loops

### `daedalus/alerts.py`
Isolates outage alert decision logic from orchestration logic.

### `daedalus/watch.py` + `watch_sources.py`
Implements the `/daedalus watch` TUI. `watch_sources.py` aggregates three sources into a snapshot dict:
- `active_lanes` — from SQLite
- `alert_state` — from `alerts.py` output
- `recent_events` — from JSONL tail

`watch.py` renders the snapshot into a Rich panel layout. Supports both live mode (1s refresh) and one-shot mode (`--once`).

### `daedalus/formatters.py`
Human-readable panel renderer for all `/daedalus` inspection commands. Each command (`status`, `doctor`, `shadow-report`, `active-gate-status`, `service-status`, `get-observability`) has a dedicated formatter that builds `Section` + `Row` objects and calls `format_panel`. ANSI color is auto-detected; `--format json` bypasses formatting entirely.

### `daedalus/migration.py` + `observability_overrides.py`
`migration.py` handles relay→daedalus filesystem renames (idempotent). `observability_overrides.py` reads/writes the `observability-overrides.json` file that lets operators change GitHub-comments behavior without editing `workflow.yaml`.

---

## 4. Runtime model

## 4.1 Canonical runtime state
Daedalus persists canonical state in SQLite under a workflow-local state path.

Important runtime entities include:
- `lanes`
- `lane_actors`
- `lane_reviews`
- `lane_actions`
- `failures`
- `leases`
- `daedalus_runtime`

This lets Daedalus answer questions like:
- what lane is active?
- who owns the lane actor session?
- which review is pending or complete?
- what action has been requested, failed, or retried?
- is this runtime still the lease holder?

## 4.2 Event log
Daedalus appends semantic events into JSONL for replay/postmortem/operator archaeology.

Typical events:
- shadow action requested
- active action requested
- action failed
- failure detected
- operator attention required
- implementation requested
- internal review requested
- merge requested

## 4.3 Lease model
The runtime loop refreshes a lease/heartbeat on every iteration.

This protects against:
- split-brain active ownership
- fake liveness after a dead process
- orphaned background instances

---

## 5. Execution modes

## 5.1 Shadow mode
Daedalus:
- ingests workflow truth
- derives what it would do
- persists shadow actions
- emits comparison/operator reports
- does **not** own primary side effects

Use shadow mode to validate parity safely.

## 5.2 Active mode
Daedalus:
- ingests workflow truth
- derives active actions
- executes allowed side effects
- records success/failure/retry state

Use active mode for real orchestration.

---

## 6. Background service model

In YoYoPod, Daedalus is supervised as a user-scoped systemd service.

Current active unit points directly at the plugin runtime:
- `python3 .hermes/plugins/daedalus/runtime.py run-active ...`

This gives:
- restart on failure
- explicit runtime profile
- stable working directory and PATH
- durable 24/7 process ownership

That matters because an orchestrator pretending to be a chat session is bullshit. A real orchestrator must have real supervision.

---

## 7. Workflow integration model

Daedalus is intentionally **workflow-aware**, not generic magic.

It consumes workflow truth from the wrapper and then maps it into a durable execution model.

### Wrapper semantic actions
Examples:
- `run_claude_review`
- `publish_ready_pr`
- `merge_and_promote`
- `dispatch_codex_turn`

### Daedalus execution actions
Examples:
- `request_internal_review`
- `publish_pr`
- `merge_pr`
- `dispatch_implementation_turn`
- `dispatch_repair_handoff`

That translation boundary is deliberate.
The wrapper speaks **workflow semantics**.
Daedalus speaks **execution semantics**.

---

## 8. Actor and review model

## 8.1 Internal coder
Usually Codex-backed persistent lane session.

Responsibilities:
- implement changes
- repair findings
- update local branch or PR branch

## 8.2 Internal reviewer
Usually Claude-backed local unpublished-branch gate.

Responsibilities:
- evaluate local unpublished head before publish
- emit local review verdict and findings

## 8.3 External reviewer
Usually Codex Cloud on published PRs.

Responsibilities:
- review ready-for-review PR head
- generate actionable external findings
- gate merge

## 8.4 Advisory reviewer
Optional additional reviewer role, for example Rock Claw.

---

## 9. Policy and phase model

## 9.1 Local phase
No PR yet.
Primary actor: internal coder.
Primary gate: internal reviewer.

Typical states:
- `implementing`
- `implementing_local`
- `awaiting_claude_prepublish`
- `claude_prepublish_findings`
- `ready_to_publish`

## 9.2 Published phase
PR exists and is ready for review.
Primary external gate: Codex Cloud.

Typical states:
- `under_review`
- `findings_open`
- `approved`

## 9.3 Merge phase
If the published PR is clean and mergeable:
- merge
- close issue
- promote next lane

---

## 10. Handoff design

### Handoff A: Orchestrator -> coder
Wrapper or Daedalus dispatches implementation work.

Artifacts:
- worktree
- lane memo
- lane state
- actor session strategy
- target head / PR context

### Handoff B: coder -> internal reviewer
Once a local candidate head exists, Claude reviews the unpublished head.

### Handoff C: internal reviewer -> coder
If local findings exist, repair handoff goes back to the coder lane.

### Handoff D: internal reviewer -> publish path
If local gate is clean, wrapper derives publish.

### Handoff E: publish -> external reviewer
Once PR is ready, Codex Cloud becomes the required reviewer.

### Handoff F: external reviewer -> coder
If external findings exist, repair handoff goes back to the coder lane.

### Handoff G: clean PR -> merge/promote
If no blockers remain, merge and advance the workflow.

This is the heart of the design: **explicit role handoffs, not vibes.**

---

## 11. Failure model

Daedalus models failures as first-class runtime state.

When an active action fails, Daedalus can persist:
- failed action row
- failure summary
- recovery state
- retry count
- optional superseding recovery action

### Why this matters
Without explicit failure state, every retry decision becomes guesswork.
With explicit failure state, the system can answer:
- did this fail before?
- should we retry?
- did retry already happen?
- has this been superseded?
- are we stuck badly enough to require operator attention?

### Recent hardening that matters
Two failure modes were fixed during lane 220 work:
1. failed internal review no longer leaves wrapper review state falsely stuck at `running`
2. failed active `request_internal_review` actions no longer permanently consume the active idempotency slot for that head

That second fix is the difference between a durable orchestrator and a deadlocked queue with nice branding.

---

## 12. Operator surfaces

Daedalus intentionally exposes operator tooling instead of forcing direct DB archaeology.

Typical surfaces:
- `status`
- `shadow-report`
- `doctor`
- `active-gate-status`
- service install/start/restart helpers

These should answer:
- who owns orchestration?
- is the runtime healthy?
- is it fresh or stale?
- does Daedalus agree with wrapper semantics?
- are there unresolved failures?
- is a service crash or lease problem hiding under the hood?

---

## 13. Current YoYoPod deployment interpretation

The current deployment uses a layered model:
- wrapper remains semantic owner
- Daedalus active service is recurring dispatcher
- wrapper `tick` remains manual fallback
- milestone notifier remains as a Hermes cron support job
- outage alerts remain a support surface, not the orchestrator

That is a sensible transitional architecture.
It is not fully pure yet, but it is sane.

---

## 14. Long-term vision

The long-term target is bigger than “a plugin that runs a bot.”

The real target is:

> full agentic SDLC lanes that run continuously, respect policy and review gates, survive failures, and let humans stay passive by default while stepping in only when judgment or escalation is truly needed.

That means:
- each lane is durable
- coding and reviewing are explicit roles
- state transitions are auditable
- failures are recoverable
- humans can observe or intervene without becoming the scheduler
- the system can run 24/7 without degrading into prompt spaghetti

Daedalus is the control-plane skeleton for that future.

---

## 15. Architecture in one sentence

**Daedalus is a durable orchestration runtime that wraps an SDLC workflow brain with leases, canonical state, action queues, role handoffs, retries, and operator tooling so agentic lanes can run continuously without turning into invisible cron-driven chaos.**
