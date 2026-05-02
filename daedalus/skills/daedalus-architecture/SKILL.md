---
name: daedalus-architecture
description: Design a long-running Hermes-native orchestrator that uses explicit state, event queues, and bounded on-demand LLM reasoning instead of cron heartbeat control loops.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, orchestration, plugin, event-driven, workflow, async]
---

# Daedalus Architecture

Use this when a user wants to evolve a cron/polling workflow into a Hermes-native, long-running orchestration system with explicit role handoffs and one visible control surface.

Current Daedalus shape:
- repo-owned workflow contracts live in `WORKFLOW.md` or `WORKFLOW-<workflow>.md`
- plugin code is the source of truth under `~/.hermes/plugins/daedalus`
- workflow instance data lives under `~/.hermes/workflows/<owner>-<repo>-<workflow-type>`
- shared runtimes live under `daedalus/runtimes/`; shared trackers live under `daedalus/trackers/`
- shared engine execution state lives in SQLite; workflow JSON/JSONL files are projections and audit trails
- `change-delivery` adds lane/action/review/failure tables on top of the shared engine tables

## Core decision model

Do not model the system as:
- a normal forever chat session
- repeated cron invocations pretending to be an orchestrator
- stateless delegate_task children as the main execution primitive

Model it as:
- a long-running orchestrator service/process
- a Hermes plugin that provides the control/integration surface
- explicit canonical state + durable event queue
- bounded Hermes reasoning only at ambiguous decision points

Short version:
- plugin = control surface
- orchestrator service = runtime
- Hermes reasoning = on-demand judgment engine

## Recommended naming pattern

Use a reusable product/plugin name plus project-specific deployment names.

Pattern:
- architecture/plugin/process: `Daedalus`
- project deployment: `<ProjectName> Daedalus`

Example:
- `Daedalus`
- `<ProjectName> Daedalus`

## Runtime mapping inside Hermes

Best fit:
- create a Hermes plugin
- register CLI commands with `ctx.register_cli_command(...)`
- optionally register slash commands with `ctx.register_command(...)`
- have the CLI command launch/manage the long-running orchestrator service

