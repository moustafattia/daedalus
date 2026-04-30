import json
import shlex
import sys
from pathlib import Path

from workflows.contract import render_workflow_markdown


def _config(tmp_path: Path) -> dict:
    return {
        "workflow": "issue-runner",
        "schema-version": 1,
        "instance": {"name": "attmous-daedalus-issue-runner", "engine-owner": "hermes"},
        "repository": {"local-path": str(tmp_path / "repo"), "github-slug": "attmous/daedalus"},
        "tracker": {
            "kind": "local-json",
            "path": "config/issues.json",
            "active_states": ["todo"],
            "terminal_states": ["done"],
        },
        "workspace": {"root": "workspace/issues"},
        "hooks": {
            "after_create": "echo created > created.txt",
            "before_run": "echo before > before.txt",
            "after_run": "echo after > after.txt",
            "before_remove": "echo removing > removing.txt",
            "timeout_ms": 10000,
        },
        "agent": {
            "name": "Issue_Runner_Agent",
            "model": "gpt-5.4",
            "runtime": "default",
            "max_concurrent_agents": 1,
        },
        "codex": {
            "command": "codex app-server",
            "ephemeral": False,
            "approval_policy": "never",
            "thread_sandbox": "workspace-write",
            "turn_sandbox_policy": "workspace-write",
            "turn_timeout_ms": 3600000,
            "read_timeout_ms": 5000,
            "stall_timeout_ms": 300000,
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


def _write_fake_codex_app_server(path: Path, *, requests_path: Path, fail: bool = False) -> None:
    thread_id = "thread-2" if fail else "thread-1"
    turn_id = "turn-2" if fail else "turn-1"
    input_tokens = 5 if fail else 11
    output_tokens = 2 if fail else 7
    total_tokens = input_tokens + output_tokens
    requests_remaining = 88 if fail else 99
    message_delta = "" if fail else "handled prompt"
    script = [
        "import json",
        "import sys",
        f"requests_path = {str(requests_path)!r}",
        f"thread_id = {thread_id!r}",
        f"turn_id = {turn_id!r}",
        f"input_tokens = {input_tokens!r}",
        f"output_tokens = {output_tokens!r}",
        f"total_tokens = {total_tokens!r}",
        f"requests_remaining = {requests_remaining!r}",
        f"message_delta = {message_delta!r}",
        "",
        "def emit(payload):",
        "    print(json.dumps(payload), flush=True)",
        "",
        "def record(payload):",
        "    with open(requests_path, 'a', encoding='utf-8') as fh:",
        "        fh.write(json.dumps(payload) + '\\n')",
        "",
        "for line in sys.stdin:",
        "    payload = json.loads(line)",
        "    record(payload)",
        "    method = payload.get('method')",
        "    request_id = payload.get('id')",
        "    if method == 'initialize':",
        "        emit({'id': request_id, 'result': {'userAgent': 'fake-codex', 'codexHome': '/tmp/codex'}})",
        "    elif method == 'initialized':",
        "        continue",
        "    elif method == 'thread/start':",
        "        emit({'id': request_id, 'result': {'thread': {'id': thread_id, 'status': 'running', 'turns': []}}})",
        "    elif method == 'thread/resume':",
        "        thread_id = payload.get('params', {}).get('threadId') or thread_id",
        "        emit({'id': request_id, 'result': {'thread': {'id': thread_id, 'status': 'running', 'turns': []}}})",
        "    elif method == 'turn/start':",
        "        turn = {'id': turn_id, 'status': 'running', 'items': []}",
        "        usage_base = {'cachedInputTokens': 0, 'reasoningOutputTokens': 0}",
        "        usage = dict(usage_base)",
        "        usage.update(inputTokens=input_tokens, outputTokens=output_tokens, totalTokens=total_tokens)",
        "        item = {'threadId': thread_id, 'turnId': turn_id, 'itemId': 'item-1'}",
        "        emit({'id': request_id, 'result': {'turn': turn}})",
        "        emit({'method': 'turn/started', 'params': {'threadId': thread_id, 'turn': turn}})",
        "        token_usage = {'last': usage, 'total': usage}",
        "        emit({'method': 'thread/tokenUsage/updated', 'params': {**item, 'tokenUsage': token_usage}})",
        "        rate_limits = {'requests_remaining': requests_remaining}",
        "        emit({'method': 'account/rateLimits/updated', 'params': {'rateLimits': rate_limits}})",
        "        if message_delta:",
        "            emit({'method': 'agent/message_delta', 'params': {**item, 'delta': message_delta}})",
        "            completed_turn = {'id': turn_id, 'status': 'completed', 'items': []}",
        "            emit({'method': 'turn/completed', 'params': {'threadId': thread_id, 'turn': completed_turn}})",
        "            break",
        "        error = {'message': 'tool call rejected'}",
        "        emit({'method': 'error', 'params': {**item, 'willRetry': False, 'error': error}})",
        "        raise SystemExit(1)",
    ]
    path.write_text("\n".join(script) + "\n", encoding="utf-8")


def test_issue_runner_tick_runs_selected_issue_and_writes_artifacts(tmp_path):
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
                    },
                    {
                        "id": "ISSUE-2",
                        "identifier": "ISSUE-2",
                        "title": "Done issue",
                        "description": "Already done.",
                        "priority": 2,
                        "state": "done",
                        "branch_name": "issue-2-done-issue",
                        "url": "https://tracker.example/issues/ISSUE-2",
                        "labels": [],
                        "blocked_by": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (workflow_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config=cfg,
            prompt_template=(
                "Issue: {{ issue.identifier }} - {{ issue.title }}\n"
                "URL: {{ issue.url }}\n"
                "Attempt: {{ attempt }}\n"
                "{{ issue.description }}"
            ),
        ),
        encoding="utf-8",
    )
    stale_terminal_workspace = workflow_root / "workspace" / "issues" / "ISSUE-2"
    stale_terminal_workspace.mkdir(parents=True)
    (stale_terminal_workspace / "stale.txt").write_text("stale\n", encoding="utf-8")

    def fake_run(command, *, cwd=None, timeout=None, env=None):
        if command[:2] == ["bash", "-lc"] and cwd is not None:
            script = command[2]
            if "created.txt" in script:
                (cwd / "created.txt").write_text("created\n", encoding="utf-8")
            if "before.txt" in script:
                (cwd / "before.txt").write_text("before\n", encoding="utf-8")
            if "after.txt" in script:
                (cwd / "after.txt").write_text("after\n", encoding="utf-8")
            if "removing.txt" in script:
                (cwd / "removing.txt").write_text("removing\n", encoding="utf-8")

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

    result = workspace.tick()

    assert result["ok"] is True
    assert result["selectedIssue"]["id"] == "ISSUE-1"
    assert result["results"][0]["retry"]["delay_type"] == "continuation"
    assert result["results"][0]["retry"]["delay_ms"] == 1000
    output_path = Path(result["outputPath"])
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == "agent finished\n"
    prompt_path = output_path.parent / "prompt.txt"
    prompt = prompt_path.read_text(encoding="utf-8")
    assert "ISSUE-1 - First issue" in prompt
    assert "https://tracker.example/issues/ISSUE-1" in prompt
    issue_workspace = Path(result["workspace"])
    assert (issue_workspace / "created.txt").exists()
    assert (issue_workspace / "before.txt").exists()
    assert (issue_workspace / "after.txt").exists()
    assert not (workflow_root / "workspace" / "issues" / "ISSUE-2").exists()
    status = workspace.build_status()
    assert status["selectedIssue"]["id"] == "ISSUE-1"
    assert status["tracker"]["eligibleCount"] == 1
    assert status["scheduler"]["retry_queue"][0]["error"] == "continuation"


def test_issue_runner_tick_uses_codex_app_server_and_persists_metrics(tmp_path):
    from workflows.issue_runner.workspace import load_workspace_from_config

    cfg = _config(tmp_path)
    cfg["agent"].pop("runtime", None)
    cfg.pop("daedalus", None)

    runtime_script = tmp_path / "fake_codex_app_server.py"
    requests_path = tmp_path / "fake_codex_requests.jsonl"
    _write_fake_codex_app_server(runtime_script, requests_path=requests_path)
    cfg["codex"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(runtime_script))}"

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
            prompt_template="Issue: {{ issue.identifier }}\nAttempt: {{ attempt }}",
        ),
        encoding="utf-8",
    )

    workspace = load_workspace_from_config(workspace_root=workflow_root)
    result = workspace.tick()

    assert result["ok"] is True
    assert result["metrics"]["session_id"] == "thread-1"
    assert result["metrics"]["thread_id"] == "thread-1"
    assert result["metrics"]["turn_id"] == "turn-1"
    assert result["metrics"]["tokens"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
    assert result["metrics"]["rate_limits"] == {
        "requests_remaining": 99,
    }
    assert Path(result["outputPath"]).read_text(encoding="utf-8") == "handled prompt\n"
    requests = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
    turn_start = next(item for item in requests if item.get("method") == "turn/start")
    assert turn_start["params"]["input"] == [{"type": "text", "text": "Issue: ISSUE-1\nAttempt:\n"}]
    assert turn_start["params"]["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": [str(workflow_root / "workspace" / "issues" / "ISSUE-1")],
    }

    status = workspace.build_status()
    assert status["metrics"]["tokens"]["total_tokens"] == 18
    assert status["metrics"]["rate_limits"]["requests_remaining"] == 99
    assert status["scheduler"]["codex_threads"]["ISSUE-1"]["thread_id"] == "thread-1"


def test_issue_runner_codex_thread_mapping_persists_and_resumes(tmp_path):
    from workflows.issue_runner.workspace import load_workspace_from_config

    cfg = _config(tmp_path)
    cfg["agent"].pop("runtime", None)
    cfg.pop("daedalus", None)

    runtime_script = tmp_path / "fake_codex_app_server.py"
    requests_path = tmp_path / "fake_codex_requests.jsonl"
    _write_fake_codex_app_server(runtime_script, requests_path=requests_path)
    cfg["codex"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(runtime_script))}"

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
            prompt_template="Issue: {{ issue.identifier }}\nAttempt: {{ attempt }}",
        ),
        encoding="utf-8",
    )

    first_workspace = load_workspace_from_config(workspace_root=workflow_root)
    first = first_workspace.tick()
    assert first["ok"] is True
    assert first_workspace.build_status()["scheduler"]["codex_threads"]["ISSUE-1"]["thread_id"] == "thread-1"

    reloaded = load_workspace_from_config(workspace_root=workflow_root)
    assert reloaded.build_status()["scheduler"]["codex_threads"]["ISSUE-1"]["thread_id"] == "thread-1"
    reloaded.retry_entries["ISSUE-1"]["due_at_epoch"] = 0.0

    second = reloaded.tick()
    assert second["ok"] is True

    requests = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
    methods = [item.get("method") for item in requests]
    assert methods.count("thread/start") == 1
    assert methods.count("thread/resume") == 1
    thread_resume = next(item for item in requests if item.get("method") == "thread/resume")
    assert thread_resume["params"]["threadId"] == "thread-1"


