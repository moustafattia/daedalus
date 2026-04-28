import importlib.util
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
    assert "run-active" in rendered
    assert "/.hermes/plugins/daedalus/runtime.py" in rendered


def test_render_template_unit_shadow_mode():
    tools = load_tools()
    rendered = tools._render_template_unit(mode="shadow")
    assert "Description=Daedalus shadow orchestrator" in rendered
    assert "%i" in rendered
    assert "run-shadow" in rendered


def test_template_unit_filename():
    tools = load_tools()
    assert tools._template_unit_filename("active") == "daedalus-active@.service"
    assert tools._template_unit_filename("shadow") == "daedalus-shadow@.service"


def test_instance_unit_name():
    tools = load_tools()
    assert tools._instance_unit_name("active", "yoyopod") == "daedalus-active@yoyopod.service"
    assert tools._instance_unit_name("shadow", "blueprint") == "daedalus-shadow@blueprint.service"


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
    (tmp_path / "yoyopod-relay-active.service").write_text("[Unit]\nDescription=old\n")
    (tmp_path / "yoyopod-relay-shadow.service").write_text("[Unit]\nDescription=old\n")

    captured_cmds = []
    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = tools.execute_raw_args(
        f"migrate-systemd --workflow-root {workflow_root}"
    )

    # Old unit files removed
    assert not (tmp_path / "yoyopod-relay-active.service").exists()
    assert not (tmp_path / "yoyopod-relay-shadow.service").exists()
    # New template units installed
    assert (tmp_path / "daedalus-active@.service").exists()
    assert (tmp_path / "daedalus-shadow@.service").exists()
    # systemctl daemon-reload was called
    assert any("daemon-reload" in cmd for cmd in captured_cmds)
