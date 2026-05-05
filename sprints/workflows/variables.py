"""Prompt variable builders for actors and deterministic actions."""

from __future__ import annotations

from typing import Any

from workflows.config import WorkflowConfig
from workflows.state_io import WorkflowState
from workflows.lanes import lane_mapping
from workflows.prompt_context import (
    compact_lane_for_prompt,
    compact_workflow_state,
    orchestrator_prompt_budget,
)


def actor_variables(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    lane: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    actor_outputs = lane_mapping(lane, "actor_outputs")
    attempt = int(inputs.get("attempt") or lane.get("attempt") or 1)
    budget = orchestrator_prompt_budget(config)
    lane_id = str(lane.get("lane_id") or "").strip()
    return {
        **inputs,
        "attempt": attempt,
        "workflow": compact_workflow_state(
            state=state,
            ready_lane_ids={lane_id} if lane_id else set(),
            budget=budget,
        ),
        "lane": compact_lane_for_prompt(
            lane=lane,
            lane_id=lane_id,
            budget=budget,
            detailed=True,
        ),
        "config": config.raw,
        "issue": lane.get("issue") or {},
        "implementation": actor_outputs.get("implementer") or {},
        "review": actor_outputs.get("reviewer") or {},
        "review_feedback": _review_feedback(lane=lane, inputs=inputs),
        "pull_request": lane.get("pull_request") or {},
        "retry": lane.get("pending_retry") or inputs.get("retry") or {},
    }


def action_variables(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    lane: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    actor_outputs = lane_mapping(lane, "actor_outputs")
    budget = orchestrator_prompt_budget(config)
    lane_id = str(lane.get("lane_id") or "").strip()
    return {
        **inputs,
        "workflow": compact_workflow_state(
            state=state,
            ready_lane_ids={lane_id} if lane_id else set(),
            budget=budget,
        ),
        "lane": compact_lane_for_prompt(
            lane=lane,
            lane_id=lane_id,
            budget=budget,
            detailed=True,
        ),
        "workflow_root": str(config.workflow_root),
        "config": config.raw,
        "issue": lane.get("issue") or {},
        "actor_outputs": actor_outputs,
        "stage_outputs": lane_mapping(lane, "stage_outputs"),
        "action_results": lane_mapping(lane, "action_results"),
        "implementation": actor_outputs.get("implementer") or {},
        "review": actor_outputs.get("reviewer") or {},
        "review_feedback": _review_feedback(lane=lane, inputs=inputs),
        "pull_request": lane.get("pull_request") or {},
        "retry": lane.get("pending_retry") or inputs.get("retry") or {},
    }


def _review_feedback(*, lane: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    actor_outputs = lane_mapping(lane, "actor_outputs")
    review = inputs.get("review")
    if not isinstance(review, dict):
        stored_review = actor_outputs.get("reviewer")
        review = stored_review if isinstance(stored_review, dict) else {}
    retry = inputs.get("retry") if isinstance(inputs.get("retry"), dict) else {}
    feedback = {
        "review": review,
        "required_fixes": inputs.get("required_fixes")
        or review.get("required_fixes")
        or retry.get("required_fixes"),
        "findings": inputs.get("findings")
        or review.get("findings")
        or retry.get("findings"),
        "verification_gaps": inputs.get("verification_gaps")
        or review.get("verification_gaps")
        or retry.get("verification_gaps"),
        "feedback": inputs.get("feedback") or retry.get("reason"),
    }
    return {
        key: value for key, value in feedback.items() if value not in (None, "", [], {})
    }
