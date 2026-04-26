"""Phase A runtime-agnostic tests: hermes-agent adapter + dispatch_agent."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from workflows.code_review.runtimes import _RUNTIME_KINDS, SessionHandle


def test_acpx_runtime_has_run_command():
    from workflows.code_review.runtimes.acpx_codex import AcpxCodexRuntime
    assert hasattr(AcpxCodexRuntime, "run_command")


def test_claude_cli_runtime_has_run_command():
    from workflows.code_review.runtimes.claude_cli import ClaudeCliRuntime
    assert hasattr(ClaudeCliRuntime, "run_command")


def test_acpx_run_command_invokes_run(tmp_path):
    from workflows.code_review.runtimes.acpx_codex import AcpxCodexRuntime

    fake_run = MagicMock(return_value=MagicMock(stdout="hello"))
    rt = AcpxCodexRuntime(
        {
            "kind": "acpx-codex",
            "session-idle-freshness-seconds": 900,
            "session-idle-grace-seconds": 1800,
            "session-nudge-cooldown-seconds": 600,
        },
        run=fake_run,
        run_json=MagicMock(),
    )
    out = rt.run_command(worktree=tmp_path, command_argv=["acpx", "echo", "hi"])
    assert out == "hello"
    fake_run.assert_called_once()
    args, kwargs = fake_run.call_args
    assert args[0] == ["acpx", "echo", "hi"]
    assert kwargs.get("cwd") == tmp_path


def test_claude_cli_run_command_invokes_run(tmp_path):
    from workflows.code_review.runtimes.claude_cli import ClaudeCliRuntime

    fake_run = MagicMock(return_value=MagicMock(stdout="ok"))
    rt = ClaudeCliRuntime(
        {"kind": "claude-cli", "max-turns-per-invocation": 24, "timeout-seconds": 1200},
        run=fake_run,
    )
    out = rt.run_command(worktree=tmp_path, command_argv=["claude", "--print", "hi"])
    assert out == "ok"
    fake_run.assert_called_once()
    args, kwargs = fake_run.call_args
    assert args[0] == ["claude", "--print", "hi"]
    assert kwargs.get("cwd") == tmp_path


def test_hermes_agent_runtime_registered():
    # Trigger registration
    from workflows.code_review.runtimes import hermes_agent  # noqa: F401
    assert "hermes-agent" in _RUNTIME_KINDS


def test_hermes_agent_run_command(tmp_path):
    from workflows.code_review.runtimes.hermes_agent import HermesAgentRuntime

    fake_run = MagicMock(return_value=MagicMock(stdout="agent-out"))
    rt = HermesAgentRuntime({"kind": "hermes-agent"}, run=fake_run, run_json=None)
    out = rt.run_command(
        worktree=tmp_path,
        command_argv=["hermes-agent", "run", "--workspace", str(tmp_path)],
    )
    assert out == "agent-out"


def test_hermes_agent_ensure_session_is_noop(tmp_path):
    from workflows.code_review.runtimes.hermes_agent import HermesAgentRuntime

    rt = HermesAgentRuntime({"kind": "hermes-agent"}, run=MagicMock(), run_json=None)
    handle = rt.ensure_session(
        worktree=tmp_path, session_name="x", model="m"
    )
    assert handle.record_id is None
    assert handle.session_id is None
    assert handle.name == "x"


def test_hermes_agent_assess_health_always_healthy(tmp_path):
    from workflows.code_review.runtimes.hermes_agent import HermesAgentRuntime

    rt = HermesAgentRuntime({"kind": "hermes-agent"}, run=MagicMock(), run_json=None)
    h = rt.assess_health(None, worktree=tmp_path)
    assert h.healthy is True


def test_build_runtimes_accepts_hermes_agent():
    from workflows.code_review.runtimes import build_runtimes

    cfg = {"hermes-default": {"kind": "hermes-agent"}}
    rts = build_runtimes(cfg, run=MagicMock(), run_json=MagicMock())
    assert "hermes-default" in rts


def _make_workspace(tmp_path, agents_cfg, runtimes_cfg, fake_run, *, workspace_dir=None):
    """Build a minimal workspace stand-in for dispatcher tests."""
    from workflows.code_review.runtimes import build_runtimes

    runtimes = build_runtimes(runtimes_cfg, run=fake_run, run_json=MagicMock())
    cfg = {"agents": agents_cfg, "runtimes": runtimes_cfg}
    ws = MagicMock()
    ws.config = cfg
    ws.runtime = lambda name: runtimes[name]
    ws.path = workspace_dir or tmp_path
    return ws


def _runtimes_cfg():
    return {
        "codex-acpx": {
            "kind": "acpx-codex",
            "session-idle-freshness-seconds": 900,
            "session-idle-grace-seconds": 1800,
            "session-nudge-cooldown-seconds": 600,
            "command": ["acpx", "--model", "{model}", "--cwd", "{worktree}",
                        "codex", "prompt", "-s", "{session_name}", "{prompt_path}"],
        },
    }


def test_dispatch_agent_substitutes_placeholders(tmp_path):
    from workflows.code_review.dispatch import dispatch_agent

    fake_run = MagicMock(return_value=MagicMock(stdout="ok"))
    agents = {
        "coder": {
            "default": {"name": "c", "model": "gpt-5", "runtime": "codex-acpx"},
        },
    }
    ws = _make_workspace(tmp_path, agents, _runtimes_cfg(), fake_run)
    out = dispatch_agent(
        workspace=ws, role="coder", tier="default",
        rendered_prompt="hi", session_name="lane-42", worktree=tmp_path,
    )
    assert out == "ok"
    argv = fake_run.call_args[0][0]
    assert "gpt-5" in argv
    assert str(tmp_path) in argv
    assert "lane-42" in argv
    # one element should be the rendered-prompt file path
    prompt_files = [a for a in argv if a.endswith(".txt")]
    assert len(prompt_files) == 1
    assert Path(prompt_files[0]).read_text() == "hi"


def test_dispatch_agent_unknown_role_raises(tmp_path):
    from workflows.code_review.dispatch import dispatch_agent, DispatchConfigError

    ws = _make_workspace(tmp_path, {"coder": {}}, _runtimes_cfg(), MagicMock())
    with pytest.raises(DispatchConfigError):
        dispatch_agent(
            workspace=ws, role="nonexistent",
            rendered_prompt="x", session_name="s", worktree=tmp_path,
        )


def test_dispatch_agent_uses_runtime_default_when_no_override(tmp_path):
    """Agent without command: -> runtime profile's command."""
    from workflows.code_review.dispatch import dispatch_agent

    fake_run = MagicMock(return_value=MagicMock(stdout="ok"))
    agents = {"coder": {"default": {"name": "c", "model": "m", "runtime": "codex-acpx"}}}
    ws = _make_workspace(tmp_path, agents, _runtimes_cfg(), fake_run)
    dispatch_agent(
        workspace=ws, role="coder", tier="default",
        rendered_prompt="x", session_name="s", worktree=tmp_path,
    )
    argv = fake_run.call_args[0][0]
    assert argv[0] == "acpx"  # runtime default kicked in


