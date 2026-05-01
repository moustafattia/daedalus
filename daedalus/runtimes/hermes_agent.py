from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import PromptRunResult, SessionHandle, SessionHealth, register


@register("hermes-agent")
class HermesAgentRuntime:
    def __init__(self, cfg: dict, *, run, run_json=None):
        self._cfg = cfg
        self._run = run
        self._last_activity: float | None = None
        self._last_result: PromptRunResult | None = None
        self._resume_session_ids: dict[str, str | None] = {}

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
        self._resume_session_ids[session_name] = resume_session_id
        return SessionHandle(record_id=None, session_id=resume_session_id, name=session_name)

    def run_prompt(
        self,
        *,
        worktree: Path,
        session_name: str,
        prompt: str,
        model: str,
    ) -> str:
        return self.run_prompt_result(
            worktree=worktree,
            session_name=session_name,
            prompt=prompt,
            model=model,
        ).output

    def run_prompt_result(
        self,
        *,
        worktree: Path,
        session_name: str,
        prompt: str,
        model: str,
    ) -> PromptRunResult:
        command = self._prompt_command(session_name=session_name, prompt=prompt, model=model)
        self._record_activity()
        completed = self._run(
            command,
            cwd=worktree,
            timeout=self._timeout(),
            env=self._env(model=model, session_name=session_name),
        )
        self._record_activity()
        output = getattr(completed, "stdout", "") or ""
        result = PromptRunResult(
            output=output,
            session_id=self._resume_session_ids.get(session_name),
            thread_id=self._resume_session_ids.get(session_name),
            last_event="turn/completed",
            last_message=(output.strip().splitlines()[-1] if output.strip() else None),
            turn_count=1,
            tokens={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            rate_limits=None,
        )
        self._last_result = result
        return result

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

    def last_result(self) -> PromptRunResult | None:
        return self._last_result

    def run_command(
        self,
        *,
        worktree: Path,
        command_argv: list[str],
        env: dict | None = None,
    ) -> str:
        self._record_activity()
        completed = self._run(command_argv, cwd=worktree, timeout=self._timeout(), env=env)
        self._record_activity()
        return getattr(completed, "stdout", "") or ""

    def _prompt_command(self, *, session_name: str, prompt: str, model: str) -> list[str]:
        mode = str(self._cfg.get("mode") or "final").strip().lower()
        if mode not in {"final", "chat"}:
            raise RuntimeError("hermes-agent mode must be 'final' or 'chat'")
        executable = str(self._cfg.get("executable") or "hermes")
        command = [executable]
        profile = self._cfg.get("profile")
        if profile:
            command.extend(["--profile", str(profile)])
        if self._bool_cfg("yolo"):
            command.append("--yolo")
        if self._bool_cfg("pass-session-id"):
            command.append("--pass-session-id")
        if self._bool_cfg("ignore-user-config"):
            command.append("--ignore-user-config")
        if self._bool_cfg("ignore-rules"):
            command.append("--ignore-rules")

        if mode == "final":
            command.extend(["-z", prompt])
            self._append_common_overrides(command, model=model, include_chat_only=False)
            return command

        command.extend(["chat", "--quiet"])
        resume_session_id = self._resume_session_ids.get(session_name)
        if resume_session_id:
            command.extend(["--resume", str(resume_session_id)])
        elif self._cfg.get("continue"):
            continue_value = self._cfg.get("continue")
            command.append("--continue")
            if not isinstance(continue_value, bool):
                command.append(str(continue_value))
        self._append_common_overrides(command, model=model, include_chat_only=True)
        command.extend(["-q", prompt])
        return command

    def _append_common_overrides(self, command: list[str], *, model: str, include_chat_only: bool) -> None:
        provider = self._cfg.get("provider")
        if provider:
            command.extend(["--provider", str(provider)])
        if model:
            command.extend(["--model", str(model)])
        if not include_chat_only:
            command.extend(str(arg) for arg in self._cfg.get("extra-args") or self._cfg.get("extra_args") or [])
            return
        source = self._cfg.get("source", "daedalus")
        if source:
            command.extend(["--source", str(source)])
        max_turns = self._cfg.get("max-turns", self._cfg.get("max_turns"))
        if max_turns is not None:
            command.extend(["--max-turns", str(max_turns)])
        toolsets = self._cfg.get("toolsets")
        if isinstance(toolsets, list):
            toolsets = ",".join(str(item) for item in toolsets)
        if toolsets:
            command.extend(["--toolsets", str(toolsets)])
        skills = self._cfg.get("skills") or []
        if isinstance(skills, str):
            skills = [skills]
        for skill in skills:
            command.extend(["--skills", str(skill)])
        command.extend(str(arg) for arg in self._cfg.get("extra-args") or self._cfg.get("extra_args") or [])

    def _env(self, *, model: str, session_name: str) -> dict[str, str]:
        env = {
            "DAEDALUS_RUNTIME_KIND": "hermes-agent",
            "DAEDALUS_SESSION_NAME": session_name,
        }
        if model:
            env["DAEDALUS_MODEL"] = model
        return env

    def _timeout(self) -> int | None:
        value = self._cfg.get("timeout-seconds", self._cfg.get("timeout_seconds"))
        if value is None:
            return None
        return int(value)

    def _bool_cfg(self, key: str) -> bool:
        value: Any = self._cfg.get(key, self._cfg.get(key.replace("-", "_")))
        return bool(value)