def test_issue_runner_retry_queue_retries_failed_issue_on_next_due_tick(tmp_path):
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
                        "title": "Retry me",
                        "description": "This issue should retry.",
                        "priority": 1,
                        "state": "todo",
                        "branch_name": "issue-1-retry-me",
                        "url": "https://tracker.example/issues/ISSUE-1",
                        "labels": [],
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

    run_calls = {"agent": 0}

    def fake_run(command, *, cwd=None, timeout=None, env=None):
        if command[:2] == ["bash", "-lc"]:
            class HookResult:
                stdout = ""
                stderr = ""
                returncode = 0

            return HookResult()

        run_calls["agent"] += 1
        if run_calls["agent"] == 1:
            raise RuntimeError("temporary agent failure")

        class Result:
            stdout = "agent recovered\n"
            stderr = ""
            returncode = 0

        return Result()

    workspace = load_workspace_from_config(
        workspace_root=workflow_root,
        run=fake_run,
        run_json=lambda *args, **kwargs: {},
    )

    failed = workspace.tick()
    assert failed["ok"] is False
    assert failed["retry"]["retry_attempt"] == 1
    assert failed["retry"]["delay_ms"] == 10000
    assert workspace.build_status()["scheduler"]["retry_queue"]

    workspace.retry_entries["ISSUE-1"]["due_at_monotonic"] = 0.0
    recovered = workspace.tick()
    assert recovered["ok"] is True
    assert recovered["selectedIssue"]["id"] == "ISSUE-1"
    retry_queue = workspace.build_status()["scheduler"]["retry_queue"]
    assert retry_queue[0]["attempt"] == 1
    assert retry_queue[0]["error"] == "continuation"
    assert workspace.build_status()["scheduler"]["codex_totals"]["total_tokens"] == 0


