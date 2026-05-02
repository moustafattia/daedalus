import json
from pathlib import Path

import pytest


def test_local_json_tracker_client_lists_candidates_terminal_and_refresh(tmp_path):
    from workflows.issue_runner.tracker import build_tracker_client

    tracker_path = tmp_path / "issues.json"
    tracker_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "id": "ISSUE-2",
                        "identifier": "ISSUE-2",
                        "title": "Lower priority",
                        "priority": 2,
                        "state": "todo",
                        "labels": [],
                        "blocked_by": [],
                    },
                    {
                        "id": "ISSUE-1",
                        "identifier": "ISSUE-1",
                        "title": "Higher priority",
                        "priority": 1,
                        "state": "todo",
                        "labels": [],
                        "blocked_by": [],
                    },
                    {
                        "id": "ISSUE-3",
                        "identifier": "ISSUE-3",
                        "title": "Done",
                        "priority": 3,
                        "state": "done",
                        "labels": [],
                        "blocked_by": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    client = build_tracker_client(
        workflow_root=tmp_path,
        tracker_cfg={
            "kind": "local-json",
            "path": str(tracker_path),
            "active_states": ["todo"],
            "terminal_states": ["done"],
        },
    )

    candidates = client.list_candidates()
    terminals = client.list_terminal()
    refreshed = client.refresh(["ISSUE-1"])

    assert [issue["id"] for issue in candidates] == ["ISSUE-1", "ISSUE-2"]
    assert [issue["id"] for issue in terminals] == ["ISSUE-3"]
    assert refreshed["ISSUE-1"]["title"] == "Higher priority"


def test_issue_workspace_slug_matches_symphony_sanitization():
    from workflows.issue_runner.tracker import issue_workspace_slug

    assert issue_workspace_slug({"identifier": "ABC-123"}) == "ABC-123"
    assert issue_workspace_slug({"identifier": "ABC/123 needs work"}) == "ABC_123_needs_work"


def test_linear_tracker_client_normalizes_graphql_payload(monkeypatch, tmp_path):
    from workflows.issue_runner.tracker import build_tracker_client

    monkeypatch.setenv("LINEAR_API_KEY", "linear-token")

    active_issue = {
        "id": "lin-1",
        "identifier": "ABC-123",
        "title": "Important work",
        "description": "Implement the thing.",
        "priority": 1,
        "branchName": "abc-123-important-work",
        "url": "https://linear.app/acme/issue/ABC-123",
        "createdAt": "2026-04-30T00:00:00Z",
        "updatedAt": "2026-04-30T01:00:00Z",
        "state": {"name": "In Progress"},
        "labels": {"nodes": [{"name": "backend"}]},
        "relations": {
            "nodes": [
                {
                    "type": "blocks",
                    "relatedIssue": {
                        "id": "lin-0",
                        "identifier": "ABC-122",
                        "createdAt": "2026-04-29T00:00:00Z",
                        "updatedAt": "2026-04-29T01:00:00Z",
                        "state": {"name": "In Progress"},
                    },
                }
            ]
        },
    }
    terminal_issue = {
        **active_issue,
        "id": "lin-2",
        "identifier": "ABC-999",
        "title": "Done work",
        "state": {"name": "Done"},
    }

    def fake_post_json(endpoint, *, query, variables, api_key):
        assert endpoint == "https://api.linear.app/graphql"
        assert api_key == "linear-token"
        if "IssueRunnerIssuesByIds" in query:
            assert "$ids: [ID!]" in query
            return {
                "data": {
                    "issues": {
                        "nodes": [active_issue],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        assert "project: { slugId: { eq: $projectSlug } }" in query
        states = {state.lower() for state in (variables.get("states") or [])}
        nodes = [active_issue] if "in progress" in states else [terminal_issue]
        return {
            "data": {
                "issues": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }

    client = build_tracker_client(
        workflow_root=tmp_path,
        tracker_cfg={
            "kind": "linear",
            "endpoint": "https://api.linear.app/graphql",
            "api_key": "$LINEAR_API_KEY",
            "project_slug": "core",
            "active_states": ["In Progress"],
            "terminal_states": ["Done"],
        },
        post_json=fake_post_json,
    )

    candidates = client.list_candidates()
    terminals = client.list_terminal()
    refreshed = client.refresh(["lin-1"])

    assert candidates[0]["identifier"] == "ABC-123"
    assert candidates[0]["labels"] == ["backend"]
    assert candidates[0]["blocked_by"][0]["identifier"] == "ABC-122"
    assert terminals[0]["state"] == "Done"
    assert refreshed["lin-1"]["branch_name"] == "abc-123-important-work"


def test_linear_tracker_client_empty_state_fetch_does_not_call_api(monkeypatch, tmp_path):
    from workflows.issue_runner.tracker import build_tracker_client

    monkeypatch.setenv("LINEAR_API_KEY", "linear-token")

    def fail_post_json(*args, **kwargs):
        raise AssertionError("empty state fetch should not call Linear")

    client = build_tracker_client(
        workflow_root=tmp_path,
        tracker_cfg={
            "kind": "linear",
            "api_key": "$LINEAR_API_KEY",
            "project_slug": "core",
            "active_states": [],
            "terminal_states": [],
        },
        post_json=fail_post_json,
    )

    assert client.list_candidates() == []
    assert client.list_terminal() == []


def test_linear_tracker_client_errors_when_pagination_cursor_missing(monkeypatch, tmp_path):
    from workflows.issue_runner.tracker import TrackerConfigError, build_tracker_client

    monkeypatch.setenv("LINEAR_API_KEY", "linear-token")

    def fake_post_json(endpoint, *, query, variables, api_key):
        del endpoint, query, variables, api_key
        return {
            "data": {
                "issues": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": True, "endCursor": None},
                }
            }
        }

    client = build_tracker_client(
        workflow_root=tmp_path,
        tracker_cfg={
            "kind": "linear",
            "api_key": "$LINEAR_API_KEY",
            "project_slug": "core",
            "active_states": ["Todo"],
        },
        post_json=fake_post_json,
    )

    with pytest.raises(TrackerConfigError, match="endCursor"):
        client.list_candidates()


def test_linear_tracker_client_requires_api_key(monkeypatch, tmp_path):
    from workflows.issue_runner.tracker import TrackerConfigError, build_tracker_client

    monkeypatch.delenv("LINEAR_API_KEY", raising=False)

    with pytest.raises(TrackerConfigError):
        build_tracker_client(
            workflow_root=tmp_path,
            tracker_cfg={
                "kind": "linear",
                "project_slug": "core",
            },
        )


def test_github_tracker_client_normalizes_issue_payloads_and_refresh(tmp_path):
    from workflows.issue_runner.tracker import build_tracker_client

    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    open_issue = {
        "number": 123,
        "title": "Important GitHub issue",
        "body": "Implement the thing.",
        "url": "https://github.com/attmous/daedalus/issues/123",
        "labels": [{"name": "backend"}],
        "createdAt": "2026-04-30T00:00:00Z",
        "updatedAt": "2026-04-30T01:00:00Z",
        "state": "OPEN",
    }
    excluded_issue = {
        **open_issue,
        "number": 124,
        "title": "Skipped",
        "url": "https://github.com/attmous/daedalus/issues/124",
        "labels": [{"name": "skip"}],
    }
    closed_issue = {
        **open_issue,
        "number": 999,
        "title": "Closed issue",
        "url": "https://github.com/attmous/daedalus/issues/999",
        "labels": [],
        "state": "CLOSED",
    }

    def fake_run_json(command, cwd=None):
        assert cwd == repo_path
        if command[:3] == ["gh", "issue", "list"]:
            state = command[command.index("--state") + 1]
            if state == "open":
                return [open_issue, excluded_issue]
            if state == "closed":
                return [closed_issue]
            if state == "all":
                return [open_issue, excluded_issue, closed_issue]
        if command[:3] == ["gh", "issue", "view"] and command[3] == "123":
            return open_issue
        raise AssertionError(f"unexpected command: {command}")

    client = build_tracker_client(
        workflow_root=tmp_path,
        repo_path=repo_path,
        tracker_cfg={
            "kind": "github",
            "active_states": ["open"],
            "terminal_states": ["closed"],
            "required_labels": ["backend"],
            "exclude_labels": ["skip"],
        },
        run_json=fake_run_json,
    )

    candidates = client.list_candidates()
    terminals = client.list_terminal()
    refreshed = client.refresh(["123"])

    assert [issue["id"] for issue in candidates] == ["123"]
    assert candidates[0]["identifier"] == "#123"
    assert candidates[0]["description"] == "Implement the thing."
    assert candidates[0]["labels"] == ["backend"]
    assert [issue["id"] for issue in terminals] == ["999"]
    assert refreshed["123"]["state"] == "open"


def test_github_tracker_client_requires_repo_path(tmp_path):
    from workflows.issue_runner.tracker import TrackerConfigError, build_tracker_client

    with pytest.raises(TrackerConfigError):
        build_tracker_client(
            workflow_root=tmp_path,
            tracker_cfg={
                "kind": "github",
            },
        )


def test_github_tracker_client_can_use_repo_slug_without_checkout(tmp_path):
    from workflows.issue_runner.tracker import build_tracker_client

    commands = []
    open_issue = {
        "number": 42,
        "title": "Slug-backed issue",
        "body": "Run through --repo.",
        "url": "https://github.com/attmous/daedalus/issues/42",
        "labels": [],
        "createdAt": "2026-04-30T00:00:00Z",
        "updatedAt": "2026-04-30T01:00:00Z",
        "state": "OPEN",
    }

    def fake_run_json(command, cwd=None):
        commands.append(command)
        assert cwd is None
        assert command[-2:] == ["--repo", "attmous/daedalus"]
        if command[:3] == ["gh", "issue", "list"]:
            return [open_issue]
        if command[:3] == ["gh", "issue", "view"]:
            return open_issue
        raise AssertionError(f"unexpected command: {command}")

    client = build_tracker_client(
        workflow_root=tmp_path,
        tracker_cfg={
            "kind": "github",
            "github_slug": "attmous/daedalus",
            "active_states": ["open"],
            "terminal_states": ["closed"],
        },
        run_json=fake_run_json,
    )

    assert client.repo_path is None
    assert client.repo_slug == "attmous/daedalus"
    assert client.list_candidates()[0]["id"] == "42"
    assert client.refresh(["42"])["42"]["title"] == "Slug-backed issue"
    assert any(command[:3] == ["gh", "issue", "list"] for command in commands)


def test_github_tracker_client_accepts_host_qualified_repo_slug_without_checkout(tmp_path):
    from workflows.issue_runner.tracker import build_tracker_client

    commands = []
    open_issue = {
        "number": 42,
        "title": "Enterprise issue",
        "body": "Run through a host-qualified --repo.",
        "url": "https://github.example.com/attmous/daedalus/issues/42",
        "labels": [],
        "createdAt": "2026-04-30T00:00:00Z",
        "updatedAt": "2026-04-30T01:00:00Z",
        "state": "OPEN",
    }

    def fake_run_json(command, cwd=None):
        commands.append(command)
        assert cwd is None
        assert command[-2:] == ["--repo", "github.example.com/attmous/daedalus"]
        if command[:3] == ["gh", "issue", "list"]:
            return [open_issue]
        raise AssertionError(f"unexpected command: {command}")

    client = build_tracker_client(
        workflow_root=tmp_path,
        tracker_cfg={
            "kind": "github",
            "github_slug": "github.example.com/attmous/daedalus",
            "active_states": ["open"],
            "terminal_states": ["closed"],
        },
        run_json=fake_run_json,
    )

    assert client.repo_path is None
    assert client.repo_slug == "github.example.com/attmous/daedalus"
    assert client.list_candidates()[0]["title"] == "Enterprise issue"
    assert any(command[:3] == ["gh", "issue", "list"] for command in commands)


def test_github_tracker_feedback_comments_and_applies_supported_state(tmp_path):
    from workflows.issue_runner.tracker import build_tracker_client

    commands = []

    class Completed:
        stdout = "https://github.com/attmous/daedalus/issues/42#issuecomment-1\n"
        stderr = ""
        returncode = 0

    def fake_run(command, cwd=None):
        commands.append((command, cwd))
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
        run_json=lambda *args, **kwargs: [],
    )

    result = client.post_feedback(
        issue_id="42",
        event="issue.completed",
        body="Completed by Daedalus.",
        summary="Completed by Daedalus.",
        state="closed",
        metadata={"attempt": 1},
    )

    assert result["ok"] is True
    assert result["state"] == "closed"
    assert commands == [
        (
            [
                "gh",
                "issue",
                "comment",
                "42",
                "--body",
                "Completed by Daedalus.",
                "--repo",
                "attmous/daedalus",
            ],
            None,
        ),
        (
            ["gh", "issue", "close", "42", "--repo", "attmous/daedalus"],
            None,
        ),
    ]


def test_github_tracker_feedback_ignores_unsupported_state_after_comment(tmp_path):
    from workflows.issue_runner.tracker import build_tracker_client

    class Completed:
        stdout = ""
        stderr = ""
        returncode = 0

    commands = []

    def fake_run(command, cwd=None):
        commands.append(command)
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
        run_json=lambda *args, **kwargs: [],
    )

    result = client.post_feedback(
        issue_id="42",
        event="issue.completed",
        body="Completed by Daedalus.",
        summary="Completed by Daedalus.",
        state="done",
    )

    assert result["ok"] is True
    assert result["state"] is None
    assert result["unsupported_state"] == "done"
    assert commands == [
        ["gh", "issue", "comment", "42", "--body", "Completed by Daedalus.", "--repo", "attmous/daedalus"]
    ]
