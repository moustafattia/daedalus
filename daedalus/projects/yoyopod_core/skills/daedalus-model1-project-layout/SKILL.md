---
name: daedalus-model1-project-layout
description: Structure Daedalus as a single plugin with generic Daedalus core plus project-specific adapter code under adapters/<project>, project-local runtime assets under projects/<project>, and a central path module for legacy-to-Model-1 migration.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architecture, plugins, daedalus, yoyopod, repo-layout]
---

# Daedalus Model 1 Project Layout

Historical note: this skill captures an older Model 1 repo layout where
`projects/<project>/runtime/` and `projects/<project>/workspace/` were treated
as the default project-local homes. The supported public model is now:

- real repo checkout as the canonical codebase
- `WORKFLOW.md` under the workflow instance root as the contract
- runtime state under `~/.hermes/workflows/<owner>-<repo>-<workflow-type>/`

Use this when reorganizing Daedalus before a full two-plugin split.

## Goal

Keep **one plugin repo** for now, but make the boundary explicit:
- top-level files = generic Daedalus engine/operator surface
- `adapters/<project>/` = importable project-specific adapter code
- `projects/<project>/` = project-local config, runtime, workspace, docs
- mutable runtime state and cloned product repo live under the project subtree, but separate from adapter code

## Recommended layout

```text
daedalus/
├── plugin.yaml
├── __init__.py
├── schemas.py
├── tools.py
├── runtime.py
├── alerts.py
├── tests/
├── docs/
├── adapters/
│   └── yoyopod_core/
│       ├── __init__.py
│       ├── paths.py
│       ├── workflow.py
│       ├── status.py
│       ├── actions.py
│       ├── reviews.py
│       ├── sessions.py
│       ├── prompts.py
│       ├── github.py
│       └── health.py
└── projects/
    └── yoyopod_core/
        ├── config/
        │   └── project.json
        ├── docs/
        ├── runtime/      # gitignored live mutable data
        │   ├── memory/
        │   ├── state/
        │   └── logs/
        └── workspace/    # gitignored cloned product repo(s)
            └── yoyopod-core/
```

## Why this layout

### 1. One plugin, honest boundary
This is Model 1: one plugin repo now, cleaner split later.
- Daedalus core stays generic at repo top level.
- Project-specific workflow logic sits under `adapters/yoyopod_core/`.
- Project-local data lives under `projects/yoyopod_core/`.
- Future Model 2 extraction becomes moving `adapters/yoyopod_core/` behind a formal adapter boundary rather than rewriting everything.

### 2. Do not mix adapter code, config, runtime, and workspace
Keep four categories separate:
- `adapters/yoyopod_core/` — project-specific workflow code
- `projects/yoyopod_core/config/` — project config/defaults
- `projects/yoyopod_core/runtime/` — mutable state, SQLite, logs, projections
- `projects/yoyopod_core/workspace/` — cloned application repo(s)

Do **not** collapse them into one folder or one file. That just recreates the god-object problem in directory form.

### 3. Add a central path module early
Create `adapters/yoyopod_core/paths.py` before the big move.

It should own:
- default workflow-root resolution
- runtime db/event-log path resolution
- alert-state path resolution
- project-data-root discovery
- plugin-entrypoint path lookup (the canonical workflow CLI at `~/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod`; the earlier `scripts/yoyopod_workflow.py` wrapper has been retired)
- runtime-layout fallback rules: if `runtime/` exists under the workflow root, store mutable state under `runtime/{memory,state,logs}`; otherwise fall back to the legacy top-level `memory/` and `state/` layout

Recommended default workflow-root resolution order learned from the migration slice:
1. explicit env var (`DAEDALUS_WORKFLOW_ROOT`)
2. current working directory or nearest ancestor containing `config/workflow.yaml`
3. repo-local `projects/yoyopod_core/` as the Model 1 target default

This keeps the migration from spreading path assumptions across `runtime.py`, `tools.py`, and `alerts.py`.

### 4. Runtime data can live under projects/, but must stay mutable-only
If the user wants everything project-specific under `projects/`, use `projects/yoyopod_core/runtime/`.

But treat it as live mutable data:
- gitignore it
- never treat it as source code
- keep services and runtime code aware that this path is mutable and environment-specific

### 5. Cloned product repo belongs under workspace/
If the product repo should also live under the project subtree, place it at:
- `projects/yoyopod_core/workspace/yoyopod-core/`

This keeps product code distinct from adapter code and runtime state.

### 6. Start by delegating through the adapter, not by moving all logic at once
A safe first slice is:
- add directory scaffolding
- add `paths.py`
- route runtime/tools/alerts through central path helpers
- make `adapters/yoyopod_core/status.py` delegate `build_status()` to the legacy wrapper
- update Daedalus call sites to go through the adapter bridge (`tools.py` shadow/doctor paths and `runtime.py` ingest-live path) instead of reaching into the wrapper directly