def test_issue_runner_retry_queue_persists_across_workspace_reload(tmp_path):
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
                        "title": "Retry me",
                        "description": "Persist the retry queue.",
                        "priority": 1,
                        "state": "todo",
                        "branch_name": "issue-1-retry-me",
                        "url": "https://tracker.example/issues/ISSUE-1",
                        "labels": [],
                        "blocked_by": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workflow_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(config=cfg, prompt_template="Issue: {{ issue.identifier }}"),
        encoding="utf-8",
    )

    def fail_run(command, *, cwd=None, timeout=None, env=None):
        if command[:2] == ["bash", "-lc"]:
            class HookResult:
                stdout = ""
                stderr = ""
                returncode = 0

            return HookResult()
        raise RuntimeError("temporary agent failure")

    workspace = load_workspace_from_config(
        workspace_root=workflow_root,
        run=fail_run,
        run_json=lambda *args, **kwargs: {},
    )
    failed = workspace.tick()
    assert failed["ok"] is False

    def success_run(command, *, cwd=None, timeout=None, env=None):
        if command[:2] == ["bash", "-lc"]:
            class HookResult:
                stdout = ""
                stderr = ""
                returncode = 0

            return HookResult()

        class Result:
            stdout = "agent recovered\n"
            stderr = ""
            returncode = 0

        return Result()

    reloaded = load_workspace_from_config(
        workspace_root=workflow_root,
        run=success_run,
        run_json=lambda *args, **kwargs: {},
    )
    assert reloaded.build_status()["scheduler"]["retry_queue"]
    reloaded.retry_entries["ISSUE-1"]["due_at_epoch"] = 0.0
    recovered = reloaded.tick()
    assert recovered["ok"] is True
    retry_queue = reloaded.build_status()["scheduler"]["retry_queue"]
    assert retry_queue[0]["attempt"] == 1
    assert retry_queue[0]["error"] == "continuation"


