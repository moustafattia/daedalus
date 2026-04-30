import importlib.util
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
    return load_module("daedalus_tools_scaffold_workflow_test", "tools.py")


def test_scaffold_workflow_writes_config_and_layout(tmp_path):
    tools = _tools()
    root = tmp_path / "attmous-daedalus-change-delivery"

    result = tools.scaffold_workflow_root(
        workflow_root=root,
        workflow_name="change-delivery",
        repo_path=None,
        github_slug="attmous/daedalus",
        active_lane_label="ready-for-daedalus",
        engine_owner="hermes",
        force=False,
    )

    contract_path = root / "WORKFLOW.md"
    cfg = load_workflow_contract_file(contract_path).config

    assert result["contract_path"] == str(contract_path)
    assert cfg["instance"]["name"] == "attmous-daedalus-change-delivery"
    assert cfg["instance"]["engine-owner"] == "hermes"
    assert cfg["repository"]["github-slug"] == "attmous/daedalus"
    assert cfg["repository"]["active-lane-label"] == "ready-for-daedalus"
    assert cfg["triggers"]["lane-selector"]["label"] == "ready-for-daedalus"
    assert cfg["repository"]["local-path"] == str(root / "workspace" / "repo")
    assert cfg[WORKFLOW_POLICY_KEY]
    assert (root / "memory").is_dir()
    assert (root / "state" / "sessions").is_dir()
    assert (root / "runtime" / "state" / "daedalus").is_dir()
    assert (root / "runtime" / "memory").is_dir()
    assert (root / "runtime" / "logs").is_dir()


def test_scaffold_workflow_refuses_to_overwrite_without_force(tmp_path):
    tools = _tools()
    root = tmp_path / "attmous-daedalus-change-delivery"
    contract_path = root / "WORKFLOW.md"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text("---\nworkflow: change-delivery\nschema-version: 1\n---\n", encoding="utf-8")

    try:
        tools.scaffold_workflow_root(
            workflow_root=root,
            workflow_name="change-delivery",
            repo_path=None,
            github_slug="attmous/daedalus",
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
    contract_path = root / "WORKFLOW.md"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text("---\nworkflow: old\nschema-version: 1\n---\n", encoding="utf-8")

    tools.scaffold_workflow_root(
        workflow_root=root,
        workflow_name="change-delivery",
        repo_path=root / "workspace" / "checkout",
        github_slug="attmous/daedalus",
        active_lane_label="active-lane",
        engine_owner="openclaw",
        force=True,
    )

    cfg = load_workflow_contract_file(contract_path).config
    assert cfg["workflow"] == "change-delivery"
    assert cfg["instance"]["name"] == "attmous-daedalus-change-delivery"
    assert cfg["instance"]["engine-owner"] == "openclaw"
    assert cfg["repository"]["local-path"] == str(root / "workspace" / "checkout")


def test_scaffold_workflow_force_retires_legacy_yaml_when_present(tmp_path):
    tools = _tools()
    root = tmp_path / "attmous-daedalus-change-delivery"
    legacy_path = root / "config" / "workflow.yaml"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text("workflow: change-delivery\nschema-version: 1\n", encoding="utf-8")

    tools.scaffold_workflow_root(
        workflow_root=root,
        workflow_name="change-delivery",
        repo_path=None,
        github_slug="attmous/daedalus",
        active_lane_label="active-lane",
        engine_owner="hermes",
        force=True,
    )

    assert (root / "WORKFLOW.md").exists()
    assert not legacy_path.exists()


def test_scaffold_workflow_requires_owner_repo_workflow_root_name(tmp_path):
    tools = _tools()
    root = tmp_path / "daedalus"

    with pytest.raises(tools.DaedalusCommandError) as exc:
        tools.scaffold_workflow_root(
            workflow_root=root,
            workflow_name="change-delivery",
            repo_path=None,
            github_slug="attmous/daedalus",
            active_lane_label="active-lane",
            engine_owner="hermes",
            force=False,
        )

    assert "<owner>-<repo>-<workflow-type>" in str(exc.value)
