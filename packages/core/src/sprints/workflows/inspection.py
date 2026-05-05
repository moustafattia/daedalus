"""Read-only workflow CLI commands."""

from __future__ import annotations

import json

from sprints.core.config import WorkflowConfig
from sprints.core.loader import load_workflow_policy
from sprints.workflows.state_io import load_state, validate_state
from sprints.workflows.status import build_lane_status
from sprints.workflows.lanes import lane_by_id, lane_summary


def validate_command(config: WorkflowConfig) -> int:
    policy = load_workflow_policy(config.workflow_root)
    missing = [
        actor
        for stage in config.stages.values()
        for actor in stage.actors
        if actor != config.orchestrator_actor and actor not in policy.actors
    ]
    if missing:
        raise RuntimeError(f"missing actor policy sections: {sorted(set(missing))}")
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    validate_state(config, state)
    print(f"{config.workflow_name} workflow valid")
    return 0


def show_command(config: WorkflowConfig) -> int:
    print(json.dumps(config.raw, indent=2, sort_keys=True))
    return 0


def status_command(config: WorkflowConfig) -> int:
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    payload = {
        "workflow": config.workflow_name,
        "workflow_root": str(config.workflow_root),
        "state_path": str(config.storage.state_path),
        "audit_log_path": str(config.storage.audit_log_path),
        **build_lane_status(config=config, state=state.to_dict()),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def lanes_command(
    config: WorkflowConfig, *, lane_id: str | None, attention_only: bool
) -> int:
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    if lane_id:
        print(json.dumps(lane_by_id(state, lane_id), indent=2, sort_keys=True))
        return 0
    lanes = [
        lane_summary(lane)
        for lane in state.lanes.values()
        if isinstance(lane, dict)
        and (
            not attention_only or str(lane.get("status") or "") == "operator_attention"
        )
    ]
    print(
        json.dumps(
            {
                "workflow": config.workflow_name,
                "workflow_root": str(config.workflow_root),
                "attention_only": attention_only,
                "lanes": lanes,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0
