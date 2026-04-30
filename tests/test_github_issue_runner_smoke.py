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


@pytest.mark.skipif(
    not os.environ.get("DAEDALUS_GITHUB_SMOKE_REPO"),
    reason="set DAEDALUS_GITHUB_SMOKE_REPO=owner/repo to run the live GitHub smoke",
)
def test_live_github_issue_runner_selects_dispatches_and_reconciles_terminal_issue(tmp_path):
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
            },
            "daedalus": {
                "runtimes": {
                    "smoke": {
                        "kind": "hermes-agent",
                        "command": [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text())",
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

        result = workspace.tick()

        assert result["ok"] is True
        assert result["selectedIssue"]["id"] == issue_number
        assert Path(result["outputPath"]).read_text(encoding="utf-8")
        scheduler = workspace._load_scheduler_state()
        assert scheduler["retry_queue"][0]["issue_id"] == issue_number

        _run(
            [
                "gh",
                "issue",
                "close",
                issue_number,
                "--repo",
                smoke_repo,
                "--comment",
                "Closing Daedalus issue-runner smoke issue.",
            ]
        )

        cleanup_result = None
        for _ in range(10):
            cleanup_result = workspace.tick()
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
