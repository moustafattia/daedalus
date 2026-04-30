from pathlib import Path


def _github_config(repo_path: Path) -> dict:
    return {
        "workflow": "issue-runner",
        "schema-version": 1,
        "instance": {"name": "attmous-daedalus-issue-runner", "engine-owner": "hermes"},
        "repository": {"local-path": str(repo_path), "slug": "attmous/daedalus"},
        "tracker": {
            "kind": "github",
            "github_slug": "attmous/daedalus",
            "active_states": ["open"],
            "terminal_states": ["closed"],
        },
        "workspace": {"root": "workspace/issues"},
        "agent": {
            "name": "runner",
            "model": "gpt-5.4",
            "runtime": "default",
            "max_concurrent_agents": 1,
        },
        "daedalus": {
            "runtimes": {
                "default": {
                    "kind": "hermes-agent",
                    "command": ["fake-agent", "--prompt", "{prompt_path}"],
                }
            }
        },
        "storage": {
            "status": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
        },
    }


def test_issue_runner_preflight_checks_github_auth_and_repo(monkeypatch, tmp_path):
    from trackers import github as github_tracker
    from workflows.issue_runner.preflight import run_preflight

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    commands = []

    def fake_run_json(command, cwd=None):
        commands.append(command)
        assert cwd == repo_path
        if command[:3] == ["gh", "auth", "status"]:
            return {"hosts": {"github.com": [{"state": "success", "active": True, "login": "attmous"}]}}
        if command[:3] == ["gh", "repo", "view"]:
            return {"nameWithOwner": "attmous/daedalus"}
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(github_tracker, "_subprocess_run_json", fake_run_json)

    result = run_preflight(_github_config(repo_path))

    assert result.ok is True
    assert any(command[:3] == ["gh", "auth", "status"] for command in commands)
    assert any(command[:3] == ["gh", "repo", "view"] for command in commands)


def test_issue_runner_preflight_checks_auth_for_configured_github_host(monkeypatch, tmp_path):
    from trackers import github as github_tracker
    from workflows.issue_runner.preflight import run_preflight

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    commands = []
    cfg = _github_config(repo_path)
    cfg["repository"]["slug"] = "attmous/daedalus"
    cfg["tracker"]["github_slug"] = "github.example.com/attmous/daedalus"

    def fake_run_json(command, cwd=None):
        commands.append(command)
        assert cwd == repo_path
        if command[:3] == ["gh", "auth", "status"]:
            assert command[3:5] == ["--hostname", "github.example.com"]
            return {
                "hosts": {
                    "github.example.com": [
                        {"state": "success", "active": True, "login": "enterprise-user"}
                    ]
                }
            }
        if command[:3] == ["gh", "repo", "view"]:
            assert command[3] == "github.example.com/attmous/daedalus"
            return {"nameWithOwner": "attmous/daedalus"}
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(github_tracker, "_subprocess_run_json", fake_run_json)

    result = run_preflight(cfg)

    assert result.ok is True
    assert any(command[:3] == ["gh", "auth", "status"] for command in commands)
    assert any(command[:3] == ["gh", "repo", "view"] for command in commands)


def test_issue_runner_preflight_rejects_repository_github_slug(tmp_path):
    from workflows.issue_runner.preflight import run_preflight

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    cfg = _github_config(repo_path)
    cfg["tracker"].pop("github_slug")
    cfg["repository"]["github-slug"] = "attmous/daedalus"

    result = run_preflight(cfg)

    assert result.ok is False
    assert result.error_code == "invalid-config"
    assert "tracker.github_slug" in str(result.error_detail)
    assert "repository.github-slug" in str(result.error_detail)


def test_issue_runner_preflight_rejects_hyphenated_tracker_github_slug(tmp_path):
    from workflows.issue_runner.preflight import run_preflight

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    cfg = _github_config(repo_path)
    cfg["tracker"].pop("github_slug")
    cfg["tracker"]["github-slug"] = "attmous/daedalus"

    result = run_preflight(cfg)

    assert result.ok is False
    assert result.error_code == "invalid-config"
    assert "tracker.github_slug" in str(result.error_detail)
    assert "tracker.github-slug" in str(result.error_detail)


