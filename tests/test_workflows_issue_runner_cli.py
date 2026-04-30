import io
import json
import threading
from contextlib import redirect_stdout
from pathlib import Path

from workflows.contract import render_workflow_markdown


def _config(tmp_path: Path) -> dict:
    return {
        "workflow": "issue-runner",
        "schema-version": 1,
        "instance": {"name": "attmous-daedalus-issue-runner", "engine-owner": "hermes"},
        "repository": {"local-path": str(tmp_path / "repo"), "slug": "attmous/daedalus"},
        "tracker": {
            "kind": "local-json",
            "path": "config/issues.json",
            "active_states": ["todo"],
            "terminal_states": ["done"],
        },
        "polling": {"interval_ms": 1000},
        "workspace": {"root": "workspace/issues"},
        "agent": {
            "name": "Issue_Runner_Agent",
            "model": "gpt-5.4",
            "runtime": "default",
            "max_concurrent_agents": 1,
        },
        "daedalus": {
            "runtimes": {
                "default": {
                    "kind": "hermes-agent",
                    "command": ["fake-agent", "--prompt", "{prompt_path}", "--issue", "{issue_identifier}"],
                }
            }
        },
        "storage": {
            "status": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
        },
    }


def test_issue_runner_cli_run_executes_one_loop_iteration(tmp_path):
    from workflows.issue_runner.cli import main
    from workflows.issue_runner.workspace import load_workspace_from_config

    cfg = _config(tmp_path)
    workflow_root = tmp_path / "attmous-daedalus-issue-runner"
    workflow_root.mkdir()
    (workflow_root / "config").mkdir()
    (workflow_root / "config" / "issues.json").write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "id": "ISSUE-1",
                        "identifier": "ISSUE-1",
                        "title": "First issue",
                        "description": "Do the thing.",
                        "priority": 1,
                        "state": "todo",
                        "branch_name": "issue-1-first-issue",
                        "url": "https://tracker.example/issues/ISSUE-1",
                        "labels": ["sample"],
                        "blocked_by": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workflow_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config=cfg,
            prompt_template="Issue: {{ issue.identifier }}",
        ),
        encoding="utf-8",
    )

    def fake_run(command, *, cwd=None, timeout=None, env=None):
        del command, cwd, timeout, env

        class Result:
            stdout = "agent finished\n"
            stderr = ""
            returncode = 0

        return Result()

    workspace = load_workspace_from_config(
        workspace_root=workflow_root,
        run=fake_run,
        run_json=lambda *args, **kwargs: {},
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main(workspace, ["run", "--json", "--max-iterations", "1"])

    payload = json.loads(buf.getvalue())
    assert exit_code == 0
    assert payload["loop_status"] == "completed"
    assert payload["iterations"] == 1
    assert payload["last_result"]["ok"] is True


def test_issue_runner_cli_serve_uses_shared_status_server(monkeypatch, tmp_path):
    from workflows.issue_runner.cli import main

    class FakeHandle:
        def __init__(self):
            self.port = 8765
            self.thread = threading.Thread(target=lambda: None)
            self.thread.start()

        def shutdown(self):
            return None

    captured = {}

    def fake_start_server(workflow_root, *, port, bind):
        captured["workflow_root"] = workflow_root
        captured["port"] = port
        captured["bind"] = bind
        return FakeHandle()

    monkeypatch.setattr("workflows.change_delivery.server.start_server", fake_start_server)

    workspace = type(
        "Workspace",
        (),
        {
            "path": tmp_path,
            "config": {"server": {"bind": "127.0.0.1", "port": 0}},
        },
    )()

    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main(workspace, ["serve", "--port", "0"])

    out = buf.getvalue()
    assert exit_code == 0
    assert "http://127.0.0.1:8765/" in out
    assert captured["workflow_root"] == tmp_path
