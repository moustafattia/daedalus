from code_hosts import build_code_host_client
from code_hosts.github import GithubCodeHostClient


class Completed:
    def __init__(self, stdout: str = ""):
        self.stdout = stdout


def test_github_code_host_client_assembles_pr_commands(tmp_path):
    calls = []

    def fake_run(command, cwd=None):
        calls.append(("run", command, cwd))
        return Completed(stdout="https://github.example/pull/7\n")

    client = GithubCodeHostClient(
        code_host_cfg={"kind": "github", "github_slug": "owner/repo"},
        repo_path=tmp_path,
        run=fake_run,
    )

    created = client.create_pull_request(head="issue-7", title="Title", body="Body")
    ready = client.mark_pull_request_ready(7)
    merged = client.merge_pull_request(7, squash=True, delete_branch=True)

    assert created == "https://github.example/pull/7"
    assert ready is True
    assert merged["ok"] is True
    assert calls[0] == (
        "run",
        ["gh", "pr", "create", "--head", "issue-7", "--title", "Title", "--body", "Body", "--repo", "owner/repo"],
        tmp_path,
    )
    assert calls[1] == ("run", ["gh", "pr", "ready", "7", "--repo", "owner/repo"], tmp_path)
    assert calls[2] == ("run", ["gh", "pr", "merge", "7", "--squash", "--delete-branch", "--repo", "owner/repo"], tmp_path)


def test_github_code_host_client_assembles_review_queries(tmp_path):
    calls = []

    def fake_run_json(command, cwd=None):
        calls.append((command, cwd))
        if command[1:3] == ["pr", "list"]:
            return [{"number": 7}]
        if "reactions" in command[2]:
            return [{"content": "+1"}]
        if "resolveReviewThread" in " ".join(command):
            return {"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}}
        return {"data": {"repository": {"pullRequest": {"headRefOid": "head", "reviewThreads": {"nodes": []}}}}}

    client = build_code_host_client(
        workflow_root=tmp_path,
        code_host_cfg={"kind": "github", "github_slug": "github.example.com/owner/repo"},
        repo_path=tmp_path,
        run_json=fake_run_json,
    )

    assert client.list_open_pull_requests() == [{"number": 7}]
    assert client.fetch_issue_reactions(7) == [{"content": "+1"}]
    assert client.resolve_review_thread("thread-1") is True
    assert client.fetch_pull_request_review_threads(7)["headRefOid"] == "head"

    assert calls[0][0][:4] == ["gh", "pr", "list", "--state"]
    assert calls[0][0][-2:] == ["--repo", "github.example.com/owner/repo"]
    assert calls[1][0][2] == "repos/owner/repo/issues/7/reactions"
    assert calls[1][0][-2:] == ["--hostname", "github.example.com"]
    assert calls[2][0][-2:] == ["--hostname", "github.example.com"]
    assert "owner:\"owner\"" in " ".join(calls[3][0])
    assert "name:\"repo\"" in " ".join(calls[3][0])
    assert "commits(last: 1)" in " ".join(calls[3][0])
    assert calls[3][0][-2:] == ["--hostname", "github.example.com"]
