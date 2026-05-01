import json
import subprocess


def test_local_json_feedback_appends_comment_and_updates_state(tmp_path):
    from trackers.local_json import LocalJsonTrackerClient

    issues_path = tmp_path / "config" / "issues.json"
    issues_path.parent.mkdir()
    issues_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "id": "ISSUE-1",
                        "identifier": "ISSUE-1",
                        "title": "Demo",
                        "state": "todo",
                        "comments": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    client = LocalJsonTrackerClient(
        workflow_root=tmp_path,
        tracker_cfg={"kind": "local-json", "path": "config/issues.json"},
    )

    result = client.post_feedback(
        issue_id="ISSUE-1",
        event="issue.running",
        body="### Daedalus update\nRuntime started.\n",
        summary="Runtime started.",
        state="in-progress",
        metadata={"run_id": "run-1"},
    )

    assert result["ok"] is True
    assert result["state"] == "in-progress"
    payload = json.loads(issues_path.read_text(encoding="utf-8"))
    issue = payload["issues"][0]
    assert issue["state"] == "in-progress"
    assert issue["updated_at"]
    assert issue["comments"][0]["event"] == "issue.running"
    assert issue["comments"][0]["summary"] == "Runtime started."
    assert issue["comments"][0]["metadata"]["run_id"] == "run-1"


def test_local_json_feedback_preserves_top_level_list_shape(tmp_path):
    from trackers.local_json import LocalJsonTrackerClient

    issues_path = tmp_path / "issues.json"
    issues_path.write_text(
        json.dumps([{"id": "ISSUE-1", "identifier": "ISSUE-1", "title": "Demo", "state": "todo"}]),
        encoding="utf-8",
    )
    client = LocalJsonTrackerClient(
        workflow_root=tmp_path,
        tracker_cfg={"kind": "local-json", "path": "issues.json"},
    )

    client.post_feedback(
        issue_id="ISSUE-1",
        event="issue.completed",
        body="Done.",
        summary="Done.",
        state="done",
    )

    payload = json.loads(issues_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert payload[0]["state"] == "done"
    assert payload[0]["comments"][0]["event"] == "issue.completed"


def test_github_feedback_posts_issue_comment_with_repo_slug(tmp_path, monkeypatch):
    from trackers import github as github_tracker

    calls = []

    def fake_run(command, *, cwd=None, check=None, capture_output=None, text=None):
        calls.append(
            {
                "command": command,
                "cwd": cwd,
                "check": check,
                "capture_output": capture_output,
                "text": text,
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout="https://github.example/comment/1\n", stderr="")

    monkeypatch.setattr(github_tracker.subprocess, "run", fake_run)
    client = github_tracker.GithubTrackerClient(
        workflow_root=tmp_path,
        tracker_cfg={"kind": "github", "github_slug": "attmous/daedalus"},
    )

    result = client.post_feedback(
        issue_id="#42",
        event="issue.selected",
        body="Daedalus selected this issue.",
        summary="Selected.",
        state="in-progress",
    )

    assert result["ok"] is True
    assert result["url"] == "https://github.example/comment/1"
    assert calls == [
        {
            "command": [
                "gh",
                "issue",
                "comment",
                "42",
                "--body",
                "Daedalus selected this issue.",
                "--repo",
                "attmous/daedalus",
            ],
            "cwd": None,
            "check": True,
            "capture_output": True,
            "text": True,
        }
    ]
