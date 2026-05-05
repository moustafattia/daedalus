"""Workflow tick lifecycle and orchestrator decision application."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sprints.workflows.actions import run_action
from sprints.workflows.actors import build_actor_runtime
from sprints.core.config import WorkflowConfig
from sprints.core.contracts import WorkflowPolicy
from sprints.workflows.dispatch import (
    actor_dispatch_mode,
    dispatch_stage_actor_background,
    run_stage_actor,
)
from sprints.workflows.effects import (
    completed_side_effect,
    record_side_effect_failed,
    record_side_effect_started,
    record_side_effect_succeeded,
    side_effect_key,
)
from sprints.core.loader import load_workflow_policy
from sprints.workflows.orchestrator import (
    OrchestratorDecision,
    parse_orchestrator_decisions,
    prepare_orchestrator_prompt,
)
from sprints.workflows.state_io import (
    WorkflowState,
    append_audit,
    load_state,
    persist_runtime_state,
    refresh_state_status,
    save_state_event,
    validate_state,
    with_state_lock,
)
from sprints.workflows.tick_journal import (
    decision_summaries,
    finish_tick_journal,
    record_tick_journal,
    result_summaries,
    start_tick_journal,
    TickJournal,
)
from sprints.workflows.variables import action_variables
from sprints.workflows.lanes import (
    active_lanes,
    advance_lane,
    actor_concurrency_usage,
    build_dispatch_audit,
    build_retry_audit,
    build_side_effect_audit,
    build_workflow_facts,
    claim_new_lanes,
    complete_lane,
    decision_ready_lanes,
    lane_for_decision,
    lane_retry_inputs,
    lane_stage,
    lane_summary,
    queue_lane_retry,
    reconcile_lanes,
    record_action_result,
    set_lane_status,
    target_or_single,
    validate_actor_capacity,
    validate_decision_for_lane,
)


def apply_action_result(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    lane: dict[str, Any],
    action_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    action = config.actions[action_name]
    operation = f"action.{action.type}"
    effect_payload = {
        "action": action_name,
        "type": action.type,
        "stage": lane_stage(lane),
        "inputs": inputs,
    }
    effect_key = side_effect_key(
        config=config,
        lane=lane,
        operation=operation,
        target=action_name,
        payload=effect_payload,
    )
    completed = completed_side_effect(config=config, lane=lane, key=effect_key)
    if completed:
        payload = {
            "ok": True,
            "skipped": True,
            "output": {
                "idempotency_key": effect_key,
                "side_effect": completed,
                "reason": "action side effect already completed",
            },
        }
        record_action_result(
            config=config, lane=lane, action_name=action_name, result=payload
        )
        return payload
    record_side_effect_started(
        config=config,
        lane=lane,
        key=effect_key,
        operation=operation,
        target=action_name,
        payload=effect_payload,
    )
    variables = action_variables(config=config, state=state, lane=lane, inputs=inputs)
    variables["idempotency_key"] = effect_key
    result = run_action(action, variables)
    payload = {
        "ok": result.ok,
        "output": {**result.output, "idempotency_key": effect_key},
    }
    if result.ok:
        payload["side_effect"] = record_side_effect_succeeded(
            config=config,
            lane=lane,
            key=effect_key,
            operation=operation,
            target=action_name,
            payload=effect_payload,
            result=payload,
        )
    else:
        payload["side_effect"] = record_side_effect_failed(
            config=config,
            lane=lane,
            key=effect_key,
            operation=operation,
            target=action_name,
            payload=effect_payload,
            result=payload,
            error=str(result.output.get("error") or "action failed"),
        )
    record_action_result(
        config=config, lane=lane, action_name=action_name, result=payload
    )
    return payload


def tick(config: WorkflowConfig, *, orchestrator_output: str) -> int:
    return with_state_lock(
        config=config,
        owner_role="workflow-tick",
        callback=lambda: tick_locked(config, orchestrator_output=orchestrator_output),
    )


def tick_locked(config: WorkflowConfig, *, orchestrator_output: str) -> int:
    journal = start_tick_journal(
        config=config,
        orchestrator_output=orchestrator_output,
    )
    state: WorkflowState | None = None
    intake: dict[str, Any] = {}
    reconcile: dict[str, Any] = {}
    decisions: list[OrchestratorDecision] = []
    results: list[dict[str, Any]] = []
    selected_count = 0
    try:
        policy = load_workflow_policy(config.workflow_root)
        record_tick_journal(
            config=config,
            journal=journal,
            state=state,
            event="policy_loaded",
            details={"workflow_root": str(config.workflow_root)},
        )
        state = load_state(
            config.storage.state_path,
            workflow=config.workflow_name,
            first_stage=config.first_stage,
        )
        validate_state(config, state)
        record_tick_journal(
            config=config,
            journal=journal,
            state=state,
            event="state_loaded",
            details={"state_path": str(config.storage.state_path)},
        )
        reconcile = reconcile_lanes(config=config, state=state)
        record_tick_journal(
            config=config,
            journal=journal,
            state=state,
            event="reconciled",
            details={"reconcile": reconcile},
        )
        intake = claim_new_lanes(config=config, state=state)
        record_tick_journal(
            config=config,
            journal=journal,
            state=state,
            event="intake_completed",
            details={"intake": intake},
        )
        selected_count = len(active_lanes(state))
        if not active_lanes(state):
            state.status = "idle"
            state.idle_reason = intake.get("reason") or "no active lanes"
            save_tick(
                config=config,
                state=state,
                event="idle",
                extra={
                    "intake": intake,
                    "reconcile": reconcile,
                    "tick_journal": journal.to_dict(),
                },
            )
            finish_tick_journal(
                config=config,
                journal=journal,
                state=state,
                status="completed",
                terminal_event="idle",
                selected_count=selected_count,
                completed_count=0,
                details={"reason": state.idle_reason},
            )
            return 0

        state.status = "running"
        state.idle_reason = None
        persist_runtime_state(config=config, state=state)
        output_override = read_output_arg(orchestrator_output)
        ready_lanes = decision_ready_lanes(state)
        record_tick_journal(
            config=config,
            journal=journal,
            state=state,
            event="decision_ready_evaluated",
            details={
                "ready_lanes": [lane_summary(lane) for lane in ready_lanes],
                "output_override": bool(output_override),
            },
        )
        if not ready_lanes and not output_override:
            save_tick(
                config=config,
                state=state,
                event="no_decision_ready",
                extra={
                    "intake": intake,
                    "reconcile": reconcile,
                    "active_lane_count": len(active_lanes(state)),
                    "reason": (
                        "active lanes are running, blocked, or waiting for retry time"
                    ),
                    "tick_journal": journal.to_dict(),
                },
            )
            finish_tick_journal(
                config=config,
                journal=journal,
                state=state,
                status="completed",
                terminal_event="no_decision_ready",
                selected_count=selected_count,
                completed_count=0,
                details={"reason": "no lanes are ready for an orchestrator decision"},
            )
            return 0
        if output_override:
            record_tick_journal(
                config=config,
                journal=journal,
                state=state,
                event="orchestrator_output_override",
                details={"output_size": len(output_override)},
            )
            output = output_override
        else:
            record_tick_journal(
                config=config,
                journal=journal,
                state=state,
                event="orchestrator_started",
                details={"ready_lane_count": len(ready_lanes)},
            )
            output = run_orchestrator(
                config=config,
                policy=policy,
                state=state,
                prompt_report=lambda report: record_tick_journal(
                    config=config,
                    journal=journal,
                    state=state,
                    event="orchestrator_prompt_prepared",
                    details=report,
                ),
            )
            record_tick_journal(
                config=config,
                journal=journal,
                state=state,
                event="orchestrator_completed",
                details={"output_size": len(output)},
            )
        decisions = parse_orchestrator_decisions(output)
        record_tick_journal(
            config=config,
            journal=journal,
            state=state,
            event="decisions_parsed",
            details={
                "decision_count": len(decisions),
                "decisions": decision_summaries(decisions),
            },
        )
        results = apply_decisions(
            config=config, policy=policy, state=state, decisions=decisions
        )
        record_tick_journal(
            config=config,
            journal=journal,
            state=state,
            event="decisions_applied",
            details={
                "result_count": len(results),
                "results": result_summaries(results),
            },
        )
        refresh_state_status(state, idle_reason="no active lanes")
        save_tick(
            config=config,
            state=state,
            event="tick",
            extra={
                "intake": intake,
                "reconcile": reconcile,
                "decisions": [decision.to_dict() for decision in decisions],
                "results": results,
                "tick_journal": journal.to_dict(),
            },
        )
        finish_tick_journal(
            config=config,
            journal=journal,
            state=state,
            status="completed",
            terminal_event="completed",
            selected_count=selected_count,
            completed_count=len(results),
            details={
                "decision_count": len(decisions),
                "result_count": len(results),
            },
        )
    except Exception as exc:
        journal_error: Exception | None = None
        try:
            if state is not None:
                save_failed_tick(
                    config=config,
                    state=state,
                    intake=intake,
                    reconcile=reconcile,
                    error=exc,
                    tick_journal=journal,
                )
            else:
                record_tick_journal(
                    config=config,
                    journal=journal,
                    state=state,
                    event="failed_before_state",
                    details={
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                    severity="error",
                )
        except Exception as failed_save_error:
            journal_error = failed_save_error
        try:
            finish_tick_journal(
                config=config,
                journal=journal,
                state=state,
                status="failed",
                terminal_event="failed",
                selected_count=selected_count,
                completed_count=len(results),
                error=exc,
                details={
                    "intake": intake,
                    "reconcile": reconcile,
                    "decisions": decision_summaries(decisions),
                    "results": result_summaries(results),
                },
            )
        except Exception as failed_finish_error:
            journal_error = journal_error or failed_finish_error
        if journal_error is not None:
            raise journal_error from exc
        raise
    return 0


def save_failed_tick(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    intake: dict[str, Any],
    reconcile: dict[str, Any],
    error: Exception,
    tick_journal: TickJournal | None = None,
) -> None:
    persist_runtime_state(config=config, state=state)
    state_payload = state.to_dict()
    append_audit(
        config.storage.audit_log_path,
        {
            "event": f"{config.workflow_name}.tick_failed",
            "state": state_payload,
            "retry_audit": build_retry_audit(state_payload),
            "dispatch_audit": build_dispatch_audit(state_payload),
            "side_effect_audit": build_side_effect_audit(state_payload),
            "intake": intake,
            "reconcile": reconcile,
            "error": str(error),
            "error_type": type(error).__name__,
            "tick_journal": tick_journal.to_dict() if tick_journal else None,
        },
    )


def save_tick(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    event: str,
    extra: dict[str, Any] | None = None,
) -> None:
    save_state_event(config=config, state=state, event=event, extra=extra)
    print(json.dumps(state.to_dict(), indent=2, sort_keys=True))


def run_orchestrator(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    prompt_report: Any | None = None,
) -> str:
    prepared = prepare_orchestrator_prompt(
        config=config,
        policy=policy,
        state=state,
        facts=build_workflow_facts(config, state),
    )
    if callable(prompt_report):
        prompt_report(prepared.report)
    actor = config.actors[config.orchestrator_actor]
    return (
        build_actor_runtime(config=config, actor=actor)
        .run(actor=actor, prompt=prepared.prompt, stage_name="orchestrator")
        .output
    )


def read_output_arg(value: str) -> str:
    if not value:
        return ""
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8-sig")
    return value


def apply_decisions(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    decisions: list[OrchestratorDecision],
) -> list[dict[str, Any]]:
    actor_usage = actor_concurrency_usage(config=config, state=state)
    planned = plan_decisions(
        config=config,
        state=state,
        decisions=decisions,
        actor_usage=actor_usage,
    )
    dispatch_counts = dict(actor_usage)
    results: list[dict[str, Any]] = []
    for decision, lane in planned:
        state.orchestrator_decisions.append(decision.to_dict())
        result = apply_decision(
            config=config,
            policy=policy,
            state=state,
            lane=lane,
            decision=decision,
            dispatch_counts=dispatch_counts,
        )
        results.append(result)
    return results


def plan_decisions(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    decisions: list[OrchestratorDecision],
    actor_usage: dict[str, int] | None = None,
) -> list[tuple[OrchestratorDecision, dict[str, Any]]]:
    planned: list[tuple[OrchestratorDecision, dict[str, Any]]] = []
    seen_lanes: set[str] = set()
    dispatch_counts: dict[str, int] = dict(actor_usage or {})
    for decision in decisions:
        lane = lane_for_decision(state=state, decision=decision)
        lane_id = str(lane.get("lane_id") or "").strip()
        if not lane_id:
            raise RuntimeError("orchestrator selected a lane without lane_id")
        if lane_id in seen_lanes:
            raise RuntimeError(
                f"orchestrator returned multiple decisions for lane {lane_id}; "
                "return at most one decision per lane per tick"
            )
        seen_lanes.add(lane_id)
        validate_decision_for_lane(config=config, lane=lane, decision=decision)
        if decision.decision == "run_actor":
            actor_name = target_or_single(
                target=decision.target,
                values=config.stages[decision.stage].actors,
                kind="actor",
            )
            validate_actor_capacity(
                config=config, actor_name=actor_name, dispatch_counts=dispatch_counts
            )
            dispatch_counts[actor_name] = dispatch_counts.get(actor_name, 0) + 1
        planned.append((decision, lane))
    return planned


def apply_decision(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    lane: dict[str, Any],
    decision: OrchestratorDecision,
    dispatch_counts: dict[str, int],
) -> dict[str, Any]:
    if decision.decision == "complete":
        complete_lane(config=config, lane=lane, reason=decision.reason or "completed")
        return {"lane_id": lane["lane_id"], "decision": "complete"}
    if decision.decision == "operator_attention":
        lane["operator_attention"] = {
            "message": decision.operator_message,
            "reason": decision.reason,
        }
        set_lane_status(
            config=config,
            lane=lane,
            status="operator_attention",
            reason=decision.reason or "operator attention",
        )
        return {"lane_id": lane["lane_id"], "decision": "operator_attention"}
    if decision.decision == "retry":
        return queue_lane_retry(config=config, lane=lane, decision=decision)
    if decision.decision == "advance":
        target_stage = (
            decision.target if decision.target in config.stages else decision.stage
        )
        advance_lane(config=config, lane=lane, target=target_stage)
        return {"lane_id": lane["lane_id"], "decision": "advance"}
    if decision.decision == "run_actor":
        if decision.stage != lane_stage(lane):
            advance_lane(config=config, lane=lane, target=decision.stage)
        actor_name = target_or_single(
            target=decision.target,
            values=config.stages[lane_stage(lane)].actors,
            kind="actor",
        )
        validate_actor_capacity(
            config=config, actor_name=actor_name, dispatch_counts=dispatch_counts
        )
        dispatch_counts[actor_name] = dispatch_counts.get(actor_name, 0) + 1
        if actor_dispatch_mode(config) == "background":
            result = dispatch_stage_actor_background(
                config=config,
                policy=policy,
                state=state,
                lane=lane,
                actor_name=actor_name,
                inputs=decision.inputs,
            )
        else:
            result = run_stage_actor(
                config=config,
                policy=policy,
                state=state,
                lane=lane,
                actor_name=actor_name,
                inputs=decision.inputs,
            )
        return {
            "lane_id": lane["lane_id"],
            "decision": "run_actor",
            "target": actor_name,
            "result": result,
        }
    if decision.decision == "run_action":
        if decision.stage != lane_stage(lane):
            advance_lane(config=config, lane=lane, target=decision.stage)
        inputs = lane_retry_inputs(lane=lane, inputs=decision.inputs)
        action_name = target_or_single(
            target=decision.target,
            values=config.stages[lane_stage(lane)].actions,
            kind="action",
        )
        result = apply_action_result(
            config=config,
            state=state,
            lane=lane,
            action_name=action_name,
            inputs=inputs,
        )
        return {
            "lane_id": lane["lane_id"],
            "decision": "run_action",
            "target": action_name,
            "result": result,
        }
    raise RuntimeError(f"unhandled orchestrator decision {decision.decision}")