Current practical note:
- on one Hermes build used during an early Daedalus bootstrap, generic plugin CLI registration was stored by the plugin manager, but the safest verified operator surface was still the slash-command path
- treat slash commands as the reliable control surface unless you have explicitly verified that your target Hermes build exposes generic project-plugin CLI subcommands in `hermes <plugin> ...`
- registering both is still worth doing so the plugin is forward-compatible when the CLI plumbing is present
- concrete field finding: a project-local Daedalus plugin can exist under `.hermes/plugins/daedalus/` with `register_cli_command(...)` and still not show up in `hermes plugins list` or `hermes daedalus ...`; on at least one checked host, `hermes daedalus doctor ...` failed with `invalid choice: 'daedalus'` while the local plugin code and runtime worked fine
- practical consequence: do not assume operator docs can tell people to run `hermes daedalus ...` just because the plugin source exists; first verify the actual CLI surface on that host, and be ready to use the runtime script or in-session slash command instead
- a good first real operator command is `shadow-report`: summarize runtime status, active lane, legacy next action, Daedalus-derived next action, compatibility, recent shadow actions, and runtime freshness in one shot
- build `shadow-report` by reading the live legacy status, ingesting it into Daedalus canonical state, deriving the current shadow action without depending on a newly persisted row, and separately querying recent `lane_actions` for operator context
- also query the runtime lease row directly; do not trust `latest_heartbeat_at` alone
- compute operator-facing freshness fields from both heartbeat and lease state: heartbeat age, lease expiry, stale boolean, stale reasons
- this avoids two dumb failure modes: (1) idempotent action persistence returns no newly inserted action so your report falsely says Daedalus has no current opinion, and (2) the DB says `running` while the lease is actually expired and the operator gets a false sense of health
- for post-publish findings-open lanes, do not derive `dispatch_repair_handoff` from review verdict alone; match the workflow package's real gating: actionable repair brief for the current head, routable session, and no already-recorded repair handoff for that exact review/head pair
- otherwise Daedalus shadow parity lies and operators get a fake mismatch where Daedalus screams "dispatch repair" while the workflow correctly returns `noop` because the repair handoff already went out or no repair brief exists yet
- the natural next operator command after `shadow-report` is `doctor`: classify stale runtime, missing lease, split-brain risk, active-lane inconsistency, shadow-parity drift, and unresolved active-execution failures as explicit checks with status/severity/summary/details
- once shadow parity is trustworthy, the next operational upgrade is a supervised service layer (for example a systemd user service) instead of an ad-hoc background shell process
- for systemd user supervision, generate/install dedicated units instead of one overloaded service: a shadow observer unit running `python3 scripts/daedalus.py run-shadow ...` and an active executor unit running `python3 scripts/daedalus.py run-active ...`
- give the shadow and active units distinct service names and default instance ids so operators can manage them explicitly and avoid the dumbest class of accidental profile confusion during cutover
- both units should set `WorkingDirectory` to the workflow root, enable restart-on-failure, and include an explicit `PATH=` environment when the legacy workflow depends on user-installed CLIs like `gh`
- if you forget the PATH bit, the service can look half-alive while crash-looping on `FileNotFoundError: 'gh'`, which is an embarrassing way to learn what environment systemd actually gives you
- once the shadow loop is good enough to trust, run it as a real long-lived background process and verify ownership before spawning another copy; first check for an existing `daedalus.py run-shadow` process, then start one instance with a stable instance id, and verify lease freshness twice (immediately and after another interval)
- a good failure slice after basic active execution is not another happy-path runner but structured failure handling: when an active action fails, mark the `lane_actions` row failed, insert a `failures` row, set lane `operator_attention_required`, update runtime `latest_error_*`, and emit `active_action_failed`, `failure_detected`, and `operator_attention_required` events
- then teach `shadow-report` / `doctor` to surface unresolved active failures directly, or operators will have a "healthy" runtime that is silently dead in the only way that matters
- the key verification is not just `runtime_status=running`; confirm the runtime owner and lease owner match, `latest_heartbeat_at` advances over time, and `expires_at` keeps moving forward. If those are not advancing, your daemon is fake and the operator report will drift back into stale-runtime lies
- for supervised active cutover, operator tooling should also check service supervision separately from lease freshness: if the runtime owner is the active service profile, `shadow-report` / `doctor` should surface whether `daedalus-active@<workflow-name>.service` is installed, enabled, and actually active instead of pretending a stale or crashed service is covered by heartbeat checks alone
- operator-facing status should also surface ownership posture directly, not force archaeology: include primary owner, whether Daedalus is primary, fallback watchdog mode, cutover gate allowance/reasons, and whether the supervised active service is healthy
- for active-lane consistency checks, compare issue numbers extracted from multiple legacy surfaces instead of trusting one field: `activeLane.number`, `ledger.activeLane`, `nextAction.issueNumber`, implementation branch/worktree/session name, and open PR branch/title when available
- surface split-brain risk even in shadow mode when the runtime still claims `running` without a valid lease; severity can stay lower than active-mode split-brain, but hiding it is stupid

Important runtime finding:
- plugin `ctx.inject_message(...)` is CLI-only and returns `False` in gateway mode
- therefore, do not base gateway-visible orchestration on plugin message injection

Use the plugin for:
- `hermes daedalus run`
- `hermes daedalus status`
- `hermes daedalus pause`
- `hermes daedalus resume`
- operator-facing slash/status surfaces
- one stable operator-visible session identity as the human-facing workflow projection
- lifecycle hooks where useful
- bundling namespaced Daedalus skills for operator guidance and reusable procedural memory

Hook guidance:
- gateway hooks are useful for startup automation, alerts, logging, and webhook forwarding in gateway environments
- plugin hooks are useful for CLI + gateway observability, tool/session lifecycle observation, and Daedalus-specific guardrails
- hooks are support infrastructure only; they must not become the canonical state store, live event queue, workflow state machine, or primary dispatcher

Useful bundled skills include:
- `daedalus:operator`
- `daedalus:failure-analysis`
- `daedalus:cutover`
- `daedalus:rollback`
- `daedalus:event-debugging`

