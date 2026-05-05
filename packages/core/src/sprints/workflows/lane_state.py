"""Lane ledger state, config parsing, and engine projections."""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sprints.engine import EngineStore, RetryPolicy
from sprints.workflows import sessions
from sprints.workflows import teardown as teardown_flow
from sprints.core.config import WorkflowConfig
from sprints.core.paths import runtime_paths

_RUNNER_INSTANCE_ID = f"{os.getpid()}:{uuid.uuid4().hex[:12]}"
_TERMINAL_LANE_STATUSES = {"complete", "released"}


def new_lane(
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


def lane_recovery_artifacts(
    lane: dict[str, Any], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    artifacts = {
        "run_id": lane_run_id(lane),
        "runtime_session": session or None,
        "runtime_sessions": lane.get("runtime_sessions"),
        "thread_id": lane.get("thread_id") or session.get("thread_id"),
        "turn_id": lane.get("turn_id") or session.get("turn_id"),
        "branch": lane.get("branch"),
        "pull_request": lane.get("pull_request"),
        "last_actor_output": lane.get("last_actor_output"),
        "runtime_recovery": lane.get("runtime_recovery"),
        "actor_dispatch": actor_dispatch_summary(lane),
        "dispatch_journal_count": len(lane.get("dispatch_journal") or []),
        "side_effects": side_effects_summary(lane),
    }
    artifacts.update(dict(extra or {}))
    return {key: value for key, value in artifacts.items() if value not in (None, "")}


def lane_actor_runtime_session(
    lane: dict[str, Any], *, actor_name: str, stage_name: str
) -> dict[str, Any]:
    return sessions.lane_actor_runtime_session(
        lane,
        actor_name=actor_name,
        stage_name=stage_name,
    )


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


def lane_id(*, config: WorkflowConfig, issue: dict[str, Any]) -> str:
    tracker_cfg = tracker_config(config)
    prefix = str(tracker_cfg.get("kind") or "tracker").strip() or "tracker"
    issue_id = str(issue.get("id") or issue.get("identifier") or "").strip()
    if not issue_id:
        raise RuntimeError("tracker issue is missing id")
    return f"{prefix}#{issue_id.lstrip('#')}"


def lane_stage(lane: dict[str, Any]) -> str:
    return str(lane.get("stage") or "").strip()


def lane_is_terminal(lane: dict[str, Any]) -> bool:
    return str(lane.get("status") or "").strip() in _TERMINAL_LANE_STATUSES


def count_lanes_with_status(lanes: list[dict[str, Any]], status: str) -> int:
    return sum(1 for lane in lanes if str(lane.get("status") or "") == status)


def lane_by_id(state: Any, lane_id: str) -> dict[str, Any]:
    lane = state.lanes.get(lane_id)
    if not isinstance(lane, dict):
        raise RuntimeError(f"unknown lane {lane_id!r}")
    return lane


def lane_summary(lane: dict[str, Any]) -> dict[str, Any]:
    return _lane_summary(lane)


def retry_summary(lane: dict[str, Any]) -> dict[str, Any] | None:
    pending = (
        lane.get("pending_retry") if isinstance(lane.get("pending_retry"), dict) else {}
    )
    history = [
        _retry_history_entry(record)
        for record in lane.get("retry_history") or []
        if isinstance(record, dict)
    ]
    latest = history[-1] if history else {}
    if not pending and not latest:
        return None
    reason = str(pending.get("reason") or latest.get("reason") or "").strip()
    delay_seconds = pending.get("delay_seconds", latest.get("delay_seconds"))
    return {
        "stage": pending.get("stage") or latest.get("stage"),
        "target": pending.get("target") or latest.get("target"),
        "reason": reason or None,
        "failure_reason": reason or None,
        "attempt": pending.get("attempt") or latest.get("next_attempt"),
        "current_attempt": pending.get("current_attempt")
        or latest.get("current_attempt"),
        "max_attempts": pending.get("max_attempts") or latest.get("max_attempts"),
        "delay_seconds": delay_seconds,
        "backoff_seconds": delay_seconds,
        "due_at": pending.get("due_at") or latest.get("due_at"),
        "due_at_epoch": pending.get("due_at_epoch") or latest.get("due_at_epoch"),
        "queued_at": pending.get("queued_at") or latest.get("queued_at"),
        "status": pending.get("status") or latest.get("status"),
        "source": pending.get("source") or latest.get("source"),
        "history_count": len(history),
        "history": history[-5:],
        "exhausted": latest.get("status") == "limit_exceeded",
    }


def actor_dispatch_summary(lane: dict[str, Any]) -> dict[str, Any] | None:
    dispatch = (
        lane.get("actor_dispatch")
        if isinstance(lane.get("actor_dispatch"), dict)
        else {}
    )
    if not dispatch:
        return None
    runtime = (
        dispatch.get("runtime") if isinstance(dispatch.get("runtime"), dict) else {}
    )
    return {
        key: value
        for key, value in {
            "dispatch_id": dispatch.get("dispatch_id"),
            "status": dispatch.get("status"),
            "actor": dispatch.get("actor"),
            "stage": dispatch.get("stage"),
            "attempt": dispatch.get("attempt"),
            "mode": runtime.get("dispatch_mode"),
            "planned_at": dispatch.get("planned_at"),
            "started_at": dispatch.get("started_at"),
            "last_progress_at": dispatch.get("last_progress_at"),
            "completed_at": dispatch.get("completed_at"),
            "updated_at": dispatch.get("updated_at"),
            "run_id": dispatch.get("run_id"),
            "thread_id": dispatch.get("thread_id"),
            "turn_id": dispatch.get("turn_id"),
        }.items()
        if value not in (None, "", [], {})
    }


def side_effects_summary(
    lane: dict[str, Any], *, limit: int = 5
) -> list[dict[str, Any]]:
    effects = (
        lane.get("side_effects") if isinstance(lane.get("side_effects"), dict) else {}
    )
    entries = [entry for entry in effects.values() if isinstance(entry, dict)]
    entries.sort(
        key=lambda entry: str(entry.get("updated_at") or entry.get("created_at") or "")
    )
    return [
        {
            key: entry.get(key)
            for key in (
                "key",
                "operation",
                "target",
                "status",
                "updated_at",
                "error",
            )
            if entry.get(key) not in (None, "", [], {})
        }
        for entry in entries[-limit:]
    ]


def _retry_history_entry(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "status",
            "source",
            "stage",
            "target",
            "reason",
            "current_attempt",
            "next_attempt",
            "max_attempts",
            "delay_seconds",
            "due_at",
            "due_at_epoch",
            "queued_at",
        )
        if record.get(key) not in (None, "", [], {})
    }


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
    retry = retry_summary(lane)
    actor_dispatch = actor_dispatch_summary(lane)
    side_effects = side_effects_summary(lane)
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
        "retry": retry,
        "retry_history_count": int((retry or {}).get("history_count") or 0),
        "actor_dispatch": actor_dispatch,
        "dispatch_journal_count": len(lane.get("dispatch_journal") or []),
        "side_effect_count": len(lane.get("side_effects") or {}),
        "side_effects": side_effects,
        "last_transition": lane.get("last_transition"),
        "transition_history_count": len(lane.get("transition_history") or []),
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
    append_engine_event(
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
    previous: dict[str, Any] | None = None,
) -> None:
    before = previous or lane_transition_side(lane)
    lane["status"] = status
    if actor is not ...:
        lane["actor"] = actor
    now = _now_iso()
    lane["last_progress_at"] = now
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
    transition = _record_lane_transition(
        config=config,
        lane=lane,
        previous=before,
        reason=reason,
        at=now,
    )
    lane["last_transition"] = transition
    history = lane_list(lane, "transition_history")
    history.append(transition)
    if len(history) > 50:
        del history[:-50]


def set_lane_operator_attention(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    reason: str,
    message: str,
    artifacts: dict[str, Any] | None = None,
) -> None:
    previous = lane_transition_side(lane)
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
        previous=previous,
    )


