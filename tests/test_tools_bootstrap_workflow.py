import importlib.util
import subprocess
from pathlib import Path

import pytest

from workflows.contract import load_workflow_contract_file, render_workflow_markdown


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tools():
    return load_module("daedalus_tools_bootstrap_workflow_test", "daedalus_cli.py")


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
        repo_slug=None,
        active_lane_label="active-lane",
        engine_owner="hermes",
        force=False,
    )

    expected_root = home / ".hermes" / "workflows" / "attmous-daedalus-change-delivery"
    contract_path = repo_root / "WORKFLOW.md"
    pointer_path = repo_root / ".hermes" / "daedalus" / "workflow-root"
    state_pointer_path = expected_root / "config" / "workflow-contract-path"
    cfg = load_workflow_contract_file(contract_path).config

    assert Path(result["workflow_root"]) == expected_root
    assert result["detected_repo_root"] == str(repo_root.resolve())
    assert result["repo_path"] == str(repo_root.resolve())
    assert result["repo_slug"] == "attmous/daedalus"
    assert result["remote_url"] == "git@github.com:attmous/daedalus.git"
    assert result["repo_pointer_path"] == str(pointer_path)
    assert result["next_edit_path"] == str(contract_path)
    assert result["next_command"] == "hermes daedalus service-up"
    assert result["git_branch"] == "daedalus/bootstrap-change-delivery"
    assert result["git_committed"] is True
    assert cfg["repository"]["local-path"] == str(repo_root.resolve())
    assert pointer_path.read_text(encoding="utf-8").strip() == str(expected_root)
    assert state_pointer_path.read_text(encoding="utf-8").strip() == str(contract_path.resolve())


def test_bootstrap_workflow_accepts_explicit_slug_for_non_github_remote(tmp_path):
    tools = _tools()
    repo_root = tmp_path / "repo"
    _init_git_repo(repo_root, remote_url="git@example.com:team/project.git")
    workflow_root = tmp_path / ".hermes" / "workflows" / "acme-widget-change-delivery"

    result = tools.bootstrap_workflow_root(
        repo_path=repo_root,
        workflow_name="change-delivery",
        workflow_root=workflow_root,
        repo_slug="acme/widget",
        active_lane_label="active-lane",
        engine_owner="hermes",
        force=False,
    )

    cfg = load_workflow_contract_file(repo_root / "WORKFLOW.md").config
    assert result["repo_slug"] == "acme/widget"
    assert cfg["repository"]["slug"] == "acme/widget"
    assert cfg["repository"]["github-slug"] == "acme/widget"
    assert cfg["repository"]["local-path"] == str(repo_root.resolve())
    assert (repo_root / ".hermes" / "daedalus" / "workflow-root").read_text(encoding="utf-8").strip() == str(workflow_root.resolve())


def test_bootstrap_issue_runner_infers_repo_slug_from_non_github_remote(tmp_path, monkeypatch):
    tools = _tools()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _init_git_repo(repo_root, remote_url="git@example.com:team/project.git")

    result = tools.bootstrap_workflow_root(
        repo_path=repo_root,
        workflow_name="issue-runner",
        workflow_root=None,
        repo_slug=None,
        active_lane_label="active-lane",
        engine_owner="hermes",
        force=False,
    )

    cfg = load_workflow_contract_file(repo_root / "WORKFLOW.md").config
    assert result["repo_slug"] == "team/project"
    assert Path(result["workflow_root"]) == home / ".hermes" / "workflows" / "team-project-issue-runner"
    assert cfg["workflow"] == "issue-runner"
    assert cfg["repository"]["slug"] == "team/project"
    assert "github-slug" not in cfg["repository"]


def test_bootstrap_issue_runner_recommends_service_up(tmp_path, monkeypatch):
    tools = _tools()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _init_git_repo(repo_root, remote_url="git@github.com:attmous/daedalus.git")

    result = tools.bootstrap_workflow_root(
        repo_path=repo_root,
        workflow_name="issue-runner",
        workflow_root=None,
        repo_slug=None,
        active_lane_label="active-lane",
        engine_owner="hermes",
        force=False,
    )

    assert result["next_command"] == "hermes daedalus service-up"
    assert result["git_branch"] == "daedalus/bootstrap-issue-runner"


