import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


TOOLS_PATH = Path(__file__).resolve().parents[1] / "daedalus" / "tools.py"


def load_tools():
    spec = importlib.util.spec_from_file_location("daedalus_tools_for_systemd_test", TOOLS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_template_unit_active_mode():
    tools = load_tools()
    rendered = tools._render_template_unit(mode="active")
    assert "[Unit]" in rendered
    assert "Description=Daedalus active orchestrator" in rendered
    # Must contain %i placeholder for instance name
    assert "%i" in rendered
    assert "service-loop" in rendered
    assert "--service-mode active" in rendered
    assert "/.hermes/plugins/daedalus/tools.py" in rendered


def test_render_template_unit_shadow_mode():
    tools = load_tools()
    rendered = tools._render_template_unit(mode="shadow")
    assert "Description=Daedalus shadow orchestrator" in rendered
    assert "%i" in rendered
    assert "service-loop" in rendered
    assert "--service-mode shadow" in rendered


def test_template_unit_filename():
    tools = load_tools()
    assert tools._template_unit_filename("active") == "daedalus-active@.service"
    assert tools._template_unit_filename("shadow") == "daedalus-shadow@.service"


def test_instance_unit_name():
    tools = load_tools()
    assert tools._instance_unit_name("active", "workflow") == "daedalus-active@workflow.service"
    assert tools._instance_unit_name("shadow", "blueprint") == "daedalus-shadow@blueprint.service"


def test_codex_app_server_service_name_and_unit(tmp_path):
    tools = load_tools()
    workflow_root = tmp_path / "attmous-daedalus-issue-runner"
    workflow_root.mkdir()

    assert (
        tools._codex_app_server_service_name(workflow_root=workflow_root)
        == "daedalus-codex-app-server@attmous-daedalus-issue-runner.service"
    )
    rendered = tools._render_codex_app_server_unit(listen="ws://127.0.0.1:4500", codex_command="codex")
    assert "Description=Daedalus Codex app-server" in rendered
    assert "ExecStart=/usr/bin/env codex app-server --listen ws://127.0.0.1:4500" in rendered
    assert "Restart=always" in rendered


def test_codex_app_server_unit_supports_websocket_auth_flags(tmp_path):
    tools = load_tools()
    token_file = tmp_path / "codex-token"
    shared_secret_file = tmp_path / "codex-shared-secret"

    token_unit = tools._render_codex_app_server_unit(
        listen="ws://127.0.0.1:4500",
        codex_command="codex",
        ws_token_file=str(token_file),
    )
    assert "--ws-auth capability-token --ws-token-file" in token_unit
    assert str(token_file) in token_unit

    signed_unit = tools._render_codex_app_server_unit(
        listen="ws://127.0.0.1:4500",
        codex_command="codex",
        ws_shared_secret_file=str(shared_secret_file),
        ws_issuer="daedalus",
        ws_audience="codex-app-server",
        ws_max_clock_skew_seconds=60,
    )
    assert "--ws-auth signed-bearer-token --ws-shared-secret-file" in signed_unit
    assert "--ws-issuer daedalus" in signed_unit
    assert "--ws-audience codex-app-server" in signed_unit
    assert "--ws-max-clock-skew-seconds 60" in signed_unit

    with pytest.raises(tools.DaedalusCommandError, match="absolute path"):
        tools._render_codex_app_server_unit(
            listen="ws://127.0.0.1:4500",
            codex_command="codex",
            ws_token_file="relative-token",
        )
    with pytest.raises(tools.DaedalusCommandError, match="mutually exclusive"):
        tools._render_codex_app_server_unit(
            listen="ws://127.0.0.1:4500",
            codex_command="codex",
            ws_token_file=str(token_file),
            ws_shared_secret_file=str(shared_secret_file),
        )


def test_codex_app_server_install_command_writes_user_unit(tmp_path, monkeypatch):
    tools = load_tools()
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setenv("DAEDALUS_SYSTEMD_USER_DIR", str(systemd_dir))
    workflow_root = tmp_path / "attmous-daedalus-issue-runner"
    workflow_root.mkdir()
    calls = []

    def fake_systemctl(*args):
        calls.append(args)
        return {
            "ok": True,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "command": ["systemctl", "--user", *args],
        }

    monkeypatch.setattr(tools, "_run_systemctl", fake_systemctl)

    result = tools.execute_raw_args(
        f"codex-app-server install --workflow-root {workflow_root} "
        "--listen ws://127.0.0.1:4500 --json"
    )

    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["service_name"] == "daedalus-codex-app-server@attmous-daedalus-issue-runner.service"
    unit_path = systemd_dir / payload["service_name"]
    assert unit_path.exists()
    assert "codex app-server --listen ws://127.0.0.1:4500" in unit_path.read_text(encoding="utf-8")
    assert ("daemon-reload",) in calls


def test_codex_app_server_status_includes_ready_probe(tmp_path, monkeypatch):
    tools = load_tools()
    workflow_root = tmp_path / "attmous-daedalus-issue-runner"
    workflow_root.mkdir()

    def fake_systemctl(*args):
        if args[0] == "is-active":
            stdout = "active"
        elif args[0] == "is-enabled":
            stdout = "enabled"
        elif args[0] == "show":
            stdout = "ActiveState=active\nSubState=running"
        else:
            stdout = ""
        return {
            "ok": True,
            "returncode": 0,
            "stdout": stdout,
            "stderr": "",
            "command": ["systemctl", "--user", *args],
        }

    monkeypatch.setattr(tools, "_run_systemctl", fake_systemctl)
    monkeypatch.setattr(
        tools,
        "_codex_app_server_readyz",
        lambda **kwargs: {"ok": True, "checked": True, **kwargs},
    )

    result = tools.codex_app_server_status(
        workflow_root=workflow_root,
        endpoint="ws://127.0.0.1:4500",
    )

    assert result["active"] == "active"
    assert result["enabled"] == "enabled"
    assert result["ready"]["ok"] is True
    assert result["ready"]["endpoint"] == "ws://127.0.0.1:4500"


def test_codex_app_server_restart_and_logs(tmp_path, monkeypatch):
    tools = load_tools()
    workflow_root = tmp_path / "attmous-daedalus-issue-runner"
    workflow_root.mkdir()
    systemctl_calls = []
    journal_calls = []

    def fake_systemctl(*args):
        systemctl_calls.append(args)
        stdout = ""
        if args[0] == "is-active":
            stdout = "active"
        elif args[0] == "is-enabled":
            stdout = "enabled"
        return {
            "ok": True,
            "returncode": 0,
            "stdout": stdout,
            "stderr": "",
            "command": ["systemctl", "--user", *args],
        }

    def fake_run(cmd, **kwargs):
        journal_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "line 1\nline 2\n", "")

    monkeypatch.setattr(tools, "_run_systemctl", fake_systemctl)
    monkeypatch.setattr(tools, "_codex_app_server_readyz", lambda **kwargs: {"ok": True, **kwargs})
    monkeypatch.setattr(tools.subprocess, "run", fake_run)

    restart = tools.codex_app_server_restart(workflow_root=workflow_root)
    logs = tools.codex_app_server_logs(workflow_root=workflow_root, lines=25)

    assert restart["ok"] is True
    assert ("restart", "daedalus-codex-app-server@attmous-daedalus-issue-runner.service") in systemctl_calls
    assert logs["stdout"] == "line 1\nline 2"
    assert journal_calls[0][:4] == [
        "journalctl",
        "--user",
        "-u",
        "daedalus-codex-app-server@attmous-daedalus-issue-runner.service",
    ]