def test_issue_runner_preflight_requires_tracker_github_slug(tmp_path):
    from workflows.issue_runner.preflight import run_preflight

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    cfg = _github_config(repo_path)
    cfg["tracker"].pop("github_slug")

    result = run_preflight(cfg)

    assert result.ok is False
    assert result.error_code == "invalid-config"
    assert "requires tracker.github_slug" in str(result.error_detail)


def test_issue_runner_preflight_rejects_github_state_shape(tmp_path):
    from workflows.issue_runner.preflight import run_preflight

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    cfg = _github_config(repo_path)
    cfg["tracker"]["active_states"] = ["todo"]

    result = run_preflight(cfg)

    assert result.ok is False
    assert result.error_code == "invalid-config"
    assert "tracker.active_states: [open]" in str(result.error_detail)


def test_issue_runner_doctor_reports_github_auth_and_repo(monkeypatch, tmp_path):
    from workflows.contract import render_workflow_markdown
    from workflows.issue_runner.workspace import load_workspace_from_config

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    workflow_root = tmp_path / "wf"
    workflow_root.mkdir()
    cfg = _github_config(repo_path)
    (workflow_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(config=cfg, prompt_template="Issue: {{ issue.identifier }}"),
        encoding="utf-8",
    )

    def fake_run_json(command, cwd=None):
        assert cwd == repo_path
        if command[:3] == ["gh", "auth", "status"]:
            return {"hosts": {"github.com": [{"state": "success", "active": True, "login": "attmous"}]}}
        if command[:3] == ["gh", "repo", "view"]:
            return {"nameWithOwner": "attmous/daedalus"}
        if command[:3] == ["gh", "issue", "list"]:
            return []
        raise AssertionError(f"unexpected command: {command}")

    workspace = load_workspace_from_config(
        workspace_root=workflow_root,
        run_json=fake_run_json,
    )

    payload = workspace.doctor()
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["ok"] is True
    assert checks["tracker"]["status"] == "pass"
    assert checks["github-auth"]["detail"] == "gh authenticated as attmous"
    assert checks["github-repo"]["detail"] == "attmous/daedalus"


def test_issue_runner_doctor_checks_auth_for_configured_github_host(monkeypatch, tmp_path):
    from workflows.contract import render_workflow_markdown
    from workflows.issue_runner.workspace import load_workspace_from_config

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    workflow_root = tmp_path / "wf"
    workflow_root.mkdir()
    cfg = _github_config(repo_path)
    cfg["repository"]["slug"] = "attmous/daedalus"
    cfg["tracker"]["github_slug"] = "github.example.com/attmous/daedalus"
    (workflow_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(config=cfg, prompt_template="Issue: {{ issue.identifier }}"),
        encoding="utf-8",
    )

    def fake_run_json(command, cwd=None):
        assert cwd == repo_path
        if command[:3] == ["gh", "auth", "status"]:
            assert command[3:5] == ["--hostname", "github.example.com"]
            return {
                "hosts": {
                    "github.example.com": [
                        {"state": "success", "active": True, "login": "enterprise-user"}
                    ]
                }
            }
        if command[:3] == ["gh", "repo", "view"]:
            assert command[3] == "github.example.com/attmous/daedalus"
            return {"nameWithOwner": "attmous/daedalus"}
        if command[:3] == ["gh", "issue", "list"]:
            return []
        raise AssertionError(f"unexpected command: {command}")

    workspace = load_workspace_from_config(
        workspace_root=workflow_root,
        run_json=fake_run_json,
    )

    payload = workspace.doctor()
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["ok"] is True
    assert checks["github-auth"]["detail"] == "gh authenticated as enterprise-user on github.example.com"
    assert checks["github-repo"]["detail"] == "attmous/daedalus"
