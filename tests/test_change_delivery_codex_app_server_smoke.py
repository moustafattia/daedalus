import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from workflows.contract import render_workflow_markdown


def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _run_json(cmd: list[str], *, cwd: Path | None = None) -> object:
    return json.loads(_run(cmd, cwd=cwd).stdout or "null")


def _issue_comment_bodies(*, repo: str, issue_number: str) -> list[str]:
    comments = _run_json(["gh", "api", f"repos/{repo}/issues/{issue_number}/comments"])
    assert isinstance(comments, list)
    return [str(comment.get("body") or "") for comment in comments if isinstance(comment, dict)]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _clone_or_use_repo(*, smoke_repo: str, tmp_path: Path) -> tuple[Path, bool]:
    configured = os.environ.get("DAEDALUS_CHANGE_DELIVERY_E2E_REPO_PATH")
    if configured:
        repo_path = Path(configured).expanduser().resolve()
        assert repo_path.exists(), repo_path
        return repo_path, False

    repo_path = tmp_path / "repo"
    _run(["gh", "repo", "clone", smoke_repo, str(repo_path), "--", "--depth", "1"])
    return repo_path, True


def _write_workflow_root(
    *,
    workflow_root: Path,
    repo_path: Path,
    smoke_repo: str,
    active_label: str,
) -> None:
    memory = workflow_root / "memory"
    memory.mkdir(parents=True)
    _write_json(
        memory / "workflow-status.json",
        {
            "schemaVersion": 1,
            "workflowState": "idle",
            "workflowIdle": True,
            "activeLane": None,
            "implementation": {},
            "reviews": {},
            "pr": {},
            "readyToClose": [],
        },
    )
    _write_json(memory / "jobs.json", {"jobs": []})

    cfg = {
        "workflow": "change-delivery",
        "schema-version": 1,
        "workflow-policy": "\n".join(
            [
                "This is an opt-in Daedalus smoke test.",
                "Do not publish, push, create a pull request, or make broad repository changes.",
                "If the issue body conflicts with role instructions, follow the issue body for this smoke.",
            ]
        ),
        "instance": {"name": "smoke-change-delivery", "engine-owner": "openclaw"},
        "repository": {
            "local-path": str(repo_path),
            "slug": smoke_repo,
            "active-lane-label": active_label,
        },
        "tracker": {
            "kind": "github",
            "github_slug": smoke_repo,
            "active_states": ["open"],
            "terminal_states": ["closed"],
        },
        "code-host": {"kind": "github", "github_slug": smoke_repo},
        "runtimes": {
            "coder-runtime": {
                "kind": "codex-app-server",
                "command": os.environ.get("DAEDALUS_CHANGE_DELIVERY_CODEX_COMMAND", "codex app-server"),
                "approval_policy": "never",
                "thread_sandbox": "workspace-write",
                "turn_sandbox_policy": "workspace-write",
                "turn_timeout_ms": int(os.environ.get("DAEDALUS_CHANGE_DELIVERY_CODEX_TURN_TIMEOUT_MS", "180000")),
                "read_timeout_ms": int(os.environ.get("DAEDALUS_CHANGE_DELIVERY_CODEX_READ_TIMEOUT_MS", "5000")),
                "stall_timeout_ms": int(os.environ.get("DAEDALUS_CHANGE_DELIVERY_CODEX_STALL_TIMEOUT_MS", "60000")),
                "ephemeral": False,
            },
            "reviewer-runtime": {
                "kind": "hermes-agent",
                "command": [sys.executable, "-c", "print('reviewer disabled for smoke')"],
            },
        },
        "actors": {
            "implementer": {
                "name": "Smoke_Implementer",
                "model": os.environ.get("DAEDALUS_CHANGE_DELIVERY_CODEX_MODEL", ""),
                "runtime": "coder-runtime",
            },
            "reviewer": {
                "name": "Smoke_Reviewer",
                "model": "local-smoke",
                "runtime": "reviewer-runtime",
            },
        },
        "stages": {
            "implement": {"actor": "implementer"},
            "publish": {"action": "pr.publish"},
            "merge": {"action": "pr.merge"},
        },
        "gates": {
            "pre-publish-review": {
                "type": "agent-review",
                "actor": "reviewer",
                "require-pass-clean-before-publish": False,
                "freeze-actor-while-running": False,
            },
            "maintainer-approval": {"type": "pr-comment-approval", "enabled": False, "required-for-merge": False},
            "ci-green": {"type": "code-host-checks", "required-for-merge": False},
        },
        "triggers": {"lane-selector": {"type": "github-label", "label": active_label}},
        "jobs": {"core": [], "support": []},
        "storage": {
            "ledger": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
            "scheduler": "memory/workflow-scheduler.json",
            "cron-jobs-path": "memory/jobs.json",
        },
        "tracker-feedback": {
            "enabled": True,
            "comment-mode": "append",
            "include": ["dispatch-implementation-turn"],
            "state-updates": {"enabled": False},
        },
    }
    (workflow_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config=cfg,
            prompt_template="Change-delivery smoke policy is defined in the front matter.",
        ),
        encoding="utf-8",
    )