Do not treat the plugin itself as the daemon. The plugin starts or integrates the daemon.

## State architecture

Use both SQLite and JSONL with hard separation of responsibilities.

SQLite:
- canonical current truth
- lanes
- current phase/state
- action queue / action status
- leases / locks
- actor session ownership
- review requests
- failure cases
- idempotency keys

JSONL:
- append-only audit/event history
- replay / postmortem / archaeology

Skills:
- procedural memory only
- operator playbooks, cutover/rollback guides, failure-analysis guides, event-debugging references
- never canonical workflow state, live queue, lease store, retry store, or mutable per-lane handoff truth

Rule:
- SQLite = truth now
- JSONL = history forever

Do not use JSONL alone as the canonical state store unless you enjoy operational self-harm.

## Event model

Drive progression from explicit events, not periodic polling.

Minimum event fields:
- `event_id`
- `lane_id`
- `event_type`
- `created_at`
- `producer`
- `causal_action_id`
- `head_sha`
- `payload_json`
- `dedupe_key`

Useful event types:
- `lane_promoted`
- `implementation_requested`
- `implementation_progressed`
- `implementation_completed`
- `implementation_failed`
- `internal_review_requested`
- `internal_review_completed`
- `pr_published`
- `pr_updated`
- `external_review_pending`
- `external_review_clean`
- `external_review_findings_open`
- `merge_requested`
- `merge_completed`
- `next_lane_promoted`
- `failure_detected`
- `error_analysis_requested`
- `error_analysis_completed`
- `operator_attention_required`

## Actor model

Recommended roles:
- `Workflow_Orchestrator`
- `Internal_Coder_Agent`
- `Internal_Reviewer_Agent`
- `External_Reviewer_Agent`
- `Workflow_Error_Analyst`

Semantics:
- `Workflow_Orchestrator` is the sole owner of workflow policy and canonical state transitions
- `implementer` should usually use a resumable runtime when the stage spans multiple ticks
- `reviewer` should be a bounded review run with a fresh context when possible
- `External_Reviewer_Agent` is an external event source / review signal source
- `Workflow_Error_Analyst` is bounded and only invoked when deterministic recovery is insufficient

## Persistent sessions vs stateless children

Strong rule:
- persistent sessions for the main coding lane
- stateless delegate_task children only for bounded analysis/research/review helpers
- bundled workflow templates default to `codex-app-server`
- use `hermes-agent` selectively when a workflow stage benefits from Hermes skills/tooling

Current practical finding:
- Codex app-server is the primary resumable runtime path
- Hermes Agent is the first-class local agent runtime path
- treat Codex App Server as a future optional alternate backend only after Daedalus's canonical state, queue, leases, and action model are proven stable
- even if adopted later, Codex App Server must remain only an execution substrate under Daedalus ownership, never the canonical owner of lane state

Use persistent sessions for:
- implementation continuity
- repair loops
- branch evolution
- long-running lane ownership

Use stateless children for:
- log analysis
- failure diagnosis
- advisory review
- architecture comparison
- bounded synthesis tasks

## How the long-running service should use Hermes runtime and LLM reasoning

The orchestrator service should be a deterministic event loop first.
It should use Hermes only at bounded decision points.

Split responsibilities:

### Control layer
The service/process does:
- hold lease ownership
- read SQLite state
- consume the event queue
- write JSONL audit logs
- dispatch actions
- enforce retry budgets / timeouts
- classify obvious failures
- decide whether LLM reasoning is needed

### Reasoning layer
Hermes is invoked on demand for:
- ambiguous failure diagnosis
- recovery recommendation among legal actions
- repair brief synthesis
- operator-facing explanations
- bounded diagnostic evidence gathering and reduction, optionally using `execute_code` when multi-step tool processing is useful

Do not let Hermes become the event loop.
Do not run one endless LLM turn forever.
Do not use `execute_code` as the orchestrator loop, canonical state manager, queue owner, or main actor dispatch mechanism.

## Recommended failure-handling tiers

### Tier 1: deterministic infra/runtime failures
No LLM needed.

