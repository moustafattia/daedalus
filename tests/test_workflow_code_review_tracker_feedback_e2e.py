"""End-to-end: change-delivery audit fanout posts tracker feedback."""
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeTracker:
    kind = "github"

    def __init__(self):
        self.calls = []

    def post_feedback(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "kind": self.kind, "issue_id": kwargs["issue_id"], "event": kwargs["event"]}


def test_end_to_end_audit_posts_tracker_feedback(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_change_delivery_workspace_e2e",
        "workflows/change_delivery/workspace.py",
    )
    audit_log_path = tmp_path / "audit.jsonl"
    tracker = FakeTracker()

    publisher = workspace_module._make_tracker_feedback_publisher(
        workflow_root=tmp_path,
        tracker_cfg={"kind": "github", "github_slug": "owner/repo"},
        repo_path=tmp_path,
        workflow_yaml={
            "tracker-feedback": {
                "enabled": True,
                "comment-mode": "append",
                "include": ["dispatch-implementation-turn", "merge-and-promote"],
                "state-updates": {"enabled": False},
            }
        },
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
        tracker_client=tracker,
    )
    audit = workspace_module._make_audit_fn(audit_log_path=audit_log_path, publisher=publisher)

    audit("dispatch-implementation-turn", "ok", model="gpt-5.3-codex-spark", sessionName="lane-329")
    audit("merge-and-promote", "merged", mergedPrNumber=382)

    audit_rows = [json.loads(line) for line in audit_log_path.read_text(encoding="utf-8").splitlines()]
    assert [row["action"] for row in audit_rows] == [
        "dispatch-implementation-turn",
        "merge-and-promote",
    ]
    assert [call["event"] for call in tracker.calls] == [
        "dispatch-implementation-turn",
        "merge-and-promote",
    ]
    assert tracker.calls[0]["issue_id"] == "329"
    assert tracker.calls[1]["metadata"]["mergedPrNumber"] == 382
