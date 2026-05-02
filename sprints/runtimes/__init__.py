"""Shared runtime adapters reused across workflows.

This package owns the runtime backend protocol and the concrete implementations
that know how to talk to Codex, Claude, Hermes Agent, and similar executors.
Workflow packages compose these backends with workflow-specific prompts,
policies, and state machines.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionHandle:
    record_id: str | None
    session_id: str | None
    name: str


@dataclass(frozen=True)
class SessionHealth:
    healthy: bool
    reason: str | None
    last_used_at: str | None


@dataclass(frozen=True)
class PromptRunResult:
    output: str
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    last_event: str | None = None
    last_message: str | None = None
    turn_count: int = 0
    tokens: dict[str, int] | None = None
    rate_limits: dict | None = None


@runtime_checkable
class Runtime(Protocol):
    def ensure_session(
        self,
        *,
        worktree: Path,
        session_name: str,
        model: str,
        resume_session_id: str | None = None,
    ) -> SessionHandle: ...

    def run_prompt(
        self,
        *,
        worktree: Path,
        session_name: str,
        prompt: str,
        model: str,
    ) -> str: ...

    def assess_health(
        self,
        session_meta: dict | None,
        *,
        worktree: Path | None,
        now_epoch: int | None = None,
    ) -> SessionHealth: ...

    def close_session(
        self,
        *,
        worktree: Path,
        session_name: str,
    ) -> None: ...

    def run_command(
        self,
        *,
        worktree: Path,
        command_argv: list[str],
        env: dict[str, str] | None = None,
    ) -> str: ...

    def last_activity_ts(self) -> float | None: ...


def _runtime_classes() -> dict[str, type]:
    from . import claude_cli
    from . import codex_acpx
    from . import codex_app_server
    from . import hermes_agent_cli

    return {
        "acpx-codex": getattr(codex_acpx, "AcpxCodexRuntime", None),
        "claude-cli": getattr(claude_cli, "ClaudeCliRuntime", None),
        "codex-app-server": getattr(codex_app_server, "CodexAppServerRuntime", None),
        "hermes-agent": getattr(hermes_agent_cli, "HermesAgentRuntime", None),
    }


def build_runtimes(
    runtimes_cfg: dict, *, run=None, run_json=None
) -> dict[str, Runtime]:
    if not runtimes_cfg:
        return {}

    runtime_classes = _runtime_classes()
    runner = run or _subprocess_run
    json_runner = run_json or _subprocess_run_json
    out: dict[str, Runtime] = {}
    for profile_name, profile_cfg in runtimes_cfg.items():
        kind = profile_cfg.get("kind")
        if kind not in runtime_classes:
            raise ValueError(
                f"runtime profile {profile_name!r} declares unknown kind={kind!r}; "
                f"supported kinds: {sorted(runtime_classes)}"
            )
        cls = runtime_classes[kind]
        out[profile_name] = cls(profile_cfg, run=runner, run_json=json_runner)
    return out


def recognized_runtime_kinds() -> frozenset[str]:
    return frozenset(_runtime_classes())


def _subprocess_run(
    command: list[str], **kwargs: Any
) -> subprocess.CompletedProcess[str]:
    cwd = kwargs.pop("cwd", None)
    timeout = kwargs.pop("timeout", None)
    env = kwargs.pop("env", None)
    process_env = None if env is None else {**os.environ, **env}
    completed = subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd) if cwd is not None else None,
        timeout=timeout,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            detail or f"runtime command failed with exit code {completed.returncode}"
        )
    return completed


def _subprocess_run_json(command: list[str], **kwargs: Any) -> dict[str, Any]:
    completed = _subprocess_run(command, **kwargs)
    output = completed.stdout.strip()
    payload = json.loads(output) if output else {}
    if not isinstance(payload, dict):
        raise RuntimeError("runtime JSON command must return a JSON object")
    return payload
