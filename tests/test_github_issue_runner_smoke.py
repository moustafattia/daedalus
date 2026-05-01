import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from workflows.contract import render_workflow_markdown


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _run_json(cmd: list[str]) -> object:
    return json.loads(_run(cmd).stdout or "null")


def _write_fail_once_runtime(path: Path, *, marker_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import sys",
                f"marker = Path({str(marker_path)!r})",
                "prompt = Path(sys.argv[1]).read_text(encoding='utf-8')",
                "if not marker.exists():",
                "    marker.write_text('failed-once\\n', encoding='utf-8')",
                "    print('intentional smoke failure after reading prompt:', prompt[:80], file=sys.stderr)",
                "    raise SystemExit(7)",
                "print('signed off after retry')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _wait_for_supervised_futures(workspace, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        futures = list(workspace._supervisor_futures.values())
        if futures and all(future.done() for future in futures):
            return
        time.sleep(0.05)
    raise AssertionError("supervised smoke worker did not finish")


def _issue_comment_bodies(*, repo: str, issue_number: str) -> list[str]:
    comments = _run_json(["gh", "api", f"repos/{repo}/issues/{issue_number}/comments"])
    assert isinstance(comments, list)
    return [str(comment.get("body") or "") for comment in comments if isinstance(comment, dict)]


@pytest.mark.skipif(
    not os.environ.get("DAEDALUS_GITHUB_SMOKE_REPO"),
    reason="set DAEDALUS_GITHUB_SMOKE_REPO=owner/repo to run the live GitHub smoke",
)
def test_live_github_issue_runner_feedback_retry_recovery_and_cleanup(tmp_path):
    from workflows.issue_runner.workspace import load_workspace_from_config

    smoke_repo = os.environ["DAEDALUS_GITHUB_SMOKE_REPO"].strip()
    repo_path = Path(os.environ.get("DAEDALUS_GITHUB_SMOKE_REPO_PATH") or (tmp_path / "repo")).expanduser().resolve()
    repo_path.mkdir(parents=True, exist_ok=True)
    label = os.environ.get("DAEDALUS_GITHUB_SMOKE_LABEL", "daedalus-smoke")
    marker = uuid4().hex[:10]
    title = f"Daedalus issue-runner smoke {marker}"
    body = f"Temporary Daedalus smoke issue. Marker: {marker}"
    issue_number: str | None = None

    _run(["gh", "auth", "status"])
    _run(
        [
            "gh",
            "label",
            "create",
            label,
            "--repo",
            smoke_repo,
            "--color",
            "2f81f7",
            "--description",
            "Temporary Daedalus smoke-test label",
        ],
        check=False,
    )

    try:
        created = _run(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{smoke_repo}/issues",
                "-f",
                f"title={title}",
                "-f",
                f"body={body}",
                "-f",
                f"labels[]={label}",
                "--jq",
                ".number",
            ]
        )
        issue_number = created.stdout.strip()
        assert issue_number

        workflow_root = tmp_path / "workflow"
        workflow_root.mkdir()
        runtime_script = tmp_path / "fail_once_runtime.py"
        fail_marker = tmp_path / "runtime-failed-once.marker"
        _write_fail_once_runtime(runtime_script, marker_path=fail_marker)
        cfg = {
            "workflow": "issue-runner",
            "schema-version": 1,
            "instance": {"name": "smoke-issue-runner", "engine-owner": "hermes"},
            "repository": {"local-path": str(repo_path), "slug": smoke_repo},
            "tracker": {
                "kind": "github",
                "github_slug": smoke_repo,
                "active_states": ["open"],
                "terminal_states": ["closed"],
                "required_labels": [label],
            },
            "workspace": {"root": "workspace/issues"},
            "agent": {
                "name": "Smoke_Agent",
                "model": "local-smoke",
                "runtime": "smoke",
                "max_concurrent_agents": 1,
                "max_retry_backoff_ms": 1,
            },
            "tracker-feedback": {
                "enabled": True,
                "comment-mode": "append",
                "include": [
                    "issue.selected",
                    "issue.dispatched",
                    "issue.running",
                    "issue.failed",
                    "issue.retry_scheduled",
                    "issue.completed",
                ],
                "state-updates": {
                    "enabled": True,
                    "on-completed": "closed",
                },
            },
            "daedalus": {
                "runtimes": {
                    "smoke": {
                        "kind": "hermes-agent",
                        "command": [
                            sys.executable,
                            str(runtime_script),
                            "{prompt_path}",
                        ],
                    }
                }
            },
            "storage": {
                "status": "memory/workflow-status.json",
                "health": "memory/workflow-health.json",
                "audit-log": "memory/workflow-audit.jsonl",
                "scheduler": "memory/workflow-scheduler.json",
            },
        }
        (workflow_root / "WORKFLOW.md").write_text(
            render_workflow_markdown(
                config=cfg,
                prompt_template="Smoke issue {{ issue.identifier }}: {{ issue.title }}",
            ),
            encoding="utf-8",
        )
        workspace = load_workspace_from_config(workspace_root=workflow_root)

        first = workspace.supervise_once()
        assert first["ok"] is True
        assert first["selectedIssue"]["id"] == issue_number
        assert first["dispatchedWorkers"][0]["issue_id"] == issue_number

        _wait_for_supervised_futures(workspace)
        failed = workspace.supervise_once()
        assert failed["completedResults"][0]["ok"] is False
        assert failed["completedResults"][0]["retry"]["issue_id"] == issue_number
        scheduler = workspace._load_scheduler_state()
        assert scheduler["retry_queue"][0]["issue_id"] == issue_number
        assert scheduler["retry_queue"][0]["error"]

        time.sleep(0.05)
        retry_dispatch = workspace.supervise_once()
        assert retry_dispatch["selectedIssue"]["id"] == issue_number
        assert retry_dispatch["dispatchedWorkers"][0]["issue_id"] == issue_number
        assert retry_dispatch["dispatchedWorkers"][0]["attempt"] == 2

        _wait_for_supervised_futures(workspace)
        completed = workspace.supervise_once()
        assert completed["completedResults"][0]["ok"] is True
        assert completed["completedResults"][0]["retry"] is None
        assert Path(completed["completedResults"][0]["outputPath"]).read_text(encoding="utf-8") == "signed off after retry\n"
        assert workspace._load_scheduler_state().get("retry_queue") == []

        issue_view = _run_json(["gh", "issue", "view", issue_number, "--repo", smoke_repo, "--json", "state,labels"])
        assert isinstance(issue_view, dict)
        assert str(issue_view["state"]).lower() == "closed"
        assert label in {item["name"] for item in issue_view.get("labels") or []}

        comment_bodies = _issue_comment_bodies(repo=smoke_repo, issue_number=issue_number)
        for event in [
            "issue.selected",
            "issue.dispatched",
            "issue.running",
            "issue.failed",
            "issue.retry_scheduled",
            "issue.completed",
        ]:
            assert any(f"Daedalus update: {event}" in body for body in comment_bodies), event

        cleanup_result = None
        for _ in range(10):
            cleanup_result = workspace.supervise_once()
            cleaned = cleanup_result.get("cleanup") or []
            if any(str(item.get("issue_id")) == issue_number for item in cleaned):
                break
            time.sleep(2)

        assert cleanup_result is not None
        assert any(str(item.get("issue_id")) == issue_number for item in cleanup_result.get("cleanup") or [])
        assert not workspace._load_scheduler_state().get("retry_queue")
    finally:
        if issue_number:
            _run(["gh", "issue", "close", issue_number, "--repo", smoke_repo], check=False)
