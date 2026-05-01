import importlib.util
import json
from pathlib import Path

import pytest

from workflows.contract import render_workflow_markdown
from workflows.validation import validate_workflow_contract


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def _tools():
    module_path = REPO_ROOT / "daedalus_cli.py"
    spec = importlib.util.spec_from_file_location("daedalus_tools_workflow_validation_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_issue_runner_contract(root: Path, repo: Path, *, overrides: dict | None = None) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "issues.json").write_text(
        json.dumps({"issues": [{"id": "ISSUE-1", "title": "First", "state": "todo"}]}),
        encoding="utf-8",
    )
    config = {
        "workflow": "issue-runner",
        "schema-version": 1,
        "instance": {"name": root.name, "engine-owner": "hermes"},
        "repository": {"local-path": str(repo), "slug": "attmous/daedalus"},
        "tracker": {"kind": "local-json", "path": "config/issues.json"},
        "workspace": {"root": "workspace/issues"},
        "agent": {"name": "Issue_Runner_Agent", "model": "gpt-5.4", "runtime": "default"},
        "daedalus": {"runtimes": {"default": {"kind": "hermes-agent", "command": ["echo", "{prompt_path}"]}}},
        "storage": {
            "status": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
        },
    }
    if overrides:
        config.update(overrides)
    (root / "WORKFLOW.md").write_text(
        render_workflow_markdown(config=config, prompt_template="Issue: {{ issue.identifier }}"),
        encoding="utf-8",
    )


def test_validate_workflow_contract_accepts_valid_issue_runner(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "attmous-daedalus-issue-runner"
    root.mkdir()
    _write_issue_runner_contract(root, repo)
    monkeypatch.chdir(tmp_path)

    report = validate_workflow_contract(root, service_mode="active")

    assert report["ok"] is True
    assert report["workflow"] == "issue-runner"
    assert report["source_path"] == str(root / "WORKFLOW.md")
    assert {check["name"]: check["status"] for check in report["checks"]}["workflow-preflight"] == "pass"


def test_validate_command_reports_schema_and_semantic_failures(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "attmous-daedalus-issue-runner"
    root.mkdir()
    _write_issue_runner_contract(
        root,
        repo,
        overrides={
            "instance": {"name": "wrong-name", "engine-owner": "hermes"},
            "repository": {"local-path": str(tmp_path / "missing"), "slug": "attmous/daedalus"},
            "storage": None,
        },
    )

    tools = _tools()
    output = tools.execute_raw_args(f"validate --workflow-root {root} --json")
    payload = json.loads(output)
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["ok"] is False
    assert checks["schema"]["status"] == "fail"
    assert any(item["path"] == "storage" for item in checks["schema"]["items"])
    assert checks["instance-name"]["status"] == "fail"
    assert checks["repository-path"]["status"] == "fail"


def test_validate_command_text_keeps_actionable_failures(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "attmous-daedalus-issue-runner"
    root.mkdir()
    _write_issue_runner_contract(root, repo, overrides={"storage": None})

    tools = _tools()
    output = tools.execute_raw_args(f"validate --workflow-root {root}")

    assert "workflow contract valid=False" in output
    assert "FAIL schema" in output
    assert "storage" in output


def test_service_up_refuses_invalid_contract_before_install(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "attmous-daedalus-issue-runner"
    root.mkdir()
    _write_issue_runner_contract(root, repo, overrides={"storage": None})

    tools = _tools()
    with pytest.raises(tools.DaedalusCommandError) as exc:
        tools.service_up(
            workflow_root=root,
            project_key=root.name,
            instance_id=None,
            interval_seconds=30,
            service_mode="active",
        )

    assert "workflow contract validation failed" in str(exc.value)
    assert "schema" in str(exc.value)
