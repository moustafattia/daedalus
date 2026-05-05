"""Lane decisions, transitions, actor outputs, and teardown handoff."""

from __future__ import annotations

from typing import Any

from sprints.workflows import sessions
from sprints.workflows import teardown as teardown_flow
from sprints.core.config import WorkflowConfig
from sprints.workflows.lane_state import (
    append_engine_event,
    blocker_reason,
    clear_engine_retry,
    completion_cleanup_retry_pending,
    concurrency_config,
    first_text,
    lane_transition_side,
    normalize_pull_request,
    now_iso,
    release_lane_lease,
    active_lanes,
    append_lane_event,
    lane_is_terminal,
    lane_list,
    lane_mapping,
    lane_recovery_artifacts,
    lane_stage,
    set_lane_operator_attention,
    set_lane_status,
)
from sprints.workflows.notifications import notify_review_changes_requested
from sprints.workflows.orchestrator import OrchestratorDecision
from sprints.workflows.retries import lane_retry_is_due


def lane_for_decision(*, state: Any, decision: OrchestratorDecision) -> dict[str, Any]:
    if decision.lane_id:
        lane = state.lanes.get(decision.lane_id)
        if isinstance(lane, dict):
            return lane
        raise RuntimeError(f"orchestrator selected unknown lane {decision.lane_id!r}")
    active = active_lanes(state)
    if len(active) == 1:
        return active[0]
    raise RuntimeError("orchestrator decision must include lane_id")


def validate_decision_for_lane(
    *, config: WorkflowConfig, lane: dict[str, Any], decision: OrchestratorDecision
) -> None:
    lane_status = str(lane.get("status") or "").strip()
    if lane_status == "running":
        raise RuntimeError(f"lane {lane.get('lane_id')} is already running")
    if lane_status == "retry_queued":
        _validate_retry_dispatch(lane=lane, decision=decision)
    if lane_status == "operator_attention" and decision.decision not in {
        "retry",
        "operator_attention",
    }:
        raise RuntimeError(f"lane {lane.get('lane_id')} requires operator attention")
    if _review_changes_are_pending(lane):
        _validate_review_changes_retry(lane=lane, decision=decision)
    if decision.decision != "retry" and decision.stage != lane_stage(lane):
        current_stage = config.stages.get(lane_stage(lane))
        if (
            lane_status == "waiting"
            and current_stage is not None
            and decision.stage == current_stage.next_stage
        ):
            return
        raise RuntimeError(
            f"decision for lane {lane.get('lane_id')} uses stage {decision.stage!r}, "
            f"but lane is at {lane_stage(lane)!r}"
        )
    if decision.decision == "retry" and decision.stage not in config.stages:
        raise RuntimeError(f"retry target stage does not exist: {decision.stage}")
    if lane_is_terminal(lane):
        raise RuntimeError(f"lane {lane.get('lane_id')} is terminal")


def _validate_retry_dispatch(
    *, lane: dict[str, Any], decision: OrchestratorDecision
) -> None:
    if completion_cleanup_retry_pending(lane):
        raise RuntimeError(
            f"lane {lane.get('lane_id')} retry is runner-owned completion cleanup"
        )
    if decision.decision not in {"run_actor", "run_action"}:
        raise RuntimeError(
            f"lane {lane.get('lane_id')} is retry queued; dispatch the retry target"
        )
    if not lane_retry_is_due(lane):
        pending = (
            lane.get("pending_retry")
            if isinstance(lane.get("pending_retry"), dict)
            else {}
        )
        raise RuntimeError(
            f"lane {lane.get('lane_id')} retry is not due until "
            f"{pending.get('due_at') or 'the configured retry time'}"
        )
    pending = (
        lane.get("pending_retry") if isinstance(lane.get("pending_retry"), dict) else {}
    )
    retry_stage = str(pending.get("stage") or "").strip()
    retry_target = str(pending.get("target") or "").strip()
    if retry_stage and decision.stage != retry_stage:
        raise RuntimeError(
            f"lane {lane.get('lane_id')} retry targets stage {retry_stage!r}, "
            f"not {decision.stage!r}"
        )
    if retry_target and decision.target and decision.target != retry_target:
        raise RuntimeError(
            f"lane {lane.get('lane_id')} retry targets {retry_target!r}, "
            f"not {decision.target!r}"
        )


def _review_changes_are_pending(lane: dict[str, Any]) -> bool:
    if lane_stage(lane) != "review":
        return False
    if str(lane.get("status") or "").strip() != "waiting":
        return False
    actor_outputs = lane_mapping(lane, "actor_outputs")
    if _stale_reviewer_changes_were_superseded(lane=lane, actor_outputs=actor_outputs):
        return False
    review = actor_outputs.get("reviewer")
    if not isinstance(review, dict):
        return False
    return str(review.get("status") or "").strip().lower() in {
        "changes_requested",
        "needs_changes",
    }