def test_dispatch_agent_role_command_overrides_runtime(tmp_path):
    """Agent's command: fully replaces runtime command."""
    from workflows.code_review.dispatch import dispatch_agent

    fake_run = MagicMock(return_value=MagicMock(stdout="ok"))
    agents = {
        "coder": {
            "default": {
                "name": "c", "model": "m", "runtime": "codex-acpx",
                "command": ["my-tool", "--prompt", "{prompt_path}"],
            },
        },
    }
    ws = _make_workspace(tmp_path, agents, _runtimes_cfg(), fake_run)
    dispatch_agent(
        workspace=ws, role="coder", tier="default",
        rendered_prompt="x", session_name="s", worktree=tmp_path,
    )
    argv = fake_run.call_args[0][0]
    assert argv[0] == "my-tool"


def test_dispatch_agent_resolves_workspace_prompt_override(tmp_path):
    """When <workspace>/config/prompts/<role>.md exists, dispatcher picks it."""
    from workflows.code_review.dispatch import resolve_prompt_template_path

    cfg_dir = tmp_path / "config"
    (cfg_dir / "prompts").mkdir(parents=True)
    custom = cfg_dir / "prompts" / "coder.md"
    custom.write_text("workspace override")
    ws = MagicMock()
    ws.path = tmp_path
    ws.config = {"agents": {"coder": {"default": {"runtime": "codex-acpx"}}}}
    p = resolve_prompt_template_path(workspace=ws, role="coder", agent_cfg={})
    assert p == custom