Examples:
- subprocess exit non-zero
- lock already held
- missing worktree
- API timeout with known retry policy
- stale actor session with retry budget remaining

### Tier 2: structured workflow ambiguity
Use Hermes reasoning.

Examples:
- conflicting review and lane signals
- repeated failure with multiple plausible legal recoveries
- stale vs superseded review evidence
- wedged lane with ambiguous next step

### Tier 3: operator attention
Stop pretending autonomy is working.

Examples:
- retry budget exhausted
- inconsistent canonical state
- repeated invalid reasoning outputs
- unrecoverable merge/publish contradiction

## Bounded reasoning contract

When invoking Hermes for error analysis, provide bounded input only:
- current canonical lane snapshot
- last N events
- last action request/result
- relevant stderr/stdout/tool trace excerpts
- actor health metadata
- retry/failure counters
- fixed legal action enum

Require strict JSON output only:
- `failure_class`
- `root_cause`
- `confidence`
- `recommended_action`
- `reasoning_summary`
- `evidence_refs`
- `escalate`

`recommended_action` must be one of a fixed enum such as:
- `retry_same_action`
- `restart_actor_session`
- `request_internal_review`
- `dispatch_repair_handoff`
- `push_pr_update`
- `publish_pr`
- `mark_operator_attention`
- `wait_for_event`
- `abort_lane`

Reject anything outside the schema.

## Migration strategy

Do not rewrite the live workflow in place.

Recommended migration:
1. take an immediate timestamped snapshot backup of the existing workflow artifacts
2. build Daedalus side-by-side as V2
3. keep the old watchdog-centric engine active during development
4. run Daedalus in shadow mode first (derive actions, record results, no primary side effects)
5. compare Daedalus behavior against the live engine
6. cut over with an explicit lease / feature flag so only one primary orchestrator exists
7. demote old watchdog behavior to fallback reconciler only

Rule:
- never allow split-brain primary ownership during cutover

## Design invariants

Freeze these early:
- one orchestrator, one writer
- plugin is the Hermes integration surface, not the daemon itself
- SQLite is canonical truth
- JSONL is append-only history
- events trigger transitions
- actors do not own workflow policy
- error analysis has bounded inputs and bounded legal outputs
- all side effects must be idempotent
- lane-state files are projections/handoff artifacts, not master truth

## Deliverables before implementation

Before coding, write:
1. ADR / architecture note
2. SQLite schema spec
3. event schema spec
4. actor contract spec
5. failure-analysis schema
6. cutover and rollback checklist

## Recommended implementation order

Do not jump straight to the forever-running daemon or plugin surface.

Recommended order:
1. take and validate a checkpoint backup first
2. freeze ADR + schema/contract/checklist docs
3. build a side-by-side Daedalus runtime skeleton
4. add canonical SQLite init, JSONL event log, runtime lease, and status reporting
5. add live legacy-status ingestion into canonical Daedalus tables
6. add shadow action derivation for key states
   - healthy local implementation -> noop / still working
   - internal review pending -> request internal review
   - ready to publish -> publish request
   - published clean -> merge request
   - external findings open -> repair handoff
   - local head ahead of published PR -> push PR update
7. persist shadow actions into `lane_actions` without executing them
8. emit shadow action events and compare Daedalus-derived actions against legacy `nextAction`
9. add heartbeat / lease refresh and a one-shot shadow iteration loop shell
10. only then build the long-running service loop and plugin control surface
11. first active-execution slice should usually wrap the existing workflow package's side-effect command (for example `dispatch-implementation-turn`) while Daedalus owns leases, action rows, and execution accounting
12. when expanding active execution beyond the first slice, keep the same pattern: add or reuse a dedicated workflow CLI subcommand per side effect (for example `dispatch-internal-review`, `dispatch-repair-handoff`, `push-pr-update`, or `publish-ready-pr`) and route Daedalus execution through a small action-runner registry keyed by Daedalus `action_type`
- when the next active slice is a repair-handoff path, do not route Daedalus through a generic implementation command and hope workflow internals happen to do the right thing; add a dedicated workflow command (for example `dispatch-repair-handoff`) backed by a shared helper that both direct execution and reconcile/tick can call
- that shared-helper extraction matters because duplicated repair-handoff logic across reconcile and direct execution will drift, and then Daedalus active mode and workflow-derived status will disagree in deeply annoying ways

