# `yoyopod-core` Model 1 implementation spec

> **Historical note:** This document describes an earlier Model 1 layout where
> project-local runtime data and cloned workspace state lived under
> `daedalus/projects/yoyopod_core/`. That is no longer the supported public
> model. Public installs now use the operator's real repo checkout plus a
> workflow instance root under `~/.hermes/workflows/<owner>-<repo>-<workflow-type>/`.
>
> **Goal:** Move the `yoyopod-core` workflow brain into the `daedalus` plugin repo with a hard internal boundary between generic Daedalus engine code, project adapter code, and project runtime assets.

## 1. Scope

This spec covers the next restructuring phase only.

### In scope

- create a Model 1 repo layout
- move YoYoPod wrapper behavior into adapter modules
- define where `yoyopod-core` config, runtime state, docs, and cloned workspace live
- define module responsibilities and migration order
- define compatibility and validation requirements

### Out of scope

- full Model 2 split into separate plugins
- redesigning all workflow semantics
- changing business workflow policy unless required by the move
- deleting compatibility shims on day one

## 2. Design goals

1. **Keep Daedalus core generic**
2. **Move `yoyopod-core` workflow logic into plugin code**
3. **Give project-local data a stable home inside the repo**
4. **Make future Model 2 extraction straightforward**
5. **Avoid a one-file monolith move**

## 3. Target layout

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
│   ├── architecture.md
│   ├── operator-cheat-sheet.md
│   ├── adr/
│   │   └── ADR-0001-yoyopod-core-model1-adapter-boundary.md
│   └── design/
│       └── yoyopod-core-model1-implementation-spec.md
├── adapters/
│   └── yoyopod_core/
│       ├── __init__.py
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
        ├── runtime/
        │   ├── memory/
        │   ├── state/
        │   └── logs/
        ├── workspace/
        │   └── yoyopod-core/
        └── docs/