def test_dispatch_agent_resolves_explicit_prompt_path(tmp_path):
    """Agent's `prompt:` key wins over workspace override."""
    from workflows.code_review.dispatch import resolve_prompt_template_path

    cfg_dir = tmp_path / "config"
    (cfg_dir / "prompts").mkdir(parents=True)
    (cfg_dir / "prompts" / "coder.md").write_text("workspace")
    explicit = tmp_path / "explicit-coder.md"
    explicit.write_text("explicit")
    ws = MagicMock()
    ws.path = tmp_path
    ws.config = {"agents": {}}
    p = resolve_prompt_template_path(
        workspace=ws, role="coder",
        agent_cfg={"prompt": str(explicit)},
    )
    assert p == explicit


def test_dispatch_agent_falls_back_to_bundled(tmp_path):
    """No explicit, no workspace override -> bundled default."""
    from workflows.code_review.dispatch import resolve_prompt_template_path

    ws = MagicMock()
    ws.path = tmp_path
    ws.config = {"agents": {}}
    p = resolve_prompt_template_path(workspace=ws, role="coder", agent_cfg={})
    assert p.name == "coder.md"
    assert "workflows/code_review/prompts" in str(p)


def test_dispatch_agent_legacy_fallback_calls_run_prompt(tmp_path):
    """When neither agent nor runtime has command:, dispatcher calls runtime.run_prompt."""
    from workflows.code_review.dispatch import dispatch_agent

    runtimes_cfg = {
        "codex-acpx": {
            "kind": "acpx-codex",
            "session-idle-freshness-seconds": 900,
            "session-idle-grace-seconds": 1800,
            "session-nudge-cooldown-seconds": 600,
            # no command: here
        },
    }
    agents = {"coder": {"default": {"name": "c", "model": "m", "runtime": "codex-acpx"}}}

    fake_run = MagicMock(return_value=MagicMock(stdout="should-not-be-called"))
    ws = _make_workspace(tmp_path, agents, runtimes_cfg, fake_run)

    # Stub run_prompt on the resolved runtime
    rt = ws.runtime("codex-acpx")
    rt.run_prompt = MagicMock(return_value="legacy-output")

    out = dispatch_agent(
        workspace=ws, role="coder", tier="default",
        rendered_prompt="hi", session_name="s", worktree=tmp_path,
    )
    assert out == "legacy-output"
    rt.run_prompt.assert_called_once()
    fake_run.assert_not_called()


def test_dispatch_agent_raises_when_no_bundled_prompt_exists(tmp_path):
    """When command: is set but no prompt template is found anywhere, raise DispatchConfigError."""
    from workflows.code_review.dispatch import resolve_prompt_template_path, DispatchConfigError

    ws = MagicMock()
    ws.path = tmp_path  # no <tmp_path>/config/prompts/madeup-role.md
    ws.config = {"agents": {}}
    with pytest.raises(DispatchConfigError, match="no prompt template found"):
        resolve_prompt_template_path(workspace=ws, role="madeup-role", agent_cfg={})
