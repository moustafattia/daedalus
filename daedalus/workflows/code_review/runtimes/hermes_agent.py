"""Hermes-agent runtime adapter.

One-shot, no persistent session: ``ensure_session`` / ``close_session`` are
no-ops, ``assess_health`` always returns healthy. The actual command is
supplied by the operator via ``command:`` on the runtime profile or the
agent role; this adapter only provides session plumbing (none) and the
``run_command`` execution path.
"""
from __future__ import annotations

import time
from pathlib import Path

from workflows.code_review.runtimes import (
    SessionHandle,
    SessionHealth,
    register,
)


@register("hermes-agent")
class HermesAgentRuntime:
    """Runs prompts by invoking a hermes-agent CLI defined in config.

    Config shape (YAML):
        kind: hermes-agent
        command: ["hermes-agent", "run", "--workspace", "{worktree}",
                  "--model", "{model}", "--prompt-file", "{prompt_path}"]
    """

    def __init__(self, cfg: dict, *, run, run_json=None):
        self._cfg = cfg
        self._run = run
        self._last_activity: float | None = None

    def _record_activity(self) -> None:
        self._last_activity = time.monotonic()

    def last_activity_ts(self) -> float | None:
        return self._last_activity

    def ensure_session(
        self,
        *,
        worktree: Path,
        session_name: str,
        model: str,
        resume_session_id: str | None = None,
    ) -> SessionHandle:
        return SessionHandle(record_id=None, session_id=None, name=session_name)

    def run_prompt(
        self,
        *,
        worktree: Path,
        session_name: str,
        prompt: str,
        model: str,
    ) -> str:
        # No built-in prompt path — operators must supply `command:` to use
        # this runtime. Surface the misconfiguration clearly.
        raise RuntimeError(
            "hermes-agent runtime requires a `command:` override on the runtime "
            "profile or agent role; no built-in invocation is provided."
        )

    def assess_health(
        self,
        session_meta: dict | None,
        *,
        worktree: Path | None,
        now_epoch: int | None = None,
    ) -> SessionHealth:
        return SessionHealth(healthy=True, reason=None, last_used_at=None)

    def close_session(self, *, worktree: Path, session_name: str) -> None:
        return None

    def run_command(
        self,
        *,
        worktree: Path,
        command_argv: list[str],
        env: dict | None = None,
    ) -> str:
        # Codex P1 on PR #18: record activity BEFORE the blocking run so a
        # long-running invocation isn't classified as stalled mid-flight.
        self._record_activity()
        completed = self._run(command_argv, cwd=worktree)
        self._record_activity()
        return getattr(completed, "stdout", "") or ""
