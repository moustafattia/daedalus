from pathlib import Path


def _make_runtime(**cfg_overrides):
    from workflows.change_delivery.runtimes.acpx_codex import AcpxCodexRuntime

    cfg = {
        "kind": "acpx-codex",
        "session-idle-freshness-seconds": 900,
        "session-idle-grace-seconds": 1800,
        "session-nudge-cooldown-seconds": 600,
        **cfg_overrides,
    }
    calls = []

    def fake_run(cmd, cwd=None, **kwargs):
        calls.append(("run", cmd, str(cwd) if cwd else None))
        class R:
            stdout = ""
            stderr = ""
            returncode = 0
        return R()

    def fake_run_json(cmd, cwd=None, **kwargs):
        calls.append(("run_json", cmd, str(cwd) if cwd else None))
        return {"name": "lane-224", "closed": False, "acpxRecordId": "rec-1", "acpxSessionId": "sess-1"}

    runtime = AcpxCodexRuntime(cfg, run=fake_run, run_json=fake_run_json)
    return runtime, calls


def test_ensure_session_invokes_acpx_with_model_and_session_name(tmp_path):
    runtime, calls = _make_runtime()
    handle = runtime.ensure_session(
        worktree=tmp_path,
        session_name="lane-224",
        model="gpt-5.3-codex-spark/high",
    )
    run_json_calls = [c for c in calls if c[0] == "run_json"]
    assert run_json_calls, "ensure_session must invoke acpx via run_json"
    cmd = run_json_calls[0][1]
    # The command must mention acpx + codex + the session name
    joined = " ".join(cmd)
    assert "acpx" in joined
    assert "codex" in cmd
    assert "lane-224" in joined
    assert handle.name == "lane-224"
    assert handle.record_id == "rec-1"


def test_run_prompt_forwards_prompt_to_acpx_codex(tmp_path):
    runtime, calls = _make_runtime()
    runtime.run_prompt(
        worktree=tmp_path,
        session_name="lane-224",
        prompt="do the thing",
        model="gpt-5.3-codex-spark/high",
    )
    run_calls = [c for c in calls if c[0] == "run"]
    assert run_calls, "run_prompt must invoke acpx via run"
    cmd = run_calls[0][1]
    assert "codex" in cmd
    assert "prompt" in cmd
    # Session name + prompt must appear somewhere in the command
    assert "lane-224" in " ".join(cmd)
    assert "do the thing" in " ".join(cmd)


def test_close_session_invokes_acpx_close(tmp_path):
    runtime, calls = _make_runtime()
    runtime.close_session(worktree=tmp_path, session_name="lane-224")
    run_calls = [c for c in calls if c[0] == "run"]
    assert run_calls, "close_session must call acpx"
    cmd = run_calls[0][1]
    # Either 'close' as an arg, or 'close' in an arg
    assert any("close" in str(p) for p in cmd), f"expected 'close' somewhere in {cmd}"


def test_assess_health_returns_unhealthy_when_meta_is_none(tmp_path):
    runtime, _ = _make_runtime()
    health = runtime.assess_health(None, worktree=tmp_path)
    assert health.healthy is False
    assert health.reason is not None


def test_assess_health_marks_closed_session_unhealthy(tmp_path):
    runtime, _ = _make_runtime()
    meta = {"last_used_at": "2026-04-24T12:00:00Z", "name": "lane-224", "closed": True}
    health = runtime.assess_health(meta, worktree=tmp_path)
    assert health.healthy is False
    assert "closed" in (health.reason or "").lower()


def test_assess_health_returns_session_health_shape_for_open_session(tmp_path):
    runtime, _ = _make_runtime()
    meta = {
        "last_used_at": "2026-04-24T12:00:00Z",
        "name": "lane-224",
        "closed": False,
    }
    health = runtime.assess_health(meta, worktree=tmp_path)
    # The health object has the SessionHealth shape
    assert hasattr(health, "healthy")
    assert hasattr(health, "reason")
    assert hasattr(health, "last_used_at")
