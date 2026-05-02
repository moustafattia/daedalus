# Workflows Flattening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flatten `daedalus/workflows` so only `change_delivery/` and `issue_runner/` remain as workflow subfolders, while introducing a standard workflow object and typed config boundary.

**Architecture:** Move shared workflow support from nested `core/` and `shared/` packages into flat modules under `daedalus/workflows`. Add `Workflow`/`ModuleWorkflow` and `registry.py` as the single workflow loading path, then adapt existing workflow packages through `WORKFLOW` objects while preserving legacy module attributes during migration.

**Tech Stack:** Python 3.10+, dataclasses, Protocols, PyYAML, jsonschema, pytest.

---

## File Structure

Create:

- `daedalus/workflows/config.py` - moved shared config helper API from `core/config.py`.
- `daedalus/workflows/hooks.py` - moved shell hook helpers from `core/hooks.py`.
- `daedalus/workflows/prompts.py` - moved prompt rendering from `core/prompts.py`.
- `daedalus/workflows/workflow.py` - workflow protocol and legacy adapter.
- `daedalus/workflows/registry.py` - discovery/loading/schema/preflight helpers.
- `daedalus/workflows/config_snapshot.py` - moved hot-reload snapshot primitives from `shared/config_snapshot.py`.
- `daedalus/workflows/config_watcher.py` - moved hot-reload watcher from `shared/config_watcher.py`.
- `daedalus/workflows/paths.py` - moved shared path helpers from `shared/paths.py`.
- `daedalus/workflows/stall.py` - moved stall detection from `shared/stall.py`.
- `tests/test_workflows_flat_layout.py` - new guardrails for flat layout and workflow objects.

Modify:

- `daedalus/workflows/__init__.py` - re-export registry functions and stop owning loader logic.
- `daedalus/workflows/__main__.py` - import flat path resolver.
- `daedalus/workflows/validation.py` - import flat modules.
- `daedalus/workflows/runtime_matrix.py` - import flat modules and direct runtimes.
- `daedalus/workflows/runtime_presets.py` - import flat modules.
- `daedalus/workflows/change_delivery/__init__.py` - expose `WORKFLOW`.
- `daedalus/workflows/change_delivery/*.py` - replace old `workflows.core.*`, `workflows.shared.*`, and workflow runtime shim imports.
- `daedalus/workflows/issue_runner/__init__.py` - expose `WORKFLOW`.
- `daedalus/workflows/issue_runner/*.py` - replace old `workflows.core.*` and `workflows.shared.*` imports.
- `daedalus/watch.py`, `daedalus/runtime.py`, `daedalus/daedalus_cli.py` - update any workflow private shim imports found during migration.
- `tests/test_restructure_import_direction.py` - update allowed compatibility files.
- `tests/test_official_plugin_layout.py` - remove expectation that private `workflows.change_delivery.runtimes` exists.
- Existing workflow tests that import deleted private shims - update to flat modules or top-level `runtimes`.
- `docs/workflows/README.md`, `daedalus/workflows/README.md`, `docs/restructure-shim-audit.md`, `docs/concepts/*.md` - update layout references.

Delete:

- `daedalus/workflows/core/__init__.py`
- `daedalus/workflows/core/config.py`
- `daedalus/workflows/core/hooks.py`
- `daedalus/workflows/core/prompts.py`
- `daedalus/workflows/core/types.py`
- `daedalus/workflows/shared/__init__.py`
- `daedalus/workflows/shared/config_snapshot.py`
- `daedalus/workflows/shared/config_watcher.py`
- `daedalus/workflows/shared/paths.py`
- `daedalus/workflows/shared/stall.py`
- `daedalus/workflows/shared/runtimes/__init__.py`
- `daedalus/workflows/shared/runtimes/acpx_codex.py`
- `daedalus/workflows/shared/runtimes/claude_cli.py`
- `daedalus/workflows/shared/runtimes/codex_app_server.py`
- `daedalus/workflows/shared/runtimes/hermes_agent.py`
- `daedalus/workflows/shared/runtimes/stages.py`
- `daedalus/workflows/change_delivery/config_snapshot.py`
- `daedalus/workflows/change_delivery/stall.py`
- `daedalus/workflows/change_delivery/runtimes/__init__.py`
- `daedalus/workflows/change_delivery/runtimes/acpx_codex.py`
- `daedalus/workflows/change_delivery/runtimes/claude_cli.py`
- `daedalus/workflows/change_delivery/runtimes/hermes_agent.py`
- `daedalus/workflows/change_delivery/runtimes/stages.py`

---

### Task 1: Add Flat Layout Guardrails

**Files:**
- Create: `tests/test_workflows_flat_layout.py`
- Modify: `tests/test_restructure_import_direction.py`
- Modify: `tests/test_official_plugin_layout.py`

- [ ] **Step 1: Write failing flat layout tests**

