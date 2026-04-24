# ADR-0001: Model 1 structure for `yoyopod-core`

- **Status:** Accepted
- **Date:** 2026-04-22
- **Decision makers:** Hermes / radxa
- **Scope:** `hermes-relay` repository restructure for the next implementation phase

## Context

Hermes Relay currently mixes two concerns:

1. **generic workflow-engine code** (`runtime.py`, `tools.py`, `alerts.py`)
2. **YoYoPod-specific workflow policy** currently living outside the plugin as wrapper code

That split is fake architecture. Relay depends on YoYoPod workflow truth, but the workflow brain still lives as a sidecar script with separate paths, separate state assumptions, and awkward coupling.

We considered two future directions:

- **Model 2:** separate `hermes-relay` and `yoyopod-core-workflow` plugins
- **Model 1:** one plugin repo, but with a hard internal boundary between generic relay code and project-specific adapter code

For the next step we want lower migration risk, faster implementation, and cleaner boundaries without designing a full plugin API too early.

## Decision

We will adopt **Model 1**.

### Code boundary

The `hermes-relay` repo will contain:

- **Relay core** at top level
- **project adapter code** under `adapters/yoyopod_core/`
- **project runtime/config/workspace/docs** under `projects/yoyopod_core/`

### Naming rule

- external project/runtime name: `yoyopod-core`
- Python package/module name: `yoyopod_core`

We use the hyphenated name for project identity and filesystem paths where safe, and the underscore form for importable Python packages.

## Decision details

### 1. Relay core stays generic

Top-level plugin code remains the generic engine surface:

- `runtime.py` — leases, runtime loop, DB, queueing, retries, failures
- `tools.py` — operator/CLI surface
- `alerts.py` — relay alerting
- `schemas.py`, `__init__.py`, `plugin.yaml` — plugin wiring

Relay core must not directly encode YoYoPod semantics such as:

- active-lane label policy
- Claude prepublish review semantics
- merge-and-promote workflow rules
- YoYoPod-specific worktree naming and session behavior

### 2. YoYoPod logic becomes adapter code

YoYoPod-specific workflow code moves into:

```text
adapters/
  yoyopod_core/
```

This adapter is the translation layer between generic relay orchestration and `yoyopod-core` workflow semantics.

The adapter owns:

- project status/read-model construction
- `nextAction` derivation
- workflow health/drift logic
- action execution behavior for `yoyopod-core`
- review/publish/merge policy
- session/worktree policy
- prompt rendering
- GitHub-specific project helpers

### 3. Project data is not plugin code

`projects/yoyopod_core/` is for project-scoped, non-importable runtime assets:

- `config/`
- `runtime/`
- `workspace/`
- `docs/`

This directory is not the Python adapter package. It is the project's local data/config/workspace home inside the repo.

### 4. Runtime state and product workspace stay separate

Within `projects/yoyopod_core/`:

- `runtime/` = mutable state, memory, logs, sqlite, transient artifacts
- `workspace/` = cloned `yoyopod-core` product repository / worktrees

Both are live mutable areas and must be treated accordingly.

## Resulting structure

```text
hermes-relay/
├── plugin.yaml
├── __init__.py
├── schemas.py
├── tools.py
├── runtime.py
├── alerts.py
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
        ├── runtime/
        ├── workspace/
        └── docs/
```

## Consequences

### Positive

- cleaner engine/app boundary now
- direct imports instead of wrapper-sidecar coupling
- future Model 2 extraction becomes easier
- project runtime data has a clear home
- operator reasoning becomes simpler: top-level engine, adapter code, project data

### Negative

- repo now contains both generic engine code and project-local runtime assets
- install/runtime path handling must be updated carefully
- old wrapper entrypoints need compatibility handling during migration

### Accepted tradeoff

We accept a single-repo mixed code/data layout for now because it reduces migration cost and avoids premature framework/API design.

## Rejected alternatives

### A. Keep wrapper outside the plugin

Rejected because the wrapper remains a fake external dependency while Relay still depends on it semantically.

### B. Merge YoYoPod logic into `runtime.py`

Rejected because it permanently YoYoPod-shapes the Relay engine and destroys the boundary.

### C. Jump directly to Model 2

Rejected for now because it would force API/versioning/plugin-boundary work before the internal boundary is fully stabilized.

## Follow-up

The next implementation phase should:

1. create `adapters/yoyopod_core/`
2. create `projects/yoyopod_core/`
3. migrate wrapper logic into adapter modules
4. update relay runtime/tools to call adapter code directly
5. keep temporary compatibility entrypoints only as thin shims
