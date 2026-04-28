"""Per-command formatter for /daedalus doctor."""
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
    return load_module("daedalus_formatters_doctor_test", "formatters.py")


def _doctor_all_pass():
    return {
        "overall_status": "pass",
        "checks": [
            {"code": "missing_lease", "status": "pass", "summary": "Runtime lease present"},
            {"code": "shadow_compatible", "status": "pass", "summary": "Shadow decision matches legacy"},
            {"code": "active_execution_failures", "status": "pass", "summary": "No active execution failures"},
        ],
    }


def _doctor_one_fail():
    return {
        "overall_status": "fail",
        "checks": [
            {"code": "missing_lease", "status": "pass", "summary": "Runtime lease present"},
            {"code": "shadow_compatible", "status": "fail", "summary": "Shadow decision differs from legacy",
             "details": {"legacy": "publish_pr", "relay": "noop"}},
        ],
    }


def _doctor_with_failure_details():
    return {
        "overall_status": "fail",
        "checks": [
            {"code": "active_execution_failures", "status": "fail", "summary": "1 unresolved failure",
             "details": {"failures": [
                 {"failure_id": "f-123", "failure_class": "subprocess_error",
                  "recommended_action": "retry", "confidence": "medium",
                  "recovery_state": "queued", "urgency": "high", "failure_age_seconds": 320}
             ]}},
        ],
    }


def test_doctor_panel_includes_overall_status():
    fmt = _fmt()
    out = fmt.format_doctor(_doctor_all_pass(), use_color=False)
    assert "Daedalus doctor" in out or "doctor" in out.lower()
    # Overall status visible
    assert "pass" in out.lower()


def test_doctor_panel_renders_each_check_with_glyph():
    fmt = _fmt()
    out = fmt.format_doctor(_doctor_one_fail(), use_color=False)
    assert "missing_lease" in out
    assert "shadow_compatible" in out
    # At least one ✓ and one ✗
    assert "✓" in out
    assert "✗" in out


def test_doctor_failure_details_rendered_inline():
    fmt = _fmt()
    out = fmt.format_doctor(_doctor_with_failure_details(), use_color=False)
    assert "f-123" in out
    assert "subprocess_error" in out
    assert "retry" in out
    # Urgency must be preserved in inline details so operators can triage
    # critical vs warning failures from text output (P6.2 regression).
    assert "urgency=high" in out


def test_doctor_no_raw_python_bools():
    fmt = _fmt()
    out = fmt.format_doctor(_doctor_all_pass(), use_color=False)
    assert " True" not in out
    assert " False" not in out
