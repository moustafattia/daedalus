"""Actor runtime dispatch for Sprints workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from sprints.runtimes import build_runtimes
from sprints.runtimes.turns import prompt_result_from_stage, run_runtime_stage
from sprints.core.config import ActorConfig, WorkflowConfig

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ActorRuntimePlan:
    runtime_name: str
    runtime_kind: str
    session_name: str
    model: str
    resume_session_id: str | None


@dataclass(frozen=True)
class ActorRuntimeResult:
    output: str
    plan: ActorRuntimePlan
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    last_event: str | None = None
    last_message: str | None = None
    turn_count: int = 0
    tokens: dict[str, int] | None = None
    rate_limits: dict[str, Any] | None = None
    prompt_path: Path | None = None
    result_path: Path | None = None
    command_argv: list[str] | None = None


class ActorRuntime(Protocol):
    def run(
        self,
        *,
        actor: ActorConfig,
        prompt: str,
        stage_name: str,
        worktree: Path | None = None,
        lane_id: str | None = None,
        resume_session_id: str | None = None,
        on_session_ready: Callable[[Any], None] | None = None,
        on_progress: Callable[[Any], None] | None = None,
    ) -> ActorRuntimeResult: ...


@dataclass(frozen=True)
class ConfiguredActorRuntime:
    config: WorkflowConfig

    def run(
        self,
        *,
        actor: ActorConfig,
        prompt: str,
        stage_name: str,
        worktree: Path | None = None,
        lane_id: str | None = None,
        resume_session_id: str | None = None,
        on_session_ready: Callable[[Any], None] | None = None,
        on_progress: Callable[[Any], None] | None = None,
    ) -> ActorRuntimeResult:
        runtime_cfg = self.config.runtimes[actor.runtime]
        plan = actor_runtime_plan(
            config=self.config,
            actor=actor,
            stage_name=stage_name,
            lane_id=lane_id,
            resume_session_id=resume_session_id,
        )
        runtime = build_runtimes({actor.runtime: runtime_cfg.raw})[actor.runtime]
        resolved_worktree = worktree or _repository_worktree(self.config)
        result = run_runtime_stage(
            runtime=runtime,
            runtime_cfg=runtime_cfg.raw,
            agent_cfg={
                **actor.raw,
                "model": actor.model or actor.raw.get("model") or "",
            },
            stage_name=stage_name,
            worktree=resolved_worktree,
            session_name=plan.session_name,
            prompt=prompt,
            resume_session_id=plan.resume_session_id,
            progress_callback=on_progress,
            on_session_ready=on_session_ready,
        )
        normalized = prompt_result_from_stage(result)
        return ActorRuntimeResult(
            output=normalized.output,
            plan=plan,
            session_id=normalized.session_id,
            thread_id=normalized.thread_id,
            turn_id=normalized.turn_id,
            last_event=normalized.last_event,
            last_message=normalized.last_message,
            turn_count=normalized.turn_count,
            tokens=normalized.tokens,
            rate_limits=normalized.rate_limits,
            prompt_path=result.prompt_path,
            result_path=result.result_path,
            command_argv=result.command_argv,
        )


def build_actor_runtime(*, config: WorkflowConfig, actor: ActorConfig) -> ActorRuntime:
    return ConfiguredActorRuntime(config=config)


def append_actor_skill_docs(
    *, config: WorkflowConfig, actor: ActorConfig, prompt: str
) -> str:
    del config
    skills = actor.raw.get("skills") or ()
    if isinstance(skills, str):
        skills = [skills]
    skill_docs = [
        _read_skill_doc(str(skill).strip()) for skill in skills if str(skill).strip()
    ]
    skill_docs = [doc for doc in skill_docs if doc]
    if not skill_docs:
        return prompt
    return "\n\n".join(
        [
            prompt.rstrip(),
            "# Actor Skill Docs",
            *skill_docs,
        ]
    )


def actor_runtime_plan(
    *,
    config: WorkflowConfig,
    actor: ActorConfig,
    stage_name: str,
    lane_id: str | None = None,
    resume_session_id: str | None = None,
) -> ActorRuntimePlan:
    runtime_cfg = config.runtimes[actor.runtime]
    return ActorRuntimePlan(
        runtime_name=actor.runtime,
        runtime_kind=runtime_cfg.kind,
        session_name=_session_name(
            config=config, actor=actor, stage_name=stage_name, lane_id=lane_id
        ),
        model=str(actor.model or actor.raw.get("model") or ""),
        resume_session_id=resume_session_id,
    )


def _repository_worktree(config: WorkflowConfig) -> Path:
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


def _session_name(
    *,
    config: WorkflowConfig,
    actor: ActorConfig,
    stage_name: str,
    lane_id: str | None = None,
) -> str:
    raw = actor.raw.get("session-name") or actor.raw.get("session_name")
    if raw:
        return str(raw)
    lane = str(lane_id or "").strip().replace("/", "-").replace("\\", "-")
    if lane:
        return f"{config.workflow_root.name}:{lane}:{stage_name}:{actor.name}"
    return f"{config.workflow_root.name}:{stage_name}:{actor.name}"


def _read_skill_doc(skill_name: str) -> str:
    path = _PLUGIN_ROOT / "skills" / skill_name / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return f"## Skill: {skill_name}\n\n{text}"