def _stale_reviewer_changes_were_superseded(
    *, lane: dict[str, Any], actor_outputs: dict[str, Any]
) -> bool:
    implementation = actor_outputs.get("implementer")
    if not isinstance(implementation, dict):
        return False
    if str(implementation.get("status") or "").strip().lower() != "done":
        return False
    last_output = lane.get("last_actor_output")
    if not isinstance(last_output, dict) or last_output != implementation:
        return False
    return int(lane.get("attempt") or 1) > 1


def _clear_superseded_reviewer_changes(
    *, lane: dict[str, Any], output: dict[str, Any]
) -> None:
    if str(output.get("status") or "").strip().lower() != "done":
        return
    actor_outputs = lane_mapping(lane, "actor_outputs")
    review = actor_outputs.get("reviewer")
    if not isinstance(review, dict):
        return
    if str(review.get("status") or "").strip().lower() not in {
        "changes_requested",
        "needs_changes",
    }:
        return
    superseded = lane_list(lane, "superseded_actor_outputs")
    superseded.append(
        {
            "actor": "reviewer",
            "stage": "review",
            "superseded_by": "implementer",
            "superseded_at": now_iso(),
            "output": review,
        }
    )
    actor_outputs.pop("reviewer", None)


def _validate_review_changes_retry(
    *, lane: dict[str, Any], decision: OrchestratorDecision
) -> None:
    if decision.decision == "operator_attention":
        return
    if decision.decision != "retry":
        raise RuntimeError(
            f"lane {lane.get('lane_id')} has pending review changes; "
            "orchestrator must retry deliver"
        )
    if decision.stage != "deliver":
        raise RuntimeError(
            f"lane {lane.get('lane_id')} has pending review changes; "
            f"retry stage must be 'deliver', not {decision.stage!r}"
        )
    if decision.target != "implementer":
        raise RuntimeError(
            f"lane {lane.get('lane_id')} has pending review changes; "
            "retry target must be 'implementer'"
        )


def validate_actor_capacity(
    *,
    config: WorkflowConfig,
    actor_name: str,
    dispatch_counts: dict[str, int],
) -> None:
    concurrency = concurrency_config(config)
    actor_limits = (
        concurrency.get("actor_limits")
        if isinstance(concurrency.get("actor_limits"), dict)
        else {}
    )
    limit = int(actor_limits.get(actor_name) or concurrency["max_lanes"])
    if dispatch_counts.get(actor_name, 0) >= limit:
        raise RuntimeError(f"concurrency limit reached for actor {actor_name}")


def actor_concurrency_usage(*, config: WorkflowConfig, state: Any) -> dict[str, int]:
    return sessions.actor_concurrency_usage(
        config=config,
        lanes=active_lanes(state),
    )


def actor_capacity_snapshot(
    *, concurrency: dict[str, Any], actor_usage: dict[str, int]
) -> dict[str, dict[str, int]]:
    capacities = (
        concurrency.get("actor_limits")
        if isinstance(concurrency.get("actor_limits"), dict)
        else {}
    )
    return {
        actor_name: {
            "limit": int(limit),
            "running": int(actor_usage.get(actor_name, 0)),
            "available": max(int(limit) - int(actor_usage.get(actor_name, 0)), 0),
        }
        for actor_name, limit in sorted(capacities.items())
    }


def guard_actor_dispatch(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    stage_name: str,
) -> dict[str, Any]:
    lane_id = str(lane.get("lane_id") or "").strip()
    conflicts = sessions.actor_dispatch_conflicts(
        config=config,
        lane=lane,
        lane_id=lane_id,
        actor_name=actor_name,
        stage_name=stage_name,
    )
    if not conflicts:
        return {"allowed": True, "conflicts": []}
    set_lane_operator_attention(
        config=config,
        lane=lane,
        reason="duplicate_dispatch_guard",
        message=(
            f"refusing to dispatch {actor_name} for lane {lane_id}; "
            "active runtime work is already recorded"
        ),
        artifacts=lane_recovery_artifacts(
            lane,
            {
                "actor": actor_name,
                "stage": stage_name,
                "conflicts": conflicts,
            },
        ),
    )
    return {
        "allowed": False,
        "reason": "duplicate_dispatch_guard",
        "conflicts": conflicts,
    }


