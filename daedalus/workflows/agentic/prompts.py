"""Prompt assembly for agentic workflows."""
from __future__ import annotations

from typing import Any
import json

from workflows.agentic.config import AgenticConfig
from workflows.agentic.contract import ActorPolicy, AgenticPolicy
from workflows.agentic.state import WorkflowState
from workflows.prompts import render_prompt_template


def build_orchestrator_prompt(
    *,
    config: AgenticConfig,
    policy: AgenticPolicy,
    state: WorkflowState,
    facts: dict[str, Any],
) -> str:
    payload = {
        "config": config.raw,
        "state": state.to_dict(),
        "facts": facts,
        "available_decisions": [
            "advance",
            "retry",
            "run_actor",
            "run_action",
            "operator_attention",
            "complete",
        ],
    }
    return (
        "# Orchestrator Policy\n\n"
        f"{policy.orchestrator}\n\n"
        "# Current Context\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
    )


def build_actor_prompt(
    *,
    actor_policy: ActorPolicy,
    variables: dict[str, Any],
) -> str:
    return render_prompt_template(prompt_template=actor_policy.body, variables=variables)
