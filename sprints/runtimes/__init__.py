"""Shared runtime adapters reused across workflows.

This package owns the runtime backend protocol and the concrete implementations
that know how to talk to Codex, Claude, Hermes Agent, and similar executors.
Workflow packages compose these backends with workflow-specific prompts,
policies, and state machines.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

BUILTIN_RUNTIME_KINDS = frozenset(
    {
        "acpx-codex",
        "claude-cli",
        "codex-app-server",
        "hermes-agent",
    }
)


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


_RUNTIME_KINDS: dict[str, type] = {}


def register(kind: str):
    def _register(cls):
        _RUNTIME_KINDS[kind] = cls
        return cls

    return _register


def _ensure_builtin_runtimes_registered() -> None:
    """Populate built-ins even if module reloading left decorator state stale."""
    from . import claude_cli
    from . import codex_acpx
    from . import codex_app_server
    from . import hermes_agent_cli

    builtins = {
        "acpx-codex": getattr(codex_acpx, "AcpxCodexRuntime", None),
        "claude-cli": getattr(claude_cli, "ClaudeCliRuntime", None),
        "codex-app-server": getattr(codex_app_server, "CodexAppServerRuntime", None),
        "hermes-agent": getattr(hermes_agent_cli, "HermesAgentRuntime", None),
    }
    for kind, cls in builtins.items():
        if cls is not None:
            _RUNTIME_KINDS.setdefault(kind, cls)


def build_runtimes(
    runtimes_cfg: dict, *, run=None, run_json=None
) -> dict[str, Runtime]:
    if not runtimes_cfg:
        return {}

    _ensure_builtin_runtimes_registered()

    out: dict[str, Runtime] = {}
    for profile_name, profile_cfg in runtimes_cfg.items():
        kind = profile_cfg.get("kind")
        if kind not in _RUNTIME_KINDS:
            raise ValueError(
                f"runtime profile {profile_name!r} declares unknown kind={kind!r}; "
                f"registered kinds: {sorted(_RUNTIME_KINDS)}"
            )
        cls = _RUNTIME_KINDS[kind]
        out[profile_name] = cls(profile_cfg, run=run, run_json=run_json)
    return out


def recognized_runtime_kinds() -> frozenset[str]:
    return BUILTIN_RUNTIME_KINDS | frozenset(_RUNTIME_KINDS)
