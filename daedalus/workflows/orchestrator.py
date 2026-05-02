"""Orchestrator decision parsing."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import json


class OrchestratorDecisionError(RuntimeError):
    """Raised when an orchestrator response is not a valid decision."""


@dataclass(frozen=True)
class OrchestratorDecision:
    decision: str
    stage: str
    target: str | None = None
    reason: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    operator_message: str | None = None

    @classmethod
    def from_output(cls, output: str) -> "OrchestratorDecision":
        try:
            raw = json.loads(output)
        except json.JSONDecodeError as exc:
            raise OrchestratorDecisionError(f"orchestrator returned invalid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise OrchestratorDecisionError("orchestrator decision must be a JSON object")
        decision = str(raw.get("decision", ""))
        if decision not in {"advance", "retry", "run_actor", "run_action", "operator_attention", "complete"}:
            raise OrchestratorDecisionError(f"unsupported orchestrator decision: {decision}")
        stage = str(raw.get("stage", ""))
        if not stage:
            raise OrchestratorDecisionError("orchestrator decision is missing stage")
        inputs = raw.get("inputs") or {}
        if not isinstance(inputs, dict):
            raise OrchestratorDecisionError("orchestrator decision inputs must be an object")
        target = raw.get("target")
        return cls(
            decision=decision,
            stage=stage,
            target=str(target) if target is not None else None,
            reason=str(raw.get("reason") or ""),
            inputs=inputs,
            operator_message=raw.get("operator_message"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
