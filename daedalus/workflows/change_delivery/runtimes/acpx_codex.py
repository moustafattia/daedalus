"""Persistent-session runtime for Codex via `acpx codex`."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from workflows.change_delivery.runtimes import (
    Runtime,
    SessionHandle,
    SessionHealth,
    register,
)


@register("acpx-codex")
class AcpxCodexRuntime:
    """Wraps the `acpx codex` CLI to manage long-lived Codex sessions.

    Config shape (YAML):
        kind: acpx-codex
        session-idle-freshness-seconds: 900
        session-idle-grace-seconds: 1800
        session-nudge-cooldown-seconds: 600
    """

    def __init__(self, cfg: dict, *, run, run_json):
        self._cfg = cfg
        self._run = run
        self._run_json = run_json
        self._freshness = int(cfg.get("session-idle-freshness-seconds", 900))
        self._grace = int(cfg.get("session-idle-grace-seconds", 1800))
        self._nudge_cooldown = int(cfg.get("session-nudge-cooldown-seconds", 600))
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
        # Mirrors workflows.change_delivery.sessions.ensure_acpx_session exactly.
        # Global flags (--model, --format, --json-strict, --cwd) precede the
        # subcommand path "codex sessions ensure".
        cmd = [
            "acpx",
            "--model",
            model,
            "--format",
            "json",
            "--json-strict",
            "--cwd",
            str(worktree),
            "codex",
            "sessions",
            "ensure",
            "--name",
            session_name,
        ]
        if resume_session_id:
            cmd.extend(["--resume-session", resume_session_id])
        payload = self._run_json(cmd, cwd=worktree)
        return SessionHandle(
            record_id=payload.get("acpxRecordId") or payload.get("acpx_record_id"),
            session_id=payload.get("acpxSessionId") or payload.get("acpSessionId"),
            name=payload.get("name") or session_name,
        )

    def run_prompt(
        self,
        *,
        worktree: Path,
        session_name: str,
        prompt: str,
        model: str,
    ) -> str:
        # Mirrors workflows.change_delivery.sessions.run_acpx_prompt exactly.
        # Global flags before "codex prompt -s <name> <prompt>".
        cmd = [
            "acpx",
            "--model",
            model,
            "--approve-all",
            "--format",
            "quiet",
            "--cwd",
            str(worktree),
            "codex",
            "prompt",
            "-s",
            session_name,
            prompt,
        ]
        # Codex P1 on PR #18: record activity BEFORE the blocking run so a
        # long-running invocation isn't classified as stalled mid-flight.
        self._record_activity()
        completed = self._run(cmd, cwd=worktree)
        self._record_activity()
        return getattr(completed, "stdout", "") or ""

    def assess_health(
        self,
        session_meta: dict | None,
        *,
        worktree: Path | None,
        now_epoch: int | None = None,
    ) -> SessionHealth:
        if session_meta is None:
            return SessionHealth(healthy=False, reason="missing-session-meta", last_used_at=None)
        if session_meta.get("closed"):
            return SessionHealth(
                healthy=False,
                reason="session-closed",
                last_used_at=session_meta.get("last_used_at"),
            )
        from workflows.change_delivery.sessions import assess_codex_session_health

        legacy_health = assess_codex_session_health(
            session_meta,
            worktree,
            now_epoch=now_epoch,
            freshness_seconds=self._freshness,
            poke_grace_seconds=self._grace,
        )
        return SessionHealth(
            healthy=bool(legacy_health.get("healthy")),
            reason=legacy_health.get("reason"),
            last_used_at=legacy_health.get("lastUsedAt"),
        )

    def close_session(self, *, worktree: Path, session_name: str) -> None:
        # Mirrors workflows.change_delivery.sessions.close_acpx_session exactly.
        # session_name is a positional argument (not --name); --cwd is a global flag.
        cmd = [
            "acpx",
            "--cwd",
            str(worktree),
            "codex",
            "sessions",
            "close",
            session_name,
        ]
        self._run(cmd, cwd=worktree)

    def run_command(
        self,
        *,
        worktree: Path,
        command_argv: list[str],
        env: dict | None = None,
    ) -> str:
        """Execute a fully-formed argv against this runtime's working dir.

        Used when an agent role supplies a `command:` override in the workflow contract.
        Session plumbing (ensure/close) is the caller's responsibility.
        """
        completed = self._run(command_argv, cwd=worktree)
        self._record_activity()
        return getattr(completed, "stdout", "") or ""
