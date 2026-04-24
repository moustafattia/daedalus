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
from types import ModuleType


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
