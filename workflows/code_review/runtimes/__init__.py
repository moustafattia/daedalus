"""Runtime abstractions for the code-review workflow.

A Runtime encapsulates *how we talk to a model*: persistent ACPX session
management for Codex, one-shot subprocess invocation for Claude CLI,
plain HTTP request/response for future providers like Kimi or Gemini.

Agents in the YAML config reference runtimes by name; the workspace
factory instantiates one Runtime per named profile and exposes them via
``ws.runtime(name)``.

To add a new runtime kind:

1. Create a new module under ``workflows/code_review/runtimes/<kind>.py``
   whose primary export is a class implementing the ``Runtime`` protocol.
2. Register the class in ``_RUNTIME_KINDS`` below (via the ``@register`` decorator).
3. Add a corresponding branch to ``workflows/code_review/schema.yaml``
   so the YAML config validator knows what shape your kind accepts.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


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


@runtime_checkable
class Runtime(Protocol):
    """Protocol every runtime kind implements.

    One-shot runtimes (e.g. claude-cli) implement ensure_session /
    close_session as no-ops and return a synthetic SessionHandle.
    """

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

    def last_activity_ts(self) -> float | None:
        """Monotonic timestamp of the most recent forward-progress signal
        from the running agent. None means either: no signal yet (still in
        startup) or the runtime does not track liveness (opts out of stall
        detection). Symphony §8.5."""
        ...


_RUNTIME_KINDS: dict[str, type] = {}


def register(kind: str):
    """Decorator: registers a class as the implementation for a runtime kind."""

    def _register(cls):
        _RUNTIME_KINDS[kind] = cls
        return cls

    return _register


def build_runtimes(runtimes_cfg: dict, *, run=None, run_json=None) -> dict[str, Runtime]:
    """Instantiate one Runtime per profile in ``runtimes_cfg``.

    ``runtimes_cfg`` is the dict parsed from the YAML ``runtimes:`` section:
    ``{profile-name: {kind: <kind>, ...profile-specific keys...}}``.

    ``run`` / ``run_json`` are workspace-scoped subprocess primitives — the
    runtime implementations accept them via constructor args so tests can
    inject fakes without mocking subprocess globally.

    The concrete runtime classes are imported lazily here so that merely
    importing ``workflows.code_review.runtimes`` does not pull in acpx_codex
    or claude_cli until they're actually needed.
    """
    if not runtimes_cfg:
        return {}
    # Trigger registration side-effects by importing the runtime modules.
    # Deferred until first call so the empty-dict test path stays fast.
    from workflows.code_review.runtimes import acpx_codex  # noqa: F401
    from workflows.code_review.runtimes import claude_cli  # noqa: F401
    from workflows.code_review.runtimes import hermes_agent  # noqa: F401

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