That creates a real boundary immediately without forcing a flag-day rewrite.

### 7. First extracted read-model slices that proved useful
After the path bridge lands, the next low-risk extractions are:
- `adapters/yoyopod_core/health.py`
  - `compute_health(...)`
  - `lane_operator_attention_reasons(...)`
  - `compute_stale_lane_reasons(...)`
- `adapters/yoyopod_core/sessions.py`
  - `decide_session_action(...)`
  - `latest_lane_progress_epoch(...)`
- `adapters/yoyopod_core/status.py`
  - keep loading the legacy wrapper for raw facts
  - but **recompute** `implementation.sessionActionRecommendation`, `staleLaneReasons`, and `health` in the adapter

This is the right shape for incremental migration: raw facts can still come from the wrapper for a while, but interpretation starts moving into the adapter immediately.

### 8. Extract `nextAction` in owned slices, not all at once
The best migration pattern is **workflow extraction by safe branch clusters**.

The first `adapters/yoyopod_core/workflow.py` slice that worked well was:
- merge path for clean published PRs
- stale-lane publish path
- stale-lane Claude prepublish path
- operator-attention noop
- healthy implementation-lane fresh-session noop
- implementation-in-progress dispatch
- ready-to-publish path
- fallback to wrapper `nextAction` for everything else

The next successful slice expanded adapter ownership to:
- `push_pr_update`
- no-progress budget retry (`reason=no-progress-budget-reached`)
- failure-budget retry (`reason=failure-retry-budget-reached`)
- postpublish repair restart (`reason=codex-cloud-findings-need-repair`, `mode=postpublish_repair`)

The following slice moved review-driven handoff gating into `adapters/yoyopod_core/reviews.py` and had `workflow.py` call:
- `should_dispatch_claude_repair_handoff(...)`
- `should_dispatch_codex_cloud_repair_handoff(...)`

so adapter-owned `nextAction` could also cover:
- `mode=claude_repair_handoff`
- `mode=codex_cloud_repair_handoff`

This staged extraction matters. Pulling `_derive_next_action()` wholesale into the adapter is a good way to break things. Own the stable branches first, keep fallback behavior for the rest, then continue branch-by-branch.

Practical rule:
- `status.py` should call `workflow.derive_next_action(normalized_status)`
- `workflow.py` should return adapter-owned decisions for known stable cases
- unknown/unmigrated cases should return the wrapper-provided `nextAction`

### 9. Tools should consume adapter status, not wrapper status
Once `status.py` exists, `tools.py` should stop doing:
- `daedalus._load_legacy_workflow_module(...).build_status()`

and instead call a small bridge like:
- `_build_project_status(workflow_root)`
- which delegates to `workflows.status.build_status(...)`

This keeps the boundary honest. Daedalus/tools can still tolerate wrapper-backed data during migration, but they should not reach into the wrapper directly anymore.

### 10. Wrapper `build_status()` should become a compatibility shim
A good intermediate cut is:
- rename the old monolithic wrapper implementation to `build_status_raw()`
- add a new wrapper `build_status()` that tries to load installed adapter status and falls back to `build_status_raw()` if the adapter is unavailable
- update adapter status loading to prefer `build_status_raw()` when present, so you avoid recursion after the wrapper starts delegating back into the plugin

This keeps the old wrapper path operational while shifting real status ownership into the adapter.

### 11. Update the install payload as soon as the adapter tree matters
Once the installed workflow starts relying on adapter code, update `scripts/install.py` so the plugin payload includes:
- `adapters/`
- `projects/`

Then add install tests that assert the destination plugin tree contains:
- `adapters/yoyopod_core/...`
- `projects/yoyopod_core/...`

Otherwise the source repo layout and the deployed plugin layout diverge, which is a stupid way to break the migration.

### 12. Dirty-branch testing rule
During the migration, do **not** assume the full existing suite is green. If the branch already has unrelated failures, add targeted tests for the new boundary and run only those first:
- path-resolution tests (`tests/test_yoyopod_core_paths.py`)
- adapter status-bridge tests (`tests/test_yoyopod_core_adapter_status.py`)
- tools/runtime bridge tests (`tests/test_yoyopod_core_tools_bridge.py`)
- extracted helper tests (`tests/test_yoyopod_core_health.py`, `tests/test_yoyopod_core_sessions.py`, `tests/test_yoyopod_core_stale_lane.py`, `tests/test_yoyopod_core_reviews.py`)
- extracted workflow-derivation tests (`tests/test_yoyopod_core_workflow.py`)
- install-payload tests (`tests/test_install.py`)

This keeps the Model 1 move from getting buried under unrelated runtime failures.

### 13. Good extraction order learned from the migration
Use this order:
1. path resolution + directory scaffolding
2. status bridge
3. health/stale-lane/session helpers
4. `nextAction` branch slices
5. review/handoff helpers
6. action execution paths

