from __future__ import annotations

import json
import threading
from types import SimpleNamespace


def test_runtime_stage_runs_command_runtime_with_prompt_file_and_callbacks(tmp_path):
    from runtimes import PromptRunResult, SessionHandle
    from runtimes.stages import prompt_result_from_stage, run_runtime_stage

    calls = {}

    class FakeRuntime:
        def set_cancel_event(self, event):
            calls.setdefault("cancel_events", []).append(event)

        def set_progress_callback(self, callback):
            calls.setdefault("progress_callbacks", []).append(callback)

        def ensure_session(self, **kwargs):
            calls["ensure"] = kwargs
            return SessionHandle(record_id="rec-1", session_id="sess-1", name=kwargs["session_name"])

        def run_command(self, *, worktree, command_argv, env=None):
            calls["command"] = command_argv
            calls["env"] = env
            return "command output"

    cancel_event = threading.Event()
    session_handles = []
    result = run_runtime_stage(
        runtime=FakeRuntime(),
        runtime_cfg={
            "kind": "hermes-agent",
            "command": ["agent", "--model", "{model}", "--prompt", "{prompt_path}", "--issue", "{issue_id}"],
        },
        agent_cfg={"model": "gpt-test", "runtime": "hermes"},
        stage_name="issue-runner",
        worktree=tmp_path,
        session_name="issue-1",
        prompt="do the work",
        env={"A": "B"},
        placeholders={"issue_id": "ISSUE-1"},
        cancel_event=cancel_event,
        progress_callback=lambda _result: None,
        on_session_ready=session_handles.append,
    )

    assert result.output == "command output"
    assert result.used_command is True
    assert calls["ensure"]["model"] == "gpt-test"
    assert calls["cancel_events"][0] is cancel_event
    assert calls["cancel_events"][-1] is None
    assert callable(calls["progress_callbacks"][0])
    assert calls["progress_callbacks"][-1] is None
    assert calls["env"]["A"] == "B"
    assert calls["env"]["DAEDALUS_SESSION_NAME"] == "issue-1"
    assert calls["env"]["DAEDALUS_MODEL"] == "gpt-test"
    assert "DAEDALUS_RESULT_PATH" in calls["env"]
    assert calls["command"][:3] == ["agent", "--model", "gpt-test"]
    assert calls["command"][-1] == "ISSUE-1"
    assert result.prompt_path is not None
    assert result.prompt_path.read_text(encoding="utf-8") == "do the work"
    assert session_handles[0].session_id == "sess-1"
    metrics_source = prompt_result_from_stage(result)
    assert isinstance(metrics_source, PromptRunResult)
    assert metrics_source.tokens == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_runtime_stage_reads_structured_command_result(tmp_path):
    from runtimes.stages import prompt_result_from_stage, run_runtime_stage

    calls = {}

    class FakeRuntime:
        def ensure_session(self, **kwargs):
            return None

        def run_command(self, *, worktree, command_argv, env=None):
            calls["argv"] = command_argv
            calls["env"] = env
            result_path = env["DAEDALUS_RESULT_PATH"]
            with open(result_path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "output": "structured output",
                        "session_id": "hermes-session-1",
                        "thread_id": "hermes-thread-1",
                        "turn_id": "hermes-turn-1",
                        "last_event": "turn/completed",
                        "last_message": "done",
                        "turn_count": 2,
                        "tokens": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
                        "rate_limits": {"requests_remaining": 42},
                    },
                    fh,
                )
            return "plain stdout fallback"

    result = run_runtime_stage(
        runtime=FakeRuntime(),
        runtime_cfg={
            "kind": "hermes-agent",
            "command": ["agent", "--prompt", "{prompt_path}", "--result", "{result_path}"],
        },
        agent_cfg={"model": "m", "runtime": "hermes"},
        stage_name="internal-reviewer",
        worktree=tmp_path,
        session_name="review-1",
        prompt="review",
    )

    assert calls["argv"][-1] == calls["env"]["DAEDALUS_RESULT_PATH"]
    assert result.output == "structured output"
    assert result.result_path is not None
    metrics_source = prompt_result_from_stage(result)
    assert metrics_source.session_id == "hermes-session-1"
    assert metrics_source.thread_id == "hermes-thread-1"
    assert metrics_source.turn_id == "hermes-turn-1"
    assert metrics_source.tokens == {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}
    assert metrics_source.rate_limits == {"requests_remaining": 42}


def test_runtime_stage_runs_prompt_runtime_and_ignores_codex_transport_command(tmp_path):
    from runtimes.stages import prompt_result_from_stage, run_runtime_stage

    calls = {}

    class FakeCodexRuntime:
        def ensure_session(self, **kwargs):
            calls["ensure"] = kwargs

        def run_prompt_result(self, **kwargs):
            calls["prompt"] = kwargs
            return SimpleNamespace(
                output="prompt output",
                session_id="thread-1",
                thread_id="thread-1",
                turn_id="turn-1",
                last_event="turn/completed",
                last_message="done",
                turn_count=1,
                tokens={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                rate_limits={"requests_remaining": 99},
            )

        def run_command(self, **kwargs):
            raise AssertionError("codex app-server transport command must not be treated as stage command")

    result = run_runtime_stage(
        runtime=FakeCodexRuntime(),
        runtime_cfg={"kind": "codex-app-server", "command": ["codex", "app-server"]},
        agent_cfg={"model": "gpt-test", "runtime": "codex"},
        stage_name="coder",
        worktree=tmp_path,
        session_name="lane-1",
        prompt="continue",
        resume_session_id="thread-previous",
    )

    assert result.output == "prompt output"
    assert result.used_command is False
    assert calls["ensure"]["resume_session_id"] == "thread-previous"
    assert calls["prompt"]["prompt"] == "continue"
    metrics_source = prompt_result_from_stage(result)
    assert metrics_source.thread_id == "thread-1"
    assert metrics_source.tokens["total_tokens"] == 3
