"""Workflow discovery, loading, and CLI dispatch."""
from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

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
    """Import a workflow module and verify it meets the public contract."""

    workflow = load_workflow_object(name)
    module = _import_workflow_module(name)
    if module.NAME != workflow.name:
        raise WorkflowContractError(
            f"workflow module for {name!r} declares NAME={module.NAME!r}, "
            f"which does not match the workflow object {workflow.name!r}"
        )
    return module


def load_workflow_object(name: str) -> Workflow:
    module = _import_workflow_module(name)
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
            f"workflow module for {name!r} declares NAME={workflow.name!r}"
        )
    return workflow


def _import_workflow_module(name: str) -> ModuleType:
    if name == "agentic":
        return importlib.import_module("workflows")
    return importlib.import_module(f"workflows.{name.replace('-', '_')}")


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
    return ["agentic"]
