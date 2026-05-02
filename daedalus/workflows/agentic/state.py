"""Generic durable state for agentic workflows."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any
import json


@dataclass
class WorkflowState:
    workflow: str = "agentic"
    current_stage: str = ""
    status: str = "running"
    attempt: int = 1
    stage_outputs: dict[str, Any] = field(default_factory=dict)
    actor_outputs: dict[str, Any] = field(default_factory=dict)
    action_results: dict[str, Any] = field(default_factory=dict)
    orchestrator_decisions: list[dict[str, Any]] = field(default_factory=list)
    operator_attention: dict[str, Any] | None = None

    @classmethod
    def initial(cls, first_stage: str) -> "WorkflowState":
        return cls(current_stage=first_stage)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkflowState":
        names = {item.name for item in fields(cls)}
        values = {name: raw[name] for name in names if name in raw}
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_state(path: Path, *, first_stage: str) -> WorkflowState:
    if not path.exists():
        return WorkflowState.initial(first_stage)
    return WorkflowState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_state(path: Path, state: WorkflowState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def append_audit(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
