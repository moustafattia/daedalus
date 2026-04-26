"""Operator-attention transitions emit semantic audit events."""
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_emit_operator_attention_transition_on_entering_state():
    orch = load_module(
        "daedalus_workflow_code_review_orchestrator_test",
        "workflows/code_review/orchestrator.py",
    )
    captured = []

    def fake_audit(action, summary, **extra):
        captured.append({"action": action, "summary": summary, "extra": extra})

    orch.emit_operator_attention_transition(
        previous_state="under_review",
        new_state="operator_attention_required",
        reasons=["operator-attention-required:failure-retry-count=5"],
        audit_fn=fake_audit,
    )
    assert len(captured) == 1
    assert captured[0]["action"] == "operator-attention-transition"
    assert "failure-retry-count=5" in captured[0]["extra"]["reason"]


def test_emit_operator_attention_recovered_on_leaving_state():
    orch = load_module(
        "daedalus_workflow_code_review_orchestrator_test",
        "workflows/code_review/orchestrator.py",
    )
    captured = []

    def fake_audit(action, summary, **extra):
        captured.append({"action": action, "summary": summary})

    orch.emit_operator_attention_transition(
        previous_state="operator_attention_required",
        new_state="under_review",
        reasons=[],
        audit_fn=fake_audit,
    )
    assert len(captured) == 1
    assert captured[0]["action"] == "operator-attention-recovered"


def test_no_emit_when_state_unchanged():
    orch = load_module(
        "daedalus_workflow_code_review_orchestrator_test",
        "workflows/code_review/orchestrator.py",
    )
    captured = []

    def fake_audit(action, summary, **extra):
        captured.append(action)

    orch.emit_operator_attention_transition(
        previous_state="under_review",
        new_state="under_review",
        reasons=[],
        audit_fn=fake_audit,
    )
    orch.emit_operator_attention_transition(
        previous_state="operator_attention_required",
        new_state="operator_attention_required",
        reasons=["x"],
        audit_fn=fake_audit,
    )
    assert captured == []
