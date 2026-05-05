"""Workflow contract and policy loading."""

from __future__ import annotations

from pathlib import Path

from sprints.core.contracts import (
    DEFAULT_WORKFLOW_MARKDOWN_FILENAME,
    WORKFLOW_CONTRACT_POINTER_RELATIVE_PATH,
    WORKFLOW_MARKDOWN_PREFIX,
    WORKFLOW_POLICY_KEY,
    ActorPolicy,
    WorkflowContract,
    WorkflowContractError,
    WorkflowPolicy,
    WorkflowPolicyError,
    find_repo_workflow_contract_path,
    find_workflow_contract_path,
    load_workflow_contract,
    load_workflow_contract_file,
    parse_workflow_policy,
    read_workflow_contract_pointer,
    render_workflow_markdown,
    workflow_contract_pointer_path,
    workflow_markdown_path,
    workflow_named_markdown_filename,
    workflow_named_markdown_path,
    write_workflow_contract_pointer,
)


def load_workflow_policy(workflow_root: Path) -> WorkflowPolicy:
    contract = load_workflow_contract(workflow_root)
    return parse_workflow_policy(contract.prompt_template)


__all__ = [
    "DEFAULT_WORKFLOW_MARKDOWN_FILENAME",
    "WORKFLOW_CONTRACT_POINTER_RELATIVE_PATH",
    "WORKFLOW_MARKDOWN_PREFIX",
    "WORKFLOW_POLICY_KEY",
    "ActorPolicy",
    "WorkflowContract",
    "WorkflowContractError",
    "WorkflowPolicy",
    "WorkflowPolicyError",
    "find_repo_workflow_contract_path",
    "find_workflow_contract_path",
    "load_workflow_policy",
    "load_workflow_contract",
    "load_workflow_contract_file",
    "parse_workflow_policy",
    "read_workflow_contract_pointer",
    "render_workflow_markdown",
    "workflow_contract_pointer_path",
    "workflow_markdown_path",
    "workflow_named_markdown_filename",
    "workflow_named_markdown_path",
    "write_workflow_contract_pointer",
]