Create `tests/test_workflows_flat_layout.py`:

```python
import importlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = REPO_ROOT / "daedalus" / "workflows"


def test_workflows_support_layer_is_flat():
    unexpected = [
        WORKFLOWS_ROOT / "core",
        WORKFLOWS_ROOT / "shared",
        WORKFLOWS_ROOT / "change_delivery" / "runtimes",
        WORKFLOWS_ROOT / "change_delivery" / "config_snapshot.py",
        WORKFLOWS_ROOT / "change_delivery" / "stall.py",
    ]

    assert [path.relative_to(REPO_ROOT).as_posix() for path in unexpected if path.exists()] == []


def test_only_bundled_workflow_subpackages_remain():
    allowed = {"change_delivery", "issue_runner", "__pycache__"}
    dirs = {
        path.name
        for path in WORKFLOWS_ROOT.iterdir()
        if path.is_dir()
    }

    assert dirs <= allowed


def test_flat_workflow_modules_import():
    for module_name in (
        "workflows.config",
        "workflows.hooks",
        "workflows.prompts",
        "workflows.workflow",
        "workflows.registry",
        "workflows.config_snapshot",
        "workflows.config_watcher",
        "workflows.paths",
        "workflows.stall",
    ):
        module = importlib.import_module(module_name)
        assert module.__file__ is not None
        assert "/daedalus/workflows/" in module.__file__.replace("\\", "/")


def test_bundled_workflows_expose_standard_workflow_object():
    for module_name, workflow_name in (
        ("workflows.issue_runner", "issue-runner"),
        ("workflows.change_delivery", "change-delivery"),
    ):
        module = importlib.import_module(module_name)
        workflow = module.WORKFLOW

        assert workflow.name == workflow_name
        assert module.NAME == workflow.name
        assert module.SUPPORTED_SCHEMA_VERSIONS == workflow.schema_versions
        assert module.CONFIG_SCHEMA_PATH == workflow.schema_path
        assert callable(workflow.load_config)
        assert callable(workflow.make_workspace)
        assert callable(workflow.run_cli)
```

Update `tests/test_restructure_import_direction.py` by replacing the old allowed workflow compatibility file:

```python
ALLOWED_COMPATIBILITY_FILES = {
    "daedalus/integrations/trackers/__init__.py",
    "daedalus/integrations/trackers/types.py",
    "daedalus/integrations/trackers/registry.py",
    "daedalus/integrations/trackers/github.py",
    "daedalus/integrations/trackers/linear.py",
    "daedalus/integrations/trackers/local_json.py",
    "daedalus/integrations/trackers/feedback.py",
    "daedalus/integrations/code_hosts/__init__.py",
    "daedalus/integrations/code_hosts/types.py",
    "daedalus/integrations/code_hosts/registry.py",
    "daedalus/integrations/code_hosts/github.py",
    "daedalus/runtimes/types.py",
    "daedalus/runtimes/registry.py",
}
```

Extend the old import scan in the same test:

```python
        for old_import in (
            "from trackers",
            "from code_hosts",
            "from engine.driver",
            "from runtimes import",
            "from workflow_core",
            "from workflows.core",
            "from workflows.shared",
            "from workflows.change_delivery.runtimes",
            "from workflows.change_delivery.config_snapshot",
            "from workflows.change_delivery.stall",
        ):
```

Update `tests/test_official_plugin_layout.py::test_repo_root_workflows_wrapper_exposes_change_delivery_submodules` to assert the deleted runtime shim is gone but the workflow package remains exposed:

```python
def test_repo_root_workflows_wrapper_exposes_change_delivery_submodules():
    for module_name in list(sys.modules):
        if module_name == "workflows" or module_name.startswith("workflows."):
            del sys.modules[module_name]

    import importlib

    workflow = importlib.import_module("workflows.change_delivery")
    status = importlib.import_module("workflows.change_delivery.status")

    assert workflow.__file__ is not None
    assert status.__file__ is not None
    assert "daedalus/workflows/change_delivery/__init__.py" in workflow.__file__.replace("\\", "/")
    assert "daedalus/workflows/change_delivery/status.py" in status.__file__.replace("\\", "/")
```

- [ ] **Step 2: Run tests to verify they fail before implementation**

Run:

```bash
pytest tests/test_workflows_flat_layout.py \
  tests/test_restructure_import_direction.py \
  tests/test_official_plugin_layout.py -q
```

Expected: FAIL because `core/`, `shared/`, workflow runtime shims, and `WORKFLOW` objects still need migration.

- [ ] **Step 3: Commit failing guardrails**

```bash
git add tests/test_workflows_flat_layout.py tests/test_restructure_import_direction.py tests/test_official_plugin_layout.py
git commit -m "test: guard flat workflows layout"
```

---

### Task 2: Introduce Workflow Object and Registry

