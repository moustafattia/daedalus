import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name, relative_path):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt():
    return load_module("daedalus_formatters_service_status_test", "formatters.py")


def _example_service_status():
    return {
        "service_name": "daedalus-active@yoyopod.service",
        "service_mode": "active",
        "installed": True,
        "enabled": True,
        "active": True,
        "unit_path": "/home/x/.config/systemd/user/daedalus-active@.service",
        "properties": {
            "ExecMainPID": "12345",
            "FragmentPath": "/home/x/.config/systemd/user/daedalus-active@.service",
        },
    }


def test_service_status_renders_identity_and_runtime():
    fmt = _fmt()
    out = fmt.format_service_status(_example_service_status(), use_color=False)
    assert "daedalus-active@yoyopod.service" in out
    assert "12345" in out
    # 3-state install row
    assert "installed" in out
    assert "enabled" in out
    assert "active" in out


def test_service_status_no_raw_python_bools():
    fmt = _fmt()
    out = fmt.format_service_status(_example_service_status(), use_color=False)
    assert " True" not in out
    assert " False" not in out


def test_service_status_handles_inactive_service():
    fmt = _fmt()
    inactive = _example_service_status()
    inactive["active"] = False
    inactive["properties"] = {}
    out = fmt.format_service_status(inactive, use_color=False)
    # Inactive should still render; pid empty becomes em-dash
    assert "—" in out
