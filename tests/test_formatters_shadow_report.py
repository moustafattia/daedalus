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
    return load_module("daedalus_formatters_shadow_test", "formatters.py")


def _example_shadow_report():
    return {
        "runtime": {"runtime_status": "running", "current_mode": "active",
                    "active_orchestrator_instance_id": "daedalus-active-yoyopod",
                    "latest_heartbeat_at": "2026-04-26T22:43:01Z"},
        "heartbeat": {"heartbeat_age_seconds": 17, "expires_at": "2026-04-26T22:44:00Z"},
        "service": {"service_mode": "active", "installed": True, "enabled": True, "active": True},
        "owner_summary": {"primary_owner": "daedalus", "active_execution_enabled": True, "gate_allowed": True},
        "active_lane": {"issue_number": 329, "lane_id": "lane-329",
                         "workflow_state": "under_review", "review_state": "pass",
                         "merge_state": "pending"},
        "legacy": {"next_action_type": "publish_pr", "reason": "head-clean"},
        "relay": {"derived_action_type": "publish_pr", "reason": "head-clean", "compatible": True},
        "warnings": [],
        "recent_shadow_actions": [],
        "recent_failures": [],
    }


def test_shadow_report_renders_runtime_and_lane_sections():
    fmt = _fmt()
    out = fmt.format_shadow_report(_example_shadow_report(), use_color=False)
    assert "Daedalus shadow-report" in out or "shadow" in out.lower()
    # Runtime + active-lane info present
    assert "running" in out
    assert "329" in out
    assert "publish_pr" in out


def test_shadow_report_warnings_appear_when_present():
    fmt = _fmt()
    rep = _example_shadow_report()
    rep["warnings"] = ["heartbeat-stale", "lease-near-expiry"]
    out = fmt.format_shadow_report(rep, use_color=False)
    assert "heartbeat-stale" in out
    assert "lease-near-expiry" in out


def test_shadow_report_no_warnings_section_when_empty():
    fmt = _fmt()
    out = fmt.format_shadow_report(_example_shadow_report(), use_color=False)
    assert "warnings" not in out.lower() or "(no warnings)" in out.lower()


def test_shadow_report_no_raw_python_bools():
    fmt = _fmt()
    out = fmt.format_shadow_report(_example_shadow_report(), use_color=False)
    assert " True" not in out
    assert " False" not in out


def test_shadow_report_renders_all_recent_actions_beyond_legacy_5_cap():
    """P6.1: text rendering must respect upstream --recent-actions-limit
    (already applied in build_shadow_report). Previously a hard-coded [:5]
    in the formatter silently truncated text output for N > 5."""
    fmt = _fmt()
    rep = _example_shadow_report()
    rep["recent_shadow_actions"] = [
        {"requested_at": f"2026-04-26T22:00:{i:02d}Z",
         "issue_number": 100 + i,
         "action_type": "publish_pr",
         "status": "ok"}
        for i in range(10)
    ]
    rep["recent_failures"] = [
        {"detected_at": f"2026-04-26T22:01:{i:02d}Z",
         "issue_number": 200 + i,
         "failure_class": "subprocess_error",
         "recovery_state": "queued"}
        for i in range(10)
    ]
    out = fmt.format_shadow_report(rep, use_color=False)
    # All 10 actions present (issue numbers 100..109)
    for i in range(10):
        assert f"#{100 + i}" in out, f"missing action issue #{100 + i} in output"
    # All 10 failures present (issue numbers 200..209)
    for i in range(10):
        assert f"#{200 + i}" in out, f"missing failure issue #{200 + i} in output"