**Files:**
- Create: `daedalus/workflows/workflow.py`
- Create: `daedalus/workflows/registry.py`
- Modify: `daedalus/workflows/__init__.py`
- Modify: `daedalus/workflows/change_delivery/__init__.py`
- Modify: `daedalus/workflows/issue_runner/__init__.py`
- Test: `tests/test_workflows_flat_layout.py`
- Test: `tests/test_workflows_dispatcher.py`
- Test: `tests/test_restructure_guardrails.py`

- [ ] **Step 1: Add workflow protocol and legacy adapter**

Create `daedalus/workflows/workflow.py`:

```python
"""Standard workflow object contract."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
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


@dataclass(frozen=True)
class ModuleWorkflow:
    """Adapter for existing workflow packages during the class migration."""

    module: ModuleType

    @property
    def name(self) -> str:
        return self.module.NAME

    @property
    def schema_versions(self) -> tuple[int, ...]:
        return tuple(self.module.SUPPORTED_SCHEMA_VERSIONS)

    @property
    def schema_path(self) -> Path:
        return Path(self.module.CONFIG_SCHEMA_PATH)

    @property
    def preflight_gated_commands(self) -> frozenset[str]:
        return frozenset(getattr(self.module, "PREFLIGHT_GATED_COMMANDS", frozenset()))

    def load_config(self, *, workflow_root: Path, raw: dict[str, Any]) -> object:
        loader = getattr(self.module, "load_config", None)
        if callable(loader):
            return loader(workflow_root=workflow_root, raw=raw)
        return raw

    def make_workspace(self, *, workflow_root: Path, config: object) -> object:
        raw = config.raw if hasattr(config, "raw") else config
        return self.module.make_workspace(workflow_root=workflow_root, config=raw)

    def run_cli(self, *, workspace: object, argv: list[str]) -> int:
        return self.module.cli_main(workspace, argv)

    def run_preflight(self, *, workflow_root: Path, config: object) -> object:
        preflight = getattr(self.module, "run_preflight", None)
        if not callable(preflight):
            return type("PreflightResult", (), {"ok": True})()
        raw = config.raw if hasattr(config, "raw") else config
        try:
            return preflight(raw, workflow_root=workflow_root)
        except TypeError:
            return preflight(raw)
```

- [ ] **Step 2: Move dispatcher logic into registry**

Create `daedalus/workflows/registry.py`:

```python
"""Workflow discovery, loading, and CLI dispatch."""
from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import yaml

from workflows.contract import WorkflowContractError, load_workflow_contract
from workflows.workflow import ModuleWorkflow, Workflow


_REQUIRED_ATTRS = (
    "NAME",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CONFIG_SCHEMA_PATH",
    "make_workspace",
    "cli_main",
)


def load_workflow(name: str) -> ModuleType:
    """Import ``workflows.<slug>`` and verify it meets the public contract."""

    workflow = load_workflow_object(name)
    module = importlib.import_module(f"workflows.{name.replace('-', '_')}")
    if module.NAME != workflow.name:
        raise WorkflowContractError(
            f"workflow module workflows/{name.replace('-', '_')} declares NAME={module.NAME!r}, "
            f"which does not match the workflow object {workflow.name!r}"
        )
    return module


def load_workflow_object(name: str) -> Workflow:
    slug = name.replace("-", "_")
    module = importlib.import_module(f"workflows.{slug}")
    workflow = getattr(module, "WORKFLOW", None)
    if workflow is None:
        missing = [attr for attr in _REQUIRED_ATTRS if not hasattr(module, attr)]
        if missing:
            raise WorkflowContractError(
                f"workflow '{name}' missing required attributes: {missing}"
            )
        workflow = ModuleWorkflow(module)
    if workflow.name != name:
        raise WorkflowContractError(
            f"workflow module workflows/{slug} declares NAME={workflow.name!r}, "
            f"which does not match the directory '{name}'"
        )
    return workflow


def run_cli(
    workflow_root: Path,
    argv: list[str],
    *,
    require_workflow: str | None = None,
) -> int:
    contract = load_workflow_contract(workflow_root)
    config_path = contract.source_path
    raw_config = contract.config
    workflow_name = raw_config.get("workflow")
    if not workflow_name:
        raise WorkflowContractError(
            f"{config_path} is missing top-level `workflow:` field"
        )
    if require_workflow and workflow_name != require_workflow:
        raise WorkflowContractError(
            f"{config_path} declares workflow={workflow_name!r}, "
            f"but invocation pins require_workflow={require_workflow!r}"
        )

    workflow = load_workflow_object(str(workflow_name))
    schema = yaml.safe_load(workflow.schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(raw_config, schema)

    schema_version = int(raw_config.get("schema-version", 1))
    if schema_version not in workflow.schema_versions:
        raise WorkflowContractError(
            f"workflow {workflow_name!r} does not support "
            f"schema-version={schema_version}; "
            f"supported: {list(workflow.schema_versions)}"
        )

    config = workflow.load_config(workflow_root=workflow_root, raw=raw_config)
    invoked_command = argv[0] if argv else None
    if invoked_command in workflow.preflight_gated_commands:
        result = workflow.run_preflight(workflow_root=workflow_root, config=config)
        if not getattr(result, "ok", True):
            _emit_dispatch_skipped_event(
                workflow_root=workflow_root,
                workflow_name=str(workflow_name),
                error_code=getattr(result, "error_code", None),
                error_detail=getattr(result, "error_detail", None),
            )
            raise WorkflowContractError(
                f"dispatch preflight failed for workflow {workflow_name!r}: "
                f"code={result.error_code} detail={result.error_detail}"
            )

    workspace = workflow.make_workspace(workflow_root=workflow_root, config=config)
    return workflow.run_cli(workspace=workspace, argv=argv)


def _emit_dispatch_skipped_event(
    *,
    workflow_root: Path,
    workflow_name: str,
    error_code: str | None,
    error_detail: str | None,
) -> None:
    try:
        from workflows.paths import runtime_paths
        import runtime as _runtime

        paths = runtime_paths(workflow_root)
        event = {
            "event": "daedalus.dispatch_skipped",
            "workflow": workflow_name,
            "code": error_code,
            "detail": error_detail,
        }
        _runtime.append_daedalus_event(
            event_log_path=paths["event_log_path"], event=event
        )
    except Exception:
        pass


def list_workflows() -> list[str]:
    pkg_dir = Path(__file__).parent
    names: list[str] = []
    for entry in sorted(pkg_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        init_file = entry / "__init__.py"
        if not init_file.exists():
            continue
        try:
            workflow = load_workflow_object(entry.name.replace("_", "-"))
        except Exception:
            continue
        names.append(workflow.name)
    return names
```