That order worked because it moves interpretation first and leaves heavy side effects for later. Doing it backwards would have been stupid.

### 14. Explicit action-entrypoint shim pattern that worked
For wrapper commands with real side effects, do **not** jump straight to `tick()`.

Use this migration order instead:
1. `publish_ready_pr()` / `merge_and_promote()`
2. reviewer/coder dispatch entrypoints:
   - `dispatch_implementation_turn()`
   - `dispatch_inter_review_agent_review()`
   - `dispatch_claude_review()`
3. remaining explicit commands like `restart_actor_session()` / `push_pr_update()`
4. `tick()` last

For each wrapper entrypoint:
- rename the old implementation to `<name>_raw()`
- add an adapter bridge in `adapters/yoyopod_core/actions.py`
- make the public wrapper function a shim that tries installed adapter actions first and falls back to `<name>_raw()`

Concrete pattern:
- wrapper:
  - `def publish_ready_pr_raw(): ...old logic...`
  - `def publish_ready_pr():`
    - `adapter_actions = _load_adapter_actions_module()`
    - `return adapter_actions.publish_ready_pr(WORKSPACE)`
    - fallback: `return publish_ready_pr_raw()`
- adapter:
  - `def publish_ready_pr(workflow_root):`
    - load legacy wrapper module
    - prefer `publish_ready_pr_raw` if present
    - otherwise fall back to `publish_ready_pr`

Repeat the same pattern for merge/review/dispatch entrypoints.

The later migration slices confirmed the same pattern also works cleanly for:
- `push_pr_update()`
- `dispatch_repair_handoff()`
- `restart_actor_session()`

By the time those are done, the wrapper should be a shim for essentially every explicit side-effect command, leaving `tick()` as the last high-blast-radius orchestration function to convert. That sequencing is important: do the leaf commands first, then make `tick()` prefer adapter orchestration and fall back to `tick_raw()`.

A concrete final `tick()` migration that worked in practice:
1. add `adapters/yoyopod_core/tick.py`
   - `plan_tick(status)`
   - `execute_tick_action(workflow_root, action)`
   - `run_tick(workflow_root)`
2. change `adapters/yoyopod_core/actions.py::tick(workflow_root)` to call `run_tick(workflow_root)` instead of re-bridging to wrapper `tick_raw()`
3. rename wrapper `tick()` to `tick_raw()`
4. add wrapper shim `tick()` that tries installed adapter actions first, fallback to `tick_raw()`
5. add focused tests for:
   - planner preferring already-derived `nextAction`
   - executor routing `dispatch_codex_turn` implementation vs repair modes correctly
   - `run_tick()` returning `{before, action, executed, after}`

Important experiential findings from live verification:
- after the shim lands, a hanging `tick()` is **not automatically a shim bug**
- in the live YoYoPod setup, `tick_raw()` initially blocked because it dispatched into `dispatch_implementation_turn()` which then blocked in `_run_acpx_prompt()` inside `subprocess.run(...).communicate()`
- so if live `tick --json` hangs, debug the execution path before assuming recursion

Recommended live-debug procedure for this exact situation:
1. keep the shim change small and covered by focused adapter tests
2. verify `status --json` still works after install
3. probe `tick --json` carefully because it may perform real work
4. if `tick` hangs, use `faulthandler.dump_traceback_later(...)` around `module.tick()` or run the CLI under a short timeout to capture the blocked stack
5. confirm whether the hang is in adapter/wrapper recursion or in downstream execution such as `_dispatch_lane_turn()` / `_run_acpx_prompt()`
6. kill any background probe process once the blocked frame is identified

Then fix operator behavior, not structure:
- background long-running tick actions instead of waiting in the foreground
- keep short actions (`publish_ready_pr`, `push_pr_update`, `merge_and_promote`) foreground
- persist background tick state under `runtime/memory/tick-dispatch/active.json`
- surface it in status as `tickDispatch`
- auto-archive stale/dead dispatch state into `runtime/memory/tick-dispatch/history/` on the next status read

Critical live gotcha discovered during this migration:
- the legacy wrapper shim file itself may be missing `import importlib.util`
- if adapter loading mysteriously falls back to raw behavior everywhere, inspect the wrapper imports first before blaming the adapter
- fix that import immediately or the whole shim phase is fake

Another useful migration pattern that worked well:
- extract deterministic helper logic before side-effectful execution helpers
- good early pure-helper extractions were:
  - `prompts.py::render_implementation_dispatch_prompt(...)`
  - `sessions.py::{slugify_issue_title, expected_lane_worktree, expected_lane_branch, lane_acpx_session_name}`
  - `github.py::{issue_label_names, get_issue_details, pick_next_lane_issue_from_repo, issue_add_label, issue_remove_label, issue_comment, issue_close}`