def test_bootstrap_second_workflow_promotes_default_contract_without_clobbering(tmp_path, monkeypatch):
    tools = _tools()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _init_git_repo(repo_root, remote_url="git@github.com:attmous/daedalus.git")

    first = tools.bootstrap_workflow_root(
        repo_path=repo_root,
        workflow_name="change-delivery",
        workflow_root=None,
        repo_slug=None,
        active_lane_label="active-lane",
        engine_owner="hermes",
        force=False,
    )
    assert Path(first["contract_path"]) == repo_root / "WORKFLOW.md"

    second = tools.bootstrap_workflow_root(
        repo_path=repo_root,
        workflow_name="issue-runner",
        workflow_root=None,
        repo_slug=None,
        active_lane_label="active-lane",
        engine_owner="hermes",
        force=False,
    )

    default_path = repo_root / "WORKFLOW.md"
    change_delivery_path = repo_root / "WORKFLOW-change-delivery.md"
    issue_runner_path = repo_root / "WORKFLOW-issue-runner.md"
    issue_root = home / ".hermes" / "workflows" / "attmous-daedalus-issue-runner"

    assert not default_path.exists()
    assert change_delivery_path.exists()
    assert issue_runner_path.exists()
    assert load_workflow_contract_file(change_delivery_path).config["workflow"] == "change-delivery"
    assert load_workflow_contract_file(issue_runner_path).config["workflow"] == "issue-runner"
    assert second["contract_path"] == str(issue_runner_path)
    assert second["next_edit_path"] == str(issue_runner_path)
    assert second["renamed_contract_paths"] == [str(change_delivery_path)]
    assert second["renamed_contract_source_paths"] == [str(default_path)]
    assert second["git_branch"] == "daedalus/bootstrap-issue-runner"
    assert second["git_commit_message"] == "Add issue-runner workflow contract"
    assert (issue_root / "config" / "workflow-contract-path").read_text(encoding="utf-8").strip() == str(issue_runner_path.resolve())

    show = subprocess.run(
        ["git", "show", "--name-status", "--format=%s", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Add issue-runner workflow contract" in show
    assert (
        "D\tWORKFLOW.md" in show
        or "R100\tWORKFLOW.md\tWORKFLOW-change-delivery.md" in show
    )
    assert "A\tWORKFLOW-issue-runner.md" in show


def test_bootstrap_rejects_non_daedalus_workflow_md_without_changes(tmp_path, monkeypatch):
    tools = _tools()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _init_git_repo(repo_root, remote_url="git@github.com:attmous/daedalus.git")
    contract_path = repo_root / "WORKFLOW.md"
    contract_path.write_text("# Existing project workflow\n\nDo not overwrite me.\n", encoding="utf-8")

    with pytest.raises(tools.DaedalusCommandError) as exc:
        tools.bootstrap_workflow_root(
            repo_path=repo_root,
            workflow_name="issue-runner",
            workflow_root=None,
            repo_slug=None,
            active_lane_label="active-lane",
            engine_owner="hermes",
            force=False,
        )

    assert "not a Daedalus workflow contract" in str(exc.value)
    assert contract_path.read_text(encoding="utf-8") == "# Existing project workflow\n\nDo not overwrite me.\n"
    assert not (repo_root / "WORKFLOW-issue-runner.md").exists()


def test_bootstrap_promotion_refuses_existing_named_target_even_with_force(tmp_path, monkeypatch):
    tools = _tools()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _init_git_repo(repo_root, remote_url="git@github.com:attmous/daedalus.git")
    default_cfg = {"workflow": "change-delivery", "schema-version": 1}
    (repo_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(config=default_cfg, prompt_template="Original default."),
        encoding="utf-8",
    )
    target_path = repo_root / "WORKFLOW-change-delivery.md"
    target_text = render_workflow_markdown(config=default_cfg, prompt_template="Existing named target.")
    target_path.write_text(target_text, encoding="utf-8")

    with pytest.raises(tools.DaedalusCommandError) as exc:
        tools.bootstrap_workflow_root(
            repo_path=repo_root,
            workflow_name="issue-runner",
            workflow_root=None,
            repo_slug=None,
            active_lane_label="active-lane",
            engine_owner="hermes",
            force=True,
        )

    assert "will not overwrite repo-owned workflow contracts" in str(exc.value)
    assert (repo_root / "WORKFLOW.md").exists()
    assert target_path.read_text(encoding="utf-8") == target_text
    assert not (repo_root / "WORKFLOW-issue-runner.md").exists()


def test_bootstrap_workflow_requires_git_repo(tmp_path):
    tools = _tools()
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()

    with pytest.raises(tools.DaedalusCommandError) as exc:
        tools.bootstrap_workflow_root(
            repo_path=non_repo,
            workflow_name="change-delivery",
            workflow_root=None,
            repo_slug=None,
            active_lane_label="active-lane",
            engine_owner="hermes",
            force=False,
        )

    assert "git repository" in str(exc.value)