- [ ] **Step 3: Replace `daedalus/workflows/__init__.py` with registry exports**

Replace the file body with:

```python
"""Workflow-plugin dispatcher for Daedalus."""
from __future__ import annotations

from workflows.registry import (
    list_workflows,
    load_workflow,
    load_workflow_object,
    run_cli,
)
from workflows.workflow import ModuleWorkflow, Workflow

__all__ = [
    "Workflow",
    "ModuleWorkflow",
    "load_workflow",
    "load_workflow_object",
    "run_cli",
    "list_workflows",
]
```

- [ ] **Step 4: Expose `WORKFLOW` in `change_delivery`**

Add this import near the existing package constants in `daedalus/workflows/change_delivery/__init__.py`:

```python
from workflows.workflow import ModuleWorkflow
```

After `run_preflight` is imported, add:

```python
import sys as _sys

WORKFLOW = ModuleWorkflow(_sys.modules[__name__])
```

Add `"WORKFLOW"` to `__all__`.

- [ ] **Step 5: Expose `WORKFLOW` in `issue_runner`**

Apply the same adapter pattern in `daedalus/workflows/issue_runner/__init__.py`:

```python
from workflows.workflow import ModuleWorkflow
import sys as _sys

WORKFLOW = ModuleWorkflow(_sys.modules[__name__])
```

Add `"WORKFLOW"` to `__all__`.

- [ ] **Step 6: Run dispatcher and workflow object tests**

Run:

```bash
pytest tests/test_workflows_flat_layout.py::test_bundled_workflows_expose_standard_workflow_object \
  tests/test_workflows_dispatcher.py \
  tests/test_restructure_guardrails.py -q
```

Expected: PASS for workflow objects and existing dispatch behavior.

- [ ] **Step 7: Commit workflow object and registry**

```bash
git add daedalus/workflows/workflow.py daedalus/workflows/registry.py daedalus/workflows/__init__.py daedalus/workflows/change_delivery/__init__.py daedalus/workflows/issue_runner/__init__.py
git commit -m "refactor: add standard workflow registry"
```

---

### Task 3: Flatten `workflows.core`

**Files:**
- Create: `daedalus/workflows/config.py`
- Create: `daedalus/workflows/hooks.py`
- Create: `daedalus/workflows/prompts.py`
- Modify: workflow imports using `workflows.core.*`
- Delete: `daedalus/workflows/core/*`
- Test: `tests/test_workflows_core_config.py`
- Test: `tests/test_workflows_core_hooks.py`
- Test: `tests/test_workflows_core_prompts.py`

- [ ] **Step 1: Move core modules to flat paths**

Run:

```bash
git mv daedalus/workflows/core/config.py daedalus/workflows/config.py
git mv daedalus/workflows/core/hooks.py daedalus/workflows/hooks.py
git mv daedalus/workflows/core/prompts.py daedalus/workflows/prompts.py
```

Do not move `core/types.py`; `workflow.py` replaces it.

