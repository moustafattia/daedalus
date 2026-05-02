import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from workflows.contract import WORKFLOW_POLICY_KEY, load_workflow_contract_file


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tools():
    return load_module("daedalus_tools_scaffold_workflow_test", "daedalus_cli.py")


def _init_git_repo(path: Path, *, remote_url: str = "git@github.com:attmous/daedalus.git") -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=path, check=True, capture_output=True, text=True)


def test_scaffold_workflow_writes_config_and_layout(tmp_path):
    tools = _tools()
    root = tmp_path / "attmous-daedalus-change-delivery"
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    result = tools.scaffold_workflow_root(
        workflow_root=root,
        workflow_name="change-delivery",
        repo_path=repo,
        repo_slug="attmous/daedalus",
        active_lane_label="ready-for-daedalus",
        engine_owner="hermes",
        force=False,
    )

    contract_path = repo / "WORKFLOW.md"
    cfg = load_workflow_contract_file(contract_path).config

    assert result["contract_path"] == str(contract_path)
    assert cfg["instance"]["name"] == "attmous-daedalus-change-delivery"
    assert cfg["instance"]["engine-owner"] == "hermes"
    assert cfg["repository"]["slug"] == "attmous/daedalus"
    assert "github-slug" not in cfg["repository"]
    assert cfg["tracker"]["kind"] == "github"
    assert cfg["tracker"]["github_slug"] == "attmous/daedalus"
    assert cfg["code-host"] == {"kind": "github", "github_slug": "attmous/daedalus"}
    assert cfg["repository"]["active-lane-label"] == "ready-for-daedalus"
    assert cfg["triggers"]["lane-selector"]["label"] == "ready-for-daedalus"
    assert cfg["repository"]["local-path"] == str(repo.resolve())
    assert cfg[WORKFLOW_POLICY_KEY]
    assert (root / "memory").is_dir()
    assert (root / "state" / "sessions").is_dir()
    assert (root / "runtime" / "state" / "daedalus").is_dir()
    assert (root / "runtime" / "memory").is_dir()
    assert (root / "runtime" / "logs").is_dir()
    assert (root / "config" / "workflow-contract-path").exists()


def test_scaffold_workflow_refuses_to_overwrite_without_force(tmp_path):
    tools = _tools()
    root = tmp_path / "attmous-daedalus-change-delivery"
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    contract_path = repo / "WORKFLOW.md"
    contract_path.write_text("---\nworkflow: change-delivery\nschema-version: 1\n---\n", encoding="utf-8")

    try:
        tools.scaffold_workflow_root(
            workflow_root=root,
            workflow_name="change-delivery",
            repo_path=repo,
            repo_slug="attmous/daedalus",
            active_lane_label="active-lane",
            engine_owner="hermes",
            force=False,
        )
    except tools.DaedalusCommandError as exc:
        assert "refusing to overwrite existing workflow contract" in str(exc)
        return
    raise AssertionError("expected DaedalusCommandError when overwriting without --force")


def test_scaffold_workflow_force_replaces_existing_config(tmp_path):
    tools = _tools()
    root = tmp_path / "attmous-daedalus-change-delivery"
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    contract_path = repo / "WORKFLOW.md"
    contract_path.write_text("---\nworkflow: old\nschema-version: 1\n---\n", encoding="utf-8")

    result = tools.scaffold_workflow_root(
        workflow_root=root,
        workflow_name="change-delivery",
        repo_path=repo,
        repo_slug="attmous/daedalus",
        active_lane_label="active-lane",
        engine_owner="openclaw",
        force=True,
    )

    cfg = load_workflow_contract_file(Path(result["contract_path"])).config
    assert cfg["workflow"] == "change-delivery"
    assert cfg["instance"]["name"] == "attmous-daedalus-change-delivery"
    assert cfg["instance"]["engine-owner"] == "openclaw"
    assert cfg["repository"]["local-path"] == str(repo.resolve())
    assert (repo / "WORKFLOW-old.md").exists()


def test_scaffold_workflow_requires_owner_repo_workflow_root_name(tmp_path):
    tools = _tools()
    root = tmp_path / "daedalus"
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    with pytest.raises(tools.DaedalusCommandError) as exc:
        tools.scaffold_workflow_root(
            workflow_root=root,
            workflow_name="change-delivery",
            repo_path=repo,
            repo_slug="attmous/daedalus",
            active_lane_label="active-lane",
            engine_owner="hermes",
            force=False,
        )

    assert "<owner>-<repo>-<workflow-type>" in str(exc.value)


def test_scaffold_issue_runner_seeds_sample_tracker_file(tmp_path):
    tools = _tools()
    root = tmp_path / "attmous-daedalus-issue-runner"
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    result = tools.scaffold_workflow_root(
        workflow_root=root,
        workflow_name="issue-runner",
        repo_path=repo,
        repo_slug="attmous/daedalus",
        active_lane_label="ignored-for-issue-runner",
        engine_owner="hermes",
        force=False,
    )

    cfg = load_workflow_contract_file(repo / "WORKFLOW.md").config
    issues_path = root / "config" / "issues.json"

    assert result["workflow"] == "issue-runner"
    assert cfg["workflow"] == "issue-runner"
    assert cfg["repository"]["slug"] == "attmous/daedalus"
    assert "github-slug" not in cfg["repository"]
    assert "triggers" not in cfg
    assert issues_path.exists()
    payload = json.loads(issues_path.read_text(encoding="utf-8"))
    assert payload["issues"][0]["id"] == "ISSUE-1"
