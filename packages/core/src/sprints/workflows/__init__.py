"""Flat policy-driven workflow package for Sprints."""

from __future__ import annotations

from sprints.core.bindings import (
    RuntimePresetError,
    available_runtime_presets,
    build_runtime_matrix_report,
    configure_runtime_contract,
    runtime_availability_checks,
    runtime_binding_checks,
    runtime_stage_checks,
)
from sprints.core.contract_apply import (
    WorkflowContractApplyError,
    apply_workflow_contract,
)
from sprints.core.contracts import (
    ActorPolicy,
    WorkflowContract,
    WorkflowContractError,
    WorkflowPolicy,
    WorkflowPolicyError,
    find_repo_workflow_contract_path,
    find_workflow_contract_path,
    load_workflow_contract,
    load_workflow_contract_file,
    render_workflow_markdown,
    workflow_contract_pointer_path,
    workflow_markdown_path,
    workflow_named_markdown_path,
    write_workflow_contract_pointer,
)
from sprints.core.loader import load_workflow_policy
from sprints.workflows.registry import (
    CONFIG_SCHEMA_PATH,
    DEFAULT_WORKFLOW_NAME,
    NAME,
    SUPPORTED_SCHEMA_VERSIONS,
    SUPPORTED_WORKFLOW_NAMES,
    WORKFLOW,
    WORKFLOWS,
    SprintsWorkflow,
    Workflow,
    list_workflows,
    load_config,
    load_workflow_object,
    make_workspace,
    run_cli,
)
from sprints.workflows.runner import main as cli_main
from sprints.core.validation import (
    build_readiness_recommendations,
    validate_workflow_contract,
)

__all__ = [
    "NAME",
    "DEFAULT_WORKFLOW_NAME",
    "SUPPORTED_WORKFLOW_NAMES",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CONFIG_SCHEMA_PATH",
    "WORKFLOW",
    "WORKFLOWS",
    "Workflow",
    "SprintsWorkflow",
    "WorkflowContract",
    "WorkflowContractApplyError",
    "WorkflowContractError",
    "WorkflowPolicy",
    "WorkflowPolicyError",
    "ActorPolicy",
    "RuntimePresetError",
    "load_config",
    "make_workspace",
    "cli_main",
    "load_workflow_object",
    "run_cli",
    "list_workflows",
    "load_workflow_contract",
    "load_workflow_contract_file",
    "load_workflow_policy",
    "render_workflow_markdown",
    "find_repo_workflow_contract_path",
    "find_workflow_contract_path",
    "workflow_contract_pointer_path",
    "workflow_markdown_path",
    "workflow_named_markdown_path",
    "write_workflow_contract_pointer",
    "apply_workflow_contract",
    "validate_workflow_contract",
    "build_readiness_recommendations",
    "available_runtime_presets",
    "configure_runtime_contract",
    "runtime_stage_checks",
    "runtime_binding_checks",
    "runtime_availability_checks",
    "build_runtime_matrix_report",
]
