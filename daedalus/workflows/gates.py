"""Gate helpers for agentic workflows."""
from __future__ import annotations

from workflows.config import AgenticConfig, AgenticConfigError


def validate_stage_gates(config: AgenticConfig, stage_name: str) -> None:
    stage = config.stages[stage_name]
    for gate_name in stage.gates:
        gate = config.gates[gate_name]
        if gate.type != "orchestrator-evaluated":
            raise AgenticConfigError(f"unsupported gate type for {gate_name}: {gate.type}")