def test_migrate_systemd_tolerant_of_missing_old_units(tmp_path, monkeypatch):
    """migrate-systemd should not fail when old units don't exist."""
    tools = load_tools()
    monkeypatch.setenv("DAEDALUS_SYSTEMD_USER_DIR", str(tmp_path))
    workflow_root = tmp_path / "wsroot"
    workflow_root.mkdir()

    # Stub systemctl so we don't actually invoke it
    captured_cmds = []
    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        if "daemon-reload" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 5, "", "Unit not loaded")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = tools.execute_raw_args(
        f"migrate-systemd --workflow-root {workflow_root}"
    )

    # Should succeed despite no old units, and install new template unit files
    assert "daedalus error" not in result.lower()
    assert (tmp_path / "daedalus-active@.service").exists()
    assert (tmp_path / "daedalus-shadow@.service").exists()


def test_migrate_systemd_removes_old_unit_files_when_present(tmp_path, monkeypatch):
    tools = load_tools()
    monkeypatch.setenv("DAEDALUS_SYSTEMD_USER_DIR", str(tmp_path))
    workflow_root = tmp_path / "wsroot"
    workflow_root.mkdir()

    # Seed old unit files
    (tmp_path / "wsroot-relay-active.service").write_text("[Unit]\nDescription=old\n")
    (tmp_path / "wsroot-relay-shadow.service").write_text("[Unit]\nDescription=old\n")

    captured_cmds = []
    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = tools.execute_raw_args(
        f"migrate-systemd --workflow-root {workflow_root}"
    )

    # Old unit files removed
    assert not (tmp_path / "wsroot-relay-active.service").exists()
    assert not (tmp_path / "wsroot-relay-shadow.service").exists()
    # New template units installed
    assert (tmp_path / "daedalus-active@.service").exists()
    assert (tmp_path / "daedalus-shadow@.service").exists()
    # systemctl daemon-reload was called
    assert any("daemon-reload" in cmd for cmd in captured_cmds)