def lane_transition_side(lane: dict[str, Any]) -> dict[str, Any]:
    claim = lane.get("claim") if isinstance(lane.get("claim"), dict) else {}
    return {
        "status": lane.get("status"),
        "stage": lane.get("stage"),
        "actor": lane.get("actor"),
        "attempt": lane.get("attempt"),
        "claim_state": claim.get("state"),
        "pending_retry": lane.get("pending_retry"),
        "operator_attention": lane.get("operator_attention"),
    }


def _record_lane_transition(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    previous: dict[str, Any],
    reason: str,
    at: str,
) -> dict[str, Any]:
    transition = {
        "transition_id": uuid.uuid4().hex,
        "lane_id": lane.get("lane_id"),
        "workflow": config.workflow_name,
        "reason": reason,
        "at": at,
        "previous": previous,
        "current": lane_transition_side(lane),
    }
    lane_id = str(lane.get("lane_id") or "").strip()
    if lane_id:
        engine_store(config).record_work_item_event(
            work_id=lane_id,
            entry=_engine_lane_entry(lane, transition=transition),
            event_type=f"{config.workflow_name}.lane.{lane.get('status')}",
            payload={
                "lane_id": lane.get("lane_id"),
                "status": lane.get("status"),
                "stage": lane.get("stage"),
                "actor": lane.get("actor"),
                "reason": reason,
                "transition": transition,
            },
            event_id=f"{config.workflow_name}:lane-transition:{transition['transition_id']}",
            run_id=lane_run_id(lane),
            now_iso=at,
        )
    return transition


