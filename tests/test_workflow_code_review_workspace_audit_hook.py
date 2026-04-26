"""The audit() closure should invoke the comment publisher when one is wired in."""
import importlib.util
import json
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_audit_calls_publisher_when_hook_provided(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_test",
        "workflows/code_review/workspace.py",
    )

    audit_log_path = tmp_path / "audit.jsonl"
    captured_calls = []

    def fake_publisher(*, action, summary, extra):
        captured_calls.append({"action": action, "summary": summary, "extra": extra})

    audit_fn = workspace_module._make_audit_fn(
        audit_log_path=audit_log_path,
        publisher=fake_publisher,
    )

    audit_fn("merge-and-promote", "Merged", mergedPrNumber=382)

    # Audit log was still written
    lines = audit_log_path.read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action"] == "merge-and-promote"
    assert entry["mergedPrNumber"] == 382

    # Publisher was called
    assert len(captured_calls) == 1
    assert captured_calls[0]["action"] == "merge-and-promote"
    assert captured_calls[0]["extra"]["mergedPrNumber"] == 382


def test_audit_does_not_raise_if_publisher_throws(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_test",
        "workflows/code_review/workspace.py",
    )
    audit_log_path = tmp_path / "audit.jsonl"

    def bad_publisher(**kwargs):
        raise RuntimeError("publisher exploded")

    audit_fn = workspace_module._make_audit_fn(
        audit_log_path=audit_log_path,
        publisher=bad_publisher,
    )
    # Must not raise
    audit_fn("merge-and-promote", "Merged", mergedPrNumber=382)
    # Audit log still written
    assert audit_log_path.exists()


def test_audit_works_with_no_publisher(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_test",
        "workflows/code_review/workspace.py",
    )
    audit_log_path = tmp_path / "audit.jsonl"

    audit_fn = workspace_module._make_audit_fn(
        audit_log_path=audit_log_path,
        publisher=None,
    )
    audit_fn("dispatch-implementation-turn", "ok", model="x")
    assert audit_log_path.exists()