def advance_lane(
    *, config: WorkflowConfig, lane: dict[str, Any], target: str | None
) -> None:
    next_stage = target or config.stages[lane_stage(lane)].next_stage
    if not next_stage:
        raise RuntimeError(f"stage {lane_stage(lane)} has no next stage")
    if lane_stage(lane) == "deliver" and next_stage == "review":
        failure = _delivery_contract_failure(lane)
        if failure:
            set_lane_operator_attention(
                config=config,
                lane=lane,
                reason="delivery_contract_failed",
                message=failure,
                artifacts=_contract_artifacts(lane),
            )
            return
    if next_stage == "done":
        complete_lane(config=config, lane=lane, reason="completed")
        return
    if next_stage not in config.stages:
        raise RuntimeError(f"unknown target stage: {next_stage}")
    previous = lane_transition_side(lane)
    lane["stage"] = next_stage
    lane["pending_retry"] = None
    clear_engine_retry(config=config, lane=lane)
    set_lane_status(
        config=config,
        lane=lane,
        status="waiting",
        reason=f"advanced to {next_stage}",
        previous=previous,
    )


def decision_ready_lanes(state: Any) -> list[dict[str, Any]]:
    return [
        lane for lane in active_lanes(state) if lane_needs_orchestrator_decision(lane)
    ]


def lane_needs_orchestrator_decision(lane: dict[str, Any]) -> bool:
    if sessions.active_actor_dispatch(lane):
        return False
    status = str(lane.get("status") or "").strip().lower()
    if status in {"claimed", "waiting"}:
        return True
    if status == "retry_queued":
        if completion_cleanup_retry_pending(lane):
            return False
        return lane_retry_is_due(lane)
    return False


def complete_lane(*, config: WorkflowConfig, lane: dict[str, Any], reason: str) -> None:
    teardown_flow.complete_lane(
        config=config,
        lane=lane,
        reason=reason,
        ops=teardown_ops(),
    )


def teardown_ops() -> teardown_flow.TeardownOps:
    return teardown_flow.TeardownOps(
        set_lane_status=set_lane_status,
        set_lane_operator_attention=set_lane_operator_attention,
        clear_engine_retry=clear_engine_retry,
        release_lane_lease=release_lane_lease,
        append_engine_event=append_engine_event,
    )


def release_lane(*, config: WorkflowConfig, lane: dict[str, Any], reason: str) -> None:
    lane["pending_retry"] = None
    clear_engine_retry(config=config, lane=lane)
    set_lane_status(
        config=config,
        lane=lane,
        status="released",
        reason=reason,
        actor=None,
    )
    release_lane_lease(config=config, lane=lane, reason=reason)


def target_or_single(*, target: str | None, values: tuple[str, ...], kind: str) -> str:
    if target:
        if target not in values:
            raise RuntimeError(
                f"orchestrator selected {kind} {target!r}, not declared on current stage"
            )
        return target
    if len(values) == 1:
        return values[0]
    raise RuntimeError(f"orchestrator decision must target one {kind}")


def record_actor_output(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    output: dict[str, Any],
) -> None:
    actor_outputs = lane_mapping(lane, "actor_outputs")
    if actor_name == "implementer":
        _clear_superseded_reviewer_changes(lane=lane, output=output)
    actor_outputs[actor_name] = output
    lane["last_actor_output"] = output
    lane["last_progress_at"] = now_iso()
    lane["pending_retry"] = None
    clear_engine_retry(config=config, lane=lane)
    stage_outputs = lane_mapping(lane, "stage_outputs")
    stage_outputs[lane_stage(lane)] = {
        **dict(stage_outputs.get(lane_stage(lane)) or {}),
        "last_actor": actor_name,
    }
    branch = first_text(output, "branch", "branch_name", "branch-name")
    if branch:
        lane["branch"] = branch
    pull_request = output.get("pull_request") or output.get("pr")
    if isinstance(pull_request, dict):
        lane["pull_request"] = normalize_pull_request(pull_request)
    elif pull_request:
        lane["pull_request"] = {"url": str(pull_request)}
    thread_id = first_text(output, "thread_id", "thread-id")
    if thread_id:
        lane["thread_id"] = thread_id
    turn_id = first_text(output, "turn_id", "turn-id")
    if turn_id:
        lane["turn_id"] = turn_id
    append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.actor_output",
        payload={"actor": actor_name, "output": output},
    )


def record_actor_runtime_start(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    stage_name: str,
    runtime_meta: dict[str, Any],
) -> None:
    sessions.record_actor_runtime_start(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        runtime_meta=runtime_meta,
    )


def record_actor_dispatch_planned(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    stage_name: str,
    runtime_meta: dict[str, Any],
) -> dict[str, Any]:
    return sessions.record_actor_dispatch_planned(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        runtime_meta=runtime_meta,
    )


def record_actor_runtime_progress(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    runtime_meta: dict[str, Any],
) -> None:
    sessions.record_actor_runtime_progress(
        config=config,
        lane=lane,
        runtime_meta=runtime_meta,
    )


