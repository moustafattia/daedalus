"""One-shot Claude CLI runtime.

No persistent session; ``ensure_session`` and ``close_session`` are no-ops,
``assess_health`` always returns healthy. Each ``run_prompt`` spawns the
``claude`` CLI, feeds the prompt, and returns stdout.

Command shape matches ``reviews.run_inter_review_agent_review`` (the existing
inline invocation) with the call-site-specific flags (``--output-format``,
``--json-schema``) excluded since they are not part of the generic interface:

    claude --model <model>
           --permission-mode bypassPermissions
           --max-turns <max_turns>
           --print <prompt>
"""
from __future__ import annotations

import time
from pathlib import Path

from workflows.change_delivery.runtimes import (
    Runtime,
    SessionHandle,
    SessionHealth,
    register,
)


@register("claude-cli")
class ClaudeCliRuntime:
    """Wraps the ``claude`` CLI for one-shot pre-publish / review invocations.

    Config shape (YAML):
        kind: claude-cli
        max-turns-per-invocation: 24
        timeout-seconds: 1200
    """

    def __init__(self, cfg: dict, *, run, run_json=None):
        self._cfg = cfg
        self._run = run
        self._max_turns = int(cfg.get("max-turns-per-invocation", 24))
        self._timeout = int(cfg.get("timeout-seconds", 1200))
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
        # One-shot runtime: no persistent session to create.
        return SessionHandle(record_id=None, session_id=None, name=session_name)

    def run_prompt(
        self,
        *,
        worktree: Path,
        session_name: str,
        prompt: str,
        model: str,
    ) -> str:
        # Command shape mirrors reviews.run_inter_review_agent_review, omitting
        # the call-site-specific --output-format / --json-schema flags.
        cmd = [
            "claude",
            "--model",
            model,
            "--permission-mode",
            "bypassPermissions",
            "--max-turns",
            str(self._max_turns),
            "--print",
            prompt,
        ]
        # Codex P1 on PR #18: record activity BEFORE the blocking call so
        # stall reconciliation measures from "work started", not from "previous
        # work ended". Otherwise a long-running invocation looks idle for its
        # entire runtime and gets force-killed once stall.timeout_ms passes.
        self._record_activity()
        completed = self._run(cmd, cwd=worktree, timeout=self._timeout)
        self._record_activity()
        return getattr(completed, "stdout", "") or ""

    def assess_health(
        self,
        session_meta: dict | None,
        *,
        worktree: Path | None,
        now_epoch: int | None = None,
    ) -> SessionHealth:
        # One-shot runtime: no persistent session to be unhealthy.
        return SessionHealth(healthy=True, reason=None, last_used_at=None)

    def close_session(self, *, worktree: Path, session_name: str) -> None:
        # One-shot runtime: nothing to close.
        return None

    def run_command(
        self,
        *,
        worktree: Path,
        command_argv: list[str],
        env: dict | None = None,
    ) -> str:
        """Execute a fully-formed argv via the configured timeout."""
        self._record_activity()
        completed = self._run(command_argv, cwd=worktree, timeout=self._timeout)
        self._record_activity()
        return getattr(completed, "stdout", "") or ""