def test_issue_runner_tick_dispatches_batch_up_to_max_concurrent_agents(tmp_path):
    from workflows.issue_runner.workspace import load_workspace_from_config

    cfg = _config(tmp_path)
    cfg["agent"]["max_concurrent_agents"] = 2
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
                        "description": "Do the first thing.",
                        "priority": 1,
                        "state": "todo",
                        "branch_name": "issue-1-first-issue",
                        "url": "https://tracker.example/issues/ISSUE-1",
                        "labels": [],
                        "blocked_by": [],
                    },
                    {
                        "id": "ISSUE-2",
                        "identifier": "ISSUE-2",
                        "title": "Second issue",
                        "description": "Do the second thing.",
                        "priority": 2,
                        "state": "todo",
                        "branch_name": "issue-2-second-issue",
                        "url": "https://tracker.example/issues/ISSUE-2",
                        "labels": [],
                        "blocked_by": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (workflow_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(config=cfg, prompt_template="Issue: {{ issue.identifier }}"),
        encoding="utf-8",
    )

    def fake_run(command, *, cwd=None, timeout=None, env=None):
        if command[:2] == ["bash", "-lc"]:
            class HookResult:
                stdout = ""
                stderr = ""
                returncode = 0

            return HookResult()

        class Result:
            stdout = f"handled {env['ISSUE_IDENTIFIER']}\n"
            stderr = ""
            returncode = 0

        return Result()

    workspace = load_workspace_from_config(
        workspace_root=workflow_root,
        run=fake_run,
        run_json=lambda *args, **kwargs: {},
    )

    result = workspace.tick()

    assert result["ok"] is True
    assert len(result["selectedIssues"]) == 2
    assert len(result["results"]) == 2
    identifiers = {item["issue"]["identifier"] for item in result["results"]}
    assert identifiers == {"ISSUE-1", "ISSUE-2"}
    assert workspace.build_status()["scheduler"]["running"] == []


def test_issue_runner_codex_failure_preserves_partial_metrics(tmp_path):
    from workflows.issue_runner.workspace import load_workspace_from_config

    cfg = _config(tmp_path)
    cfg["agent"].pop("runtime", None)
    cfg.pop("daedalus", None)

    runtime_script = tmp_path / "fake_codex_app_server_fail.py"
    requests_path = tmp_path / "fake_codex_fail_requests.jsonl"
    _write_fake_codex_app_server(runtime_script, requests_path=requests_path, fail=True)
    cfg["codex"]["command"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(runtime_script))}"

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
                        "title": "Fail issue",
                        "description": "This should fail after emitting metrics.",
                        "priority": 1,
                        "state": "todo",
                        "branch_name": "issue-1-fail-issue",
                        "url": "https://tracker.example/issues/ISSUE-1",
                        "labels": [],
                        "blocked_by": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workflow_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(config=cfg, prompt_template="Issue: {{ issue.identifier }}"),
        encoding="utf-8",
    )

    workspace = load_workspace_from_config(workspace_root=workflow_root)
    result = workspace.tick()

    assert result["ok"] is False
    assert result["metrics"]["tokens"]["total_tokens"] == 7
    assert result["metrics"]["rate_limits"]["requests_remaining"] == 88
    assert workspace.build_status()["scheduler"]["codex_totals"]["total_tokens"] == 7