- then patch the live wrapper helper functions to delegate into adapter modules first with local fallback
- this shrinks the wrapper materially without touching the lane

A later migration slice confirmed the next safe helper cluster to move is session/runtime plus model-routing logic in `sessions.py`.
Good extractions there were:
- ACP/session runtime helpers:
  - `show_acpx_session(...)`
  - `close_acpx_session(...)`
  - `ensure_acpx_session(...)`
  - `run_acpx_prompt(...)`
- lane artifact and worktree helpers:
  - `snapshot_lane_artifacts(...)`
  - `restore_lane_artifacts(...)`
  - `prepare_lane_worktree(...)`
- deterministic lane/model helpers:
  - `issue_number_from_branch(...)`
  - `issue_number_from_worktree(...)`
  - `implementation_lane_matches(...)`
  - `should_escalate_codex_model(...)`
  - `codex_model_for_issue(...)`
  - `coder_agent_name_for_model(...)`
  - `actor_labels_payload(...)`

This cluster belongs in the adapter even when some functions touch subprocesses or filesystem state, because the behavior is still YoYoPod-specific session/worktree policy rather than generic Daedalus core logic.

Recommended extraction sequence for this cluster:
1. add failing focused tests first (`tests/test_yoyopod_core_session_runtime.py`, `tests/test_yoyopod_core_sessions.py`)
2. implement the helpers in `adapters/yoyopod_core/sessions.py`
3. patch wrapper helpers to prefer adapter functions first with local fallback
4. rerun the targeted migration suite
5. reinstall the plugin
6. verify live `status --json` still works without advancing the lane

For GitHub helper migration, the same pattern worked cleanly for repo-backed lane discovery:
- add adapter tests for `pick_next_lane_issue_from_repo(...)`
- implement it in `adapters/yoyopod_core/github.py`
- change wrapper `_pick_next_lane_issue()` to prefer adapter github first, fallback local

A later read-model slice showed the next useful GitHub/status split:
- move active-lane repo discovery into `github.py` as `get_active_lane_from_repo(...)`
- move PR discovery for a lane into `github.py` as `get_open_pr_for_issue(...)`
- move implementation-shape normalization for the active lane into `status.py` as `normalize_implementation_for_active_lane(...)`
- patch wrapper helpers `_get_active_lane()`, `_get_open_pr_for_issue(...)`, and `_normalize_implementation_for_active_lane(...)` to prefer adapter-owned behavior first with local fallback

Why this slice is worth doing:
- it peels real `build_status_raw()` fact-gathering and normalization out of the wrapper without touching forward-moving actions
- `github.py` becomes the home for GitHub-backed repo facts, not just label/comment helpers
- `status.py` becomes the home for implementation-state normalization policy, which is read-model logic and does not belong in the compatibility shell

Recommended test-first sequence for this slice:
1. add failing tests in `tests/test_yoyopod_core_github.py` for:
   - `get_active_lane_from_repo(...)`
   - multi-active-lane error shape
   - `get_open_pr_for_issue(...)`
2. add failing tests in `tests/test_yoyopod_core_adapter_status.py` for:
   - matching-lane normalization preserving the session but rewriting expected worktree/branch
   - mismatched-lane normalization resetting to the fresh expected shape
3. implement the adapter helpers
4. patch the live wrapper helpers to delegate to the adapter first
5. run the focused tests, then rerun the targeted migration suite
6. reinstall the plugin and verify live `status --json` still works with the lane left idle

The next safe `build_status_raw()` peel after that was adapter-owned read-only worktree/session fact gathering inside `status.py`:
- `collect_worktree_repo_facts(...)` to own branch / commits-ahead / local-head probing for the lane worktree
- `load_implementation_session_meta(...)` to own the decision between ACPX session inspection and legacy session-record lookup
- patch the live wrapper to use adapter status first at the `build_status_raw()` call sites and keep the old `_git_*` / `_load_implementation_session_meta()` code as fallback only

Why this follow-up slice matters:
- it removes more status assembly from the wrapper without touching action execution
- the adapter starts owning real lane facts, not just interpretation
- it keeps session/worktree read-model policy close to `status.py` instead of scattered across the compatibility shell

Good focused tests for this slice:
1. add adapter status tests for `collect_worktree_repo_facts(...)`:
   - happy path returns branch / commitsAhead / localHeadSha
   - missing path or broken git commands degrade to `None` values cleanly
2. add adapter status tests for `load_implementation_session_meta(...)`:
   - ACPX runtime path uses injected `show_acpx_session(...)`
   - non-ACPX path falls back to legacy session-meta lookup
3. implement the helpers in `adapters/yoyopod_core/status.py`
4. patch `build_status_raw()` and wrapper `_load_implementation_session_meta(...)` to prefer adapter status first
5. rerun the targeted migration suite, reinstall, and smoke-test live `status --json` again with the lane still idle