- [ ] **Step 2: Update imports inside moved files**

In `daedalus/workflows/hooks.py`, keep the relative import:

```python
from .config import get_value
```

No behavior changes are needed in `config.py` or `prompts.py`.

- [ ] **Step 3: Rewrite production imports**

Replace these imports:

```python
from workflows.core.config import ConfigView, resolve_path
from workflows.core.config import ConfigError
from workflows.core.hooks import build_hook_env, run_shell_hook
from workflows.core.prompts import render_prompt_template
from workflows.core.types import WorkflowDriver
```

With:

```python
from workflows.config import ConfigView, resolve_path
from workflows.config import ConfigError
from workflows.hooks import build_hook_env, run_shell_hook
from workflows.prompts import render_prompt_template
from engine.driver import WorkflowDriver
```

If `engine.driver` is unavailable in a clean interpreter, use the existing fallback pattern:

```python
try:
    from engine.driver import WorkflowDriver
except ModuleNotFoundError:
    from daedalus.engine.driver import WorkflowDriver
```

- [ ] **Step 4: Update tests to flat names**

Rename test files only if desired. At minimum, update imports inside:

```text
tests/test_workflows_core_config.py
tests/test_workflows_core_hooks.py
tests/test_workflows_core_prompts.py
```

Use:

```python
from workflows.config import ConfigError, ConfigView, first_present, get_bool, get_int, get_list, get_mapping, get_str, get_value, require, resolve_env_indirection, resolve_path
from workflows.hooks import build_hook_env, run_shell_hook
from workflows.prompts import render_prompt_template
```

- [ ] **Step 5: Delete empty `core` package**

Run:

```bash
git rm daedalus/workflows/core/__init__.py daedalus/workflows/core/types.py
```

- [ ] **Step 6: Run core tests**

Run:

```bash
pytest tests/test_workflows_core_config.py \
  tests/test_workflows_core_hooks.py \
  tests/test_workflows_core_prompts.py \
  tests/test_workflows_flat_layout.py::test_flat_workflow_modules_import -q
```

Expected: PASS.

- [ ] **Step 7: Commit flat core modules**

```bash
git add daedalus/workflows tests
git commit -m "refactor: flatten workflow core helpers"
```

---

### Task 4: Flatten `workflows.shared`

**Files:**
- Create: `daedalus/workflows/config_snapshot.py`
- Create: `daedalus/workflows/config_watcher.py`
- Create: `daedalus/workflows/paths.py`
- Create: `daedalus/workflows/stall.py`
- Modify: callers of `workflows.shared.*`
- Modify: callers of `workflows.change_delivery.config_snapshot`
- Modify: callers of `workflows.change_delivery.stall`
- Delete: `daedalus/workflows/shared/*`
- Delete: `daedalus/workflows/change_delivery/config_snapshot.py`
- Delete: `daedalus/workflows/change_delivery/stall.py`
- Test: `tests/test_config_snapshot.py`
- Test: `tests/test_config_watcher.py`
- Test: `tests/test_stall_detection.py`
- Test: `tests/test_workflows_code_review_paths.py`

- [ ] **Step 1: Move shared modules to flat paths**

Run:

```bash
git mv daedalus/workflows/shared/config_snapshot.py daedalus/workflows/config_snapshot.py
git mv daedalus/workflows/shared/config_watcher.py daedalus/workflows/config_watcher.py
git mv daedalus/workflows/shared/paths.py daedalus/workflows/paths.py
git mv daedalus/workflows/shared/stall.py daedalus/workflows/stall.py
```

- [ ] **Step 2: Update moved module imports**

In `daedalus/workflows/config_watcher.py`, replace:

```python
from workflows.shared.config_snapshot import AtomicRef, ConfigSnapshot
```

With:

```python
from workflows.config_snapshot import AtomicRef, ConfigSnapshot
```

In `daedalus/workflows/stall.py`, replace:

```python
from workflows.shared.config_snapshot import ConfigSnapshot
```

With:

```python
from workflows.config_snapshot import ConfigSnapshot
```

- [ ] **Step 3: Rewrite production imports**

Replace:

```python
from workflows.shared.config_snapshot import AtomicRef, ConfigSnapshot
from workflows.shared.config_watcher import ConfigWatcher
from workflows.shared.paths import runtime_paths
from workflows.shared.paths import lane_memo_path, lane_state_path
from workflows.change_delivery.config_snapshot import AtomicRef, ConfigSnapshot
from workflows.change_delivery.stall import reconcile_stalls
from workflows.change_delivery.stall import StallVerdict
from workflows.change_delivery.paths import runtime_paths
from workflows.change_delivery.paths import workflow_cli_argv
```

With:

```python
from workflows.config_snapshot import AtomicRef, ConfigSnapshot
from workflows.config_watcher import ConfigWatcher
from workflows.paths import runtime_paths
from workflows.paths import lane_memo_path, lane_state_path
from workflows.stall import reconcile_stalls
from workflows.stall import StallVerdict
```

