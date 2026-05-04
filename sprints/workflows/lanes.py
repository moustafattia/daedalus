"""Lane ledger, tracker intake, reconciliation, and lane transitions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from engine import EngineStore, RetryPolicy
from trackers import (
    build_code_host_client,
    build_tracker_client,
    issue_priority_sort_key,
)
from workflows.config import WorkflowConfig
from workflows.orchestrator import OrchestratorDecision
from workflows.paths import runtime_paths

_RUNNER_INSTANCE_ID = f"{os.getpid()}:{uuid.uuid4().hex[:12]}"
_TERMINAL_LANE_STATUSES = {"complete", "released"}
_RUNTIME_RUNNING_STATUSES = {"running"}
_RUNTIME_FINAL_STATUSES = {"completed", "failed", "interrupted", "blocked"}


def build_workflow_facts(config: WorkflowConfig, state: Any) -> dict[str, Any]:
    tracker_facts = _tracker_facts(config=config, state=state)
    concurrency = _concurrency_config(config)
    current_active_lanes = active_lanes(state)
    return {
        "tracker": tracker_facts,
        "engine": {
            "lanes": state.lanes,
            "work_items": _engine_store(config).work_items(limit=200),
            "runtime_sessions": _engine_store(config).runtime_sessions(limit=200),
            "active_lane_count": len(current_active_lanes),
            "idle_reason": state.idle_reason,
            "due_retries": _engine_store(config).due_retries(limit=50),
            "capacity": {
                "max_active_lanes": concurrency["max_active_lanes"],
                "available_lanes": max(
                    concurrency["max_active_lanes"] - len(current_active_lanes), 0
                ),
            },
        },
        "concurrency": concurrency,
        "intake": {"auto_activate": _intake_auto_activate_config(config)},
        "recovery": _recovery_config(config),
        "retry": _retry_config(config),
    }


def build_lane_status(
    *, config: WorkflowConfig, state: dict[str, Any]
) -> dict[str, Any]:
    lanes = state.get("lanes") if isinstance(state.get("lanes"), dict) else {}
    active = [
        lane
        for lane in lanes.values()
        if isinstance(lane, dict) and not lane_is_terminal(lane)
    ]
    runtime_sessions = _lane_runtime_session_summaries(lanes.values())
    scheduler = _engine_store(config).read_scheduler() or {}
    runtime_totals = (
        scheduler.get("runtime_totals")
        if isinstance(scheduler.get("runtime_totals"), dict)
        else {}
    )
    return {
        "status": state.get("status"),
        "idle_reason": state.get("idle_reason"),
        "lane_count": len(lanes),
        "active_lane_count": len(active),
        "running_count": _count_lanes_with_status(active, "running"),
        "retry_count": _count_lanes_with_status(active, "retry_queued"),
        "operator_attention_count": _count_lanes_with_status(
            active, "operator_attention"
        ),
        "total_tokens": int(runtime_totals.get("total_tokens") or 0),
        "runtime_totals": runtime_totals,
        "latest_runs": _engine_store(config).latest_runs(limit=10),
        "engine_work_items": _engine_store(config).work_items(limit=200),
        "engine_runtime_sessions": _engine_store(config).runtime_sessions(limit=200),
        "runtime_sessions": runtime_sessions,
        "operator_attention_lanes": [
            _lane_summary(lane)
            for lane in active
            if str(lane.get("status") or "") == "operator_attention"
        ],
        "retry_lanes": [
            _lane_summary(lane)
            for lane in active
            if str(lane.get("status") or "") == "retry_queued"
        ],
        "lanes": lanes,
    }


def claim_new_lanes(*, config: WorkflowConfig, state: Any) -> dict[str, Any]:
    tracker_cfg = _tracker_config(config)
    if not tracker_cfg:
        return _claim_manual_lane(config=config, state=state)
    concurrency = _concurrency_config(config)
    available = max(concurrency["max_active_lanes"] - len(active_lanes(state)), 0)
    if available <= 0:
        return {"status": "full", "reason": "lane capacity reached"}

    facts = _tracker_facts(config=config, state=state)
    auto_activate = {"status": "skipped", "reason": "eligible candidates found"}
    candidates = facts.get("candidates") if isinstance(facts, dict) else []
    if isinstance(candidates, list) and not candidates and not facts.get("error"):
        auto_activate = _auto_activate_tracker_candidates(
            config=config,
            state=state,
            available=available,
        )
        if auto_activate.get("activated"):
            facts = _tracker_facts(config=config, state=state)
            candidates = facts.get("candidates") if isinstance(facts, dict) else []
            if not isinstance(candidates, list) or not candidates:
                candidates = _eligible_candidates(
                    config=config,
                    tracker_cfg=tracker_cfg,
                    issues=[
                        issue
                        for issue in auto_activate.get("activated_issues", [])
                        if isinstance(issue, dict)
                    ],
                    state=state,
                )
                facts = {
                    **facts,
                    "candidates": candidates,
                    "candidate_count": len(candidates),
                }
    if not isinstance(candidates, list) or not candidates:
        return {
            "status": "idle",
            "reason": str(facts.get("error") or "no eligible tracker candidates"),
            "facts": facts,
            "auto_activate": auto_activate,
        }

    claimed: list[dict[str, Any]] = []
    for issue in candidates:
        if len(claimed) >= available:
            break
        lane_id = _lane_id(config=config, issue=issue)
        if lane_id in state.lanes and not lane_is_terminal(state.lanes[lane_id]):
            continue
        lease = _acquire_lane_lease(config=config, lane_id=lane_id, issue=issue)
        if not lease.get("acquired"):
            continue
        lane = _new_lane(
            config=config,
            lane_id=lane_id,
            issue=issue,
            lease=lease,
        )
        state.lanes[lane_id] = lane
        _record_engine_lane(config=config, lane=lane)
        _append_engine_event(
            config=config,
            lane=lane,
            event_type=f"{config.workflow_name}.lane.claimed",
            payload={"lane": lane},
        )
        claimed.append(lane)

    if claimed:
        return {
            "status": "claimed",
            "claimed": [lane["lane_id"] for lane in claimed],
            "facts": facts,
            "auto_activate": auto_activate,
        }
    return {
        "status": "idle",
        "reason": "all eligible tracker candidates are already claimed",
        "facts": facts,
        "auto_activate": auto_activate,
    }


def _claim_manual_lane(*, config: WorkflowConfig, state: Any) -> dict[str, Any]:
    lane_id = f"{config.workflow_name}#manual"
    if lane_id in state.lanes:
        return {"status": "skipped", "reason": "manual lane already exists"}
    if active_lanes(state):
        return {"status": "full", "reason": "lane capacity reached"}
    issue = {"id": "manual", "identifier": lane_id, "title": config.workflow_name}
    lease = _acquire_lane_lease(config=config, lane_id=lane_id, issue=issue)
    if not lease.get("acquired"):
        return {"status": "idle", "reason": "manual lane is already claimed"}
    lane = _new_lane(config=config, lane_id=lane_id, issue=issue, lease=lease)
    state.lanes[lane_id] = lane
    _record_engine_lane(config=config, lane=lane)
    _append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.claimed",
        payload={"lane": lane},
    )
    return {"status": "claimed", "claimed": [lane_id]}


def reconcile_lanes(*, config: WorkflowConfig, state: Any) -> dict[str, Any]:
    active = active_lanes(state)
    if not active:
        return {"status": "skipped", "reason": "no active lanes"}
    runtime_result = reconcile_runtime_lanes(config=config, lanes=active)
    tracker_result = _reconcile_tracker_lanes(config=config, lanes=active)
    pr_result = _reconcile_pull_requests(config=config, lanes=active)
    return {
        "status": "ok",
        "runtime": runtime_result,
        "tracker": tracker_result,
        "pull_requests": pr_result,
    }


def reconcile_runtime_lanes(
    *, config: WorkflowConfig, lanes: list[dict[str, Any]]
) -> dict[str, Any]:
    cfg = _recovery_config(config)
    stale_seconds = cfg["running_stale_seconds"]
    if stale_seconds <= 0:
        return {"status": "skipped", "reason": "running stale detection disabled"}
    now = time.time()
    interrupted: list[str] = []
    recovery_queued: list[str] = []
    operator_attention: list[str] = []
    for lane in lanes:
        if str(lane.get("status") or "") != "running":
            continue
        timestamp = _runtime_updated_at(lane) or str(lane.get("last_progress_at") or "")
        age = now - _iso_to_epoch(timestamp, default=now)
        if age < stale_seconds:
            continue
        record_actor_runtime_interrupted(
            config=config,
            lane=lane,
            reason="actor_interrupted",
            message=(
                "actor was still marked running from an earlier tick; "
                f"last update was {int(age)}s ago"
            ),
            age_seconds=int(age),
        )
        session = lane_mapping(lane, "runtime_session")
        recovery = _runtime_recovery_record(
            lane=lane,
            session=session,
            age_seconds=int(age),
            message=(
                "actor was still marked running from an earlier tick; "
                f"last update was {int(age)}s ago"
            ),
        )
        lane["runtime_recovery"] = recovery
        queued = _queue_interrupted_actor_recovery(
            config=config,
            lane=lane,
            recovery=recovery,
            enabled=cfg["auto_retry_interrupted"],
        )
        if queued.get("status") == "queued":
            recovery_queued.append(str(lane.get("lane_id") or ""))
        else:
            operator_attention.append(str(lane.get("lane_id") or ""))
        interrupted.append(str(lane.get("lane_id") or ""))
    if interrupted:
        return {
            "status": "interrupted",
            "lanes": interrupted,
            "recovery_queued": recovery_queued,
            "operator_attention": operator_attention,
        }
    return {"status": "ok", "interrupted": []}


def _reconcile_tracker_lanes(
    *, config: WorkflowConfig, lanes: list[dict[str, Any]]
) -> dict[str, Any]:
    tracker_cfg = _tracker_config(config)
    if not tracker_cfg:
        return {"status": "skipped", "reason": "no tracker config"}
    issue_ids = [
        str((lane.get("issue") or {}).get("id") or "").strip()
        for lane in lanes
        if isinstance(lane.get("issue"), dict)
    ]
    issue_ids = [issue_id for issue_id in issue_ids if issue_id]
    if not issue_ids:
        return {"status": "skipped", "reason": "no lane issue ids"}
    try:
        client = build_tracker_client(
            workflow_root=config.workflow_root,
            tracker_cfg=tracker_cfg,
            repo_path=_repository_path(config),
        )
        refreshed = client.refresh(issue_ids)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    updated: list[str] = []
    released: list[str] = []
    for lane in lanes:
        issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
        issue_id = str(issue.get("id") or "").strip()
        fresh = refreshed.get(issue_id)
        if not fresh:
            continue
        lane["issue"] = fresh
        updated.append(str(lane.get("lane_id") or ""))
        if not _issue_is_still_active(tracker_cfg=tracker_cfg, issue=fresh):
            set_lane_status(
                config=config,
                lane=lane,
                status="released",
                reason="tracker issue is no longer eligible",
            )
            _release_lane_lease(
                config=config, lane=lane, reason="tracker issue is no longer eligible"
            )
            released.append(str(lane.get("lane_id") or ""))
    return {"status": "ok", "updated": updated, "released": released}


def _reconcile_pull_requests(
    *, config: WorkflowConfig, lanes: list[dict[str, Any]]
) -> dict[str, Any]:
    code_host_cfg = _code_host_config(config)
    if not code_host_cfg:
        return {"status": "skipped", "reason": "no code-host config"}
    lanes_by_branch = {
        str(lane.get("branch") or "").strip(): lane
        for lane in lanes
        if str(lane.get("branch") or "").strip()
    }
    if not lanes_by_branch:
        return {"status": "skipped", "reason": "no lane branches"}
    try:
        client = build_code_host_client(
            workflow_root=config.workflow_root,
            code_host_cfg=code_host_cfg,
            repo_path=_repository_path(config),
        )
        prs = client.list_open_pull_requests()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    updated: list[str] = []
    for pr in prs:
        branch = str(pr.get("headRefName") or "").strip()
        lane = lanes_by_branch.get(branch)
        if not lane:
            continue
        lane["pull_request"] = _normalize_pull_request(pr)
        lane["last_progress_at"] = _now_iso()
        updated.append(str(lane.get("lane_id") or ""))
    return {"status": "ok", "updated": updated}


def _tracker_facts(*, config: WorkflowConfig, state: Any) -> dict[str, Any]:
    tracker_cfg = _tracker_config(config)
    if not tracker_cfg:
        return {"enabled": False, "candidates": [], "candidate_count": 0}
    base = {
        "enabled": True,
        "kind": str(tracker_cfg.get("kind") or ""),
        "active_states": _configured_texts(
            tracker_cfg, "active_states", "active-states"
        ),
        "terminal_states": _configured_texts(
            tracker_cfg, "terminal_states", "terminal-states"
        ),
        "required_labels": _configured_texts(
            tracker_cfg, "required_labels", "required-labels"
        ),
        "exclude_labels": _configured_texts(
            tracker_cfg, "exclude_labels", "exclude-labels"
        ),
    }
    try:
        client = build_tracker_client(
            workflow_root=config.workflow_root,
            tracker_cfg=tracker_cfg,
            repo_path=_repository_path(config),
        )
        raw_candidates = client.list_candidates()
        terminal = client.list_terminal()
    except Exception as exc:
        return {
            **base,
            "error": str(exc),
            "candidates": [],
            "candidate_count": 0,
            "terminal": [],
            "terminal_count": 0,
        }
    candidates = _eligible_candidates(
        config=config,
        tracker_cfg=tracker_cfg,
        issues=raw_candidates,
        state=state,
    )
    return {
        **base,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "terminal": terminal,
        "terminal_count": len(terminal),
    }


def _auto_activate_tracker_candidates(
    *, config: WorkflowConfig, state: Any, available: int
) -> dict[str, Any]:
    auto_cfg = _intake_auto_activate_config(config)
    if not auto_cfg["enabled"]:
        return {"status": "skipped", "reason": "intake auto-activate disabled"}
    tracker_cfg = _tracker_config(config)
    if not tracker_cfg:
        return {"status": "skipped", "reason": "no tracker config"}
    limit = min(auto_cfg["max_per_tick"], max(int(available or 0), 0))
    if limit <= 0:
        return {"status": "skipped", "reason": "lane capacity reached"}
    try:
        client = build_tracker_client(
            workflow_root=config.workflow_root,
            tracker_cfg=tracker_cfg,
            repo_path=_repository_path(config),
        )
        issues = client.list_candidates()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    candidates = _auto_activation_candidates(
        config=config,
        tracker_cfg=tracker_cfg,
        issues=issues,
        state=state,
        add_label=auto_cfg["add_label"],
        exclude_labels=auto_cfg["exclude_labels"],
    )
    activated: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for issue in candidates[:limit]:
        issue_id = issue.get("id")
        lane_id = _lane_id(config=config, issue=issue)
        try:
            added = client.add_labels(issue_id, [auto_cfg["add_label"]])
        except Exception as exc:
            failed.append(
                {
                    "lane_id": lane_id,
                    "issue_id": issue_id,
                    "error": str(exc),
                }
            )
            _append_engine_event(
                config=config,
                lane={"lane_id": lane_id, "issue": issue},
                event_type=f"{config.workflow_name}.lane.auto_activate_failed",
                payload={"issue": issue, "error": str(exc), "config": auto_cfg},
                severity="warning",
            )
            continue
        if not added:
            failed.append(
                {
                    "lane_id": lane_id,
                    "issue_id": issue_id,
                    "error": "tracker did not add label",
                }
            )
            continue
        activated_issue = {
            **issue,
            "labels": sorted({*_issue_labels(issue), auto_cfg["add_label"].lower()}),
        }
        activated.append(activated_issue)
        _append_engine_event(
            config=config,
            lane={"lane_id": lane_id, "issue": activated_issue},
            event_type=f"{config.workflow_name}.lane.auto_activated",
            payload={
                "issue": activated_issue,
                "add_label": auto_cfg["add_label"],
                "config": auto_cfg,
            },
        )

    status = "activated" if activated else "idle"
    return {
        "status": status,
        "activated": [_lane_id(config=config, issue=issue) for issue in activated],
        "activated_issues": activated,
        "failed": failed,
        "candidate_count": len(candidates),
    }


def _auto_activation_candidates(
    *,
    config: WorkflowConfig,
    tracker_cfg: dict[str, Any],
    issues: list[dict[str, Any]],
    state: Any,
    add_label: str,
    exclude_labels: list[str],
) -> list[dict[str, Any]]:
    active_states = set(
        _configured_texts(tracker_cfg, "active_states", "active-states")
    )
    terminal_states = set(
        _configured_texts(tracker_cfg, "terminal_states", "terminal-states")
    )
    excluded = {label.lower() for label in exclude_labels}
    activation_label = add_label.lower()
    known_lane_ids = set(state.lanes)
    candidates: list[dict[str, Any]] = []
    for issue in issues:
        lane_id = _lane_id(config=config, issue=issue)
        if lane_id in known_lane_ids:
            continue
        issue_state = str(issue.get("state") or "").strip().lower()
        if active_states and issue_state not in active_states:
            continue
        labels = _issue_labels(issue)
        if activation_label in labels:
            continue
        if excluded.intersection(labels):
            continue
        if _has_open_blockers(issue, terminal_states=terminal_states):
            continue
        candidates.append(issue)
    return sorted(candidates, key=issue_priority_sort_key)


def _eligible_candidates(
    *,
    config: WorkflowConfig,
    tracker_cfg: dict[str, Any],
    issues: list[dict[str, Any]],
    state: Any,
) -> list[dict[str, Any]]:
    active_states = set(
        _configured_texts(tracker_cfg, "active_states", "active-states")
    )
    terminal_states = set(
        _configured_texts(tracker_cfg, "terminal_states", "terminal-states")
    )
    required_labels = set(
        _configured_texts(tracker_cfg, "required_labels", "required-labels")
    )
    exclude_labels = set(
        _configured_texts(tracker_cfg, "exclude_labels", "exclude-labels")
    )
    known_lane_ids = set(state.lanes)
    candidates: list[dict[str, Any]] = []
    for issue in issues:
        lane_id = _lane_id(config=config, issue=issue)
        if lane_id in known_lane_ids:
            continue
        issue_state = str(issue.get("state") or "").strip().lower()
        if active_states and issue_state not in active_states:
            continue
        labels = _issue_labels(issue)
        if required_labels and not required_labels.issubset(labels):
            continue
        if exclude_labels.intersection(labels):
            continue
        if _has_open_blockers(issue, terminal_states=terminal_states):
            continue
        candidates.append(issue)
    return sorted(candidates, key=issue_priority_sort_key)


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
    review = lane_mapping(lane, "actor_outputs").get("reviewer")
    if not isinstance(review, dict):
        return False
    return str(review.get("status") or "").strip().lower() in {
        "changes_requested",
        "needs_changes",
    }


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
    concurrency = _concurrency_config(config)
    if actor_name == "implementer":
        limit = concurrency["max_implementers"]
    elif actor_name == "reviewer":
        limit = concurrency["max_reviewers"]
    else:
        limit = concurrency["max_active_lanes"]
    if dispatch_counts.get(actor_name, 0) >= limit:
        raise RuntimeError(f"concurrency limit reached for actor {actor_name}")


def guard_actor_dispatch(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    stage_name: str,
) -> dict[str, Any]:
    lane_id = str(lane.get("lane_id") or "").strip()
    conflicts = _actor_dispatch_conflicts(
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
    lane["stage"] = next_stage
    lane["pending_retry"] = None
    _clear_engine_retry(config=config, lane=lane)
    set_lane_status(
        config=config,
        lane=lane,
        status="waiting",
        reason=f"advanced to {next_stage}",
    )


def queue_lane_retry(
    *, config: WorkflowConfig, lane: dict[str, Any], decision: OrchestratorDecision
) -> dict[str, Any]:
    current_attempt = max(int(lane.get("attempt") or 1), 1)
    schedule = _engine_store(config).schedule_retry(
        work_id=str(lane.get("lane_id") or ""),
        entry=_retry_engine_entry(lane),
        policy=_retry_policy(config),
        current_attempt=current_attempt,
        error=decision.reason or "retry requested",
        delay_type="workflow-retry",
        run_id=_lane_run_id(lane),
        now_iso=_now_iso(),
    )
    record = _retry_record(
        decision=decision,
        schedule=schedule,
    )
    retry_history = lane_list(lane, "retry_history")
    if schedule.get("status") == "limit_exceeded":
        retry_history.append(record)
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="retry_limit_exceeded",
            message=(
                f"retry limit exceeded for stage {decision.stage!r}; "
                f"attempt {current_attempt} reached max {schedule['max_attempts']}"
            ),
            artifacts={
                "retry": record,
                "last_actor_output": lane.get("last_actor_output"),
                "branch": lane.get("branch"),
                "pull_request": lane.get("pull_request"),
            },
        )
        return {
            "lane_id": lane.get("lane_id"),
            "decision": "retry",
            "status": "operator_attention",
            "reason": "retry_limit_exceeded",
        }

    pending = _pending_retry_projection(decision=decision, schedule=schedule)
    next_attempt = int(pending.get("attempt") or current_attempt)
    lane["attempt"] = next_attempt
    lane["stage"] = decision.stage
    lane["operator_attention"] = None
    lane["pending_retry"] = pending
    retry_history.append(record)
    set_lane_status(
        config=config,
        lane=lane,
        status="retry_queued",
        reason=decision.reason or "retry requested",
        actor=None,
    )
    return {
        "lane_id": lane.get("lane_id"),
        "decision": "retry",
        "status": "queued",
        "attempt": next_attempt,
        "due_at": pending["due_at"],
        "engine_retry": pending.get("engine_retry"),
    }


def _runtime_recovery_record(
    *, lane: dict[str, Any], session: dict[str, Any], age_seconds: int, message: str
) -> dict[str, Any]:
    actor_name = str(session.get("actor") or lane.get("actor") or "").strip()
    stage_name = str(session.get("stage") or lane.get("stage") or "").strip()
    resume_session_id = str(
        session.get("thread_id") or session.get("session_id") or ""
    ).strip()
    return {
        "status": "pending",
        "reason": "actor_interrupted",
        "message": message,
        "lane_id": lane.get("lane_id"),
        "stage": stage_name,
        "actor": actor_name,
        "resume_session_id": resume_session_id or None,
        "runtime_session": dict(session),
        "age_seconds": age_seconds,
        "branch": lane.get("branch"),
        "pull_request": lane.get("pull_request"),
        "created_at": _now_iso(),
    }


def _queue_interrupted_actor_recovery(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    recovery: dict[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    actor_name = str(recovery.get("actor") or "").strip()
    stage_name = str(recovery.get("stage") or "").strip()
    message = str(recovery.get("message") or "actor was interrupted")
    if not enabled:
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="actor_interrupted",
            message=message,
            artifacts={"recovery": recovery},
        )
        return {"status": "operator_attention", "reason": "auto recovery disabled"}
    if not actor_name or not stage_name:
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="actor_interrupted",
            message="cannot recover interrupted actor without actor and stage",
            artifacts={"recovery": recovery},
        )
        return {"status": "operator_attention", "reason": "missing actor or stage"}
    decision = OrchestratorDecision(
        decision="retry",
        stage=stage_name,
        lane_id=str(lane.get("lane_id") or ""),
        target=actor_name,
        reason="resume interrupted actor session",
        inputs={
            "feedback": message,
            "recovery": recovery,
            "resume_session_id": recovery.get("resume_session_id"),
        },
    )
    queued = queue_lane_retry(config=config, lane=lane, decision=decision)
    if queued.get("status") == "queued":
        recovery["status"] = "queued"
        recovery["retry"] = queued
        _append_engine_event(
            config=config,
            lane=lane,
            event_type=f"{config.workflow_name}.lane.runtime_recovery_queued",
            payload={"recovery": recovery},
            severity="warning",
        )
    return queued


def consume_lane_retry(*, config: WorkflowConfig, lane: dict[str, Any]) -> None:
    if not isinstance(lane.get("pending_retry"), dict):
        return
    lane["pending_retry"] = None
    _clear_engine_retry(config=config, lane=lane)


def lane_retry_inputs(
    *, lane: dict[str, Any], inputs: dict[str, Any]
) -> dict[str, Any]:
    if str(lane.get("status") or "").strip() != "retry_queued":
        return inputs
    pending = (
        lane.get("pending_retry") if isinstance(lane.get("pending_retry"), dict) else {}
    )
    retry_inputs = (
        pending.get("inputs") if isinstance(pending.get("inputs"), dict) else {}
    )
    return {**retry_inputs, **inputs, "retry": pending}


def lane_retry_is_due(lane: dict[str, Any], *, now_epoch: float | None = None) -> bool:
    pending = (
        lane.get("pending_retry") if isinstance(lane.get("pending_retry"), dict) else {}
    )
    due_at_epoch = _retry_due_at_epoch(pending)
    return (time.time() if now_epoch is None else now_epoch) >= due_at_epoch


def complete_lane(*, config: WorkflowConfig, lane: dict[str, Any], reason: str) -> None:
    failure = _completion_contract_failure(lane)
    if failure:
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="completion_contract_failed",
            message=failure,
            artifacts=_contract_artifacts(lane),
        )
        return
    auto_merge = _auto_merge_completed_pull_request(config=config, lane=lane)
    if auto_merge.get("status") == "error":
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="auto_merge_failed",
            message=str(auto_merge.get("error") or "auto-merge failed"),
            artifacts={"auto_merge": auto_merge, "pull_request": lane.get("pull_request")},
        )
        return
    lane["completion_auto_merge"] = auto_merge
    cleanup = _cleanup_completed_lane(config=config, lane=lane)
    if cleanup.get("status") == "error":
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="tracker_cleanup_failed",
            message=str(cleanup.get("error") or "tracker cleanup failed"),
            artifacts={"cleanup": cleanup},
        )
        return
    lane["completion_cleanup"] = cleanup
    lane["pending_retry"] = None
    _clear_engine_retry(config=config, lane=lane)
    set_lane_status(config=config, lane=lane, status="complete", reason=reason)
    _release_lane_lease(config=config, lane=lane, reason=reason)


def _auto_merge_completed_pull_request(
    *, config: WorkflowConfig, lane: dict[str, Any]
) -> dict[str, Any]:
    cfg = _completion_auto_merge_config(config)
    if not cfg["enabled"]:
        return {"status": "skipped", "reason": "auto-merge disabled"}
    existing = lane.get("completion_auto_merge")
    if isinstance(existing, dict) and existing.get("status") == "ok":
        return existing
    method = str(cfg["method"] or "").strip().lower()
    if method not in {"squash", "merge", "rebase"}:
        return {
            "status": "error",
            "error": f"unsupported auto-merge method {method!r}",
        }
    pr_number = _pull_request_number(lane)
    if not pr_number:
        return {"status": "error", "error": "pull request number missing"}
    if _pull_request_is_merged(lane):
        return {
            "status": "ok",
            "method": method,
            "delete_branch": cfg["delete_branch"],
            "pull_request": {"number": pr_number, "already_merged": True},
        }
    code_host_cfg = _code_host_config(config)
    if not code_host_cfg:
        return {
            "status": "error",
            "error": "auto-merge requires code-host config",
        }
    try:
        client = build_code_host_client(
            workflow_root=config.workflow_root,
            code_host_cfg=code_host_cfg,
            repo_path=_repository_path(config),
        )
        readiness = _pull_request_merge_readiness(client=client, pr_number=pr_number)
        if readiness.get("already_merged"):
            pull_request = lane_mapping(lane, "pull_request")
            pull_request["state"] = "merged"
            pull_request["merged"] = True
            return {
                "status": "ok",
                "method": method,
                "delete_branch": cfg["delete_branch"],
                "readiness": readiness,
                "pull_request": {"number": pr_number, "already_merged": True},
            }
        if not readiness.get("ready"):
            return {
                "status": "error",
                "error": _merge_readiness_error(readiness),
                "readiness": readiness,
            }
        result = client.merge_pull_request(
            pr_number,
            method=method,
            squash=method == "squash",
            delete_branch=cfg["delete_branch"],
        )
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    payload = {
        "status": "ok" if result.get("ok") is not False else "error",
        "method": method,
        "delete_branch": cfg["delete_branch"],
        "pull_request": result,
    }
    if payload["status"] == "error":
        payload["error"] = str(result.get("error") or "pull request merge failed")
        _append_engine_event(
            config=config,
            lane=lane,
            event_type=f"{config.workflow_name}.lane.auto_merge_failed",
            payload=payload,
            severity="error",
        )
        return payload
    pull_request = lane_mapping(lane, "pull_request")
    pull_request["state"] = "merged"
    pull_request["merged"] = True
    pull_request["merged_at"] = _now_iso()
    _append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.auto_merged",
        payload=payload,
    )
    return payload


def _pull_request_merge_readiness(client: Any, pr_number: str) -> dict[str, Any]:
    checker = getattr(client, "pull_request_merge_status", None)
    if not callable(checker):
        return {
            "ready": True,
            "status": "skipped",
            "reason": "code host does not expose merge readiness",
            "blockers": [],
        }
    readiness = checker(pr_number)
    if not isinstance(readiness, dict):
        return {
            "ready": False,
            "status": "blocked",
            "blockers": [
                {
                    "kind": "invalid_merge_readiness",
                    "message": "code host returned invalid merge readiness payload",
                }
            ],
        }
    return readiness


def _merge_readiness_error(readiness: dict[str, Any]) -> str:
    blockers = readiness.get("blockers") if isinstance(readiness.get("blockers"), list) else []
    if not blockers:
        return "pull request is not ready to merge"
    first = blockers[0] if isinstance(blockers[0], dict) else {}
    message = str(first.get("message") or first.get("kind") or "").strip()
    if len(blockers) == 1:
        return message or "pull request is not ready to merge"
    return f"{message or 'pull request is not ready to merge'} (+{len(blockers) - 1} more)"


def release_lane(*, config: WorkflowConfig, lane: dict[str, Any], reason: str) -> None:
    lane["pending_retry"] = None
    _clear_engine_retry(config=config, lane=lane)
    set_lane_status(
        config=config,
        lane=lane,
        status="released",
        reason=reason,
        actor=None,
    )
    _release_lane_lease(config=config, lane=lane, reason=reason)


def _cleanup_completed_lane(
    *, config: WorkflowConfig, lane: dict[str, Any]
) -> dict[str, Any]:
    tracker_cfg = _tracker_config(config)
    if not tracker_cfg:
        return {"status": "skipped", "reason": "no tracker config"}
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    issue_id = str(issue.get("id") or "").strip()
    if not issue_id:
        return {"status": "skipped", "reason": "lane issue is missing id"}
    completion = _completion_labels(config)
    try:
        client = build_tracker_client(
            workflow_root=config.workflow_root,
            tracker_cfg=tracker_cfg,
            repo_path=_repository_path(config),
        )
        removed = client.remove_labels(issue_id, completion["remove"])
        added = client.add_labels(issue_id, completion["add"])
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    return {
        "status": "ok",
        "issue_id": issue_id,
        "removed": completion["remove"] if removed else [],
        "added": completion["add"] if added else [],
    }


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


def _new_lane(
    *,
    config: WorkflowConfig,
    lane_id: str,
    issue: dict[str, Any],
    lease: dict[str, Any],
) -> dict[str, Any]:
    return {
        "lane_id": lane_id,
        "issue": issue,
        "stage": config.first_stage,
        "status": "claimed",
        "actor": None,
        "thread_id": None,
        "turn_id": None,
        "runtime_session": {},
        "runtime_sessions": {},
        "branch": issue.get("branch_name"),
        "pull_request": None,
        "attempt": 1,
        "last_progress_at": _now_iso(),
        "last_actor_output": None,
        "actor_outputs": {},
        "action_results": {},
        "stage_outputs": {},
        "pending_retry": None,
        "retry_history": [],
        "operator_attention": None,
        "claim": {"state": "Claimed", "lease": lease},
    }


def record_actor_output(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    output: dict[str, Any],
) -> None:
    actor_outputs = lane_mapping(lane, "actor_outputs")
    actor_outputs[actor_name] = output
    lane["last_actor_output"] = output
    lane["last_progress_at"] = _now_iso()
    lane["pending_retry"] = None
    _clear_engine_retry(config=config, lane=lane)
    stage_outputs = lane_mapping(lane, "stage_outputs")
    stage_outputs[lane_stage(lane)] = {
        **dict(stage_outputs.get(lane_stage(lane)) or {}),
        "last_actor": actor_name,
    }
    branch = _first_text(output, "branch", "branch_name", "branch-name")
    if branch:
        lane["branch"] = branch
    pull_request = output.get("pull_request") or output.get("pr")
    if isinstance(pull_request, dict):
        lane["pull_request"] = _normalize_pull_request(pull_request)
    elif pull_request:
        lane["pull_request"] = {"url": str(pull_request)}
    thread_id = _first_text(output, "thread_id", "thread-id")
    if thread_id:
        lane["thread_id"] = thread_id
    turn_id = _first_text(output, "turn_id", "turn-id")
    if turn_id:
        lane["turn_id"] = turn_id
    _append_engine_event(
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
    started_at = _now_iso()
    session_key, session = _mutable_actor_runtime_session(
        lane=lane, actor_name=actor_name, stage_name=stage_name
    )
    run = _start_engine_actor_run(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        runtime_meta=runtime_meta,
        started_at=started_at,
    )
    session.update(
        {
            **_runtime_meta_payload(runtime_meta),
            "run_id": run["run_id"],
            "session_key": session_key,
            "actor": actor_name,
            "stage": stage_name,
            "attempt": int(lane.get("attempt") or 0),
            "status": "running",
            "started_at": started_at,
            "updated_at": started_at,
        }
    )
    _apply_runtime_session_ids(lane=lane, session=session)
    _set_latest_runtime_session(lane=lane, session_key=session_key, session=session)
    _upsert_engine_runtime_session(config=config, lane=lane)
    _append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.runtime_started",
        payload={"runtime_session": session},
    )


def record_actor_runtime_progress(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    runtime_meta: dict[str, Any],
) -> None:
    session_key, session = _current_runtime_session(lane)
    session.update(_runtime_meta_payload(runtime_meta))
    session["status"] = "running"
    session["updated_at"] = _now_iso()
    _apply_runtime_session_ids(lane=lane, session=session)
    _set_latest_runtime_session(lane=lane, session_key=session_key, session=session)
    _upsert_engine_runtime_session(config=config, lane=lane)
    lane["last_progress_at"] = _now_iso()
    _append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.runtime_progress",
        payload={"runtime_session": session},
    )


def record_actor_runtime_result(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    runtime_meta: dict[str, Any],
    status: str,
) -> None:
    updated_at = _now_iso()
    session_key, session = _current_runtime_session(lane)
    session.update(_runtime_meta_payload(runtime_meta))
    session["status"] = _normalize_runtime_session_status(status)
    session["updated_at"] = updated_at
    _apply_runtime_session_ids(lane=lane, session=session)
    _set_latest_runtime_session(lane=lane, session_key=session_key, session=session)
    _upsert_engine_runtime_session(config=config, lane=lane)
    _finish_engine_actor_run(
        config=config,
        lane=lane,
        status=session["status"],
        error=str(runtime_meta.get("last_message") or "") or None,
        metadata=_runtime_meta_payload(runtime_meta),
        completed_at=updated_at,
    )
    lane["last_progress_at"] = updated_at
    _append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.runtime_{status}",
        payload={"runtime_session": session},
    )


def record_actor_runtime_interrupted(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    reason: str,
    message: str,
    age_seconds: int,
) -> None:
    interrupted_at = _now_iso()
    session_key, session = _current_runtime_session(lane)
    session.update(
        {
            "status": "interrupted",
            "interrupted_at": interrupted_at,
            "updated_at": interrupted_at,
            "last_event": reason,
            "last_message": message,
            "age_seconds": age_seconds,
        }
    )
    _apply_runtime_session_ids(lane=lane, session=session)
    _set_latest_runtime_session(lane=lane, session_key=session_key, session=session)
    _upsert_engine_runtime_session(config=config, lane=lane)
    _finish_engine_actor_run(
        config=config,
        lane=lane,
        status="interrupted",
        error=message,
        metadata={"reason": reason, "age_seconds": age_seconds},
        completed_at=interrupted_at,
    )
    lane["last_progress_at"] = interrupted_at
    _append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.runtime_interrupted",
        payload={"runtime_session": session, "reason": reason},
        severity="warning",
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
    running_entries: dict[str, dict[str, Any]] = {}
    runtime_sessions: dict[str, dict[str, Any]] = {}
    runtime_totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "turn_count": 0,
    }
    last_rate_limits: dict[str, Any] | None = None

    for lane in state.lanes.values():
        if not isinstance(lane, dict):
            continue
        lane_id = str(lane.get("lane_id") or "").strip()
        if not lane_id:
            continue
        entry = _scheduler_entry(lane)
        status = str(lane.get("status") or "")
        if status == "running":
            running_entries[lane_id] = entry
        session = lane.get("runtime_session")
        if isinstance(session, dict) and _runtime_session_has_identity(session):
            runtime_sessions[lane_id] = entry
            tokens = (
                session.get("tokens") if isinstance(session.get("tokens"), dict) else {}
            )
            runtime_totals["input_tokens"] += int(tokens.get("input_tokens") or 0)
            runtime_totals["output_tokens"] += int(tokens.get("output_tokens") or 0)
            runtime_totals["total_tokens"] += int(tokens.get("total_tokens") or 0)
            runtime_totals["turn_count"] += int(session.get("turn_count") or 0)
            rate_limits = session.get("rate_limits")
            if isinstance(rate_limits, dict):
                last_rate_limits = rate_limits
    if last_rate_limits is not None:
        runtime_totals["rate_limits"] = last_rate_limits

    _engine_store(config).save_scheduler(
        retry_entries=None,
        running_entries=running_entries,
        runtime_totals=runtime_totals,
        runtime_sessions=runtime_sessions,
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
        _notify_review_changes_requested(config=config, lane=lane, output=output)
    if status in {"blocked", "failed"} or blockers:
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason=_blocker_reason(output) or status or "actor_blocked",
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


def _completion_contract_failure(lane: dict[str, Any]) -> str:
    if lane_stage(lane) != "review":
        return ""
    review = lane_mapping(lane, "actor_outputs").get("reviewer")
    if not isinstance(review, dict):
        return "completion requires reviewer output"
    if str(review.get("status") or "").strip().lower() != "approved":
        return "completion requires reviewer status `approved`"
    if not _pull_request_url(lane):
        return "completion requires pull_request.url"
    return ""


def _pull_request_url(lane: dict[str, Any]) -> str:
    pull_request = lane.get("pull_request")
    if isinstance(pull_request, dict):
        return str(pull_request.get("url") or "").strip()
    return ""


def _pull_request_is_merged(lane: dict[str, Any]) -> bool:
    pull_request = lane.get("pull_request")
    if not isinstance(pull_request, dict):
        return False
    state = str(pull_request.get("state") or "").strip().lower()
    return state == "merged" or bool(pull_request.get("merged"))


def _contract_artifacts(lane: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": lane.get("stage"),
        "actor_outputs": lane.get("actor_outputs"),
        "pull_request": lane.get("pull_request"),
        "branch": lane.get("branch"),
        "completion_auto_merge": lane.get("completion_auto_merge"),
    }


def lane_recovery_artifacts(
    lane: dict[str, Any], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    artifacts = {
        "run_id": _lane_run_id(lane),
        "runtime_session": session or None,
        "runtime_sessions": lane.get("runtime_sessions"),
        "thread_id": lane.get("thread_id") or session.get("thread_id"),
        "turn_id": lane.get("turn_id") or session.get("turn_id"),
        "branch": lane.get("branch"),
        "pull_request": lane.get("pull_request"),
        "last_actor_output": lane.get("last_actor_output"),
        "runtime_recovery": lane.get("runtime_recovery"),
    }
    artifacts.update(dict(extra or {}))
    return {key: value for key, value in artifacts.items() if value not in (None, "")}


def runtime_session_key(*, actor_name: str, stage_name: str) -> str:
    actor = str(actor_name or "").strip()
    stage = str(stage_name or "").strip()
    return f"{stage}:{actor}" if actor and stage else actor or stage


def lane_actor_runtime_session(
    lane: dict[str, Any], *, actor_name: str, stage_name: str
) -> dict[str, Any]:
    key = runtime_session_key(actor_name=actor_name, stage_name=stage_name)
    sessions = lane.get("runtime_sessions")
    if isinstance(sessions, dict):
        session = sessions.get(key)
        if isinstance(session, dict):
            return session
    latest = lane.get("runtime_session")
    if isinstance(latest, dict) and _runtime_session_matches(
        latest, actor_name=actor_name, stage_name=stage_name
    ):
        return latest
    return {}


def _lane_runtime_session_summaries(lanes: Any) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        sessions = lane.get("runtime_sessions")
        if isinstance(sessions, dict) and sessions:
            for key, session in sessions.items():
                if not isinstance(session, dict):
                    continue
                summaries.append(
                    {
                        **dict(session),
                        "session_key": session.get("session_key") or key,
                        "lane_id": lane.get("lane_id"),
                        "lane_stage": lane.get("stage"),
                        "lane_actor": lane.get("actor"),
                    }
                )
            continue
        latest = lane.get("runtime_session")
        if isinstance(latest, dict):
            summaries.append(
                {
                    **dict(latest),
                    "lane_id": lane.get("lane_id"),
                    "lane_stage": lane.get("stage"),
                    "lane_actor": lane.get("actor"),
                }
            )
    return summaries


def lane_mapping(lane: dict[str, Any], key: str) -> dict[str, Any]:
    value = lane.get(key)
    if isinstance(value, dict):
        return value
    lane[key] = {}
    return lane[key]


def lane_list(lane: dict[str, Any], key: str) -> list[Any]:
    value = lane.get(key)
    if isinstance(value, list):
        return value
    lane[key] = []
    return lane[key]


def _lane_id(*, config: WorkflowConfig, issue: dict[str, Any]) -> str:
    tracker_cfg = _tracker_config(config)
    prefix = str(tracker_cfg.get("kind") or "tracker").strip() or "tracker"
    issue_id = str(issue.get("id") or issue.get("identifier") or "").strip()
    if not issue_id:
        raise RuntimeError("tracker issue is missing id")
    return f"{prefix}#{issue_id.lstrip('#')}"


def lane_stage(lane: dict[str, Any]) -> str:
    return str(lane.get("stage") or "").strip()


def lane_is_terminal(lane: dict[str, Any]) -> bool:
    return str(lane.get("status") or "").strip() in _TERMINAL_LANE_STATUSES


def _count_lanes_with_status(lanes: list[dict[str, Any]], status: str) -> int:
    return sum(1 for lane in lanes if str(lane.get("status") or "") == status)


def lane_by_id(state: Any, lane_id: str) -> dict[str, Any]:
    lane = state.lanes.get(lane_id)
    if not isinstance(lane, dict):
        raise RuntimeError(f"unknown lane {lane_id!r}")
    return lane


def lane_summary(lane: dict[str, Any]) -> dict[str, Any]:
    return _lane_summary(lane)


def _lane_summary(lane: dict[str, Any]) -> dict[str, Any]:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    attention = (
        lane.get("operator_attention")
        if isinstance(lane.get("operator_attention"), dict)
        else {}
    )
    pending_retry = (
        lane.get("pending_retry") if isinstance(lane.get("pending_retry"), dict) else {}
    )
    return {
        "lane_id": lane.get("lane_id"),
        "status": lane.get("status"),
        "stage": lane.get("stage"),
        "actor": lane.get("actor"),
        "attempt": lane.get("attempt"),
        "issue": {
            "identifier": issue.get("identifier") or issue.get("id"),
            "title": issue.get("title"),
            "url": issue.get("url"),
        },
        "branch": lane.get("branch"),
        "pull_request": lane.get("pull_request"),
        "operator_attention": attention or None,
        "pending_retry": pending_retry or None,
        "thread_id": lane.get("thread_id"),
        "turn_id": lane.get("turn_id"),
        "last_progress_at": lane.get("last_progress_at"),
    }


def active_lanes(state: Any) -> list[dict[str, Any]]:
    return [
        lane
        for lane in state.lanes.values()
        if isinstance(lane, dict) and not lane_is_terminal(lane)
    ]


def append_lane_event(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
) -> None:
    _append_engine_event(
        config=config,
        lane=lane,
        event_type=event_type,
        payload=payload,
    )


def now_iso() -> str:
    return _now_iso()


def set_lane_status(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    status: str,
    reason: str,
    actor: str | None | object = ...,
) -> None:
    lane["status"] = status
    if actor is not ...:
        lane["actor"] = actor
    lane["last_progress_at"] = _now_iso()
    claim = lane_mapping(lane, "claim")
    if status == "retry_queued":
        claim["state"] = "RetryQueued"
    elif status == "running":
        claim["state"] = "Running"
    elif status in _TERMINAL_LANE_STATUSES:
        claim["state"] = "Released"
    else:
        claim["state"] = "Claimed"
    claim["reason"] = reason
    _record_engine_lane(config=config, lane=lane)
    _append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.{status}",
        payload={"lane_id": lane.get("lane_id"), "status": status, "reason": reason},
    )


def set_lane_operator_attention(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    reason: str,
    message: str,
    artifacts: dict[str, Any] | None = None,
) -> None:
    lane["operator_attention"] = {
        "reason": reason,
        "message": message,
        "artifacts": lane_recovery_artifacts(lane, artifacts),
    }
    set_lane_status(
        config=config,
        lane=lane,
        status="operator_attention",
        reason=reason,
        actor=None,
    )


def _has_open_blockers(issue: dict[str, Any], *, terminal_states: set[str]) -> bool:
    for blocker in issue.get("blocked_by") or []:
        if not isinstance(blocker, dict):
            return True
        blocker_state = str(blocker.get("state") or "").strip().lower()
        if not blocker_state or blocker_state not in terminal_states:
            return True
    return False


def _issue_is_still_active(
    *, tracker_cfg: dict[str, Any], issue: dict[str, Any]
) -> bool:
    active_states = set(
        _configured_texts(tracker_cfg, "active_states", "active-states")
    )
    exclude_labels = set(
        _configured_texts(tracker_cfg, "exclude_labels", "exclude-labels")
    )
    state = str(issue.get("state") or "").strip().lower()
    if active_states and state not in active_states:
        return False
    if exclude_labels.intersection(_issue_labels(issue)):
        return False
    return True


def _issue_labels(issue: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for label in issue.get("labels") or []:
        text = str(label.get("name") if isinstance(label, dict) else label).strip()
        if text:
            labels.add(text.lower())
    return labels


def _configured_texts(config: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = config.get(key)
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
    return []


def _concurrency_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("concurrency")
    cfg = raw if isinstance(raw, dict) else {}
    return {
        "max_active_lanes": _positive_int(
            cfg, "max-active-lanes", "max_active_lanes", default=1
        ),
        "max_implementers": _positive_int(
            cfg, "max-implementers", "max_implementers", default=1
        ),
        "max_reviewers": _positive_int(
            cfg, "max-reviewers", "max_reviewers", default=1
        ),
        "per_lane_lock": bool(cfg.get("per-lane-lock", cfg.get("per_lane_lock", True))),
    }


def _intake_auto_activate_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("intake")
    intake = raw if isinstance(raw, dict) else {}
    auto_raw = intake.get("auto-activate") or intake.get("auto_activate")
    cfg = auto_raw if isinstance(auto_raw, dict) else {}
    tracker_cfg = _tracker_config(config)
    required_labels = _configured_texts(
        tracker_cfg, "required_labels", "required-labels"
    )
    default_add_label = required_labels[0] if required_labels else "active"
    add_label = str(
        cfg.get("add_label") or cfg.get("add-label") or default_add_label
    ).strip()
    exclude_labels = _configured_texts(cfg, "exclude_labels", "exclude-labels")
    if not exclude_labels:
        exclude_labels = _configured_texts(
            tracker_cfg, "exclude_labels", "exclude-labels"
        )
    return {
        "enabled": _configured_bool(cfg, "enabled", default=False),
        "add_label": add_label or default_add_label,
        "exclude_labels": exclude_labels,
        "max_per_tick": _positive_int(cfg, "max-per-tick", "max_per_tick", default=1),
    }


def _recovery_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("recovery")
    cfg = raw if isinstance(raw, dict) else {}
    return {
        "running_stale_seconds": _nonnegative_int(
            cfg, "running-stale-seconds", "running_stale_seconds", default=1800
        ),
        "auto_retry_interrupted": _configured_bool(
            cfg,
            "auto-retry-interrupted",
            "auto_retry_interrupted",
            default=True,
        ),
    }


def _retry_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("retry")
    cfg = raw if isinstance(raw, dict) else {}
    return {
        "max_attempts": _positive_int(cfg, "max-attempts", "max_attempts", default=3),
        "initial_delay_seconds": _nonnegative_int(
            cfg,
            "initial-delay-seconds",
            "initial_delay_seconds",
            default=0,
        ),
        "backoff_multiplier": _positive_float(
            cfg,
            "backoff-multiplier",
            "backoff_multiplier",
            default=2.0,
        ),
        "max_delay_seconds": _nonnegative_int(
            cfg,
            "max-delay-seconds",
            "max_delay_seconds",
            default=300,
        ),
    }


def _retry_policy(config: WorkflowConfig) -> RetryPolicy:
    cfg = _retry_config(config)
    return RetryPolicy(
        max_attempts=cfg["max_attempts"],
        initial_delay_seconds=cfg["initial_delay_seconds"],
        backoff_multiplier=cfg["backoff_multiplier"],
        max_delay_seconds=cfg["max_delay_seconds"],
    )


def _retry_record(
    *,
    decision: OrchestratorDecision,
    schedule: dict[str, Any],
) -> dict[str, Any]:
    due_at_epoch = _schedule_due_at_epoch(schedule)
    return {
        "status": schedule.get("status"),
        "queued_at": _engine_retry_updated_at(schedule) or _now_iso(),
        "stage": decision.stage,
        "target": decision.target,
        "reason": decision.reason,
        "inputs": decision.inputs,
        "current_attempt": int(schedule.get("current_attempt") or 0),
        "next_attempt": int(schedule.get("next_attempt") or 0),
        "max_attempts": int(schedule.get("max_attempts") or 0),
        "delay_seconds": schedule.get("delay_seconds"),
        "due_at": _epoch_to_iso(due_at_epoch) if due_at_epoch is not None else None,
        "due_at_epoch": due_at_epoch,
        "engine_retry": schedule.get("engine_retry") or None,
    }


def _pending_retry_projection(
    *, decision: OrchestratorDecision, schedule: dict[str, Any]
) -> dict[str, Any]:
    due_at_epoch = _schedule_due_at_epoch(schedule)
    return {
        "source": "engine_retry_queue",
        "stage": decision.stage,
        "target": decision.target,
        "reason": decision.reason,
        "inputs": decision.inputs,
        "attempt": int(schedule.get("next_attempt") or 0),
        "current_attempt": int(schedule.get("current_attempt") or 0),
        "queued_at": _engine_retry_updated_at(schedule) or _now_iso(),
        "delay_seconds": int(schedule.get("delay_seconds") or 0),
        "due_at": _epoch_to_iso(due_at_epoch or time.time()),
        "due_at_epoch": due_at_epoch if due_at_epoch is not None else time.time(),
        "max_attempts": int(schedule.get("max_attempts") or 0),
        "engine_retry": schedule.get("engine_retry") or None,
    }


def _engine_retry_updated_at(schedule: dict[str, Any]) -> str:
    engine_retry = (
        schedule.get("engine_retry")
        if isinstance(schedule.get("engine_retry"), dict)
        else {}
    )
    return str(engine_retry.get("updated_at") or "").strip()


def _schedule_due_at_epoch(schedule: dict[str, Any]) -> float | None:
    value = schedule.get("due_at_epoch")
    if value in (None, ""):
        engine_retry = (
            schedule.get("engine_retry")
            if isinstance(schedule.get("engine_retry"), dict)
            else {}
        )
        value = engine_retry.get("due_at_epoch")
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _retry_due_at_epoch(pending_retry: dict[str, Any]) -> float:
    value = pending_retry.get("due_at_epoch")
    if value not in (None, ""):
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return _iso_to_epoch(str(pending_retry.get("due_at") or ""), default=time.time())


def _completion_labels(config: WorkflowConfig) -> dict[str, list[str]]:
    raw = config.raw.get("completion")
    cfg = raw if isinstance(raw, dict) else {}
    return {
        "remove": _configured_texts(cfg, "remove_labels", "remove-labels")
        or ["active"],
        "add": _configured_texts(cfg, "add_labels", "add-labels") or ["done"],
    }


def _completion_auto_merge_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("completion")
    completion = raw if isinstance(raw, dict) else {}
    raw_auto_merge = (
        completion.get("auto-merge")
        or completion.get("auto_merge")
        or completion.get("automerge")
    )
    cfg = raw_auto_merge if isinstance(raw_auto_merge, dict) else {}
    method = str(
        cfg.get("method") or cfg.get("merge-method") or cfg.get("merge_method") or "squash"
    ).strip().lower()
    return {
        "enabled": _configured_bool(cfg, "enabled", default=False),
        "method": method or "squash",
        "delete_branch": _configured_bool(
            cfg, "delete-branch", "delete_branch", default=True
        ),
    }


def _review_notification_config(config: WorkflowConfig) -> dict[str, bool]:
    raw = config.raw.get("notifications")
    root = raw if isinstance(raw, dict) else {}
    review = root.get("review-changes-requested") or root.get(
        "review_changes_requested"
    )
    cfg = review if isinstance(review, dict) else {}
    return {
        "pull_request_review": _configured_bool(
            cfg, "pull-request-review", "pull_request_review", default=False
        ),
        "pull_request_comment": _configured_bool(
            cfg, "pull-request-comment", "pull_request_comment", default=False
        ),
        "issue_comment": _configured_bool(
            cfg, "issue-comment", "issue_comment", default=False
        ),
    }


def _notify_review_changes_requested(
    *, config: WorkflowConfig, lane: dict[str, Any], output: dict[str, Any]
) -> dict[str, Any]:
    notification_cfg = _review_notification_config(config)
    fingerprint = _review_changes_requested_fingerprint(lane=lane, output=output)
    existing = _existing_review_notification(lane=lane, fingerprint=fingerprint)
    if existing:
        return existing
    if not any(notification_cfg.values()):
        return _record_lane_notification(
            config=config,
            lane=lane,
            payload={
                "event": "review_changes_requested",
                "status": "skipped",
                "fingerprint": fingerprint,
                "reason": "notifications disabled",
            },
        )
    code_host_cfg = _code_host_config(config)
    if not code_host_cfg:
        return _record_lane_notification(
            config=config,
            lane=lane,
            payload={
                "event": "review_changes_requested",
                "status": "skipped",
                "fingerprint": fingerprint,
                "reason": "no code-host config",
            },
        )
    body = _review_changes_requested_body(lane=lane, output=output)
    result: dict[str, Any] = {
        "event": "review_changes_requested",
        "status": "ok",
        "fingerprint": fingerprint,
        "targets": {},
    }
    try:
        client = build_code_host_client(
            workflow_root=config.workflow_root,
            code_host_cfg=code_host_cfg,
            repo_path=_repository_path(config),
        )
        if notification_cfg["pull_request_comment"]:
            pr_number = _pull_request_number(lane)
            result["targets"]["pull_request"] = (
                client.comment_on_pull_request(pr_number, body=body)
                if pr_number
                else {"ok": False, "error": "pull request number missing"}
            )
        if notification_cfg["pull_request_review"]:
            pr_number = _pull_request_number(lane)
            result["targets"]["pull_request_review"] = (
                client.request_changes_on_pull_request(pr_number, body=body)
                if pr_number
                else {"ok": False, "error": "pull request number missing"}
            )
        if notification_cfg["issue_comment"]:
            issue_number = _issue_number(lane)
            result["targets"]["issue"] = (
                client.comment_on_issue(issue_number, body=body)
                if issue_number
                else {"ok": False, "error": "issue number missing"}
            )
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
    if any(
        isinstance(target, dict) and target.get("ok") is False
        for target in dict(result.get("targets") or {}).values()
    ) and result.get("status") == "ok":
        result["status"] = "partial"
    return _record_lane_notification(config=config, lane=lane, payload=result)


def _existing_review_notification(
    *, lane: dict[str, Any], fingerprint: str
) -> dict[str, Any] | None:
    for record in reversed(lane_list(lane, "notifications")):
        if not isinstance(record, dict):
            continue
        if record.get("event") != "review_changes_requested":
            continue
        if record.get("fingerprint") != fingerprint:
            continue
        if record.get("status") in {"ok", "partial"}:
            return record
    return None


def _review_changes_requested_fingerprint(
    *, lane: dict[str, Any], output: dict[str, Any]
) -> str:
    payload = {
        "lane_id": lane.get("lane_id"),
        "pull_request": _pull_request_number(lane),
        "issue": _issue_number(lane),
        "status": output.get("status"),
        "summary": output.get("summary"),
        "required_fixes": output.get("required_fixes"),
        "findings": output.get("findings"),
        "verification_gaps": output.get("verification_gaps"),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_lane_notification(
    *, config: WorkflowConfig, lane: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    record = {"created_at": _now_iso(), **payload}
    lane_list(lane, "notifications").append(record)
    _append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.notification",
        payload=record,
        severity="warning" if record.get("status") in {"error", "partial"} else "info",
    )
    return record


def _review_changes_requested_body(
    *, lane: dict[str, Any], output: dict[str, Any]
) -> str:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    lines = [
        "### Sprints review requested changes",
        "",
        f"Lane: {lane.get('lane_id')}",
    ]
    issue_label = " ".join(
        part
        for part in [
            str(issue.get("identifier") or issue.get("id") or "").strip(),
            str(issue.get("title") or "").strip(),
        ]
        if part
    )
    if issue_label:
        lines.append(f"Issue: {issue_label}")
    summary = str(output.get("summary") or "").strip()
    if summary:
        lines.extend(["", "Summary:", summary])
    _append_markdown_items(lines, "Required fixes", output.get("required_fixes"))
    _append_markdown_items(lines, "Findings", output.get("findings"))
    _append_markdown_items(lines, "Verification gaps", output.get("verification_gaps"))
    lines.extend(["", "Generated by Sprints."])
    return "\n".join(lines).strip()


def _append_markdown_items(lines: list[str], title: str, value: Any) -> None:
    if not isinstance(value, list) or not value:
        return
    lines.extend(["", f"{title}:"])
    for index, item in enumerate(value, start=1):
        lines.append(f"{index}. {_markdown_item_text(item)}")


def _markdown_item_text(item: Any) -> str:
    if isinstance(item, dict):
        parts = [
            f"{key}: {item[key]}"
            for key in sorted(item)
            if item.get(key) not in (None, "", [], {})
        ]
        return "; ".join(parts) or "{}"
    return str(item)


def _pull_request_number(lane: dict[str, Any]) -> str:
    pull_request = lane.get("pull_request")
    if not isinstance(pull_request, dict):
        return ""
    for key in ("number", "pr_number"):
        value = pull_request.get(key)
        if value not in (None, ""):
            number = _trailing_number(value)
            if number:
                return number
    url = str(pull_request.get("url") or "").strip()
    match = re.search(r"/pull/([0-9]+)(?:$|[/?#])", url)
    if match:
        return match.group(1)
    return _trailing_number(pull_request.get("id"))


def _issue_number(lane: dict[str, Any]) -> str:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    for key in ("number", "id", "identifier"):
        value = issue.get(key)
        if value not in (None, ""):
            number = _trailing_number(value)
            if number:
                return number
    return ""


def _trailing_number(value: Any) -> str:
    text = str(value or "").strip().lstrip("#")
    match = re.search(r"([0-9]+)$", text)
    return match.group(1) if match else ""


def _positive_int(config: dict[str, Any], *keys: str, default: int) -> int:
    for key in keys:
        value = config.get(key)
        if value not in (None, ""):
            try:
                return max(int(value), 1)
            except (TypeError, ValueError):
                return default
    return default


def _nonnegative_int(config: dict[str, Any], *keys: str, default: int) -> int:
    for key in keys:
        value = config.get(key)
        if value not in (None, ""):
            try:
                return max(int(value), 0)
            except (TypeError, ValueError):
                return default
    return default


def _positive_float(config: dict[str, Any], *keys: str, default: float) -> float:
    for key in keys:
        value = config.get(key)
        if value not in (None, ""):
            try:
                return max(float(value), 1.0)
            except (TypeError, ValueError):
                return default
    return default


def _configured_bool(config: dict[str, Any], *keys: str, default: bool) -> bool:
    for key in keys:
        value = config.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def _tracker_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("tracker")
    return raw if isinstance(raw, dict) else {}


def _code_host_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("code-host")
    return raw if isinstance(raw, dict) else {}


def _repository_path(config: WorkflowConfig) -> Path | None:
    raw = config.raw.get("repository")
    if not isinstance(raw, dict):
        return None
    value = str(raw.get("local-path") or raw.get("local_path") or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (config.workflow_root / path).resolve()


def _engine_store(config: WorkflowConfig) -> EngineStore:
    return EngineStore(
        db_path=runtime_paths(config.workflow_root)["db_path"],
        workflow=config.workflow_name,
    )


def _record_engine_lane(*, config: WorkflowConfig, lane: dict[str, Any]) -> None:
    lane_id = str(lane.get("lane_id") or "").strip()
    if not lane_id:
        return
    _engine_store(config).record_work_item(
        work_id=lane_id,
        entry=_engine_lane_entry(lane),
        now_iso=_now_iso(),
    )


def _engine_lane_entry(lane: dict[str, Any]) -> dict[str, Any]:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    return {
        "work_id": lane.get("lane_id"),
        "issue_id": lane.get("lane_id"),
        "identifier": issue.get("identifier") or lane.get("lane_id"),
        "state": lane.get("status"),
        "status": lane.get("status"),
        "title": issue.get("title"),
        "url": issue.get("url"),
        "source": "workflow-lane",
        "metadata": {
            "stage": lane.get("stage"),
            "actor": lane.get("actor"),
            "attempt": lane.get("attempt"),
            "branch": lane.get("branch"),
            "pull_request": lane.get("pull_request"),
            "thread_id": lane.get("thread_id"),
            "turn_id": lane.get("turn_id"),
            "operator_attention": lane.get("operator_attention"),
            "pending_retry": lane.get("pending_retry"),
            "claim": lane.get("claim"),
        },
    }


def _retry_engine_entry(lane: dict[str, Any]) -> dict[str, Any]:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    return {
        **_scheduler_entry(lane),
        "issue_id": lane.get("lane_id"),
        "identifier": issue.get("identifier") or lane.get("lane_id"),
        "error": "retry queued",
        "current_attempt": int(lane.get("attempt") or 0),
        "delay_type": "workflow-retry",
        "run_id": _lane_run_id(lane),
    }


def _upsert_engine_runtime_session(
    *, config: WorkflowConfig, lane: dict[str, Any]
) -> None:
    lane_id = str(lane.get("lane_id") or "").strip()
    session = lane.get("runtime_session")
    if not lane_id or not isinstance(session, dict):
        return
    _engine_store(config).upsert_runtime_session(
        work_id=lane_id,
        entry=_runtime_session_entry(lane),
        now_iso=_now_iso(),
    )


def _runtime_session_entry(lane: dict[str, Any]) -> dict[str, Any]:
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    return {
        **_scheduler_entry(lane),
        "issue_id": lane.get("lane_id"),
        "identifier": issue.get("identifier") or lane.get("lane_id"),
        "session_name": session.get("session_name"),
        "runtime_name": session.get("runtime_name"),
        "runtime_kind": session.get("runtime_kind"),
        "session_id": session.get("session_id"),
        "thread_id": session.get("thread_id") or lane.get("thread_id"),
        "turn_id": session.get("turn_id") or lane.get("turn_id"),
        "status": session.get("status") or lane.get("status"),
        "run_id": session.get("run_id"),
        "updated_at": session.get("updated_at") or lane.get("last_progress_at"),
        "actor": session.get("actor") or lane.get("actor"),
        "stage": session.get("stage") or lane.get("stage"),
        "attempt": session.get("attempt") or lane.get("attempt"),
        "tokens": session.get("tokens"),
        "rate_limits": session.get("rate_limits"),
        "turn_count": session.get("turn_count"),
        "last_event": session.get("last_event"),
        "last_message": session.get("last_message"),
        "prompt_path": session.get("prompt_path"),
        "result_path": session.get("result_path"),
        "command_argv": session.get("command_argv"),
    }


def _runtime_session_has_identity(session: dict[str, Any]) -> bool:
    return bool(
        str(
            session.get("run_id")
            or session.get("thread_id")
            or session.get("session_id")
            or ""
        ).strip()
    )


def _actor_dispatch_conflicts(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    lane_id: str,
    actor_name: str,
    stage_name: str,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    if not lane_id:
        return [{"source": "lane", "status": "missing_lane_id"}]
    lane_status = str(lane.get("status") or "").strip()
    if lane_status == "running":
        conflicts.append({"source": "lane", "status": lane_status})
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    if _runtime_session_is_running(session):
        conflicts.append(
            {
                "source": "lane_runtime_session",
                "run_id": session.get("run_id"),
                "thread_id": session.get("thread_id"),
                "turn_id": session.get("turn_id"),
                "status": session.get("status"),
            }
        )
    for engine_session in _engine_store(config).runtime_sessions(
        work_id=lane_id, limit=5
    ):
        if _runtime_session_is_running(engine_session):
            conflicts.append(
                {
                    "source": "engine_runtime_session",
                    "run_id": engine_session.get("run_id"),
                    "thread_id": engine_session.get("thread_id"),
                    "turn_id": engine_session.get("turn_id"),
                    "status": engine_session.get("status"),
                    "updated_at": engine_session.get("updated_at"),
                }
            )
    for run in _engine_store(config).running_runs(mode="actor", limit=200):
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        if str(metadata.get("lane_id") or "").strip() != lane_id:
            continue
        if str(metadata.get("actor") or "").strip() != actor_name:
            continue
        if str(metadata.get("stage") or "").strip() != stage_name:
            continue
        conflicts.append(
            {
                "source": "engine_run",
                "run_id": run.get("run_id"),
                "status": run.get("status"),
                "started_at": run.get("started_at"),
            }
        )
    return _dedupe_conflicts(conflicts)


def _dedupe_conflicts(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for conflict in conflicts:
        key = (
            str(conflict.get("source") or ""),
            str(conflict.get("run_id") or ""),
            str(conflict.get("thread_id") or ""),
            str(conflict.get("status") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(conflict)
    return deduped


def _runtime_session_is_running(session: dict[str, Any]) -> bool:
    return _normalize_runtime_session_status(
        str(session.get("status") or "")
    ) in _RUNTIME_RUNNING_STATUSES


def _normalize_runtime_session_status(status: str) -> str:
    text = str(status or "").strip().lower()
    if text in _RUNTIME_RUNNING_STATUSES:
        return "running"
    if text in _RUNTIME_FINAL_STATUSES:
        return text
    return "failed" if text else ""


def _start_engine_actor_run(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    stage_name: str,
    runtime_meta: dict[str, Any],
    started_at: str,
) -> dict[str, Any]:
    return _engine_store(config).start_run(
        mode="actor",
        selected_count=1,
        metadata={
            **_runtime_run_metadata(lane=lane, runtime_meta=runtime_meta),
            "actor": actor_name,
            "stage": stage_name,
            "attempt": int(lane.get("attempt") or 0),
            "started_at": started_at,
        },
        now_iso=started_at,
    )


def _finish_engine_actor_run(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    status: str,
    error: str | None,
    metadata: dict[str, Any],
    completed_at: str,
) -> None:
    run_id = _lane_run_id(lane)
    if not run_id:
        return
    final_status = _normalize_runtime_session_status(status) or "failed"
    completed_count = 1 if final_status == "completed" else 0
    run_error = None if final_status == "completed" else error
    try:
        _engine_store(config).finish_run(
            run_id,
            status=final_status,
            selected_count=1,
            completed_count=completed_count,
            error=run_error,
            metadata={
                **_runtime_run_metadata(lane=lane, runtime_meta=metadata),
                "final_status": final_status,
            },
            now_iso=completed_at,
        )
    except KeyError:
        _engine_store(config).append_event(
            event_type=f"{config.workflow_name}.runtime_run_missing",
            payload={"run_id": run_id, "status": final_status},
            work_id=str(lane.get("lane_id") or ""),
            severity="warning",
        )


def _runtime_run_metadata(
    *, lane: dict[str, Any], runtime_meta: dict[str, Any]
) -> dict[str, Any]:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    return {
        **_runtime_meta_payload(runtime_meta),
        "lane_id": lane.get("lane_id"),
        "issue_identifier": issue.get("identifier") or lane.get("lane_id"),
        "actor": session.get("actor") or lane.get("actor"),
        "stage": session.get("stage") or lane.get("stage"),
        "branch": lane.get("branch"),
        "pull_request": lane.get("pull_request"),
        "thread_id": lane.get("thread_id") or session.get("thread_id"),
        "turn_id": lane.get("turn_id") or session.get("turn_id"),
    }


def _clear_engine_retry(*, config: WorkflowConfig, lane: dict[str, Any]) -> None:
    lane_id = str(lane.get("lane_id") or "").strip()
    if lane_id:
        _engine_store(config).clear_retry(work_id=lane_id)


def _lane_run_id(lane: dict[str, Any]) -> str | None:
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    value = session.get("run_id")
    text = str(value or "").strip()
    return text or None


def _append_engine_event(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    severity: str = "info",
) -> None:
    _engine_store(config).append_event(
        event_type=event_type,
        payload=payload,
        work_id=str(lane.get("lane_id") or ""),
        run_id=_lane_run_id(lane),
        severity=severity,
    )


def _acquire_lane_lease(
    *, config: WorkflowConfig, lane_id: str, issue: dict[str, Any]
) -> dict[str, Any]:
    return _engine_store(config).acquire_lease(
        lease_scope=_claim_lease_scope(config),
        lease_key=lane_id,
        owner_instance_id=_claim_owner(config),
        owner_role="workflow-runner",
        ttl_seconds=86_400,
        metadata={"issue": issue, "lane_id": lane_id},
    )


def _release_lane_lease(
    *, config: WorkflowConfig, lane: dict[str, Any], reason: str
) -> dict[str, Any]:
    claim = lane.get("claim") if isinstance(lane.get("claim"), dict) else {}
    lease = claim.get("lease") if isinstance(claim.get("lease"), dict) else {}
    owner = str(lease.get("owner_instance_id") or "").strip() or _claim_owner(config)
    return _engine_store(config).release_lease(
        lease_scope=_claim_lease_scope(config),
        lease_key=str(lane.get("lane_id") or ""),
        owner_instance_id=owner,
        release_reason=reason,
    )


def _claim_lease_scope(config: WorkflowConfig) -> str:
    return f"{config.workflow_name}:lane-claim"


def _claim_owner(config: WorkflowConfig) -> str:
    return f"{config.workflow_name}:{config.workflow_root}:{_RUNNER_INSTANCE_ID}"


def _first_text(source: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _normalize_pull_request(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in {
            "number": value.get("number"),
            "url": value.get("url"),
            "title": value.get("title"),
            "state": value.get("state"),
            "head": value.get("head") or value.get("headRefName"),
            "head_oid": value.get("head_oid") or value.get("headRefOid"),
            "is_draft": value.get("is_draft")
            if "is_draft" in value
            else value.get("isDraft"),
            "merged": value.get("merged") if "merged" in value else value.get("isMerged"),
            "merged_at": value.get("merged_at") or value.get("mergedAt"),
            "updated_at": value.get("updated_at") or value.get("updatedAt"),
        }.items()
        if item not in (None, "")
    }


def _runtime_meta_payload(runtime_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(runtime_meta or {}).items()
        if value not in (None, "", [], {})
    }


def _mutable_actor_runtime_session(
    *, lane: dict[str, Any], actor_name: str, stage_name: str
) -> tuple[str, dict[str, Any]]:
    key = runtime_session_key(actor_name=actor_name, stage_name=stage_name)
    sessions = lane_mapping(lane, "runtime_sessions")
    session = sessions.get(key)
    if not isinstance(session, dict):
        session = {}
        sessions[key] = session
    session["session_key"] = key
    return key, session


def _current_runtime_session(lane: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    latest = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    key = str(latest.get("session_key") or "").strip()
    sessions = lane_mapping(lane, "runtime_sessions")
    if key and isinstance(sessions.get(key), dict):
        return key, sessions[key]
    if latest:
        key = runtime_session_key(
            actor_name=str(latest.get("actor") or lane.get("actor") or ""),
            stage_name=str(latest.get("stage") or lane.get("stage") or ""),
        )
        if key:
            latest["session_key"] = key
            sessions[key] = latest
            return key, latest
    for candidate_key, candidate in sessions.items():
        if isinstance(candidate, dict) and _runtime_session_is_running(candidate):
            candidate["session_key"] = str(candidate.get("session_key") or candidate_key)
            return str(candidate_key), candidate
    key = "latest"
    session = lane_mapping(lane, "runtime_session")
    session["session_key"] = key
    sessions[key] = session
    return key, session


def _set_latest_runtime_session(
    *, lane: dict[str, Any], session_key: str, session: dict[str, Any]
) -> None:
    session["session_key"] = session_key
    lane_mapping(lane, "runtime_sessions")[session_key] = session
    lane["runtime_session"] = session


def _runtime_session_matches(
    session: dict[str, Any], *, actor_name: str, stage_name: str
) -> bool:
    return (
        str(session.get("actor") or "").strip() == str(actor_name or "").strip()
        and str(session.get("stage") or "").strip() == str(stage_name or "").strip()
    )


def _apply_runtime_session_ids(
    *, lane: dict[str, Any], session: dict[str, Any]
) -> None:
    session_id = str(session.get("session_id") or "").strip()
    thread_id = str(session.get("thread_id") or session_id or "").strip()
    turn_id = str(session.get("turn_id") or "").strip()
    if session_id:
        session["session_id"] = session_id
    if thread_id:
        session["thread_id"] = thread_id
        lane["thread_id"] = thread_id
    if turn_id:
        session["turn_id"] = turn_id
        lane["turn_id"] = turn_id


def _runtime_updated_at(lane: dict[str, Any]) -> str:
    session = lane.get("runtime_session")
    if isinstance(session, dict):
        return str(session.get("updated_at") or session.get("started_at") or "")
    return ""


def _scheduler_entry(lane: dict[str, Any]) -> dict[str, Any]:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    return {
        "issue_id": lane.get("lane_id"),
        "identifier": issue.get("identifier") or lane.get("lane_id"),
        "state": lane.get("status"),
        "title": issue.get("title"),
        "url": issue.get("url"),
        "worker_id": lane.get("actor"),
        "attempt": int(lane.get("attempt") or 0),
        "worker_status": lane.get("status"),
        "started_at_epoch": _iso_to_epoch(
            str(session.get("started_at") or lane.get("last_progress_at") or ""),
            default=time.time(),
        ),
        "heartbeat_at_epoch": _iso_to_epoch(
            str(session.get("updated_at") or lane.get("last_progress_at") or ""),
            default=time.time(),
        ),
        "thread_id": lane.get("thread_id") or session.get("thread_id"),
        "turn_id": lane.get("turn_id") or session.get("turn_id"),
        "session_name": session.get("session_name"),
        "runtime_name": session.get("runtime_name"),
        "runtime_kind": session.get("runtime_kind"),
        "session_id": session.get("session_id"),
        "status": session.get("status") or lane.get("status"),
        "run_id": session.get("run_id"),
        "actor": session.get("actor") or lane.get("actor"),
        "stage": session.get("stage") or lane.get("stage"),
        "branch": lane.get("branch"),
        "pull_request": lane.get("pull_request"),
        "last_event": session.get("last_event"),
        "last_message": session.get("last_message"),
        "tokens": session.get("tokens"),
        "rate_limits": session.get("rate_limits"),
        "turn_count": session.get("turn_count"),
    }


def _iso_to_epoch(value: str, *, default: float) -> float:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return default


def _epoch_to_iso(value: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def _blocker_reason(output: dict[str, Any]) -> str:
    blockers = (
        output.get("blockers") if isinstance(output.get("blockers"), list) else []
    )
    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        kind = str(blocker.get("kind") or "").strip()
        if kind:
            return kind
    return ""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