def has_open_blockers(issue: dict[str, Any], *, terminal_states: set[str]) -> bool:
    for blocker in issue.get("blocked_by") or []:
        if not isinstance(blocker, dict):
            return True
        blocker_state = str(blocker.get("state") or "").strip().lower()
        if not blocker_state or blocker_state not in terminal_states:
            return True
    return False


def issue_is_still_active(
    *, tracker_cfg: dict[str, Any], issue: dict[str, Any]
) -> bool:
    active_states = set(configured_texts(tracker_cfg, "active_states", "active-states"))
    required_labels = set(
        configured_texts(tracker_cfg, "required_labels", "required-labels")
    )
    exclude_labels = set(
        configured_texts(tracker_cfg, "exclude_labels", "exclude-labels")
    )
    state = str(issue.get("state") or "").strip().lower()
    if active_states and state not in active_states:
        return False
    labels = issue_labels(issue)
    if required_labels and not required_labels.issubset(labels):
        return False
    if exclude_labels.intersection(labels):
        return False
    return True


def issue_labels(issue: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for label in issue.get("labels") or []:
        text = str(label.get("name") if isinstance(label, dict) else label).strip()
        if text:
            labels.add(text.lower())
    return labels


def configured_texts(config: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = config.get(key)
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
    return []


def concurrency_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("concurrency")
    cfg = raw if isinstance(raw, dict) else {}
    max_lanes = _positive_int(
        cfg,
        "max-lanes",
        "max_lanes",
        "max-active-lanes",
        "max_active_lanes",
        default=1,
    )
    actor_limits = _actor_limit_config(config=config, cfg=cfg, max_lanes=max_lanes)
    return {
        "max_lanes": max_lanes,
        "max_active_lanes": max_lanes,
        "actor_limits": actor_limits,
        "max_implementers": actor_limits.get("implementer", max_lanes),
        "max_reviewers": actor_limits.get("reviewer", max_lanes),
        "per_lane_lock": bool(cfg.get("per-lane-lock", cfg.get("per_lane_lock", True))),
    }


def _actor_limit_config(
    *, config: WorkflowConfig, cfg: dict[str, Any], max_lanes: int
) -> dict[str, int]:
    actors_cfg = cfg.get("actors") if isinstance(cfg.get("actors"), dict) else {}
    limits: dict[str, int] = {}
    stage_actors = {
        actor_name
        for stage in config.stages.values()
        for actor_name in stage.actors
        if actor_name and actor_name != config.orchestrator_actor
    }
    configured_names = {
        str(name).strip()
        for name in actors_cfg
        if str(name).strip() and str(name).strip() != config.orchestrator_actor
    }
    for actor_name in sorted(stage_actors | configured_names):
        raw_limit = _actor_limit_value(
            actors_cfg=actors_cfg, cfg=cfg, actor_name=actor_name
        )
        limits[actor_name] = min(raw_limit or max_lanes, max_lanes)
    return limits


def _actor_limit_value(
    *, actors_cfg: dict[str, Any], cfg: dict[str, Any], actor_name: str
) -> int | None:
    raw = actors_cfg.get(actor_name)
    if isinstance(raw, dict):
        actor_limit = _configured_positive_int(
            raw, "max-running", "max_running", "limit"
        )
    else:
        actor_limit = _positive_int_value(raw)
    if actor_limit is not None:
        return actor_limit
    if actor_name == "implementer":
        return _configured_positive_int(cfg, "max-implementers", "max_implementers")
    if actor_name == "reviewer":
        return _configured_positive_int(cfg, "max-reviewers", "max_reviewers")
    return None


def intake_auto_activate_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("intake")
    intake = raw if isinstance(raw, dict) else {}
    auto_raw = intake.get("auto-activate") or intake.get("auto_activate")
    cfg = auto_raw if isinstance(auto_raw, dict) else {}
    tracker_cfg = tracker_config(config)
    required_labels = configured_texts(
        tracker_cfg, "required_labels", "required-labels"
    )
    default_add_label = required_labels[0] if required_labels else "active"
    add_label = str(
        cfg.get("add_label") or cfg.get("add-label") or default_add_label
    ).strip()
    exclude_labels = configured_texts(cfg, "exclude_labels", "exclude-labels")
    if not exclude_labels:
        exclude_labels = configured_texts(
            tracker_cfg, "exclude_labels", "exclude-labels"
        )
    return {
        "enabled": _configured_bool(cfg, "enabled", default=False),
        "add_label": add_label or default_add_label,
        "exclude_labels": exclude_labels,
        "max_per_tick": _positive_int(cfg, "max-per-tick", "max_per_tick", default=1),
    }


def recovery_config(config: WorkflowConfig) -> dict[str, Any]:
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


def retry_config(config: WorkflowConfig) -> dict[str, Any]:
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


def retry_policy(config: WorkflowConfig) -> RetryPolicy:
    cfg = retry_config(config)
    return RetryPolicy(
        max_attempts=cfg["max_attempts"],
        initial_delay_seconds=cfg["initial_delay_seconds"],
        backoff_multiplier=cfg["backoff_multiplier"],
        max_delay_seconds=cfg["max_delay_seconds"],
    )


def completion_cleanup_retry_pending(lane: dict[str, Any]) -> bool:
    return teardown_flow.cleanup_retry_pending(lane)


def review_notification_config(config: WorkflowConfig) -> dict[str, bool]:
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


def _positive_int(config: dict[str, Any], *keys: str, default: int) -> int:
    for key in keys:
        parsed = _positive_int_value(config.get(key))
        if parsed is not None:
            return parsed
    return default


def _configured_positive_int(config: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        parsed = _positive_int_value(config.get(key))
        if parsed is not None:
            return parsed
    return None


def _positive_int_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return None


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


def tracker_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("tracker")
    return raw if isinstance(raw, dict) else {}


def code_host_config(config: WorkflowConfig) -> dict[str, Any]:
    raw = config.raw.get("code-host")
    return raw if isinstance(raw, dict) else {}


def repository_path(config: WorkflowConfig) -> Path | None:
    raw = config.raw.get("repository")
    if not isinstance(raw, dict):
        return None
    value = str(raw.get("local-path") or raw.get("local_path") or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (config.workflow_root / path).resolve()


def engine_store(config: WorkflowConfig) -> EngineStore:
    return EngineStore(
        db_path=runtime_paths(config.workflow_root)["db_path"],
        workflow=config.workflow_name,
    )


def record_engine_lane(*, config: WorkflowConfig, lane: dict[str, Any]) -> None:
    lane_id = str(lane.get("lane_id") or "").strip()
    if not lane_id:
        return
    engine_store(config).record_work_item(
        work_id=lane_id,
        entry=_engine_lane_entry(lane),
        now_iso=_now_iso(),
    )


def _engine_lane_entry(
    lane: dict[str, Any], *, transition: dict[str, Any] | None = None
) -> dict[str, Any]:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    latest_transition = transition or lane.get("last_transition")
    transition_count = len(lane.get("transition_history") or [])
    if transition is not None and transition != lane.get("last_transition"):
        transition_count += 1
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
            "last_transition": latest_transition,
            "transition_history_count": transition_count,
        },
    }


def retry_engine_entry(lane: dict[str, Any]) -> dict[str, Any]:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    return {
        **sessions.scheduler_entry(lane),
        "issue_id": lane.get("lane_id"),
        "identifier": issue.get("identifier") or lane.get("lane_id"),
        "error": "retry queued",
        "current_attempt": int(lane.get("attempt") or 0),
        "delay_type": "workflow-retry",
        "run_id": lane_run_id(lane),
    }


def clear_engine_retry(*, config: WorkflowConfig, lane: dict[str, Any]) -> None:
    lane_id = str(lane.get("lane_id") or "").strip()
    if lane_id:
        engine_store(config).clear_retry(work_id=lane_id)


def lane_run_id(lane: dict[str, Any]) -> str | None:
    return sessions.lane_run_id(lane)


def append_engine_event(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    severity: str = "info",
) -> None:
    sessions.append_engine_event(
        config=config,
        lane=lane,
        event_type=event_type,
        payload=payload,
        severity=severity,
    )


def acquire_lane_lease(
    *, config: WorkflowConfig, lane_id: str, issue: dict[str, Any]
) -> dict[str, Any]:
    return engine_store(config).acquire_lease(
        lease_scope=_claim_lease_scope(config),
        lease_key=lane_id,
        owner_instance_id=_claim_owner(config),
        owner_role="workflow-runner",
        ttl_seconds=86_400,
        metadata={"issue": issue, "lane_id": lane_id},
    )


def release_lane_lease(
    *, config: WorkflowConfig, lane: dict[str, Any], reason: str
) -> dict[str, Any]:
    claim = lane.get("claim") if isinstance(lane.get("claim"), dict) else {}
    lease = claim.get("lease") if isinstance(claim.get("lease"), dict) else {}
    owner = str(lease.get("owner_instance_id") or "").strip() or _claim_owner(config)
    return engine_store(config).release_lease(
        lease_scope=_claim_lease_scope(config),
        lease_key=str(lane.get("lane_id") or ""),
        owner_instance_id=owner,
        release_reason=reason,
    )


def _claim_lease_scope(config: WorkflowConfig) -> str:
    return f"{config.workflow_name}:lane-claim"


def _claim_owner(config: WorkflowConfig) -> str:
    return f"{config.workflow_name}:{config.workflow_root}:{_RUNNER_INSTANCE_ID}"


def first_text(source: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def normalize_pull_request(value: dict[str, Any]) -> dict[str, Any]:
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
            "merged": value.get("merged")
            if "merged" in value
            else value.get("isMerged"),
            "merged_at": value.get("merged_at") or value.get("mergedAt"),
            "updated_at": value.get("updated_at") or value.get("updatedAt"),
        }.items()
        if item not in (None, "")
    }


def iso_to_epoch(value: str, *, default: float) -> float:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return default


def _epoch_to_iso(value: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def blocker_reason(output: dict[str, Any]) -> str:
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
