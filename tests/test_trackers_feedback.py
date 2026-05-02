import json
import subprocess


def test_publish_tracker_feedback_passes_configured_comment_mode():
    from trackers.feedback import publish_tracker_feedback

    calls = []

    class Tracker:
        def post_feedback(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "event": kwargs["event"], "comment_mode": kwargs["comment_mode"]}

    result = publish_tracker_feedback(
        tracker_client=Tracker(),
        workflow_config={
            "tracker-feedback": {
                "enabled": True,
                "comment-mode": "upsert",
                "include": ["issue.running"],
            }
        },
        issue={"id": "ISSUE-1"},
        event="issue.running",
        summary="Runtime started.",
        metadata={"workflow": "issue-runner"},
    )

    assert result["comment_mode"] == "upsert"
    assert calls[0]["comment_mode"] == "upsert"


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


def test_local_json_feedback_upserts_comment_by_workflow_and_event(tmp_path):
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
                        "comments": [
                            {
                                "at": "2026-04-30T00:00:00Z",
                                "event": "issue.running",
                                "summary": "Old summary.",
                                "body": "Old body.",
                                "metadata": {"workflow": "issue-runner"},
                            }
                        ],
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
        body="New body.",
        summary="New summary.",
        state="in-progress",
        metadata={"workflow": "issue-runner", "run_id": "run-2"},
        comment_mode="upsert",
    )

    assert result["comment_mode"] == "upsert"
    assert result["comment_action"] == "updated"
    payload = json.loads(issues_path.read_text(encoding="utf-8"))
    issue = payload["issues"][0]
    assert issue["state"] == "in-progress"
    assert len(issue["comments"]) == 1
    assert issue["comments"][0]["summary"] == "New summary."
    assert issue["comments"][0]["metadata"]["run_id"] == "run-2"


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


def test_github_feedback_upsert_creates_marked_comment(tmp_path, monkeypatch):
    from trackers import github as github_tracker

    calls = []

    def fake_run_json(command, *, cwd=None):
        calls.append({"kind": "json", "command": command, "cwd": cwd})
        return [[]]

    def fake_run(command, *, cwd=None, check=None, capture_output=None, text=None):
        calls.append({"kind": "run", "command": command, "cwd": cwd})
        return subprocess.CompletedProcess(command, 0, stdout="https://github.example/comment/1\n", stderr="")

    client = github_tracker.GithubTrackerClient(
        workflow_root=tmp_path,
        tracker_cfg={"kind": "github", "github_slug": "attmous/daedalus"},
        run=fake_run,
        run_json=fake_run_json,
    )

    result = client.post_feedback(
        issue_id="42",
        event="issue.running",
        body="Runtime started.",
        summary="Runtime started.",
        metadata={"workflow": "issue-runner"},
        comment_mode="upsert",
    )

    assert result["comment_mode"] == "upsert"
    assert result["comment_action"] == "created"
    assert calls[0]["command"] == [
        "gh",
        "api",
        "repos/attmous/daedalus/issues/42/comments",
        "--paginate",
        "--slurp",
        "--hostname",
        "github.com",
    ]
    created_body = calls[1]["command"][5]
    assert "Runtime started." in created_body
    assert "<!-- daedalus-feedback:issue-runner:issue.running -->" in created_body


def test_github_feedback_upsert_updates_existing_marked_comment_and_applies_state(tmp_path):
    from workflows.issue_runner.tracker import build_tracker_client

    commands = []

    class Completed:
        stdout = "https://github.example/comment/1\n"
        stderr = ""
        returncode = 0

    def fake_run_json(command, cwd=None):
        commands.append(("json", command, cwd))
        return [
            [
                {
                    "id": 123,
                    "body": "Old body.\n\n<!-- daedalus-feedback:issue-runner:issue.completed -->\n",
                    "html_url": "https://github.example/comment/old",
                }
            ]
        ]

    def fake_run(command, cwd=None):
        commands.append(("run", command, cwd))
        return Completed()

    client = build_tracker_client(
        workflow_root=tmp_path,
        tracker_cfg={
            "kind": "github",
            "github_slug": "attmous/daedalus",
            "active_states": ["open"],
            "terminal_states": ["closed"],
        },
        run=fake_run,
        run_json=fake_run_json,
    )

    result = client.post_feedback(
        issue_id="42",
        event="issue.completed",
        body="Completed by Daedalus.",
        summary="Completed by Daedalus.",
        state="closed",
        metadata={"workflow": "issue-runner"},
        comment_mode="upsert",
    )

    assert result["ok"] is True
    assert result["state"] == "closed"
    assert result["comment_action"] == "updated"
    assert commands == [
        (
            "json",
            [
                "gh",
                "api",
                "repos/attmous/daedalus/issues/42/comments",
                "--paginate",
                "--slurp",
                "--hostname",
                "github.com",
            ],
            None,
        ),
        (
            "run",
            [
                "gh",
                "api",
                "repos/attmous/daedalus/issues/comments/123",
                "--method",
                "PATCH",
                "-f",
                "body=Completed by Daedalus.\n\n<!-- daedalus-feedback:issue-runner:issue.completed -->\n",
                "--jq",
                ".html_url",
                "--hostname",
                "github.com",
            ],
            None,
        ),
        (
            "run",
            ["gh", "issue", "close", "42", "--repo", "attmous/daedalus"],
            None,
        ),
    ]