Rule:
- runtime and shadow parity before UI polish
- plugin/control surface after the orchestrator has something real to control
- first active execution should prefer Daedalus-owned action records plus workflow-backed side-effect execution over prematurely reimplementing every side effect from scratch
- for the next active slices, do not pile on bespoke one-off execution branches forever; use a bounded runner map so each new action type is an incremental wrapper-backed addition instead of another spaghetti conditional
- not every active action belongs to the coder actor: review requests should target `Internal_Reviewer_Agent`, while orchestration-owned side effects like merge/promotion should target `Workflow_Orchestrator` directly rather than pretending the coder owns them
- before auto-executing any active action, the runtime should enforce a real gate instead of vibes: Daedalus marked as desired primary owner, active execution explicitly enabled, runtime actually running in `active` mode, legacy watchdog disabled, and current Daedalus-vs-legacy action parity still compatible. If any of those fail, block the iteration and say why.

Real implementation finding:
- do not jump straight from ADR into daemon code
- the first safe Daedalus slice is: validated backup -> frozen specs -> side-by-side runtime skeleton -> status command -> live legacy-state ingestion into canonical SQLite -> shadow action derivation/persistence -> legacy-vs-Daedalus comparison reporting
- this order keeps the migration observable and reduces the chance of split-brain or invisible semantic drift

Current proven implementation sequence from an early Daedalus bootstrap:
1. create and validate a timestamped backup before touching runtime code; if the full tarball is too large/slow, create a slim but validated backup containing workflow config/docs/memory/scripts/tests/state/archive plus the active lane worktree snapshot
2. implement a side-by-side Daedalus runtime script first (for example `scripts/daedalus.py`) instead of touching the legacy watchdog engine
3. start with a minimal but real core: SQLite init, WAL pragmas, runtime lease acquisition, JSONL event append, and shadow-mode bootstrap
4. add tests first for the runtime skeleton before expanding features; the first useful slice is DB init, lease enforcement, and bootstrap event creation
5. next add `status` and `ingest-live` commands so Daedalus can observe the legacy workflow and canonically ingest the current active lane, actor backend, and normalized review rows without executing side effects
6. keep shadow derivation logic intentionally narrow at first (for example one internal-review request rule), then expand it incrementally to mirror workflow policy

Useful early file layout proven in practice:
- specs under `docs/specs/`
- side-by-side runtime under `scripts/daedalus.py`
- phase bootstrap tests under `tests/test_daedalus_phase1_skeleton.py`
- canonical DB under `runtime/state/daedalus/daedalus.db` for current workflow roots
- append-only runtime event log under `runtime/memory/daedalus-events.jsonl`
- workflow-owned status/scheduler/audit projections under `memory/` unless a workflow explicitly configures different storage paths

