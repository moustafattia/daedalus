"""Per-command formatter for /daedalus active-gate-status."""
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name, relative_path):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt():
    return load_module("daedalus_formatters_active_gate_test", "formatters.py")


def _gate_open():
    return {
        "allowed": True,
        "reasons": [],
        "execution": {"active_execution_enabled": True},
        "primary_owner": "daedalus",
        "runtime": {"runtime_status": "running", "current_mode": "active"},
        "legacy_health": None,
    }


def _gate_blocked_active_disabled():
    return {
        "allowed": False,
        "reasons": ["active-execution-disabled"],
        "execution": {"active_execution_enabled": False},
        "primary_owner": "daedalus",
        "runtime": {"runtime_status": "running", "current_mode": "active"},
    }


def _gate_blocked_runtime_not_active():
    return {
        "allowed": False,
        "reasons": ["runtime-not-active-mode"],
        "execution": {"active_execution_enabled": True},
        "primary_owner": "daedalus",
        "runtime": {"runtime_status": "running", "current_mode": "shadow"},
    }


def test_open_gate_renders_all_pass_and_open_footer():
    fmt = _fmt()
    out = fmt.format_active_gate_status(_gate_open(), use_color=False)
    assert "Active execution gate" in out
    # All four conditions present
    assert "ownership" in out
    assert "active execution" in out
    assert "runtime mode" in out
    # Open status footer
    assert "open" in out.lower()


def test_blocked_active_disabled_shows_remediation_hint():
    fmt = _fmt()
    out = fmt.format_active_gate_status(_gate_blocked_active_disabled(), use_color=False)
    assert "BLOCKED" in out or "blocked" in out.lower()
    # Remediation hint
    assert "set-active-execution" in out


def test_blocked_runtime_not_active_shows_correct_failing_row():
    fmt = _fmt()
    out = fmt.format_active_gate_status(_gate_blocked_runtime_not_active(), use_color=False)
    assert "BLOCKED" in out or "blocked" in out.lower()
    # The runtime mode row should be the failing one
    lines = [l for l in out.split("\n") if "runtime mode" in l]
    assert lines
    assert "✗" in lines[0] or "x" in lines[0].lower()


def test_no_raw_python_bools_in_output():
    fmt = _fmt()
    out = fmt.format_active_gate_status(_gate_open(), use_color=False)
    assert " True" not in out
    assert " False" not in out
