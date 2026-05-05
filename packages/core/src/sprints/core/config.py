"""Typed config for Sprints workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class WorkflowConfigError(RuntimeError):
    """Raised when workflow config is structurally invalid."""


@dataclass(frozen=True)
class RuntimeConfig:
    name: str
    kind: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActorConfig:
    name: str
    runtime: str
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StageConfig:
    name: str
    actors: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()
    next_stage: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateConfig:
    name: str
    type: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionConfig:
    name: str
    type: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StorageConfig:
    state_path: Path
    audit_log_path: Path


@dataclass(frozen=True)
class WorkflowConfig:
    workflow_root: Path
    workflow_name: str
    raw: dict[str, Any]
    orchestrator_actor: str
    runtimes: dict[str, RuntimeConfig]
    actors: dict[str, ActorConfig]
    stages: dict[str, StageConfig]
    gates: dict[str, GateConfig]
    actions: dict[str, ActionConfig]
    storage: StorageConfig

    @classmethod
    def from_raw(cls, *, raw: dict[str, Any], workflow_root: Path) -> "WorkflowConfig":
        root = workflow_root.resolve()
        workflow_name = str(raw.get("workflow") or "").strip()
        if not workflow_name:
            raise WorkflowConfigError("workflow config requires top-level workflow")
        runtimes = {
            name: RuntimeConfig(
                name=name,
                kind=str(value.get("kind") or name),
                raw=dict(value),
            )
            for name, value in dict(raw.get("runtimes") or {}).items()
        }
        actors = {
            name: ActorConfig(
                name=name,
                runtime=str(value["runtime"]),
                model=value.get("model"),
                raw=dict(value),
            )
            for name, value in dict(raw.get("actors") or {}).items()
        }
        stages = {
            name: StageConfig(
                name=name,
                actors=tuple(str(item) for item in value.get("actors") or ()),
                actions=tuple(str(item) for item in value.get("actions") or ()),
                gates=tuple(str(item) for item in value.get("gates") or ()),
                next_stage=value.get("next"),
                raw=dict(value),
            )
            for name, value in dict(raw.get("stages") or {}).items()
        }
        gates = {
            name: GateConfig(name=name, type=str(value["type"]), raw=dict(value))
            for name, value in dict(raw.get("gates") or {}).items()
        }
        actions = {
            name: ActionConfig(name=name, type=str(value["type"]), raw=dict(value))
            for name, value in dict(raw.get("actions") or {}).items()
        }
        storage_raw = dict(raw.get("storage") or {})
        state_path = _resolve(
            root, str(storage_raw.get("state", f".sprints/{workflow_name}-state.json"))
        )
        audit_log_path = _resolve(
            root,
            str(storage_raw.get("audit-log", f".sprints/{workflow_name}-audit.jsonl")),
        )
        orchestrator_actor = str(dict(raw.get("orchestrator") or {}).get("actor", ""))
        config = cls(
            workflow_root=root,
            workflow_name=workflow_name,
            raw=dict(raw),
            orchestrator_actor=orchestrator_actor,
            runtimes=runtimes,
            actors=actors,
            stages=stages,
            gates=gates,
            actions=actions,
            storage=StorageConfig(state_path=state_path, audit_log_path=audit_log_path),
        )
        config.validate_references()
        return config

    @property
    def first_stage(self) -> str:
        try:
            return next(iter(self.stages))
        except StopIteration as exc:
            raise WorkflowConfigError("workflow requires at least one stage") from exc

    def validate_references(self) -> None:
        if self.orchestrator_actor not in self.actors:
            raise WorkflowConfigError(
                f"unknown orchestrator actor: {self.orchestrator_actor}"
            )
        for actor in self.actors.values():
            if actor.runtime not in self.runtimes:
                raise WorkflowConfigError(
                    f"actor {actor.name} references unknown runtime {actor.runtime}"
                )
        for stage in self.stages.values():
            for actor in stage.actors:
                if actor not in self.actors:
                    raise WorkflowConfigError(
                        f"stage {stage.name} references unknown actor {actor}"
                    )
            for gate in stage.gates:
                if gate not in self.gates:
                    raise WorkflowConfigError(
                        f"stage {stage.name} references unknown gate {gate}"
                    )
            for action in stage.actions:
                if action not in self.actions:
                    raise WorkflowConfigError(
                        f"stage {stage.name} references unknown action {action}"
                    )
            if (
                stage.next_stage
                and stage.next_stage != "done"
                and stage.next_stage not in self.stages
            ):
                raise WorkflowConfigError(
                    f"stage {stage.name} references unknown next stage {stage.next_stage}"
                )


def _resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path
