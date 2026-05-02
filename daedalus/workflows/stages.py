"""Mechanical stage operations for agentic workflows."""
from __future__ import annotations

from typing import Any
import json

from workflows.actions import run_action
from workflows.actors import build_actor_runtime
from workflows.config import AgenticConfig
from workflows.contract import WorkflowPolicy
from workflows.gates import validate_stage_gates
from workflows.prompts import build_actor_prompt
from workflows.state import WorkflowState


def actor_variables(
    *,
    config: AgenticConfig,
    state: WorkflowState,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "workflow": state.to_dict(),
        "config": config.raw,
        **inputs,
    }


def run_stage_actor(
    *,
    config: AgenticConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    actor_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    actor = config.actors[actor_name]
    actor_policy = policy.actors.get(actor_name)
    if actor_policy is None:
        raise RuntimeError(f"missing actor policy section for {actor_name}")
    prompt = build_actor_prompt(
        actor_policy=actor_policy,
        variables=actor_variables(config=config, state=state, inputs=inputs),
    )
    raw_output = build_actor_runtime(config=config, actor=actor).run(actor=actor, prompt=prompt)
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"actor {actor_name} returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"actor {actor_name} output must be a JSON object")
    state.actor_outputs[actor_name] = parsed
    state.stage_outputs[state.current_stage] = {
        **dict(state.stage_outputs.get(state.current_stage) or {}),
        "last_actor": actor_name,
    }
    return parsed


def apply_action_result(
    *,
    config: AgenticConfig,
    state: WorkflowState,
    action_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    action = config.actions[action_name]
    result = run_action(action, inputs)
    payload = {
        "ok": result.ok,
        "output": result.output,
    }
    state.action_results[action_name] = payload
    state.stage_outputs[state.current_stage] = {
        **dict(state.stage_outputs.get(state.current_stage) or {}),
        "last_action": action_name,
    }
    return payload


def validate_current_stage(config: AgenticConfig, state: WorkflowState) -> None:
    if state.current_stage not in config.stages:
        raise RuntimeError(f"unknown current stage: {state.current_stage}")
    validate_stage_gates(config, state.current_stage)
