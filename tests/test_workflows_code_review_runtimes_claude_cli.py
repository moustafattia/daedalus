from pathlib import Path


def _make_runtime(**cfg_overrides):
    from workflows.change_delivery.runtimes.claude_cli import ClaudeCliRuntime

    cfg = {
        "kind": "claude-cli",
        "max-turns-per-invocation": 24,
        "timeout-seconds": 1200,
        **cfg_overrides,
    }
    calls = []

    def fake_run(cmd, cwd=None, **kwargs):
        calls.append(("run", cmd, str(cwd) if cwd else None, kwargs))
        class R:
            stdout = "claude said hi"
            stderr = ""
            returncode = 0
        return R()

    return ClaudeCliRuntime(cfg, run=fake_run, run_json=None), calls


def test_ensure_session_is_a_noop_and_returns_synthetic_handle(tmp_path):
    runtime, calls = _make_runtime()
    handle = runtime.ensure_session(
        worktree=tmp_path,
        session_name="inter-review-agent:abc",
        model="claude-sonnet-4-6",
    )
    assert calls == []
    assert handle.name == "inter-review-agent:abc"
    assert handle.session_id is None
    assert handle.record_id is None


def test_close_session_is_a_noop(tmp_path):
    runtime, calls = _make_runtime()
    runtime.close_session(worktree=tmp_path, session_name="anything")
    assert calls == []


def test_assess_health_is_always_healthy_for_oneshot_runtime(tmp_path):
    runtime, _ = _make_runtime()
    health = runtime.assess_health({}, worktree=tmp_path)
    assert health.healthy is True


def test_run_prompt_invokes_claude_cli_with_model(tmp_path):
    runtime, calls = _make_runtime()
    out = runtime.run_prompt(
        worktree=tmp_path,
        session_name="inter-review-agent:abc",
        prompt="review this",
        model="claude-sonnet-4-6",
    )
    assert out == "claude said hi"
    run_calls = [c for c in calls if c[0] == "run"]
    assert run_calls
    cmd = run_calls[0][1]
    assert cmd[0] == "claude"
    # Model must appear in the command (as a flag value, not as a positional)
    assert "claude-sonnet-4-6" in cmd
    # The prompt must be passed through somehow (either as a positional arg
    # at the end or via --print)
    assert "review this" in cmd