The next safe session-focused peel after that was to move remaining read-only session status policy into `adapters/yoyopod_core/sessions.py`:
- `assess_codex_session_health(...)`
- `build_acp_session_strategy(...)`
- `should_nudge_session(...)`
- and optionally make wrapper `decide_lane_session_action(...)` delegate to `decide_session_action(...)` in the adapter too

Why this slice matters:
- these functions are part of lane/session read-model policy, not compatibility-shell responsibilities
- `build_status_raw()` depends on them heavily, so moving them cuts real wrapper mass instead of cosmetic helpers
- it keeps session freshness, pokeability, and ACP strategy rules in one adapter module instead of split between wrapper and adapter

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_sessions.py` for:
   - healthy vs pokeable vs invalid session-health outcomes
   - ACPX vs legacy `build_acp_session_strategy(...)`
   - `should_nudge_session(...)` blocking recent same-head nudges while allowing other cases
2. implement the helpers in `adapters/yoyopod_core/sessions.py`
3. patch wrapper `_assess_codex_session_health(...)`, `build_acp_session_strategy(...)`, and `should_nudge_session(...)` to prefer adapter sessions first with local fallback
4. patch wrapper `decide_lane_session_action(...)` to prefer adapter `decide_session_action(...)` first
5. rerun the targeted migration suite, reinstall, and live-check `status --json` with the lane still idle

The next useful read-only peel after that was to move local review-readiness helpers into `adapters/yoyopod_core/reviews.py`:
- `pr_ready_for_review(...)`
- `has_local_candidate(...)`
- `current_inter_review_agent_matches_local_head(...)`
- `local_inter_review_agent_review_count(...)`
- `single_pass_local_claude_gate_satisfied(...)`

Why this slice matters:
- these helpers are used by both `build_status_raw()` and `reconcile()`, so they are exactly the kind of duplicated read-model/review-policy glue that should leave the compatibility shell
- they belong with review policy, not in the wrapper root
- moving them gives you real wrapper shrinkage around the local prepublish gate and ready-to-publish logic

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for:
   - PR ready/draft detection
   - local-candidate detection
   - current-review head matching
   - incrementing local review count only for a new completed local-prepublish head
   - `single_pass_local_claude_gate_satisfied(...)` for `PASS_CLEAN`, `PASS_WITH_FINDINGS`, and `REWORK`
2. implement the helpers in `adapters/yoyopod_core/reviews.py`
3. add wrapper adapter loader support for `reviews.py` if missing
4. patch wrapper `_pr_ready_for_review(...)`, `_has_local_candidate(...)`, `_current_inter_review_agent_matches_local_head(...)`, `_local_inter_review_agent_review_count(...)`, and `_single_pass_local_claude_gate_satisfied(...)` to prefer adapter reviews first with local fallback
5. rerun the targeted migration suite, reinstall, and verify live `status --json` still works with the lane left idle

The next worthwhile follow-up inside the same review-policy area is to extract preflight/readiness helper logic that still feeds `build_status_raw()`:
- `determine_review_loop_state(...)`
- `inter_review_agent_preflight(...)`
- and later, if useful, local-review seed normalization (`_normalize_local_inter_review_agent_seed(...)`) plus review normalization helpers

Why this follow-up slice matters:
- these functions determine whether the local prepublish review should run and how the status read model interprets required review outcomes
- they are still wrapper-heavy even though they are read-only policy, not operator shell concerns
- moving them into `reviews.py` continues the same boundary cleanup without touching lane-moving actions

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for:
   - `determine_review_loop_state(...)` across pending / findings / rework cases
   - `inter_review_agent_preflight(...)` when a local prepublish run is cleanly allowed
   - `inter_review_agent_preflight(...)` when no-active-lane / no-local-head / stale workflow / running-current-head reasons should block
2. implement the helpers in `adapters/yoyopod_core/reviews.py`, using injected helper callbacks for checks, target-head lookup, started-epoch lookup, and current time
3. patch wrapper `_determine_review_loop_state(...)` and `_inter_review_agent_preflight(...)` to prefer adapter reviews first with local fallback
4. rerun focused tests, the targeted migration suite, reinstall the plugin, and smoke-test live `status --json` again with the lane still idle

The next natural follow-up after that is to move local review seed/normalization helpers into `adapters/yoyopod_core/reviews.py` too:
- `normalize_review(...)`
- `inter_review_agent_pending_seed(...)`
- `inter_review_agent_superseded(...)`
- `inter_review_agent_timed_out(...)`
- `normalize_local_inter_review_agent_seed(...)`

Why this slice matters:
- these helpers still wrap a lot of `build_status_raw()` review assembly and normalization logic
- they are pure/read-only policy helpers, so leaving them in the compatibility shell is pointless
- moving them into the adapter keeps review-state shaping near the rest of the review policy instead of scattered across the wrapper

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for:
   - `normalize_review(...)` default-filling and field preservation
   - pending/superseded/timed-out seed helpers
   - `normalize_local_inter_review_agent_seed(...)` for current-head, timed-out, superseded, and empty-review cases
2. implement the helpers in `adapters/yoyopod_core/reviews.py`, injecting target-head and timing callbacks where needed
3. patch wrapper `_normalize_review(...)`, `_inter_review_agent_pending_seed(...)`, `_inter_review_agent_superseded(...)`, `_inter_review_agent_timed_out(...)`, and `_normalize_local_inter_review_agent_seed(...)` to prefer adapter reviews first with local fallback
4. rerun focused tests, the targeted migration suite, reinstall the plugin, and verify live `status --json` still works with the lane kept idle

A small but useful follow-up right after that is to move the last obvious review-assembly stragglers into `adapters/yoyopod_core/reviews.py`:
- `review_bucket(...)`
- `codex_cloud_placeholder(...)`

Why this micro-slice is still worth doing:
- `review_bucket(...)` still feeds review-loop-state interpretation in the wrapper fallback path
- `codex_cloud_placeholder(...)` is a pure review-state shaping helper used in `build_status_raw()`
- leaving tiny pure helpers behind just because they are small is how the compatibility shell stays annoyingly fat forever

Good test-first sequence for this micro-slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for:
   - `review_bucket(...)` mapping `REWORK`, `PASS_WITH_FINDINGS`, `PASS_CLEAN`, and pending states
   - `codex_cloud_placeholder(...)` producing the expected postpublish placeholder payload through injected `normalize_review(...)`
2. implement the helpers in `adapters/yoyopod_core/reviews.py`
3. patch wrapper `_review_bucket(...)` and `_codex_cloud_placeholder(...)` to prefer adapter reviews first with local fallback
4. rerun the focused tests, the targeted migration suite, reinstall, and live-check `status --json` again with the lane still idle

The next reusable slice after that is dispatch-review bookkeeping normalization.
Move the per-run review-payload builders into `adapters/yoyopod_core/reviews.py`:
- `build_inter_review_agent_running_review(...)`
- `build_inter_review_agent_failed_review(...)`
- `build_inter_review_agent_completed_review(...)`

Why this slice is worth saving:
- these builders are still pure policy/state-shaping logic even though they are used from dispatch code
- extracting them shrinks the wrapper in a meaningful place without touching the side-effectful review execution itself
- it keeps all review payload shaping in one adapter module instead of split across wrapper status/reconcile/dispatch code

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for running/failed/completed review payload shapes
2. implement the builders in `adapters/yoyopod_core/reviews.py`
3. patch `dispatch_inter_review_agent_review_raw()` to prefer adapter review builders first with local fallback
4. rerun focused tests, the targeted migration suite, reinstall, and smoke-test live `status --json`
5. during live verification, watch for real-world drift separately from migration bugs — e.g. `status --json` may surface `activeLane=null` with ledger still tracking an active lane, which is a live stale-ledger condition, not necessarily a regression from the extraction slice

A solid follow-up slice is to move the remaining pure review-outcome and lane-failure classification helpers into `adapters/yoyopod_core/reviews.py`:
- `inter_review_agent_target_head(...)`
- `inter_review_agent_started_epoch(...)`
- `inter_review_agent_is_running_on_head(...)`
- `classify_inter_review_agent_failure_text(...)`
- `extract_inter_review_agent_payload(...)`
- `inter_review_agent_failure_message(...)`
- `inter_review_agent_failure_class(...)`
- `classify_lane_failure(...)`

Why this slice matters:
- these functions are deterministic review-policy / repair-outcome parsing, not compatibility-shell concerns
- `classify_lane_failure(...)` feeds `.lane-state.json` failure bookkeeping, so moving it cuts real wrapper read-model policy instead of cosmetic helpers
- extracting the review-outcome parsers keeps subprocess output interpretation beside the rest of the review-state logic in `reviews.py`

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for target-head derivation, structured-output extraction, subprocess failure classification/message shaping, and lane-failure classification paths
2. implement the helpers in `adapters/yoyopod_core/reviews.py`
3. patch wrapper `_inter_review_agent_*` and `_classify_lane_failure(...)` helpers to prefer adapter reviews first with local fallback
4. rerun focused review tests, the targeted migration suite, reinstall, and smoke-test live `status --json` again with the lane kept idle

The next low-risk follow-up is to move codex-review shaping and repair-brief synthesis into `adapters/yoyopod_core/reviews.py`:
- `build_codex_cloud_thread(...)`
- `summarize_codex_cloud_review(...)`
- `synthesize_repair_brief(...)`

Why this slice matters:
- it peels the remaining pure Codex review-state shaping out of `_fetch_codex_cloud_review(...)` without moving the GitHub fetch itself yet
- it moves repair-brief assembly next to the rest of the review policy instead of leaving it buried in the wrapper
- it keeps the wrapper focused on transport and fallback glue while the adapter owns verdict/summary/item synthesis

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for codex thread mapping, codex review summary shaping across findings/pending/clean cases, and repair-brief synthesis
2. implement the helpers in `adapters/yoyopod_core/reviews.py`
3. patch wrapper `_fetch_codex_cloud_review(...)` and `_synthesize_repair_brief(...)` to prefer adapter reviews first with local fallback
4. rerun focused review tests, the targeted migration suite, reinstall, and smoke-test live `status --json` again with the lane kept idle

The next small follow-up after that is to move the remaining codex review mutation/read-transport helpers into `adapters/yoyopod_core/reviews.py`:
- `mark_pr_ready_for_review(...)`
- `resolve_review_thread(...)`
- `resolve_codex_superseded_threads(...)`
- `fetch_codex_pr_body_signal(...)`

Why this slice matters:
- these functions are still YoYoPod review policy/transport glue, not compatibility-shell responsibilities
- it keeps codex review lifecycle operations in one adapter module instead of scattered through wrapper-only helpers
- it shrinks the wrapper further without changing lane movement because the live verification remains read-only

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for PR-ready mutation, review-thread resolution, superseded-thread cleanup, and latest PR-body reaction selection
2. implement the helpers in `adapters/yoyopod_core/reviews.py`
3. patch wrapper `_mark_pr_ready_for_review(...)`, `_resolve_review_thread(...)`, `_resolve_codex_superseded_threads(...)`, and `_fetch_codex_pr_body_signal(...)` to prefer adapter reviews first with local fallback
4. rerun focused review tests, the targeted migration suite, reinstall, and smoke-test live `status --json` again with the lane kept idle

The next follow-up after that is to move the full Codex review fetch/read-model helper into `adapters/yoyopod_core/reviews.py`:
- `fetch_codex_cloud_review(...)`

Why this slice matters:
- it collapses the remaining wrapper-owned Codex review fetch path into a single adapter-owned helper instead of half-wrapper/half-adapter glue
- it centralizes cache handling, PR-body signal use, GraphQL fetch shaping, and review summary construction in one module
- once this lands, the obvious remaining wrapper leftovers in this area are tiny parsing/check helpers rather than the whole review fetch pipeline

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for cached review reuse and GraphQL-thread-to-review shaping
2. implement `fetch_codex_cloud_review(...)` in `adapters/yoyopod_core/reviews.py` with injected helpers for signal fetch, GraphQL transport, severity/summary parsing, and time
3. patch wrapper `_fetch_codex_cloud_review(...)` to prefer the adapter helper first with local fallback
4. rerun focused review tests, the targeted migration suite, reinstall, and smoke-test live `status --json` again with the lane kept idle

The next micro-slice after that is to move the remaining Codex parsing/check helpers into `adapters/yoyopod_core/reviews.py`:
- `extract_severity(...)`
- `extract_summary(...)`
- `checks_acceptable(...)`

Why this slice still matters:
- these are tiny, pure helpers, but leaving them in the wrapper keeps the compatibility shell annoyingly fat for no good reason
- they are directly used by the adapter-owned Codex review fetch/preflight flow, so the boundary is cleaner if the adapter owns them too

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for severity parsing, summary stripping, and acceptable-check mapping
2. implement the helpers in `adapters/yoyopod_core/reviews.py`
3. patch wrapper `_extract_severity(...)`, `_extract_summary(...)`, and `_checks_acceptable(...)` to prefer adapter reviews first with local fallback
4. rerun focused review tests, the targeted migration suite, reinstall, and smoke-test live `status --json` again with the lane kept idle

The next small cleanup slice after that is to move the remaining generic lane git/path read helpers out of the wrapper:
- `paths.py::{lane_state_path, lane_memo_path}`
- `status.py::{git_branch, git_commits_ahead, git_head_sha}`
- `sessions.py::is_git_repo`

Why this slice matters:
- these are still wrapper-owned utility helpers used all over status/build-status and worktree preparation
- they are small, deterministic, and safe to migrate with focused tests
- once moved, the wrapper stops owning boring file/git plumbing and gets closer to being just a compatibility shell

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_paths.py`, `tests/test_yoyopod_core_adapter_status.py`, and `tests/test_yoyopod_core_session_runtime.py`
2. implement the helpers in the adapter modules above
3. patch wrapper `_lane_state_path(...)`, `_lane_memo_path(...)`, `_git_branch(...)`, `_git_commits_ahead(...)`, `_git_head_sha(...)`, and `_is_git_repo(...)` to prefer adapters first with local fallback
4. rerun focused tests, the targeted migration suite, reinstall, and smoke-test live `status --json` again with the lane kept idle