Keep `workflows.change_delivery.paths` only where the tests intentionally load that module and where workflow-local compatibility is still needed. Otherwise use `workflows.paths`.

- [ ] **Step 4: Remove old change-delivery shim files**

Run:

```bash
git rm daedalus/workflows/change_delivery/config_snapshot.py
git rm daedalus/workflows/change_delivery/stall.py
git rm daedalus/workflows/shared/__init__.py
```

- [ ] **Step 5: Update tests to flat imports**

In these tests, replace old imports with flat imports:

```text
tests/test_config_snapshot.py
tests/test_config_watcher.py
tests/test_stall_detection.py
tests/test_workflows_preflight_cli_integration.py
```

Examples:

```python
from workflows.config_snapshot import AtomicRef, ConfigSnapshot
from workflows.config_watcher import ConfigWatcher, parse_and_validate_contract
from workflows.stall import StallVerdict, reconcile_stalls
from workflows.paths import runtime_paths
```

- [ ] **Step 6: Run shared helper tests**

Run:

```bash
pytest tests/test_config_snapshot.py \
  tests/test_config_watcher.py \
  tests/test_stall_detection.py \
  tests/test_workflows_code_review_paths.py \
  tests/test_workflows_preflight_cli_integration.py \
  tests/test_workflows_flat_layout.py::test_flat_workflow_modules_import -q
```

Expected: PASS.

- [ ] **Step 7: Commit flat shared modules**

```bash
git add daedalus/workflows daedalus/watch.py tests
git commit -m "refactor: flatten workflow shared helpers"
```

---

### Task 5: Delete Workflow Runtime Shim Packages

**Files:**
- Modify: `daedalus/workflows/change_delivery/workspace.py`
- Modify: `daedalus/workflows/change_delivery/dispatch.py`
- Modify: `daedalus/workflows/change_delivery/reviews.py`
- Modify: `daedalus/workflows/runtime_matrix.py`
- Modify: runtime-related tests
- Delete: `daedalus/workflows/change_delivery/runtimes/*`
- Delete: `daedalus/workflows/shared/runtimes/*`

- [ ] **Step 1: Rewrite runtime imports in production**

Replace:

```python
from workflows.change_delivery.runtimes import build_runtimes
from workflows.change_delivery.runtimes import Runtime
from workflows.change_delivery.runtimes import SessionHandle
from workflows.change_delivery.runtimes.acpx_codex import AcpxCodexRuntime
from workflows.change_delivery.runtimes.claude_cli import ClaudeCliRuntime
from workflows.change_delivery.runtimes.hermes_agent import HermesAgentRuntime
from workflows.change_delivery.runtimes.stages import run_runtime_stage
from workflows.shared.runtimes import build_runtimes
```

With direct runtime imports:

```python
from runtimes.registry import build_runtimes
from runtimes.types import Runtime, SessionHandle
from runtimes.acpx_codex import AcpxCodexRuntime
from runtimes.claude_cli import ClaudeCliRuntime
from runtimes.hermes_agent import HermesAgentRuntime
from runtimes.stages import run_runtime_stage
```

Keep existing direct imports such as:

```python
from runtimes.stages import prompt_result_from_stage, run_runtime_stage
```

- [ ] **Step 2: Rewrite runtime imports in tests**

Update these tests to import from `runtimes.*`:

```text
tests/test_runtime_agnostic_phase_a.py
tests/test_stall_detection.py
tests/test_workflows_code_review_runtimes_acpx_codex.py
tests/test_workflows_code_review_runtimes_claude_cli.py
tests/test_workflows_code_review_runtimes_init.py
tests/test_workflows_code_review_sessions.py
```

Examples:

```python
from runtimes.types import Runtime, SessionHandle
from runtimes.registry import build_runtimes
from runtimes.acpx_codex import AcpxCodexRuntime
from runtimes.claude_cli import ClaudeCliRuntime
from runtimes.hermes_agent import HermesAgentRuntime
```

- [ ] **Step 3: Delete runtime shim packages**

Run:

```bash
git rm -r daedalus/workflows/change_delivery/runtimes
git rm -r daedalus/workflows/shared/runtimes
```

- [ ] **Step 4: Run runtime tests**

Run:

```bash
pytest tests/test_runtime_agnostic_phase_a.py \
  tests/test_stall_detection.py \
  tests/test_runtime_matrix.py \
  tests/test_runtimes_split_namespace.py \
  tests/test_workflows_code_review_runtimes_init.py \
  tests/test_workflows_code_review_runtimes_acpx_codex.py \
  tests/test_workflows_code_review_runtimes_claude_cli.py \
  tests/test_workflows_flat_layout.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit runtime shim removal**

```bash
git add daedalus tests
git commit -m "refactor: remove workflow runtime shims"
```

---

### Task 6: Route Workflows Through Typed Config

**Files:**
- Modify: `daedalus/workflows/issue_runner/__init__.py`
- Modify: `daedalus/workflows/issue_runner/workspace.py`
- Modify: `daedalus/workflows/issue_runner/orchestrator.py`
- Modify: `daedalus/workflows/change_delivery/__init__.py`
- Modify: `daedalus/workflows/change_delivery/workspace.py`
- Modify: `daedalus/workflows/change_delivery/preflight.py`
- Test: `tests/test_issue_runner_config.py`
- Test: `tests/test_change_delivery_config.py`
- Test: workflow CLI and workspace tests

- [ ] **Step 1: Add `load_config` functions to workflow packages**

In `daedalus/workflows/issue_runner/__init__.py`, add:

```python
from workflows.issue_runner.config import IssueRunnerConfig


def load_config(*, workflow_root: Path, raw: dict[str, Any]) -> IssueRunnerConfig:
    return IssueRunnerConfig.from_raw(raw, workflow_root=workflow_root)
```

In `daedalus/workflows/change_delivery/__init__.py`, add:

```python
from workflows.change_delivery.config import ChangeDeliveryConfig


def load_config(*, workflow_root: Path, raw: dict[str, Any]) -> ChangeDeliveryConfig:
    return ChangeDeliveryConfig.from_raw(raw, workflow_root=workflow_root)
```

Add `"load_config"` to each package `__all__`.

- [ ] **Step 2: Make workspace factories accept typed config**

In both workflow package `make_workspace` functions, unwrap typed configs for unchanged internals:

```python
def make_workspace(*, workflow_root: Path, config: dict | IssueRunnerConfig):
    raw_config = config.raw if hasattr(config, "raw") else config
    return _make_workspace_inner(workspace_root=workflow_root, config=raw_config)
```

Use `ChangeDeliveryConfig` in the change-delivery package version.

- [ ] **Step 3: Use typed config in low-risk issue-runner call sites**

In `daedalus/workflows/issue_runner/workspace.py`, when config is available, derive typed config once:

```python
typed_config = IssueRunnerConfig.from_raw(config, workflow_root=workspace_root)
```

Then replace repeated helper calls where already covered by tests:

```python
poll_interval_seconds = typed_config.polling.interval_seconds
max_retry_backoff_ms = typed_config.agent.max_retry_backoff_ms
storage_paths = {
    "status": typed_config.storage.status,
    "health": typed_config.storage.health,
    "audit_log": typed_config.storage.audit_log,
    "scheduler": typed_config.storage.scheduler,
}
```

Keep `typed_config.raw` for schema/debug payloads.

- [ ] **Step 4: Use typed config in low-risk change-delivery call sites**

In `daedalus/workflows/change_delivery/workspace.py`, derive:

```python
typed_config = ChangeDeliveryConfig.from_raw(config, workflow_root=workspace_root)
```

Replace storage path derivation with:

```python
storage_paths = typed_config.storage.as_dict()
```

Replace repository local path access with:

```python
repo_path = typed_config.repository.local_path
```

Keep workflow policy, lane state machine, review policy, and gate policy on raw config in this task.

- [ ] **Step 5: Run typed config tests**

Run:

```bash
pytest tests/test_issue_runner_config.py \
  tests/test_change_delivery_config.py \
  tests/test_workflows_issue_runner_workspace.py \
  tests/test_workflows_code_review_workspace.py \
  tests/test_workflows_dispatcher.py \
  tests/test_workflow_driver_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit typed config routing**

```bash
git add daedalus/workflows tests
git commit -m "refactor: route workflows through typed config"
```

---

### Task 7: Update Docs and Measure Slimming

**Files:**
- Modify: `daedalus/workflows/README.md`
- Modify: `docs/workflows/README.md`
- Modify: `docs/restructure-shim-audit.md`
- Modify: `docs/concepts/hot-reload.md`
- Modify: `docs/concepts/stalls.md`
- Modify: `docs/concepts/runtimes.md`
- Modify: `docs/concepts/sessions.md`

- [ ] **Step 1: Update layout docs**

In `daedalus/workflows/README.md`, replace the layout section with:

````markdown
## Layout

`daedalus/workflows/` has one flat support layer plus two bundled workflow packages.

