"""Workflow and lane status projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workflows import sessions
from workflows.config import WorkflowConfig
from workflows.contracts import load_workflow_contract
from workflows.intake import tracker_facts
from workflows.lane_state import (
    active_lanes,
    actor_dispatch_summary,
    concurrency_config,
    count_lanes_with_status,
    engine_store,
    intake_auto_activate_config,
    recovery_config,
    retry_config,
    lane_is_terminal,
    lane_summary,
    retry_summary,
    side_effects_summary,
)
from workflows.transitions import (
    actor_capacity_snapshot,
    actor_concurrency_usage,
    decision_ready_lanes,
    lane_needs_orchestrator_decision,
)

_TERMINAL_ENGINE_STATES = {"complete", "released", "merged", "closed", "archived"}


def build_status(workflow_root: Path) -> dict[str, Any]:
    root = Path(workflow_root).expanduser().resolve()
    contract = load_workflow_contract(root)
    config = WorkflowConfig.from_raw(raw=contract.config, workflow_root=root)
    state: dict[str, Any] = {}
    if config.storage.state_path.exists():
        try:
            state = json.loads(config.storage.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    lane_status = build_lane_status(config=config, state=state)
    return {
        "workflow": config.workflow_name,
        "health": "ok" if state or lane_status.get("engine_lane_count") else "unknown",
        "workflow_root": str(root),
        "contract_path": str(contract.source_path),
        "state_path": str(config.storage.state_path),
        "audit_log_path": str(config.storage.audit_log_path),
        **lane_status,
        "canceling_count": 0,
    }


def build_workflow_facts(config: WorkflowConfig, state: Any) -> dict[str, Any]:
    tracker_facts_payload = tracker_facts(config=config, state=state)
    concurrency = concurrency_config(config)
    actor_usage = actor_concurrency_usage(config=config, state=state)
    current_active_lanes = active_lanes(state)
    current_decision_ready_lanes = decision_ready_lanes(state)
    terminal_lanes = [
        lane
        for lane in state.lanes.values()
        if isinstance(lane, dict) and lane_is_terminal(lane)
    ]
    terminal_by_status: dict[str, int] = {}
    for lane in terminal_lanes:
        status = str(lane.get("status") or "unknown")
        terminal_by_status[status] = terminal_by_status.get(status, 0) + 1
    lane_limit = concurrency["max_lanes"]
    available_lanes = max(lane_limit - len(current_active_lanes), 0)
    store = engine_store(config)
    return {
        "tracker": tracker_facts_payload,
        "engine": {
            "lanes": [lane_summary(lane) for lane in current_active_lanes],
            "lane_count": len(state.lanes),
            "terminal_lane_count": len(terminal_lanes),
            "terminal_lanes_by_status": terminal_by_status,
            "decision_ready_lanes": [
                lane_summary(lane) for lane in current_decision_ready_lanes
            ],
            "work_items": store.work_items(limit=50),
            "runtime_sessions": store.runtime_sessions(limit=50),
            "active_lane_count": len(current_active_lanes),
            "decision_ready_lane_count": len(current_decision_ready_lanes),
            "idle_reason": state.idle_reason,
            "due_retries": store.due_retries(limit=50),
            "capacity": {
                "max_lanes": lane_limit,
                "max_active_lanes": lane_limit,
                "available_lanes": available_lanes,
            },
        },
        "concurrency": {
            **concurrency,
            "lanes": {
                "limit": lane_limit,
                "active": len(current_active_lanes),
                "available": available_lanes,
            },
            "actor_usage": actor_usage,
            "actor_capacity": actor_capacity_snapshot(
                concurrency=concurrency, actor_usage=actor_usage
            ),
        },
        "intake": {"auto_activate": intake_auto_activate_config(config)},
        "recovery": recovery_config(config),
        "retry": retry_config(config),
    }


def build_lane_status(
    *, config: WorkflowConfig, state: dict[str, Any]
) -> dict[str, Any]:
    lanes = state.get("lanes") if isinstance(state.get("lanes"), dict) else {}
    state_active = [
        lane
        for lane in lanes.values()
        if isinstance(lane, dict) and not lane_is_terminal(lane)
    ]
    store = engine_store(config)
    engine_work_items = store.work_items(limit=500)
    engine_runtime_sessions = store.runtime_sessions(limit=500)
    projected_lanes = _engine_first_lanes(
        state_lanes=lanes,
        engine_work_items=engine_work_items,
        engine_runtime_sessions=engine_runtime_sessions,
    )
    active = [
        lane
        for lane in projected_lanes.values()
        if isinstance(lane, dict) and not _projected_lane_is_terminal(lane)
    ]
    runtime_session_summaries = (
        engine_runtime_sessions
        or sessions.lane_runtime_session_summaries(lanes.values())
    )
    scheduler = store.read_scheduler() or {}
    runtime_totals = (
        scheduler.get("runtime_totals")
        if isinstance(scheduler.get("runtime_totals"), dict)
        else {}
    )
    retry_audit = build_retry_audit(state)
    due_retries = store.due_retries(limit=50)
    retry_wakeup = store.retry_wakeup()
    status = "running" if active else str(state.get("status") or "idle")
    latest_runs = store.latest_runs(limit=10)
    latest_tick_runs = store.latest_runs(mode="tick", limit=5)
    latest_tick_events = (
        store.events_for_run(str(latest_tick_runs[0]["run_id"]), limit=25)
        if latest_tick_runs
        else []
    )
    return {
        "status": status,
        "idle_reason": None if active else state.get("idle_reason"),
        "lane_status_source": "engine_work_items",
        "lane_count": len(projected_lanes),
        "state_lane_count": len(lanes),
        "engine_lane_count": len(engine_work_items),
        "active_lane_count": len(active),
        "decision_ready_count": len(
            [
                lane
                for lane in state_active
                if isinstance(lane, dict) and lane_needs_orchestrator_decision(lane)
            ]
        ),
        "running_count": count_lanes_with_status(active, "running"),
        "retry_count": count_lanes_with_status(active, "retry_queued"),
        "operator_attention_count": count_lanes_with_status(
            active, "operator_attention"
        ),
        "total_tokens": int(runtime_totals.get("total_tokens") or 0),
        "runtime_totals": runtime_totals,
        "retry_policy": retry_config(config),
        "due_retries": due_retries,
        "retry_wakeup": retry_wakeup,
        "next_retry_due_in_seconds": retry_wakeup.get("next_due_in_seconds"),
        "retry_audit": retry_audit,
        "active_dispatch_count": len(
            [lane for lane in active if sessions.active_actor_dispatch(lane)]
        ),
        "dispatch_audit": build_dispatch_audit(state),
        "side_effect_count": sum(
            int(lane.get("side_effect_count") or 0)
            for lane in active
            if isinstance(lane, dict)
        ),
        "side_effect_audit": build_side_effect_audit(state),
        "latest_runs": latest_runs,
        "latest_tick_runs": latest_tick_runs,
        "latest_tick_events": latest_tick_events,
        "engine_work_items": engine_work_items,
        "engine_runtime_sessions": engine_runtime_sessions,
        "runtime_sessions": runtime_session_summaries,
        "operator_attention_lanes": [
            lane
            for lane in active
            if str(lane.get("status") or "") == "operator_attention"
        ],
        "retry_lanes": [
            lane for lane in active if str(lane.get("status") or "") == "retry_queued"
        ],
        "lanes": projected_lanes,
        "state_lanes": lanes,
    }


def _engine_first_lanes(
    *,
    state_lanes: dict[str, Any],
    engine_work_items: list[dict[str, Any]],
    engine_runtime_sessions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    runtime_by_work_id = {
        str(session.get("work_id") or session.get("issue_id") or ""): session
        for session in engine_runtime_sessions
        if isinstance(session, dict)
    }
    projected: dict[str, dict[str, Any]] = {}
    for work_item in engine_work_items:
        if not isinstance(work_item, dict):
            continue
        lane_id = str(work_item.get("work_id") or work_item.get("issue_id") or "")
        if not lane_id:
            continue
        state_lane = state_lanes.get(lane_id)
        projected[lane_id] = _engine_lane_summary(
            work_item=work_item,
            state_lane=state_lane if isinstance(state_lane, dict) else {},
            runtime_session=runtime_by_work_id.get(lane_id) or {},
        )
    for lane_id, lane in state_lanes.items():
        if lane_id in projected or not isinstance(lane, dict):
            continue
        projected[lane_id] = {
            **lane_summary(lane),
            "lane_status_source": "workflow_state",
            "state_json_present": True,
        }
    return projected


def _engine_lane_summary(
    *,
    work_item: dict[str, Any],
    state_lane: dict[str, Any],
    runtime_session: dict[str, Any],
) -> dict[str, Any]:
    metadata = (
        work_item.get("metadata") if isinstance(work_item.get("metadata"), dict) else {}
    )
    state_summary = lane_summary(state_lane) if state_lane else {}
    lane_id = str(work_item.get("work_id") or state_summary.get("lane_id") or "")
    issue = (
        state_summary.get("issue")
        if isinstance(state_summary.get("issue"), dict)
        else {}
    )
    pull_request = metadata.get("pull_request") or state_summary.get("pull_request")
    pending_retry = metadata.get("pending_retry") or state_summary.get("pending_retry")
    operator_attention = metadata.get("operator_attention") or state_summary.get(
        "operator_attention"
    )
    runtime_metadata = (
        runtime_session.get("metadata")
        if isinstance(runtime_session.get("metadata"), dict)
        else {}
    )
    return {
        **state_summary,
        "lane_id": lane_id,
        "status": work_item.get("state") or state_summary.get("status"),
        "stage": metadata.get("stage") or state_summary.get("stage"),
        "actor": metadata.get("actor") or state_summary.get("actor"),
        "attempt": metadata.get("attempt") or state_summary.get("attempt"),
        "issue": {
            "identifier": work_item.get("identifier")
            or issue.get("identifier")
            or lane_id,
            "title": work_item.get("title") or issue.get("title"),
            "url": work_item.get("url") or issue.get("url"),
        },
        "branch": metadata.get("branch") or state_summary.get("branch"),
        "pull_request": pull_request,
        "operator_attention": operator_attention,
        "pending_retry": pending_retry,
        "last_transition": metadata.get("last_transition")
        or state_summary.get("last_transition"),
        "transition_history_count": metadata.get("transition_history_count")
        or state_summary.get("transition_history_count"),
        "thread_id": runtime_session.get("thread_id")
        or metadata.get("thread_id")
        or runtime_metadata.get("thread_id")
        or state_summary.get("thread_id"),
        "turn_id": runtime_session.get("turn_id")
        or metadata.get("turn_id")
        or runtime_metadata.get("turn_id")
        or state_summary.get("turn_id"),
        "runtime_status": runtime_session.get("status"),
        "runtime_session": runtime_session or None,
        "last_progress_at": runtime_session.get("updated_at")
        or work_item.get("updated_at")
        or state_summary.get("last_progress_at"),
        "engine_updated_at": work_item.get("updated_at"),
        "engine_work_item": work_item,
        "lane_status_source": "engine_work_items",
        "state_json_present": bool(state_lane),
    }


def _projected_lane_is_terminal(lane: dict[str, Any]) -> bool:
    return str(lane.get("status") or "").strip().lower() in _TERMINAL_ENGINE_STATES


def build_retry_audit(state: dict[str, Any]) -> list[dict[str, Any]]:
    lanes = state.get("lanes") if isinstance(state.get("lanes"), dict) else {}
    audit: list[dict[str, Any]] = []
    for lane_id, lane in lanes.items():
        if not isinstance(lane, dict):
            continue
        retry = retry_summary(lane)
        if retry is None:
            continue
        audit.append(
            {
                "lane_id": lane.get("lane_id") or lane_id,
                "stage": lane.get("stage"),
                "status": lane.get("status"),
                **retry,
            }
        )
    return audit


def build_dispatch_audit(state: dict[str, Any]) -> list[dict[str, Any]]:
    lanes = state.get("lanes") if isinstance(state.get("lanes"), dict) else {}
    audit: list[dict[str, Any]] = []
    for lane_id, lane in lanes.items():
        if not isinstance(lane, dict):
            continue
        dispatch = actor_dispatch_summary(lane)
        if dispatch is None:
            continue
        audit.append(
            {
                "lane_id": lane.get("lane_id") or lane_id,
                "lane_status": lane.get("status"),
                **dispatch,
                "journal_count": len(lane.get("dispatch_journal") or []),
            }
        )
    return audit


def build_side_effect_audit(state: dict[str, Any]) -> list[dict[str, Any]]:
    lanes = state.get("lanes") if isinstance(state.get("lanes"), dict) else {}
    audit: list[dict[str, Any]] = []
    for lane_id, lane in lanes.items():
        if not isinstance(lane, dict):
            continue
        for entry in side_effects_summary(lane, limit=50):
            audit.append(
                {
                    "lane_id": lane.get("lane_id") or lane_id,
                    "lane_status": lane.get("status"),
                    **entry,
                }
            )
    return audit
