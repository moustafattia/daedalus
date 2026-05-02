# Workflows Flattening Design

## Goal

Slim `daedalus/workflows` by roughly one quarter over the refactor, make the package easy to navigate, and replace the ad hoc workflow module contract with a standard workflow class and typed config boundary.

## Scope

This design covers only `daedalus/workflows` and the repo-root `workflows` compatibility package when needed to preserve public imports. The workflow implementations `change_delivery` and `issue_runner` remain folders with their internals for now. All other shared workflow support should move to a flat `daedalus/workflows` layer.

Public behavior must stay compatible for:

- `/workflow <name> ...`
- `python -m workflows ...`
- `python -m workflows.change_delivery ...`
- `python -m workflows.issue_runner ...`
- workflow names `change-delivery` and `issue-runner`
- repo-owned `WORKFLOW.md` and `WORKFLOW-<workflow>.md`
- root-level `workflows` compatibility imports

Private compatibility shims inside `daedalus/workflows` may be removed after internal imports and tests are migrated.

## Target Layout

```text
daedalus/workflows/
  __init__.py
  __main__.py
  config.py
  config_snapshot.py
  contract.py
  paths.py
  readiness.py
  registry.py
  runtime_matrix.py
  runtime_presets.py
  stall.py
  validation.py
  workflow.py
  change_delivery/
  issue_runner/
```

Only `change_delivery/` and `issue_runner/` remain as workflow subpackages. Remove the nested shared support packages:

```text
daedalus/workflows/core/
daedalus/workflows/shared/
daedalus/workflows/shared/runtimes/
daedalus/workflows/change_delivery/runtimes/
```

The `change_delivery/config_snapshot.py` and `change_delivery/stall.py` one-line compatibility modules should also disappear once callers use the flat `workflows.config_snapshot` and `workflows.stall` modules.

## Workflow Class

Each workflow should expose a `WORKFLOW` object. The old module attributes remain temporarily as adapters until callers and tests use the new object.

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class Workflow(Protocol):
    name: str
    schema_versions: tuple[int, ...]
    schema_path: Path
    preflight_gated_commands: frozenset[str]

    def load_config(self, *, workflow_root: Path, raw: dict[str, Any]) -> object:
        ...

    def make_workspace(self, *, workflow_root: Path, config: object) -> object:
        ...

    def run_cli(self, *, workspace: object, argv: list[str]) -> int:
        ...

    def run_preflight(self, *, workflow_root: Path, config: object) -> object:
        ...
```

`workflows.registry` becomes the only place that imports workflow packages by slug, checks the workflow shape, validates schema versions, and adapts old package-level attributes during migration.

## Typed Config

Raw YAML dictionaries should exist only at the loading boundary. Workflow internals should receive frozen dataclasses or typed config objects.

Shared config helpers move from `workflows.core.config` to `workflows.config`. Workflow-specific config remains inside each workflow folder:

- `workflows.issue_runner.config.IssueRunnerConfig`
- `workflows.change_delivery.config.ChangeDeliveryConfig`

Typed config objects own alias normalization, environment indirection, and path resolution. Callers should stop repeating `dict.get(...)`, dash/underscore alias handling, and local storage path derivation.

## Slimming Strategy

The first measurable reduction should come from deleting private shims and removing duplicate import layers:

1. Move `core` modules to flat workflow modules.
2. Move `shared` modules to flat workflow modules.
3. Delete runtime shim packages and import `runtimes.*` directly.
4. Replace package-level workflow attributes with `WORKFLOW` adapters.
5. Migrate `issue_runner` to the class/typed-config path first.
6. Migrate `change_delivery` config access next.

After that, reduce the largest files by moving cohesive responsibilities behind typed interfaces:

- `change_delivery/workspace.py`
- `change_delivery/reviews.py`
- `change_delivery/status.py`
- `issue_runner/workspace.py`

Those splits should be separate implementation slices so file moves are not mixed with behavior changes.

## Error Handling

Config load failures should raise the existing workflow contract/config errors where public callers already expect them. The registry should translate class-level failures into the current `WorkflowContractError` surface for CLI dispatch. Workflow-specific validation can keep existing error payloads, but should read typed config once loading succeeds.

Preflight gating stays command-scoped. Diagnostic and repair commands must remain available when config is invalid unless the workflow explicitly gates them.

## Testing

Guardrail tests should prove:

- both bundled workflows are discoverable through `workflows.list_workflows()`;
- root-level `workflows` still exposes submodules;
- `/workflow` CLI dispatch still resolves both workflow names;
- old package-level attrs still work during migration;
- new `WORKFLOW` objects conform to the class/protocol;
- internal imports no longer use deleted private shim paths;
- typed config keeps existing alias, path, and storage behavior.

Narrow verification for the first implementation slice:

```bash
pytest tests/test_restructure_guardrails.py \
  tests/test_official_plugin_layout.py \
  tests/test_workflows_dispatcher.py \
  tests/test_workflow_driver_api.py \
  tests/test_workflows_core_config.py \
  tests/test_issue_runner_config.py
```

Run full `pytest` before claiming the slimming pass is complete.

## Acceptance

The refactor is done for this folder when:

- `daedalus/workflows` has one flat support layer and only two workflow subfolders;
- one-line private re-export files under workflow internals are removed;
- workflow loading goes through a standard `WORKFLOW` object;
- workflow code receives typed config after YAML loading;
- public workflow commands and compatibility imports still pass guardrail tests;
- the next pass can shrink large implementation files without needing another namespace cleanup.