def test_issue_runner_run_loop_keeps_last_known_good_on_invalid_reload(tmp_path):
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
                        "labels": [],
                        "blocked_by": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    workflow_file = workflow_root / "WORKFLOW.md"
    workflow_file.write_text(
        render_workflow_markdown(config=cfg, prompt_template="Issue: {{ issue.identifier }}"),
        encoding="utf-8",
    )

    def fake_run(command, *, cwd=None, timeout=None, env=None):
        if command[:2] == ["bash", "-lc"]:
            class HookResult:
                stdout = ""
                stderr = ""
                returncode = 0

            return HookResult()

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
    workflow_file.write_text("---\nworkflow: [unclosed\n", encoding="utf-8")

    result = workspace.run_loop(interval_seconds=1, max_iterations=1, sleep_fn=lambda _seconds: None)

    assert result["loop_status"] == "completed"
    assert result["last_result"]["ok"] is True
    events = (workflow_root / "memory" / "workflow-audit.jsonl").read_text(encoding="utf-8")
    assert "daedalus.config_reload_failed" in events


def test_issue_runner_rejects_workspace_symlink_escape(tmp_path):
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
                        "title": "Escape issue",
                        "description": "Should not run outside root.",
                        "priority": 1,
                        "state": "todo",
                        "labels": [],
                        "blocked_by": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workflow_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(config=cfg, prompt_template="Issue: {{ issue.identifier }}"),
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    issue_root = workflow_root / "workspace" / "issues"
    issue_root.mkdir(parents=True)
    (issue_root / "ISSUE-1").symlink_to(outside, target_is_directory=True)

    workspace = load_workspace_from_config(
        workspace_root=workflow_root,
        run=lambda *args, **kwargs: None,
        run_json=lambda *args, **kwargs: {},
    )

    result = workspace.tick()

    assert result["ok"] is False
    assert "not a child" in result["error"]
