"""Run one Daedalus actor turn through a configured runtime.

A Daedalus turn is one prompt/result exchange at the runtime boundary. Some
backends, such as Codex app-server, implement that with their own protocol
turns (`turn/start`, `turn/completed`), but this module stays backend-neutral.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import PromptRunResult


@dataclass(frozen=True)
class RuntimeStageResult:
    output: str
    prompt_path: Path | None
    command_argv: list[str] | None
    runtime_result: Any
    session_handle: Any
    result_path: Path | None = None

    @property
    def used_command(self) -> bool:
        return self.command_argv is not None


def command_output_result(output: str) -> PromptRunResult:
    return PromptRunResult(
        output=output,
        tokens={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        rate_limits=None,
    )


def prompt_result_from_payload(payload: dict[str, Any], *, fallback_output: str = "") -> PromptRunResult:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    output = payload.get("output")
    if output is None:
        output = payload.get("text")
    if output is None:
        output = fallback_output
    tokens = payload.get("tokens")
    if tokens is None:
        tokens = payload.get("token_usage") or metrics.get("tokens")
    rate_limits = payload.get("rate_limits")
    if rate_limits is None:
        rate_limits = metrics.get("rate_limits")
    turn_count = payload.get("turn_count")
    if turn_count is None:
        turn_count = metrics.get("turn_count") or 0
    return PromptRunResult(
        output=str(output or ""),
        session_id=_first_str(payload, metrics, "session_id", "sessionId"),
        thread_id=_first_str(payload, metrics, "thread_id", "threadId"),
        turn_id=_first_str(payload, metrics, "turn_id", "turnId"),
        last_event=_first_str(payload, metrics, "last_event", "lastEvent"),
        last_message=_first_str(payload, metrics, "last_message", "lastMessage"),
        turn_count=int(turn_count or 0),
        tokens=tokens if isinstance(tokens, dict) else None,
        rate_limits=rate_limits if isinstance(rate_limits, dict) else None,
    )


def load_structured_result(path: Path, *, fallback_output: str = "") -> PromptRunResult | None:
    path = Path(path)
    if not path.exists() or path.stat().st_size <= 0:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"structured runtime result must be a JSON object: {path}")
    return prompt_result_from_payload(payload, fallback_output=fallback_output)


def prompt_result_from_stage(result: RuntimeStageResult) -> PromptRunResult:
    runtime_result = result.runtime_result
    if isinstance(runtime_result, PromptRunResult):
        return runtime_result
    if all(hasattr(runtime_result, name) for name in ("output", "tokens", "rate_limits")):
        return runtime_result
    return command_output_result(result.output)


def raw_output_from_runtime_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    output = getattr(result, "output", None)
    if output is not None:
        return str(output)
    stdout = getattr(result, "stdout", None)
    if stdout is not None:
        return str(stdout)
    return str(result or "")


def resolve_stage_command(*, agent_cfg: dict[str, Any], runtime_cfg: dict[str, Any]) -> list[str] | None:
    command = agent_cfg.get("command")
    if command:
        return _ensure_argv(command)

    if runtime_cfg.get("stage-command") is False or runtime_cfg.get("stage_command") is False:
        return None

    command_role = str(runtime_cfg.get("command-role") or runtime_cfg.get("command_role") or "stage").strip()
    if command_role != "stage":
        return None

    command = runtime_cfg.get("command")
    if not command:
        return None
    return _ensure_argv(command)


def materialize_prompt(*, worktree: Path, stage_name: str, prompt: str, prompt_path: Path | None = None) -> Path:
    if prompt_path is None:
        out_dir = Path(worktree) / ".daedalus" / "dispatch"
        out_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
        prompt_path = out_dir / f"{stage_name}-{digest}.txt"
    else:
        prompt_path = Path(prompt_path)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def runtime_result_path(*, worktree: Path, stage_name: str, prompt: str, prompt_path: Path | None = None) -> Path:
    if prompt_path is not None:
        return Path(prompt_path).with_suffix(".result.json")
    out_dir = Path(worktree) / ".daedalus" / "dispatch"
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    return out_dir / f"{stage_name}-{digest}.result.json"


def substitute_command_placeholders(argv: list[str], values: dict[str, str]) -> list[str]:
    resolved = []
    for arg in argv:
        text = str(arg)
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        resolved.append(text)
    return resolved


def run_runtime_stage(
    *,
    runtime: Any,
    runtime_cfg: dict[str, Any],
    agent_cfg: dict[str, Any],
    stage_name: str,
    worktree: Path,
    session_name: str,
    prompt: str,
    prompt_path: Path | None = None,
    env: dict[str, str] | None = None,
    placeholders: dict[str, str] | None = None,
    resume_session_id: str | None = None,
    cancel_event: Any | None = None,
    progress_callback: Callable[[Any], None] | None = None,
    on_session_ready: Callable[[Any], None] | None = None,
) -> RuntimeStageResult:
    """Run one workflow stage through a configured runtime profile.

    Workflows own policy, prompts, and state transitions. This helper owns the
    common runtime boundary: session setup, command overrides, prompt-file
    materialization, placeholder substitution, cancellation/progress hooks, and
    a normalized output/result shape.
    """
    worktree = Path(worktree)
    model = str(agent_cfg.get("model") or "")
    command = resolve_stage_command(agent_cfg=agent_cfg, runtime_cfg=runtime_cfg)
    set_cancel_event = getattr(runtime, "set_cancel_event", None)
    set_progress_callback = getattr(runtime, "set_progress_callback", None)
    if callable(set_cancel_event):
        set_cancel_event(cancel_event)
    if callable(set_progress_callback):
        set_progress_callback(progress_callback)

    session_handle = None
    try:
        ensure_session = getattr(runtime, "ensure_session", None)
        if callable(ensure_session):
            session_handle = ensure_session(
                worktree=worktree,
                session_name=session_name,
                model=model,
                resume_session_id=resume_session_id,
            )
        if on_session_ready is not None:
            on_session_ready(session_handle)

        if command is not None:
            resolved_prompt_path = materialize_prompt(
                worktree=worktree,
                stage_name=stage_name,
                prompt=prompt,
                prompt_path=prompt_path,
            )
            resolved_result_path = runtime_result_path(
                worktree=worktree,
                stage_name=stage_name,
                prompt=prompt,
                prompt_path=resolved_prompt_path,
            )
            if resolved_result_path.exists():
                resolved_result_path.unlink()
            argv = substitute_command_placeholders(
                command,
                {
                    "model": model,
                    "prompt": prompt,
                    "prompt_path": str(resolved_prompt_path),
                    "result_path": str(resolved_result_path),
                    "worktree": str(worktree),
                    "session_name": session_name,
                    **(placeholders or {}),
                },
            )
            stage_env = dict(env or {})
            stage_env.setdefault("DAEDALUS_PROMPT_PATH", str(resolved_prompt_path))
            stage_env.setdefault("DAEDALUS_RESULT_PATH", str(resolved_result_path))
            stage_env.setdefault("DAEDALUS_WORKTREE", str(worktree))
            stage_env.setdefault("DAEDALUS_SESSION_NAME", session_name)
            stage_env.setdefault("DAEDALUS_MODEL", model)
            output = raw_output_from_runtime_result(
                runtime.run_command(worktree=worktree, command_argv=argv, env=stage_env)
            )
            runtime_result = (
                load_structured_result(resolved_result_path, fallback_output=output)
                or command_output_result(output)
            )
            return RuntimeStageResult(
                output=runtime_result.output,
                prompt_path=resolved_prompt_path,
                command_argv=argv,
                runtime_result=runtime_result,
                session_handle=session_handle,
                result_path=resolved_result_path,
            )

        runner = getattr(runtime, "run_prompt_result", None)
        if callable(runner):
            runtime_result = runner(
                worktree=worktree,
                session_name=session_name,
                prompt=prompt,
                model=model,
            )
        else:
            runtime_result = runtime.run_prompt(
                worktree=worktree,
                session_name=session_name,
                prompt=prompt,
                model=model,
            )
        output = raw_output_from_runtime_result(runtime_result)
        return RuntimeStageResult(
            output=output,
            prompt_path=prompt_path,
            command_argv=None,
            runtime_result=runtime_result,
            session_handle=session_handle,
        )
    finally:
        if callable(set_progress_callback):
            set_progress_callback(None)
        if callable(set_cancel_event):
            set_cancel_event(None)


def _ensure_argv(command: Any) -> list[str]:
    if not isinstance(command, list) or not command:
        raise RuntimeError("agent.command and runtime command must be a non-empty argv list")
    return [str(part) for part in command]


def _first_str(primary: dict[str, Any], secondary: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = primary.get(key)
        if value is None:
            value = secondary.get(key)
        if value is not None:
            return str(value)
    return None
