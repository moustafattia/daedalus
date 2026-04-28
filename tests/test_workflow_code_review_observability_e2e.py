"""End-to-end: enabled → audit fires → publisher runs → state updated.

Mocks subprocess.run so no live GitHub calls. Verifies the wiring works
across all the modules added in Task 1.1–1.9.
"""
import importlib.util
import json
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_end_to_end_audit_creates_then_edits_bot_comment(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_code_review_workspace_e2e",
        "workflows/code_review/workspace.py",
    )
    state_dir = tmp_path / "lane-comments"
    audit_log_path = tmp_path / "audit.jsonl"

    fake_run_responses = [
        # First call: gh issue comment → returns URL with comment id 99
        mock.Mock(returncode=0, stdout="https://github.com/owner/repo/issues/329#issuecomment-99\n", stderr=""),
        # Second call: gh api PATCH (no stdout, success)
        mock.Mock(returncode=0, stdout="", stderr=""),
    ]
    fake_run_calls = []

    def fake_run(argv, **kwargs):
        fake_run_calls.append(argv)
        return fake_run_responses.pop(0)

    publisher = workspace_module._make_comment_publisher(
        workflow_root=tmp_path,
        repo_slug="owner/repo",
        workflow_yaml={
            "observability": {
                "github-comments": {
                    "enabled": True,
                    "include-events": [],   # empty = include all
                    }
            }
        },
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
        run_fn=fake_run,
    )
    audit = workspace_module._make_audit_fn(audit_log_path=audit_log_path, publisher=publisher)

    # The state_dir the publisher writes into is workflow_root/runtime/state/lane-comments.
    # Our tmp_path is workflow_root.
    expected_state_dir = tmp_path / "runtime" / "state" / "lane-comments"

    audit("dispatch-implementation-turn", "ok", model="gpt-5.3-codex-spark", sessionName="lane-329")
    assert len(fake_run_calls) == 1
    assert fake_run_calls[0][0] == "gh"
    assert "issue" in fake_run_calls[0]
    state_path = expected_state_dir / "329.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["comment_id"] == "99"

    audit("merge-and-promote", "merged", mergedPrNumber=382)
    assert len(fake_run_calls) == 2
    assert fake_run_calls[1][0] == "gh"
    assert fake_run_calls[1][1] == "api"
