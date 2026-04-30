import importlib.util
from pathlib import Path

import pytest


TOOLS_PATH = Path(__file__).resolve().parents[1] / "daedalus" / "daedalus_cli.py"


def load_tools():
    spec = importlib.util.spec_from_file_location("daedalus_tools_for_workflow_test", TOOLS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_execute_workflow_command_lists_workflows_with_no_args(tmp_path, monkeypatch):
    tools = load_tools()
    workflow_root = tmp_path
    (workflow_root / "config").mkdir()
    (workflow_root / "config" / "workflow.yaml").write_text(
        "workflow:\n  name: change-delivery\n  schema-version: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DAEDALUS_WORKFLOW_ROOT", str(workflow_root))

    result = tools.execute_workflow_command("")
    assert "available workflows" in result.lower()
    assert "change-delivery" in result
    assert "issue-runner" in result


def test_execute_workflow_command_routes_to_workflow_cli(tmp_path, monkeypatch):
    tools = load_tools()
    workflow_root = tmp_path
    (workflow_root / "config").mkdir()
    (workflow_root / "config" / "workflow.yaml").write_text(
        "workflow:\n  name: change-delivery\n  schema-version: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DAEDALUS_WORKFLOW_ROOT", str(workflow_root))

    captured = {}

    def fake_run_cli(workflow_root_arg, argv, *, require_workflow=None):
        captured["workflow_root"] = workflow_root_arg
        captured["argv"] = argv
        captured["require_workflow"] = require_workflow
        return 0

    import workflows
    monkeypatch.setattr(workflows, "run_cli", fake_run_cli)

    result = tools.execute_workflow_command("change-delivery status --json")

    assert captured["require_workflow"] == "change-delivery"
    assert captured["argv"] == ["status", "--json"]
    assert isinstance(result, str)


def test_execute_workflow_command_rejects_unknown_workflow_name(tmp_path, monkeypatch):
    tools = load_tools()
    workflow_root = tmp_path
    (workflow_root / "config").mkdir()
    (workflow_root / "config" / "workflow.yaml").write_text(
        "workflow:\n  name: change-delivery\n  schema-version: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DAEDALUS_WORKFLOW_ROOT", str(workflow_root))

    result = tools.execute_workflow_command("nonexistent-workflow status")
    assert "daedalus error" in result.lower() or "unknown workflow" in result.lower()
