"""Workflow-plugin dispatcher for hermes-relay.

A workflow is a Python package at ``workflows/<name>/`` (hyphens in the
canonical name map to underscores in the Python slug). Every workflow
must expose these five attributes in its package ``__init__.py``:

- NAME: str                     — canonical hyphenated name
- SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...]  — YAML schema versions this module can load
- CONFIG_SCHEMA_PATH: Path      — path to JSON Schema for the workflow's config
- make_workspace(*, workflow_root: Path, config: dict) -> object
- cli_main(workspace: object, argv: list[str]) -> int
"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import jsonschema
import yaml


class WorkflowContractError(RuntimeError):
    """Raised when a workflow package does not meet the required contract."""


_REQUIRED_ATTRS = (
    "NAME",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CONFIG_SCHEMA_PATH",
    "make_workspace",
    "cli_main",
)


def load_workflow(name: str) -> ModuleType:
    """Import ``workflows.<slug>`` and verify it meets the contract.

    ``name`` is the canonical hyphenated form (``code-review``);
    internally it maps to the Python slug (``code_review``).
    """
    slug = name.replace("-", "_")
    module = importlib.import_module(f"workflows.{slug}")
    missing = [attr for attr in _REQUIRED_ATTRS if not hasattr(module, attr)]
    if missing:
        raise WorkflowContractError(
            f"workflow '{name}' missing required attributes: {missing}"
        )
    if module.NAME != name:
        raise WorkflowContractError(
            f"workflow module workflows/{slug} declares NAME={module.NAME!r}, "
            f"which does not match the directory '{name}'"
        )
    return module


def run_cli(
    workflow_root: Path,
    argv: list[str],
    *,
    require_workflow: str | None = None,
) -> int:
    """Read <workflow_root>/config/workflow.yaml, dispatch to the named workflow.

    When ``require_workflow`` is set, the dispatcher asserts that the YAML's
    ``workflow:`` field matches before dispatching. Used by the per-workflow
    direct form (``python3 -m workflows.code_review ...``) to pin the module
    regardless of what the YAML declares.
    """
    config_path = workflow_root / "config" / "workflow.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise WorkflowContractError(
            f"{config_path} must contain a YAML mapping at the top level"
        )
    workflow_name = cfg.get("workflow")
    if not workflow_name:
        raise WorkflowContractError(
            f"{config_path} is missing top-level `workflow:` field"
        )
    if require_workflow and workflow_name != require_workflow:
        raise WorkflowContractError(
            f"{config_path} declares workflow={workflow_name!r}, "
            f"but invocation pins require_workflow={require_workflow!r}"
        )

    module = load_workflow(workflow_name)

    schema = yaml.safe_load(module.CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(cfg, schema)

    schema_version = int(cfg.get("schema-version", 1))
    if schema_version not in module.SUPPORTED_SCHEMA_VERSIONS:
        raise WorkflowContractError(
            f"workflow {workflow_name!r} does not support "
            f"schema-version={schema_version}; "
            f"supported: {list(module.SUPPORTED_SCHEMA_VERSIONS)}"
        )

    workspace = module.make_workspace(workflow_root=workflow_root, config=cfg)
    return module.cli_main(workspace, argv)


def list_workflows() -> list[str]:
    """Return canonical names of installed workflows.

    Scans the ``workflows/`` package directory for sub-packages that declare
    the workflow-plugin contract (have a ``NAME`` attribute).
    """
    pkg_dir = Path(__file__).parent
    names: list[str] = []
    for entry in sorted(pkg_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        init_file = entry / "__init__.py"
        if not init_file.exists():
            continue
        try:
            module = load_workflow(entry.name.replace("_", "-"))
        except Exception:
            continue
        if hasattr(module, "NAME"):
            names.append(module.NAME)
    return names
