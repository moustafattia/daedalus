from __future__ import annotations

import inspect
import time
from pathlib import Path

from . import SessionHandle, SessionHealth


class ClaudeCliRuntime:
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

    def _run_process(
        self, command: list[str], *, worktree: Path, env: dict | None = None
    ):
        kwargs = {"cwd": worktree, "timeout": self._timeout}
        if env is not None:
            kwargs["env"] = env
        try:
            signature = inspect.signature(self._run)
        except (TypeError, ValueError):
            supported_kwargs = kwargs
        else:
            accepts_var_kwargs = any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in signature.parameters.values()
            )
            supported_kwargs = (
                kwargs
                if accepts_var_kwargs
                else {
                    key: value
                    for key, value in kwargs.items()
                    if key in signature.parameters
                }
            )
        return self._run(command, **supported_kwargs)

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
        self._record_activity()
        completed = self._run_process(cmd, worktree=worktree)
        self._record_activity()
        return getattr(completed, "stdout", "") or ""

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
        self._record_activity()
        completed = self._run_process(command_argv, worktree=worktree, env=env)
        self._record_activity()
        return getattr(completed, "stdout", "") or ""
