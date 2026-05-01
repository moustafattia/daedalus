from pathlib import Path

import pytest
import yaml

from workflows.contract import (
    WORKFLOW_POLICY_KEY,
    WorkflowContractError,
    find_workflow_contract_path,
    load_workflow_contract,
    load_workflow_contract_file,
    write_workflow_contract_pointer,
)


def _native_config() -> dict:
    return {
        "workflow": "change-delivery",
        "schema-version": 1,
        "instance": {"name": "attmous-daedalus-change-delivery", "engine-owner": "hermes"},
        "repository": {
            "local-path": "/tmp/repo",
            "slug": "attmous/daedalus",
            "active-lane-label": "active-lane",
        },
        "tracker": {
            "kind": "github",
            "github_slug": "attmous/daedalus",
            "active_states": ["open"],
            "terminal_states": ["closed"],
        },
        "code-host": {"kind": "github", "github_slug": "attmous/daedalus"},
        "runtimes": {"r1": {"kind": "claude-cli", "max-turns-per-invocation": 8, "timeout-seconds": 60}},
        "actors": {
            "implementer": {"name": "coder", "model": "gpt-5", "runtime": "r1"},
            "implementer-high-effort": {"name": "coder-high", "model": "gpt-5-high", "runtime": "r1"},
            "reviewer": {"name": "reviewer", "model": "claude", "runtime": "r1"},
        },
        "stages": {
            "implement": {
                "actor": "implementer",
                "escalation": {"after-attempts": 2, "actor": "implementer-high-effort"},
            },
            "publish": {"action": "pr.publish"},
            "merge": {"action": "pr.merge"},
        },
        "gates": {
            "pre-publish-review": {"type": "agent-review", "actor": "reviewer"},
            "maintainer-approval": {"type": "pr-comment-approval", "enabled": False},
            "ci-green": {"type": "code-host-checks"},
        },
        "triggers": {"lane-selector": {"type": "github-label", "label": "active-lane"}},
        "storage": {
            "ledger": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
        },
    }


def _workflow_markdown(config: dict, *, prompt_role: str = "coder", body: str = "You are the workflow prompt.") -> str:
    del prompt_role
    return "---\n" + yaml.safe_dump(config, sort_keys=False) + "---\n\n" + body + "\n"


def test_load_workflow_contract_ignores_removed_yaml_contract(tmp_path):
    root = tmp_path / "wf"
    (root / "config").mkdir(parents=True)
    path = root / "config" / "workflow.yaml"
    path.write_text(yaml.safe_dump(_native_config()), encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        load_workflow_contract(root)


def test_load_workflow_contract_reads_markdown_and_injects_prompt(tmp_path):
    root = tmp_path / "wf"
    root.mkdir()
    path = root / "WORKFLOW.md"
    path.write_text(
        _workflow_markdown(
            _native_config(),
            prompt_role="reviewer",
            body="Review the lane strictly.",
        ),
        encoding="utf-8",
    )

    contract = load_workflow_contract(root)

    assert contract.source_path == path
    assert contract.config["workflow"] == "change-delivery"
    assert contract.config[WORKFLOW_POLICY_KEY] == "Review the lane strictly."
    assert contract.prompt_template == "Review the lane strictly."


def test_load_workflow_contract_markdown_body_becomes_workflow_policy(tmp_path):
    path = tmp_path / "WORKFLOW.md"
    path.write_text(
        _workflow_markdown(_native_config(), body="Prompt body."),
        encoding="utf-8",
    )

    contract = load_workflow_contract_file(path)

    assert contract.config[WORKFLOW_POLICY_KEY] == "Prompt body."


def test_load_workflow_contract_markdown_rejects_duplicate_policy_sources(tmp_path):
    payload = _native_config()
    payload[WORKFLOW_POLICY_KEY] = "front matter policy"
    path = tmp_path / "WORKFLOW.md"
    path.write_text(_workflow_markdown(payload, body="body policy"), encoding="utf-8")

    with pytest.raises(WorkflowContractError, match="workflow-policy"):
        load_workflow_contract_file(path)


def test_load_workflow_contract_file_rejects_yaml_contract(tmp_path):
    root = tmp_path / "wf"
    (root / "config").mkdir(parents=True)
    yaml_path = root / "config" / "workflow.yaml"
    yaml_path.write_text(yaml.safe_dump(_native_config()), encoding="utf-8")

    with pytest.raises(WorkflowContractError, match="expected Markdown"):
        load_workflow_contract_file(yaml_path)


def test_load_workflow_contract_follows_workflow_root_pointer_to_repo_contract(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    contract_path = repo_root / "WORKFLOW.md"
    contract_path.write_text(_workflow_markdown(_native_config(), body="Repo-owned contract."), encoding="utf-8")

    workflow_root = tmp_path / "workflow-root"
    write_workflow_contract_pointer(workflow_root, contract_path)

    contract = load_workflow_contract(workflow_root)

    assert contract.source_path == contract_path.resolve()
    assert contract.config["workflow"] == "change-delivery"


def test_find_workflow_contract_path_resolves_named_repo_contract_when_single(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    contract_path = repo_root / "WORKFLOW-issue-runner.md"
    cfg = _native_config()
    cfg["workflow"] = "issue-runner"
    contract_path.write_text(_workflow_markdown(cfg, body="Issue runner contract."), encoding="utf-8")

    assert find_workflow_contract_path(repo_root) == contract_path.resolve()
