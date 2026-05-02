"""Workflow-plugin dispatcher for daedalus.

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
import inspect
from pathlib import Path
from types import ModuleType

import jsonschema

from .contract import WorkflowContractError, load_workflow_contract


_REQUIRED_ATTRS = (
    "NAME",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CONFIG_SCHEMA_PATH",
    "make_workspace",
    "cli_main",
)


def load_workflow(name: str) -> ModuleType:
    """Import ``workflows.<slug>`` and verify it meets the contract.

    ``name`` is the canonical hyphenated form (``change-delivery``);
    internally it maps to the Python slug (``change_delivery``).
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
    """Read the workflow contract under ``workflow_root`` and dispatch.

    When ``require_workflow`` is set, the dispatcher asserts that the contract's
    ``workflow:`` field matches before dispatching. Used by the per-workflow
    direct form (``python3 -m workflows.change_delivery ...``) to pin the module
    regardless of what the contract declares.
    """
    contract = load_workflow_contract(workflow_root)
    config_path = contract.source_path
    cfg = contract.config
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

    import yaml

    schema = yaml.safe_load(module.CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(cfg, schema)

    schema_version = int(cfg.get("schema-version", 1))
    if schema_version not in module.SUPPORTED_SCHEMA_VERSIONS:
        raise WorkflowContractError(
            f"workflow {workflow_name!r} does not support "
            f"schema-version={schema_version}; "
            f"supported: {list(module.SUPPORTED_SCHEMA_VERSIONS)}"
        )

    # Symphony §6.3 dispatch preflight. If the loaded workflow module
    # exposes ``run_preflight``, call it before dispatch — but only for
    # commands the workflow declares as dispatch-gated. Codex P1 on
    # PR #21: gating ALL commands prevents operators from running
    # diagnostic / repair commands when the config is unhealthy, which
    # is exactly when those commands are needed.
    #
    # NOTE on reconciliation: Symphony §6.3 says preflight failure must
    # not block reconciliation. Daedalus's tick is a one-shot CLI
    # invocation, so reconciliation across CLI invocations is naturally
    # preserved by the next invocation re-running successfully once the
    # config is fixed. Within a single invocation, dispatch is aborted —
    # the structured event-log trail is the operator-visible signal.
    preflight_fn = getattr(module, "run_preflight", None)
    gated_commands = getattr(module, "PREFLIGHT_GATED_COMMANDS", None)
    invoked_command = argv[0] if argv else None
    should_gate = (
        callable(preflight_fn)
        and gated_commands is not None
        and invoked_command in gated_commands
    )
    if should_gate:
        if "workflow_root" in inspect.signature(preflight_fn).parameters:
            result = preflight_fn(cfg, workflow_root=workflow_root)
        else:
            result = preflight_fn(cfg)
        if not getattr(result, "ok", True):
            _emit_dispatch_skipped_event(
                workflow_root=workflow_root,
                workflow_name=workflow_name,
                error_code=getattr(result, "error_code", None),
                error_detail=getattr(result, "error_detail", None),
            )
            raise WorkflowContractError(
                f"dispatch preflight failed for workflow {workflow_name!r}: "
                f"code={result.error_code} detail={result.error_detail}"
            )

    workspace = module.make_workspace(workflow_root=workflow_root, config=cfg)
    return module.cli_main(workspace, argv)


def _emit_dispatch_skipped_event(
    *,
    workflow_root: Path,
    workflow_name: str,
    error_code: str | None,
    error_detail: str | None,
) -> None:
    """Append a ``daedalus.dispatch_skipped`` event to the workflow event log.

    Best-effort: silently swallows I/O errors so a broken event log path
    cannot mask the underlying preflight failure that the caller is about
    to surface as a WorkflowContractError.
    """
    try:
        from engine.store import EngineStore
        from workflows.shared.paths import runtime_paths

        paths = runtime_paths(workflow_root)
        event = {
            "event": "daedalus.dispatch_skipped",
            "workflow": workflow_name,
            "code": error_code,
            "detail": error_detail,
        }
        EngineStore(db_path=paths["db_path"], workflow=workflow_name).append_event(
            event_type="daedalus.dispatch_skipped",
            severity="warn",
            payload=event,
        )
    except Exception:
        # Best-effort: never let event-log failures shadow the preflight error.
        pass


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
