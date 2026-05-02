"""Actor runtime dispatch for agentic workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from runtimes import build_runtimes
from runtimes.turns import run_runtime_stage
from workflows.config import ActorConfig, AgenticConfig


class ActorRuntime(Protocol):
    def run(self, *, actor: ActorConfig, prompt: str, stage_name: str) -> str: ...


@dataclass(frozen=True)
class ConfiguredActorRuntime:
    config: AgenticConfig

    def run(self, *, actor: ActorConfig, prompt: str, stage_name: str) -> str:
        runtime_cfg = self.config.runtimes[actor.runtime]
        runtime = build_runtimes({actor.runtime: runtime_cfg.raw})[actor.runtime]
        result = run_runtime_stage(
            runtime=runtime,
            runtime_cfg=runtime_cfg.raw,
            agent_cfg={
                **actor.raw,
                "model": actor.model or actor.raw.get("model") or "",
            },
            stage_name=stage_name,
            worktree=_repository_worktree(self.config),
            session_name=_session_name(
                config=self.config, actor=actor, stage_name=stage_name
            ),
            prompt=prompt,
        )
        return result.output


def build_actor_runtime(*, config: AgenticConfig, actor: ActorConfig) -> ActorRuntime:
    return ConfiguredActorRuntime(config=config)


def _repository_worktree(config: AgenticConfig) -> Path:
    repository = config.raw.get("repository") or {}
    if not isinstance(repository, dict):
        raise RuntimeError("repository config must be a mapping")
    raw_path = str(
        repository.get("local-path") or repository.get("local_path") or ""
    ).strip()
    if not raw_path:
        raise RuntimeError(
            "repository.local-path is required for actor runtime execution"
        )
    path = Path(raw_path).expanduser()
    resolved = path if path.is_absolute() else (config.workflow_root / path).resolve()
    if not resolved.is_dir():
        raise RuntimeError(
            f"repository.local-path must be an existing directory: {resolved}"
        )
    return resolved


def _session_name(*, config: AgenticConfig, actor: ActorConfig, stage_name: str) -> str:
    raw = actor.raw.get("session-name") or actor.raw.get("session_name")
    if raw:
        return str(raw)
    return f"{config.workflow_root.name}:{stage_name}:{actor.name}"