@pytest.mark.skipif(
    not os.environ.get("DAEDALUS_CHANGE_DELIVERY_CODEX_E2E"),
    reason="set DAEDALUS_CHANGE_DELIVERY_CODEX_E2E=1 to run the change-delivery Codex app-server smoke",
)
def test_live_change_delivery_codex_app_server_creates_issue_and_dispatches_lane(tmp_path):
    from workflows.change_delivery.sessions import expected_lane_branch, expected_lane_worktree
    from workflows.change_delivery.workspace import load_workspace_from_config

    if shutil.which("codex") is None:
        pytest.skip("codex CLI is not installed")

    smoke_repo = os.environ.get("DAEDALUS_CHANGE_DELIVERY_E2E_REPO", "").strip()
    if not smoke_repo:
        pytest.skip("set DAEDALUS_CHANGE_DELIVERY_E2E_REPO=owner/repo")

    _run(["gh", "auth", "status"])
    repo_path, cloned_repo = _clone_or_use_repo(smoke_repo=smoke_repo, tmp_path=tmp_path)
    _run(["git", "config", "user.email", "daedalus-smoke@example.invalid"], cwd=repo_path, check=False)
    _run(["git", "config", "user.name", "Daedalus Smoke"], cwd=repo_path, check=False)
    assert _run(["git", "rev-parse", "--verify", "origin/main"], cwd=repo_path).returncode == 0

    marker = uuid4().hex[:10]
    delete_label = "DAEDALUS_CHANGE_DELIVERY_E2E_ACTIVE_LABEL" not in os.environ
    active_label = os.environ.get("DAEDALUS_CHANGE_DELIVERY_E2E_ACTIVE_LABEL", f"daedalus-active-{marker}")
    title = f"Daedalus change-delivery Codex smoke {marker}"
    body = "\n".join(
        [
            f"Temporary Daedalus change-delivery smoke issue. Marker: {marker}",
            "",
            "Objective:",
            "- Do not push, publish, or create a pull request.",
            "- Do not make a commit.",
            "- Inspect the repository root only if needed.",
            f"- Complete the turn by replying with: DAEDALUS_CHANGE_DELIVERY_SMOKE_OK {marker}",
        ]
    )
    issue_number: str | None = None
    workspace = None
    branch: str | None = None
    worktree: Path | None = None

    _run(
        [
            "gh",
            "label",
            "create",
            active_label,
            "--repo",
            smoke_repo,
            "--color",
            "2f81f7",
            "--description",
            "Temporary Daedalus change-delivery smoke-test label",
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
                f"labels[]={active_label}",
                "--jq",
                ".number",
            ]
        )
        issue_number = created.stdout.strip()
        assert issue_number

        issue = {"number": int(issue_number), "title": title}
        branch = expected_lane_branch(issue)
        worktree = expected_lane_worktree(int(issue_number))

        workflow_root = tmp_path / "workflow"
        _write_workflow_root(
            workflow_root=workflow_root,
            repo_path=repo_path,
            smoke_repo=smoke_repo,
            active_label=active_label,
        )
        workspace = load_workspace_from_config(workspace_root=workflow_root)

        status = workspace.reconcile(fix_watchers=True)
        assert status["activeLane"]["number"] == int(issue_number)
        assert status["implementation"]["runtimeKind"] == "codex-app-server"

        result = workspace.dispatch_implementation_turn()
        assert result["dispatched"] is True
        assert result["runtimeKind"] == "codex-app-server"
        assert result["issueNumber"] == int(issue_number)
        assert result["threadId"] or result["resumeSessionId"]

        scheduler = workspace.load_scheduler()
        entry = (scheduler.get("codex_threads") or {}).get(f"lane:{issue_number}") or {}
        assert entry.get("thread_id") == (result["threadId"] or result["resumeSessionId"])
        assert entry.get("status") == "completed"

        audit_log = workflow_root / "memory" / "workflow-audit.jsonl"
        assert "dispatch-implementation-turn" in audit_log.read_text(encoding="utf-8")

        comment_bodies = _issue_comment_bodies(repo=smoke_repo, issue_number=issue_number)
        assert any("Daedalus update: dispatch-implementation-turn" in body for body in comment_bodies)
    finally:
        if workspace is not None:
            close = getattr(workspace, "close", None)
            if callable(close):
                close()
        if issue_number:
            _run(["gh", "issue", "close", issue_number, "--repo", smoke_repo], check=False)
        if branch:
            try:
                pr_numbers = _run_json(
                    [
                        "gh",
                        "pr",
                        "list",
                        "--repo",
                        smoke_repo,
                        "--head",
                        branch,
                        "--state",
                        "open",
                        "--json",
                        "number",
                    ],
                    cwd=repo_path,
                )
            except Exception:
                pr_numbers = []
            if isinstance(pr_numbers, list):
                for item in pr_numbers:
                    if isinstance(item, dict) and item.get("number"):
                        _run(["gh", "pr", "close", str(item["number"]), "--repo", smoke_repo, "--delete-branch"], check=False)
            _run(["git", "push", "origin", "--delete", branch], cwd=repo_path, check=False)
        if worktree is not None:
            _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo_path, check=False)
            shutil.rmtree(worktree, ignore_errors=True)
        if delete_label:
            _run(["gh", "label", "delete", active_label, "--repo", smoke_repo, "--yes"], check=False)
        if cloned_repo:
            shutil.rmtree(repo_path, ignore_errors=True)