```text
workflows/
|-- __init__.py              # public loader exports
|-- __main__.py              # `python -m workflows <name> ...`
|-- workflow.py              # standard workflow object contract
|-- registry.py              # workflow discovery + dispatch
|-- config.py                # typed config helpers
|-- config_snapshot.py       # hot-reload snapshot primitives
|-- config_watcher.py        # workflow contract file watcher
|-- paths.py                 # workflow root/path helpers
|-- stall.py                 # shared stall detection
|-- contract.py              # WORKFLOW.md parser/projector
|-- validation.py            # schema validation helpers
|-- runtime_matrix.py        # runtime matrix command support
|-- runtime_presets.py       # runtime config normalization
|-- change_delivery/         # managed SDLC workflow internals
`-- issue_runner/            # generic tracker-driven workflow internals
```

Runtime adapters live under top-level `runtimes/`. Workflow code imports them directly.
````

- [ ] **Step 2: Update shim audit**

In `docs/restructure-shim-audit.md`, record that private workflow shims were removed:

```markdown
## Removed In Workflows Flattening

- `daedalus/workflows/core/`
- `daedalus/workflows/shared/`
- `daedalus/workflows/shared/runtimes/`
- `daedalus/workflows/change_delivery/runtimes/`
- `daedalus/workflows/change_delivery/config_snapshot.py`
- `daedalus/workflows/change_delivery/stall.py`

The public root-level `workflows/` compatibility package remains.
```

- [ ] **Step 3: Update concept docs**

Replace references:

```text
daedalus/workflows/change_delivery/config_snapshot.py -> daedalus/workflows/config_snapshot.py
daedalus/workflows/change_delivery/stall.py -> daedalus/workflows/stall.py
daedalus/workflows/shared/stall.py -> daedalus/workflows/stall.py
daedalus/workflows/change_delivery/runtimes/ -> daedalus/runtimes/
daedalus/workflows/shared/runtimes/ -> daedalus/runtimes/
```

- [ ] **Step 4: Measure file and line reduction**

Run:

```bash
git diff --stat HEAD~6..HEAD -- daedalus/workflows
powershell -NoProfile -Command "Get-ChildItem daedalus\\workflows -Recurse -File | Measure-Object | Select-Object -ExpandProperty Count"
powershell -NoProfile -Command "Get-ChildItem daedalus\\workflows -Recurse -File -Filter *.py | Get-Content | Measure-Object -Line | Select-Object -ExpandProperty Lines"
```

Expected: file count drops because private shim packages are deleted. A full 25% line reduction is not expected until the large-file slimming tasks that follow this namespace cleanup.

- [ ] **Step 5: Run docs-sensitive tests**

Run:

```bash
pytest tests/test_public_cli_docs_drift.py \
  tests/test_docs_public_workflow_template.py \
  tests/test_official_plugin_layout.py \
  tests/test_workflows_flat_layout.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit docs and measurement notes**

```bash
git add docs daedalus/workflows/README.md
git commit -m "docs: document flat workflows layout"
```

---

### Task 8: Final Verification

**Files:**
- No source edits unless tests expose missed imports.

- [ ] **Step 1: Search for removed private imports**

Run:

```bash
rg -n "workflows\\.core|workflows\\.shared|workflows\\.change_delivery\\.runtimes|workflows\\.change_delivery\\.config_snapshot|workflows\\.change_delivery\\.stall" daedalus tests docs
```

Expected: no matches, except this implementation plan if it is still in the repository. If the plan appears, do not change it.

- [ ] **Step 2: Run targeted workflow suite**

Run:

```bash
pytest tests/test_restructure_guardrails.py \
  tests/test_official_plugin_layout.py \
  tests/test_workflows_flat_layout.py \
  tests/test_workflows_dispatcher.py \
  tests/test_workflow_driver_api.py \
  tests/test_workflows_core_config.py \
  tests/test_workflows_core_hooks.py \
  tests/test_workflows_core_prompts.py \
  tests/test_issue_runner_config.py \
  tests/test_change_delivery_config.py \
  tests/test_runtime_matrix.py \
  tests/test_workflows_issue_runner_workspace.py \
  tests/test_workflows_code_review_workspace.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full suite**

Run:

```bash
pytest
```

Expected: PASS.

- [ ] **Step 4: Commit fixes if final verification required edits**

If Step 1-3 required edits:

```bash
git add daedalus tests docs
git commit -m "fix: complete workflows flattening migration"
```

If no edits were needed, do not create an empty commit.

---

## Follow-Up Slimming Plan

This plan flattens the namespace and removes private shim layers. It prepares the codebase for the 25% line reduction but may not achieve it alone because the largest files still contain real behavior. The next implementation plan should target these files with tests per extraction:

- `daedalus/workflows/change_delivery/workspace.py`
- `daedalus/workflows/change_delivery/reviews.py`
- `daedalus/workflows/change_delivery/status.py`
- `daedalus/workflows/issue_runner/workspace.py`

Recommended first extraction after this plan:

- `change_delivery/workspace.py` path/bootstrap responsibilities into `change_delivery/bootstrap.py`.
- `change_delivery/reviews.py` provider parsing into `change_delivery/review_signals.py`.
- `change_delivery/status.py` view-model shaping into `change_delivery/status_projection.py`.
- `issue_runner/workspace.py` storage/status helpers into `issue_runner/state_files.py`.
