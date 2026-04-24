"""Tests for the plugin-side entrypoint ``adapters.yoyopod_core.__main__``.

The entrypoint lets external callers drop the workspace-side
``scripts/yoyopod_workflow.py`` wrapper by invoking the plugin directly.
"""
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _minimal_config(tmp_path: Path) -> dict:
    return {
        "repoPath": str(tmp_path / "repo"),
        "cronJobsPath": str(tmp_path / "cron-jobs.json"),
        "ledgerPath": str(tmp_path / "ledger.json"),
        "healthPath": str(tmp_path / "health.json"),
        "auditLogPath": str(tmp_path / "audit.jsonl"),
        "engineOwner": "hermes",
        "activeLaneLabel": "active-lane",
        "coreJobNames": [],
        "hermesJobNames": [],
        "sessionPolicy": {"codexModel": "gpt-5.3-codex-spark/high"},
        "reviewPolicy": {"claudeModel": "claude-sonnet-4-6"},
        "agentLabels": {"internalReviewerAgent": "Internal_Reviewer_Agent"},
    }


def test_resolve_workflow_root_explicit_flag_wins(tmp_path, monkeypatch):
    main_module = load_module("hermes_relay_yoyopod_core_main_test", "adapters/yoyopod_core/__main__.py")
    monkeypatch.delenv("YOYOPOD_WORKFLOW_ROOT", raising=False)
    root, remaining = main_module.resolve_workflow_root([
        "--workflow-root", str(tmp_path / "a"), "status",
    ])
    assert root == (tmp_path / "a").resolve()
    assert remaining == ["status"]


def test_resolve_workflow_root_equals_form(tmp_path, monkeypatch):
    main_module = load_module("hermes_relay_yoyopod_core_main_test", "adapters/yoyopod_core/__main__.py")
    monkeypatch.delenv("YOYOPOD_WORKFLOW_ROOT", raising=False)
    root, remaining = main_module.resolve_workflow_root([
        f"--workflow-root={tmp_path / 'b'}", "tick", "--json",
    ])
    assert root == (tmp_path / "b").resolve()
    assert remaining == ["tick", "--json"]


def test_resolve_workflow_root_env_fallback(tmp_path, monkeypatch):
    main_module = load_module("hermes_relay_yoyopod_core_main_test", "adapters/yoyopod_core/__main__.py")
    monkeypatch.setenv("YOYOPOD_WORKFLOW_ROOT", str(tmp_path / "env-root"))
    root, remaining = main_module.resolve_workflow_root(["status"])
    assert root == (tmp_path / "env-root").resolve()
    assert remaining == ["status"]


def test_resolve_workflow_root_requires_value(tmp_path, monkeypatch):
    main_module = load_module("hermes_relay_yoyopod_core_main_test", "adapters/yoyopod_core/__main__.py")
    monkeypatch.delenv("YOYOPOD_WORKFLOW_ROOT", raising=False)
    import pytest

    with pytest.raises(SystemExit):
        main_module.resolve_workflow_root(["--workflow-root"])


def test_main_calls_cli_main_with_workspace(tmp_path, monkeypatch):
    """The entrypoint wires ``workflow_root → workspace`` and forwards argv to cli.main."""
    workflow_root = tmp_path / "workflow"
    config_dir = workflow_root / "config"
    config_dir.mkdir(parents=True)
    config = _minimal_config(tmp_path)
    (config_dir / "yoyopod-workflow.json").write_text(json.dumps(config), encoding="utf-8")

    main_module = load_module("hermes_relay_yoyopod_core_main_test", "adapters/yoyopod_core/__main__.py")

    # Patch cli.main so we don't actually dispatch a real command — we just
    # want to verify the workspace was built and argv was forwarded.
    import sys as _sys
    plugin_root = str(REPO_ROOT)
    if plugin_root not in _sys.path:
        _sys.path.insert(0, plugin_root)
    from adapters.yoyopod_core import cli as cli_module

    received: dict = {}

    def _fake_cli_main(ws, argv=None):
        received["ws"] = ws
        received["argv"] = argv
        return 0

    monkeypatch.setattr(cli_module, "main", _fake_cli_main)

    exit_code = main_module.main([
        "--workflow-root", str(workflow_root),
        "status",
        "--json",
    ])
    assert exit_code == 0
    ws = received["ws"]
    assert ws.WORKSPACE == workflow_root.resolve()
    assert ws.REPO_PATH == Path(config["repoPath"])
    # --workflow-root should be stripped; command + flags should pass through.
    assert received["argv"] == ["status", "--json"]


def test_main_subprocess_calledprocesserror_returns_nonzero(tmp_path, monkeypatch):
    """If cli.main raises CalledProcessError, the entrypoint prints + returns its exit code."""
    workflow_root = tmp_path / "workflow"
    config_dir = workflow_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "yoyopod-workflow.json").write_text(json.dumps(_minimal_config(tmp_path)), encoding="utf-8")

    main_module = load_module("hermes_relay_yoyopod_core_main_test", "adapters/yoyopod_core/__main__.py")

    import subprocess as _sp
    import sys as _sys
    plugin_root = str(REPO_ROOT)
    if plugin_root not in _sys.path:
        _sys.path.insert(0, plugin_root)
    from adapters.yoyopod_core import cli as cli_module

    def _raise(ws, argv=None):
        raise _sp.CalledProcessError(returncode=7, cmd=["gh", "issue", "list"], stderr="boom")

    monkeypatch.setattr(cli_module, "main", _raise)

    code = main_module.main(["--workflow-root", str(workflow_root), "status"])
    assert code == 7