```

## 4. Naming rules

### External/project-facing name

Use `yoyopod-core` for:

- human-facing project identity and upstream repository naming
- design docs and operator language
- project identity in status/reporting

### Python/package-safe directory name

Use `yoyopod_core` for:

- adapter package directory: `adapters/yoyopod_core/`
- project directory: `projects/yoyopod_core/`
- imports
- Python module names

## 5. Responsibility split

## 5.1 Daedalus core

### `runtime.py`
Owns only generic orchestration concerns:

- runtime bootstrap
- sqlite schema and migrations
- leases / heartbeats
- action persistence and transitions
- failure tracking and retry bookkeeping
- active/shadow loop mechanics
- operator-safe execution gates

Must not own `yoyopod-core` business workflow semantics.

### `tools.py`
Owns:

- operator CLI / slash-command surface
- service install/start/stop/status helpers
- doctor/shadow/operator summaries built from Daedalus + adapter outputs

### `alerts.py`
Owns:

- Daedalus outage detection and alert state persistence
- generic alert rendering around runtime/doctor state

## 5.2 `yoyopod-core` adapter

### `adapters/yoyopod_core/status.py`
Owns:

- project read model
- active lane discovery
- open PR state
- review state assembly
- session/worktree-derived status inputs
- final `build_status()` equivalent

### `adapters/yoyopod_core/workflow.py`
Owns:

- top-level project workflow orchestration entrypoints
- `nextAction` derivation
- reconcile/tick-like high-level sequencing if retained
- bridge between status and actions

Keep this file thin. It should compose other modules, not become the new god file.

### `adapters/yoyopod_core/actions.py`
Owns project side effects:

- dispatch implementation turn
- dispatch internal review
- publish ready PR
- push PR update
- merge and promote
- restart actor session

### `adapters/yoyopod_core/reviews.py`
Owns:

- internal review policy
- external review policy
- verdict interpretation
- review gating and repair-loop rules

### `adapters/yoyopod_core/sessions.py`
Owns:

- ACP / Codex session policy
- session ensure/restart/poke logic
- worktree materialization helpers tightly coupled to sessions

### `adapters/yoyopod_core/prompts.py`
Owns:

- prompt rendering only
- no side effects
- no process control

### `adapters/yoyopod_core/github.py`
Owns:

- GitHub issue/PR helpers specific to `yoyopod-core`
- active-lane detection and label logic
- merge/promotion GitHub operations where not already abstracted elsewhere

### `adapters/yoyopod_core/health.py`
Owns:

- `yoyopod-core`-specific health interpretation
- drift rules
- stale-lane reasons
- project-specific doctor checks if needed

## 5.3 Project-local assets

### `projects/yoyopod_core/config/`
Owns static/local project config such as:

- model choices
- job names
- path settings
- thresholds
- agent labels

### `projects/yoyopod_core/runtime/`
Owns mutable local state:

- sqlite state
- memory/status snapshots
- audit/event logs
- transient operator artifacts

### `projects/yoyopod_core/workspace/yoyopod-core/`
Owns the real cloned product repo / worktree base.

This is where code for the actual product lives.

### `projects/yoyopod_core/docs/`
Owns project-specific local docs/runbooks that belong to the hosted project inside this repo rather than to generic Daedalus docs.

## 6. Import and dependency rules

## Allowed

- Daedalus core may import adapter entrypoints deliberately
- Adapter modules may import Daedalus-shared helpers only when generic
- Adapter modules may import other adapter modules

## Not allowed

- Daedalus core must not import project runtime data paths as hardcoded global assumptions
- Daedalus core must not encode `yoyopod-core` workflow states directly
- Adapter modules must not write directly into generic Daedalus tables except through Daedalus-owned functions or defined interfaces
- `workflow.py` must not become a dump of every helper again

## 7. Execution model

Conceptually:

1. Daedalus runtime runs one loop
2. Daedalus asks the `yoyopod-core` adapter for current project truth
3. Adapter builds project status / derives next action
4. Daedalus persists action state and decides whether execution is allowed
5. Daedalus calls adapter action execution code for project-specific side effects
6. Daedalus records results/failures/retries generically

## 8. Migration plan

### Phase 1 — create structure

- create `adapters/yoyopod_core/`
- create `projects/yoyopod_core/{config,runtime,workspace,docs}/`
- add `__init__.py` files and placeholder modules
- update `.gitignore` for mutable/runtime/workspace paths

### Phase 2 — move configuration and path resolution

- define a single path-resolution module for `yoyopod-core`
- stop relying on scattered old wrapper constants
- point config/state/workspace to the new project-local locations

### Phase 3 — extract read model first

- move status-building logic into `status.py`
- move project health/drift logic into `health.py`
- keep behavior stable before moving side effects

### Phase 4 — extract execution logic

- move project actions into `actions.py`
- move review logic into `reviews.py`
- move session/worktree logic into `sessions.py`
- move prompt builders into `prompts.py`

### Phase 5 — wire Daedalus to adapter directly

- update Daedalus runtime/tools to import adapter entrypoints
- reduce subprocess/file-path dependence on the old wrapper location

### Phase 6 — compatibility shim

Keep a temporary shim entrypoint that preserves old operator/script paths but delegates into the new adapter implementation.

The shim must stay thin and dumb.

## 9. Compatibility requirements

During the move, the following operator behaviors must continue to work or be deliberately shimmed:

- status reporting
- Daedalus doctor/shadow status
- dispatch implementation turn
- dispatch internal review
- publish/merge flows
- service startup path resolution

## 10. `.gitignore` requirements

At minimum, ignore:

```text
projects/yoyopod_core/runtime/
projects/yoyopod_core/workspace/
```

If any seed files are needed inside those directories, ignore contents surgically and keep only required placeholders.

## 11. Tests and validation

### Unit / module-level

- adapter status derivation tests
- adapter action-selection tests
- adapter session/review helper tests

### Integration-level

- Daedalus runtime can call adapter successfully
- Daedalus doctor/shadow-report still work with the new adapter path
- active-gate behavior unchanged
- existing Daedalus hardening tests remain green

### Manual/operator checks

- service can resolve the new layout
- status output references the new project-local paths correctly
- compatibility shim works for legacy operator entrypoints during migration

## 12. Risks

### Risk: path breakage

Moving runtime/config/workspace paths can silently break service startup and local operator commands.

**Mitigation:** centralize path resolution early and test service/operator commands after each phase.

### Risk: new monolith in `workflow.py`

The move can fail by simply rebuilding the same blob inside a new directory.

**Mitigation:** enforce module ownership and keep `workflow.py` thin.

### Risk: mixed code/data confusion

Putting project data under the repo can tempt code to hardcode paths casually.

**Mitigation:** define one path/config module and use it everywhere.

## 13. Open questions

1. Should `projects/yoyopod_core/config/project.json` replace the current external JSON config fully or start as a mirrored/shimmed location first?
2. Do we want the cloned product repo path fixed as `projects/yoyopod_core/workspace/yoyopod-core/`, or do we still need a temporary compatibility alias during migration?
3. Which old wrapper CLI path must remain stable during migration, and for how long?

## 14. Implementation start condition

Implementation can start once the team agrees on:

- `adapters/yoyopod_core/` as the code home
- `projects/yoyopod_core/` as the project-local data home
- a compatibility-shim period rather than a flag-day cutover