Practical lessons learned:
- do not trust a huge timed-out tarball just because the file exists; validate with `gzip -t` and checksum or it is garbage
- the active lane can move while architecture work is happening; Daedalus shadow ingestion must tolerate live drift and treat imported legacy state as observation, not authority over GitHub/worktree truth
- if `sqlite3` CLI is unavailable on the host, Python-based verification plus tests are enough; do not block on missing shell conveniences
- do not assume historical managed jobs live only in `coreJobNames`; on some old configs, `coreJobNames` can be empty while the real watchdog/notification jobs are listed under `hermesJobNames`
- therefore migration-era pause/resume/status logic should operate on a managed-job union (`coreJobNames` + `hermesJobNames`) or Daedalus cutover gating will lie that the watchdog is still enabled even after a pause
- when importing legacy review state, normalize it into stable internal/external reviewer rows immediately so Daedalus policy can remain role-based while still recording backend-specific fields like `claudeCode` and `codexCloud`
- once active execution starts creating retry or recovery actions, `request_active_actions_for_lane()` must return already-requested active rows before deriving new ones, or queued retries become invisible and the loop falsely reports `no-active-actions`
- for deterministic actor-side failures, a good v1 recovery policy is: first failure of coder-side actions (`dispatch_implementation_turn` / `dispatch_repair_handoff`) gets one bounded automatic same-action retry; if that retry also fails, queue a dedicated `restart_actor_session` recovery action; only after the restart action fails should Daedalus mark operator attention
- that restart slice wants a dedicated workflow command (for example `restart-actor-session`) instead of overloading generic implementation dispatch through hidden prompt conventions
- when a queued recovery action succeeds, resolve the prior failure row (`resolved_at`, `resolution_action_id`) or your doctor report will keep screaming about dead failures that were already recovered
- once deterministic recovery runs out, ambiguous failures should move into a bounded `Workflow_Error_Analyst` path instead of jumping straight to vibes or human panic: build a compact analysis input (lane snapshot, actor health, recent event window, last action payload, retry/failure counters, narrow allowed actions), persist that evidence, and emit `error_analysis_requested` / `error_analysis_completed`
- treat the analyst like an untrusted structured-output component: validate every required field (`failure_class`, `root_cause`, `confidence`, `recommended_action`, `reasoning_summary`, `evidence_refs`, `escalate`), reject outputs outside the allowed action subset, and fall back to `mark_operator_attention` on invalid output or analyst execution failure
- v1 can ship a real runtime-backed default analyst without yet calling an external LLM: use bounded heuristic analysis for ambiguous actions like `publish_pr`, `push_pr_update`, `request_internal_review`, and `merge_pr`, and keep it schema-valid so live active execution no longer collapses into "analyst unavailable"
- give operators a direct inspection surface for that backend (for example `analyze-failure --failure-id ...`) so they can re-run bounded analysis on recorded failures without manufacturing a new failure event
- once failure analysis is real, `shadow-report` and `doctor` should stop hiding the interesting bits: surface `root_cause`, `recommended_action`, `confidence`, and a recovery-state view (queued recovery vs operator attention vs resolved) for unresolved failures
- that recovery-state surfacing should join the failed action to any superseding queued recovery action instead of guessing from failure class alone, otherwise operators can't tell the difference between "waiting on bounded recovery" and "dead and needs a human"
- go one step further and compute explicit failure urgency from recovery state + age: queued recovery is usually `warning`; operator attention / failed recovery is `critical`; unresolved no-recovery cases should age from `warning` to `critical` after a bounded threshold so the operator surface ranks pain instead of merely listing corpses
- queued recovery also needs its own stall semantics: if the superseding recovery action stays `requested`/`dispatched` beyond the bounded threshold, mark the failure `recovery_stalled` and promote urgency to `critical` instead of pretending queued recovery is still healthy forever
- reporting is not enough for stalled recovery forever; after repeated active-iteration detection, Daedalus should persist a bounded stalled-detection count in failure metadata and escalate lane operator attention with an explicit reason like `recovery-stalled:<action_type>` once the threshold is crossed, while emitting a single operator-attention event instead of spamming duplicates every loop
- active action ownership should follow role truth, not lazy defaults: `request_internal_review` targets `Internal_Reviewer_Agent`; `publish_pr`, `push_pr_update`, and `merge_pr` are orchestrator-owned side effects; only coder-session actions target `Internal_Coder_Agent`
- legacy status snapshots can be internally messy or partially stale; compare against the workflow-semantic fields that actually drive decisions, not every incidental field in the file
- for shadow parity, an implementing lane should only derive Daedalus `noop` when the actor is healthy, the session recommendation is effectively continue/poke, and a current head SHA exists; otherwise derive a dispatch action such as `dispatch_implementation_turn` so missing head/session-health gaps do not get falsely treated as healthy progress
- compatibility reporting should allow a legacy action name and Daedalus action name to differ when they are semantically the same policy decision; maintain an explicit compatibility map instead of pretending naming drift is a runtime mismatch

## Good operator language

Prefer phrasing like:
- "one visible workflow owner"
- "event-driven progression"
- "explicit handoffs"
- "bounded analysis"
- "persistent actor session"

Avoid fluffy nonsense like:
- "autonomous forever chat"
- "just let the LLM orchestrate it"
- "everything is a prompt"