def record_actor_runtime_result(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    runtime_meta: dict[str, Any],
    status: str,
) -> None:
    sessions.record_actor_runtime_result(
        config=config,
        lane=lane,
        runtime_meta=runtime_meta,
        status=status,
    )


def record_actor_runtime_interrupted(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    reason: str,
    message: str,
    age_seconds: int,
) -> None:
    sessions.record_actor_runtime_interrupted(
        config=config,
        lane=lane,
        reason=reason,
        message=message,
        age_seconds=age_seconds,
    )


def record_action_result(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    action_name: str,
    result: dict[str, Any],
) -> None:
    action_results = lane_mapping(lane, "action_results")
    action_results[action_name] = result
    stage_outputs = lane_mapping(lane, "stage_outputs")
    stage_outputs[lane_stage(lane)] = {
        **dict(stage_outputs.get(lane_stage(lane)) or {}),
        "last_action": action_name,
    }
    lane["last_progress_at"] = now_iso()
    append_lane_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.action",
        payload={"action": action_name, "result": result},
    )


def save_scheduler_snapshot(*, config: WorkflowConfig, state: Any) -> None:
    sessions.save_scheduler_snapshot(
        config=config,
        lanes=state.lanes.values(),
    )


def apply_actor_output_status(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    output: dict[str, Any],
) -> None:
    status = str(output.get("status") or "").strip().lower()
    blockers = (
        output.get("blockers") if isinstance(output.get("blockers"), list) else []
    )
    if not status:
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="actor_output_contract_failed",
            message=f"{actor_name} output is missing status",
            artifacts={"actor": actor_name, "output": output},
        )
        return
    if actor_name == "implementer":
        if status not in {"done", "blocked", "failed"}:
            set_lane_operator_attention(
                config=config,
                lane=lane,
                reason="actor_output_contract_failed",
                message=f"implementer returned unsupported status {status!r}",
                artifacts={"actor": actor_name, "output": output},
            )
            return
        if status == "done":
            failure = _delivery_contract_failure(lane)
            if failure:
                set_lane_operator_attention(
                    config=config,
                    lane=lane,
                    reason="actor_output_contract_failed",
                    message=failure,
                    artifacts=_contract_artifacts(lane),
                )
                return
    if actor_name == "reviewer" and status not in {
        "approved",
        "blocked",
        "failed",
        "changes_requested",
        "needs_changes",
    }:
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="actor_output_contract_failed",
            message=f"reviewer returned unsupported status {status!r}",
            artifacts={"actor": actor_name, "output": output},
        )
        return
    if actor_name == "reviewer" and status in {"changes_requested", "needs_changes"}:
        required_fixes = output.get("required_fixes")
        if not isinstance(required_fixes, list) or not required_fixes:
            set_lane_operator_attention(
                config=config,
                lane=lane,
                reason="actor_output_contract_failed",
                message="review changes require non-empty required_fixes",
                artifacts={"actor": actor_name, "output": output},
            )
            return
        notify_review_changes_requested(config=config, lane=lane, output=output)
    if status in {"blocked", "failed"} or blockers:
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason=blocker_reason(output) or status or "actor_blocked",
            message=str(
                output.get("summary") or f"{actor_name} returned {status or 'blockers'}"
            ),
            artifacts={
                "actor": actor_name,
                "blockers": blockers,
                "branch": lane.get("branch"),
                "pull_request": lane.get("pull_request"),
                "artifacts": output.get("artifacts")
                if isinstance(output.get("artifacts"), dict)
                else {},
            },
        )
        return
    set_lane_status(
        config=config,
        lane=lane,
        status="waiting",
        actor=None,
        reason=f"{actor_name} returned output",
    )


def _delivery_contract_failure(lane: dict[str, Any]) -> str:
    implementation = lane_mapping(lane, "actor_outputs").get("implementer")
    if not isinstance(implementation, dict):
        return "delivery cannot advance before implementer output exists"
    if str(implementation.get("status") or "").strip().lower() != "done":
        return "delivery requires implementer status `done`"
    if not _pull_request_url(lane):
        return "delivery requires pull_request.url"
    verification = implementation.get("verification")
    if not isinstance(verification, list) or not verification:
        return "delivery requires non-empty verification evidence"
    return ""


def _pull_request_url(lane: dict[str, Any]) -> str:
    pull_request = lane.get("pull_request")
    if isinstance(pull_request, dict):
        return str(pull_request.get("url") or "").strip()
    return ""


def _contract_artifacts(lane: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": lane.get("stage"),
        "actor_outputs": lane.get("actor_outputs"),
        "pull_request": lane.get("pull_request"),
        "branch": lane.get("branch"),
        "completion_auto_merge": lane.get("completion_auto_merge"),
    }
