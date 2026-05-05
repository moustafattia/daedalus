"""Operator lane commands for manual retry, release, and completion."""

from __future__ import annotations

import json
from typing import Any

from sprints.core.config import WorkflowConfig
from sprints.workflows.orchestrator import OrchestratorDecision
from sprints.workflows.state_io import (
    WorkflowState,
    load_state,
    refresh_state_status,
    save_state_event,
    with_state_lock,
)
from sprints.workflows.lanes import (
    complete_lane,
    lane_by_id,
    lane_stage,
    queue_lane_retry,
    release_lane,
)


def _save_operator_event(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    event: str,
    extra: dict[str, Any] | None = None,
) -> None:
    save_state_event(config=config, state=state, event=event, extra=extra)
    print(json.dumps(state.to_dict(), indent=2, sort_keys=True))


def operator_retry(
    config: WorkflowConfig, *, lane_id: str, reason: str, target: str | None
) -> int:
    return with_state_lock(
        config=config,
        owner_role="operator-retry",
        callback=lambda: operator_retry_locked(
            config, lane_id=lane_id, reason=reason, target=target
        ),
    )


def operator_retry_locked(
    config: WorkflowConfig, *, lane_id: str, reason: str, target: str | None
) -> int:
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    lane = lane_by_id(state, lane_id)
    status = str(lane.get("status") or "").strip()
    if status == "running":
        raise RuntimeError(f"lane {lane_id} is running; refusing duplicate work")
    if status == "retry_queued":
        raise RuntimeError(f"lane {lane_id} already has a queued retry")
    if status in {"complete", "released"}:
        raise RuntimeError(f"lane {lane_id} is terminal")
    decision = OrchestratorDecision(
        decision="retry",
        stage=lane_stage(lane) or config.first_stage,
        lane_id=lane_id,
        target=target,
        reason=reason,
        inputs={"feedback": reason, "operator_requested": True},
    )
    result = queue_lane_retry(config=config, lane=lane, decision=decision)
    refresh_state_status(state, idle_reason="no active lanes")
    _save_operator_event(
        config=config,
        state=state,
        event="operator.retry",
        extra={"lane_id": lane_id, "result": result},
    )
    return 0


def operator_release(config: WorkflowConfig, *, lane_id: str, reason: str) -> int:
    return with_state_lock(
        config=config,
        owner_role="operator-release",
        callback=lambda: operator_release_locked(
            config, lane_id=lane_id, reason=reason
        ),
    )


def operator_release_locked(
    config: WorkflowConfig, *, lane_id: str, reason: str
) -> int:
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    lane = lane_by_id(state, lane_id)
    if str(lane.get("status") or "") == "running":
        raise RuntimeError(f"lane {lane_id} is running; refusing release")
    if str(lane.get("status") or "") in {"complete", "released"}:
        raise RuntimeError(f"lane {lane_id} is already terminal")
    release_lane(config=config, lane=lane, reason=reason)
    refresh_state_status(state, idle_reason="no active lanes")
    _save_operator_event(
        config=config,
        state=state,
        event="operator.release",
        extra={"lane_id": lane_id, "reason": reason},
    )
    return 0


def operator_complete(config: WorkflowConfig, *, lane_id: str, reason: str) -> int:
    return with_state_lock(
        config=config,
        owner_role="operator-complete",
        callback=lambda: operator_complete_locked(
            config, lane_id=lane_id, reason=reason
        ),
    )


def operator_complete_locked(
    config: WorkflowConfig, *, lane_id: str, reason: str
) -> int:
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    lane = lane_by_id(state, lane_id)
    if str(lane.get("status") or "") == "running":
        raise RuntimeError(f"lane {lane_id} is running; refusing completion")
    if str(lane.get("status") or "") in {"complete", "released"}:
        raise RuntimeError(f"lane {lane_id} is already terminal")
    stage = config.stages.get(lane_stage(lane))
    if stage is None or stage.next_stage != "done":
        raise RuntimeError(
            f"lane {lane_id} is at stage {lane_stage(lane)!r}; "
            "operator completion is only allowed at a terminal handoff stage"
        )
    complete_lane(config=config, lane=lane, reason=reason)
    refresh_state_status(state, idle_reason="no active lanes")
    _save_operator_event(
        config=config,
        state=state,
        event="operator.complete",
        extra={"lane_id": lane_id, "reason": reason},
    )
    return 0
