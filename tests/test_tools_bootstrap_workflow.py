import importlib.util
import subprocess
from pathlib import Path

import pytest

from workflows.contract import load_workflow_contract_file


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tools():
    return load_module("daedalus_tools_bootstrap_workflow_test", "tools.py")


def _init_git_repo(path: Path, *, remote_url: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=path, check=True, capture_output=True, text=True)


def test_bootstrap_workflow_infers_repo_root_slug_and_default_root(tmp_path, monkeypatch):
    tools = _tools()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _init_git_repo(repo_root, remote_url="git@github.com:attmous/daedalus.git")
    nested = repo_root / "src" / "pkg"
    nested.mkdir(parents=True)

    result = tools.bootstrap_workflow_root(
        repo_path=nested,
        workflow_name="change-delivery",
        workflow_root=None,
        github_slug=None,
        active_lane_label="active-lane",
        engine_owner="hermes",
        force=False,
    )

    expected_root = home / ".hermes" / "workflows" / "attmous-daedalus-change-delivery"
    contract_path = expected_root / "WORKFLOW.md"
    pointer_path = repo_root / ".hermes" / "daedalus" / "workflow-root"
    cfg = load_workflow_contract_file(contract_path).config

    assert Path(result["workflow_root"]) == expected_root
    assert result["detected_repo_root"] == str(repo_root.resolve())
    assert result["repo_path"] == str(repo_root.resolve())
    assert result["github_slug"] == "attmous/daedalus"
    assert result["remote_url"] == "git@github.com:attmous/daedalus.git"
    assert result["repo_pointer_path"] == str(pointer_path)
    assert result["next_edit_path"] == str(contract_path)
    assert result["next_command"] == "hermes daedalus service-up"
    assert cfg["repository"]["local-path"] == str(repo_root.resolve())
    assert pointer_path.read_text(encoding="utf-8").strip() == str(expected_root)


def test_bootstrap_workflow_accepts_explicit_slug_for_non_github_remote(tmp_path):
    tools = _tools()
    repo_root = tmp_path / "repo"
    _init_git_repo(repo_root, remote_url="git@example.com:team/project.git")
    workflow_root = tmp_path / ".hermes" / "workflows" / "acme-widget-change-delivery"

    result = tools.bootstrap_workflow_root(
        repo_path=repo_root,
        workflow_name="change-delivery",
        workflow_root=workflow_root,
        github_slug="acme/widget",
        active_lane_label="active-lane",
        engine_owner="hermes",
        force=False,
    )

    cfg = load_workflow_contract_file(workflow_root / "WORKFLOW.md").config
    assert result["github_slug"] == "acme/widget"
    assert cfg["repository"]["github-slug"] == "acme/widget"
    assert cfg["repository"]["local-path"] == str(repo_root.resolve())
    assert (repo_root / ".hermes" / "daedalus" / "workflow-root").read_text(encoding="utf-8").strip() == str(workflow_root.resolve())


def test_bootstrap_workflow_requires_git_repo(tmp_path):
    tools = _tools()
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()

    with pytest.raises(tools.DaedalusCommandError) as exc:
        tools.bootstrap_workflow_root(
            repo_path=non_repo,
            workflow_name="change-delivery",
            workflow_root=None,
            github_slug=None,
            active_lane_label="active-lane",
            engine_owner="hermes",
            force=False,
        )

    assert "git repository" in str(exc.value)