A useful follow-up after the git/path cleanup is to move lane memo and session-nudge shaping helpers out of the wrapper too:
- `prompts.py::{summarize_validation, render_lane_memo}`
- `sessions.py::{build_session_nudge_payload, record_session_nudge}`

Why this slice matters:
- these helpers are still wrapper-owned despite being deterministic prompt/state shaping rather than compatibility-shell responsibilities
- they sit on the hot path for lane memo generation and session nudge bookkeeping, so moving them shrinks real wrapper behavior rather than dead code
- the split is clean: prompt text stays in `prompts.py`, nudge payload/state recording stays in `sessions.py`

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_prompts.py` and `tests/test_yoyopod_core_sessions.py` for validation summary shaping, lane memo rendering, session-nudge payload building, and nudge state recording
2. implement the helpers in `adapters/yoyopod_core/prompts.py` and `adapters/yoyopod_core/sessions.py`
3. patch wrapper `_summarize_validation(...)`, `render_lane_memo(...)`, `build_session_nudge_payload(...)`, and `record_session_nudge(...)` to prefer adapters first with local fallback
4. rerun focused tests, the targeted migration suite, reinstall, and smoke-test live `status --json` again with the lane kept idle

The tiny follow-up after that is to move the remaining Codex parsing/check helpers into `adapters/yoyopod_core/reviews.py`:
- `extract_severity(...)`
- `extract_summary(...)`
- `checks_acceptable(...)`

Why this micro-slice is still worth saving:
- once the big Codex review fetch path is adapter-owned, leaving these tiny parser/check helpers in the wrapper is just stupid residue
- they are deterministic policy helpers used by the review fetch/preflight path, not compatibility-shell responsibilities
- this slice finishes off the obvious Codex review helper cluster and leaves the wrapper with less domain logic noise

Good test-first sequence for this slice:
1. add focused failing tests in `tests/test_yoyopod_core_reviews.py` for severity parsing, summary extraction, and acceptable-check-state detection
2. implement the helpers in `adapters/yoyopod_core/reviews.py`
3. patch wrapper `_extract_severity(...)`, `_extract_summary(...)`, and `_checks_acceptable(...)` to prefer adapter reviews first with local fallback
4. rerun focused review tests, the targeted migration suite, reinstall, and smoke-test live `status --json` again with the lane kept idle

If the user explicitly wants the lane kept idle while restructuring, keep live verification read-only:
- prefer `status --json`
- avoid `tick` or any dispatch action
- do helper smoke checks only if they do not move the lane

This matters because once the wrapper shim phase is complete, the next problem is usually not structural migration anymore — it is control-loop semantics and blocking side effects inside the real tick path.

Why this works:
- the wrapper stays operational during rollout
- installed plugin code becomes the preferred control path
- recursion is avoided because adapter bridges prefer raw names when present
- you can validate each explicit command slice with focused tests before touching the control-loop brain
- moving pure helpers first gives you real shrinkage without risking lane movement

Test rule for these slices:
- add focused failing-first tests first (`tests/test_yoyopod_core_actions.py`, `tests/test_yoyopod_core_tick.py`, helper-specific tests)
- verify the adapter prefers `<name>_raw()` over the old public name where shims are involved
- for extracted helper modules, verify wrapper smoke checks still return the same values after install
- rerun the targeted migration suite
- reinstall the plugin payload
- smoke-test live `status --json` after each shim cut before moving on
## Boundary rules

### Top-level Daedalus core may own
- runtime loop
- leases / heartbeats
- queue/action persistence
- failures / retries / recovery
- alerts
- service supervision
- operator CLI surface

### `adapters/yoyopod_core/` may own
- workflow read model / status
- next-action derivation
- reconcile / tick semantics
- review / publish / merge policy
- session/worktree handling
- GitHub-specific conventions
- prompt construction
- YoYoPod-specific health/drift rules

### `projects/yoyopod_core/runtime/` may contain
- status projections
- audit logs
- alert state
- SQLite/state files
- other mutable operator/runtime artifacts

### `projects/yoyopod_core/workspace/` may contain
- cloned `yoyopod-core` repo
- worktrees if desired
- repo-local code/docs/tests belonging to the product, not Daedalus

## Strong opinions

- Do not dump the former wrapper into plugin root unchanged.
- Do not merge YoYoPod workflow semantics into top-level `runtime.py`.
- Do not store mutable runtime files in tracked source folders.
- Do not pretend docs are state or state is docs.
- If using `projects/yoyopod_core/runtime/` and `projects/yoyopod_core/workspace/`, both should be gitignored.

## When to use this vs Model 2

Use this layout when:
- you want cleaner boundaries now
- you do not want to design a full plugin-to-plugin API yet
- you want an intermediate architecture step before extracting YoYoPod into its own plugin

Move to Model 2 later once the boundary between Daedalus core and YoYoPod app is stable.
