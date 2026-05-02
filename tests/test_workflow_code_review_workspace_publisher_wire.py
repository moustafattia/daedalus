"""change-delivery audit fanout should publish through shared tracker feedback."""
import importlib.util
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
        return {
            "ok": True,
            "kind": self.kind,
            "issue_id": kwargs["issue_id"],
            "event": kwargs["event"],
            "state": kwargs.get("state"),
        }


def test_tracker_feedback_publisher_skips_when_disabled(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_change_delivery_workspace_publisher_wire_test",
        "workflows/change_delivery/workspace.py",
    )
    tracker = FakeTracker()

    publisher = workspace_module._make_tracker_feedback_publisher(
        workflow_root=tmp_path,
        tracker_cfg={"kind": "github", "github_slug": "owner/repo"},
        repo_path=tmp_path,
        workflow_yaml={"tracker-feedback": {"enabled": False}},
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
        tracker_client=tracker,
    )

    publisher(action="merge-and-promote", summary="Merged PR", extra={"mergedPrNumber": 1})

    assert tracker.calls == []


def test_tracker_feedback_publisher_posts_included_audit_event(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_change_delivery_workspace_publisher_wire_test",
        "workflows/change_delivery/workspace.py",
    )
    tracker = FakeTracker()

    publisher = workspace_module._make_tracker_feedback_publisher(
        workflow_root=tmp_path,
        tracker_cfg={"kind": "github", "github_slug": "owner/repo"},
        repo_path=tmp_path,
        workflow_yaml={
            "tracker-feedback": {
                "enabled": True,
                "comment-mode": "append",
                "include": ["merge-and-promote"],
                "state-updates": {"enabled": False},
            }
        },
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
        tracker_client=tracker,
    )

    publisher(action="merge-and-promote", summary="Merged PR", extra={"mergedPrNumber": 382})

    assert len(tracker.calls) == 1
    call = tracker.calls[0]
    assert call["issue_id"] == "329"
    assert call["event"] == "merge-and-promote"
    assert call["summary"] == "Merged PR"
    assert call["state"] is None
    assert call["metadata"]["workflow"] == "change-delivery"
    assert call["metadata"]["workflow_state"] == "under_review"
    assert call["metadata"]["mergedPrNumber"] == 382


def test_tracker_feedback_publisher_builds_from_tracker_config(tmp_path, monkeypatch):
    workspace_module = load_module(
        "daedalus_workflow_change_delivery_workspace_publisher_wire_test",
        "workflows/change_delivery/workspace.py",
    )
    tracker = FakeTracker()
    captured = {}

    def fake_build_tracker_client(**kwargs):
        captured.update(kwargs)
        return tracker

    monkeypatch.setattr(workspace_module, "build_tracker_client", fake_build_tracker_client)

    publisher = workspace_module._make_tracker_feedback_publisher(
        workflow_root=tmp_path,
        tracker_cfg={"kind": "github", "github_slug": "tracker-owner/tracker-repo"},
        repo_path=tmp_path / "checkout",
        workflow_yaml={"tracker-feedback": {"enabled": True, "include": ["merge-and-promote"]}},
        get_active_issue_number=lambda: 329,
        get_workflow_state=lambda: "under_review",
        get_is_operator_attention=lambda: False,
    )

    publisher(action="merge-and-promote", summary="Merged PR", extra={})

    assert captured["tracker_cfg"] == {"kind": "github", "github_slug": "tracker-owner/tracker-repo"}
    assert captured["repo_path"] == tmp_path / "checkout"
    assert tracker.calls[0]["issue_id"] == "329"


def test_tracker_feedback_publisher_skips_when_no_active_issue(tmp_path):
    workspace_module = load_module(
        "daedalus_workflow_change_delivery_workspace_publisher_wire_test",
        "workflows/change_delivery/workspace.py",
    )
    tracker = FakeTracker()

    publisher = workspace_module._make_tracker_feedback_publisher(
        workflow_root=tmp_path,
        tracker_cfg={"kind": "github", "github_slug": "owner/repo"},
        repo_path=tmp_path,
        workflow_yaml={"tracker-feedback": {"enabled": True, "include": ["merge-and-promote"]}},
        get_active_issue_number=lambda: None,
        get_workflow_state=lambda: "no_active_lane",
        get_is_operator_attention=lambda: False,
        tracker_client=tracker,
    )

    publisher(action="merge-and-promote", summary="x", extra={"mergedPrNumber": 1})

    assert tracker.calls == []
