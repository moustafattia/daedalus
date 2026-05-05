"""Read-only aggregation of state from Sprints event sources for /sprints watch.

This module never writes â€” it only reads from:

  - ``<workflow_root>/runtime/memory/sprints-events.jsonl``
  - ``<workflow_root>/runtime/memory/workflow-audit.jsonl``
  - workflow state JSON declared in ``WORKFLOW.md``
  - ``<workflow_root>/runtime/state/sprints/sprints.db`` engine projections
  - ``<workflow_root>/runtime/memory/sprints-alert-state.json``

Each function tolerates the source being absent / corrupt and returns an
empty result rather than raising. The TUI must keep rendering even if
one source is unavailable.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sprints.engine import EngineStore
from sprints.engine.state import (
    read_engine_events,
    read_engine_runs,
    read_engine_scheduler_state,
)
from sprints.engine.work import work_item_from_issue
from sprints.core.contracts import WorkflowContractError, load_workflow_contract
from sprints.core.paths import runtime_paths


def _read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    parsed: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    parsed.reverse()  # newest first
    return parsed


def _load_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _workflow_name(workflow_root: Path) -> str | None:
    try:
        return (
            str(
                load_workflow_contract(Path(workflow_root)).config.get("workflow") or ""
            ).strip()
            or None
        )
    except (FileNotFoundError, WorkflowContractError, OSError):
        return None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _engine_scheduler(workflow_root: Path, workflow: str) -> dict[str, Any]:
    payload = read_engine_scheduler_state(
        runtime_paths(Path(workflow_root))["db_path"],
        workflow=workflow,
        now_iso=_now_iso(),
        now_epoch=time.time(),
    )
    return payload or {}


def _engine_runs(
    workflow_root: Path, workflow: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    return read_engine_runs(
        runtime_paths(Path(workflow_root))["db_path"],
        workflow=workflow,
        limit=limit,
    )


def _resolve_issue_runner_storage_path(
    workflow_root: Path, key: str, default: str
) -> Path | None:
    return _resolve_workflow_storage_path(workflow_root, key, default)


def _resolve_workflow_storage_path(
    workflow_root: Path, key: str, default: str
) -> Path | None:
    try:
        contract = load_workflow_contract(Path(workflow_root))
    except (FileNotFoundError, WorkflowContractError, OSError):
        return None
    storage_cfg = contract.config.get("storage") or {}
    raw = str(storage_cfg.get(key) or default).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (Path(workflow_root) / path).resolve()
    return path


def _workflow_state_payload(workflow_root: Path, workflow_name: str) -> dict[str, Any]:
    state_path = _resolve_workflow_storage_path(
        workflow_root, "state", f".sprints/{workflow_name}-state.json"
    )
    return _load_optional_json(state_path) or {}


def _state_lanes(workflow_root: Path, workflow_name: str) -> list[dict[str, Any]]:
    state = _workflow_state_payload(workflow_root, workflow_name)
    lanes = state.get("lanes") if isinstance(state.get("lanes"), dict) else {}
    return [lane for lane in lanes.values() if isinstance(lane, dict)]


def _active_state_lanes(
    workflow_root: Path, workflow_name: str
) -> list[dict[str, Any]]:
    return [
        lane
        for lane in _state_lanes(workflow_root, workflow_name)
        if not _lane_is_terminal(lane)
    ]


def _state_lanes_by_id(
    workflow_root: Path, workflow_name: str
) -> dict[str, dict[str, Any]]:
    lanes: dict[str, dict[str, Any]] = {}
    for lane in _state_lanes(workflow_root, workflow_name):
        lane_id = str(lane.get("lane_id") or "").strip()
        if lane_id:
            lanes[lane_id] = lane
    return lanes


def _lane_is_terminal(lane: dict[str, Any]) -> bool:
    return str(lane.get("status") or "").strip() in {
        "complete",
        "released",
        "merged",
        "closed",
        "archived",
    }


def _state_lane_entry(lane: dict[str, Any], *, workflow_name: str) -> dict[str, Any]:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    lane_id = str(lane.get("lane_id") or issue.get("id") or "").strip()
    identifier = str(
        issue.get("identifier") or issue.get("number") or lane_id or "unknown"
    )
    status = str(lane.get("status") or "active").strip() or "active"
    stage = str(lane.get("stage") or status).strip() or status
    work_item = work_item_from_issue(
        {
            "id": issue.get("id") or lane_id or identifier,
            "identifier": identifier,
            "title": issue.get("title") or "",
            "url": issue.get("url"),
            "state": status,
        },
        source=workflow_name,
    ).to_dict()
    pull_request = (
        lane.get("pull_request") if isinstance(lane.get("pull_request"), dict) else {}
    )
    pending_retry = (
        lane.get("pending_retry") if isinstance(lane.get("pending_retry"), dict) else {}
    )
    retry_history = [
        record for record in lane.get("retry_history") or [] if isinstance(record, dict)
    ]
    retry_latest = retry_history[-1] if retry_history else {}
    attention = (
        lane.get("operator_attention")
        if isinstance(lane.get("operator_attention"), dict)
        else {}
    )
    runtime_session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    actor_dispatch = (
        lane.get("actor_dispatch")
        if isinstance(lane.get("actor_dispatch"), dict)
        else {}
    )
    dispatch_runtime = (
        actor_dispatch.get("runtime")
        if isinstance(actor_dispatch.get("runtime"), dict)
        else {}
    )
    return {
        "lane_id": lane_id or identifier,
        "state": stage,
        "workflow_state": stage,
        "issue_number": issue.get("number"),
        "issue_identifier": identifier,
        "issue_title": issue.get("title"),
        "lane_status": status,
        "status": status,
        "stage": stage,
        "actor": lane.get("actor"),
        "attempt": lane.get("attempt"),
        "branch": lane.get("branch"),
        "pull_request": pull_request or None,
        "pull_request_number": pull_request.get("number"),
        "pull_request_url": pull_request.get("url"),
        "retry_at": pending_retry.get("due_at"),
        "retry_target": pending_retry.get("target"),
        "retry_attempt": pending_retry.get("attempt"),
        "retry_current_attempt": pending_retry.get("current_attempt")
        or retry_latest.get("current_attempt"),
        "retry_max_attempts": pending_retry.get("max_attempts")
        or retry_latest.get("max_attempts"),
        "retry_delay_seconds": pending_retry.get("delay_seconds")
        or retry_latest.get("delay_seconds"),
        "retry_backoff_seconds": pending_retry.get("delay_seconds")
        or retry_latest.get("delay_seconds"),
        "retry_reason": pending_retry.get("reason") or retry_latest.get("reason"),
        "retry_history_count": len(retry_history),
        "operator_attention_reason": attention.get("reason"),
        "operator_attention_message": attention.get("message"),
        "last_progress_at": lane.get("last_progress_at"),
        "runtime_status": runtime_session.get("status"),
        "dispatch_id": actor_dispatch.get("dispatch_id"),
        "dispatch_status": actor_dispatch.get("status"),
        "dispatch_actor": actor_dispatch.get("actor"),
        "dispatch_stage": actor_dispatch.get("stage"),
        "dispatch_mode": dispatch_runtime.get("dispatch_mode"),
        "dispatch_updated_at": actor_dispatch.get("updated_at"),
        "dispatch_journal_count": len(lane.get("dispatch_journal") or []),
        "side_effect_count": len(lane.get("side_effects") or []),
        "thread_id": lane.get("thread_id") or runtime_session.get("thread_id"),
        "turn_id": lane.get("turn_id") or runtime_session.get("turn_id"),
        "kind": status,
        "work_item": work_item,
    }


def _engine_work_items(workflow_root: Path, workflow_name: str) -> list[dict[str, Any]]:
    try:
        return EngineStore(
            db_path=runtime_paths(workflow_root)["db_path"],
            workflow=workflow_name,
        ).work_items(limit=500)
    except Exception:
        return []


def _engine_runtime_sessions(
    workflow_root: Path, workflow_name: str
) -> dict[str, dict[str, Any]]:
    try:
        sessions = EngineStore(
            db_path=runtime_paths(workflow_root)["db_path"],
            workflow=workflow_name,
        ).runtime_sessions(limit=500)
    except Exception:
        return {}
    return {
        str(session.get("work_id") or session.get("issue_id") or ""): session
        for session in sessions
        if isinstance(session, dict)
    }


def _engine_lane_entry(
    work_item: dict[str, Any],
    *,
    workflow_name: str,
    state_lane: dict[str, Any] | None,
    runtime_session: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = (
        work_item.get("metadata") if isinstance(work_item.get("metadata"), dict) else {}
    )
    state_entry = (
        _state_lane_entry(state_lane, workflow_name=workflow_name)
        if isinstance(state_lane, dict)
        else {}
    )
    lane_id = str(work_item.get("work_id") or state_entry.get("lane_id") or "")
    runtime_session = runtime_session if isinstance(runtime_session, dict) else {}
    pull_request = metadata.get("pull_request") or state_entry.get("pull_request") or {}
    attention = (
        metadata.get("operator_attention")
        if isinstance(metadata.get("operator_attention"), dict)
        else {}
    )
    if not attention and state_entry.get("operator_attention_reason"):
        attention = {
            "reason": state_entry.get("operator_attention_reason"),
            "message": state_entry.get("operator_attention_message"),
        }
    work_item_ref = work_item_from_issue(
        {
            "id": lane_id or work_item.get("identifier") or "unknown",
            "identifier": work_item.get("identifier") or lane_id,
            "title": work_item.get("title") or "",
            "url": work_item.get("url"),
            "state": work_item.get("state"),
        },
        source=workflow_name,
    ).to_dict()
    return {
        **state_entry,
        "lane_id": lane_id,
        "state": metadata.get("stage")
        or state_entry.get("state")
        or work_item.get("state"),
        "workflow_state": metadata.get("stage")
        or state_entry.get("workflow_state")
        or work_item.get("state"),
        "issue_identifier": work_item.get("identifier")
        or state_entry.get("issue_identifier")
        or lane_id,
        "issue_title": work_item.get("title") or state_entry.get("issue_title"),
        "lane_status": work_item.get("state") or state_entry.get("lane_status"),
        "status": work_item.get("state") or state_entry.get("status"),
        "stage": metadata.get("stage") or state_entry.get("stage"),
        "actor": metadata.get("actor") or state_entry.get("actor"),
        "attempt": metadata.get("attempt") or state_entry.get("attempt"),
        "branch": metadata.get("branch") or state_entry.get("branch"),
        "pull_request": pull_request or None,
        "pull_request_number": (pull_request or {}).get("number")
        if isinstance(pull_request, dict)
        else state_entry.get("pull_request_number"),
        "pull_request_url": (pull_request or {}).get("url")
        if isinstance(pull_request, dict)
        else state_entry.get("pull_request_url"),
        "operator_attention_reason": attention.get("reason"),
        "operator_attention_message": attention.get("message"),
        "runtime_status": runtime_session.get("status")
        or state_entry.get("runtime_status"),
        "thread_id": runtime_session.get("thread_id")
        or metadata.get("thread_id")
        or state_entry.get("thread_id"),
        "turn_id": runtime_session.get("turn_id")
        or metadata.get("turn_id")
        or state_entry.get("turn_id"),
        "last_progress_at": runtime_session.get("updated_at")
        or work_item.get("updated_at")
        or state_entry.get("last_progress_at"),
        "engine_updated_at": work_item.get("updated_at"),
        "lane_status_source": "engine_work_items",
        "state_json_present": isinstance(state_lane, dict),
        "kind": work_item.get("state") or state_entry.get("kind"),
        "work_item": work_item_ref,
    }


def recent_sprints_events(workflow_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    paths = runtime_paths(Path(workflow_root))
    return _read_jsonl_tail(paths["event_log_path"], limit)


def recent_workflow_audit(workflow_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    base = Path(workflow_root)
    if _workflow_name(base) == "issue-runner":
        audit_path = _resolve_issue_runner_storage_path(
            base, "audit-log", "memory/workflow-audit.jsonl"
        )
        return _read_jsonl_tail(audit_path, limit) if audit_path is not None else []
    # workflow-audit.jsonl lives under <root>/runtime/memory/ in the project layout
    # and under <root>/memory/ in the legacy layout â€” match runtime_paths logic.
    runtime_event_log = runtime_paths(base)["event_log_path"]
    audit_path = runtime_event_log.parent / "workflow-audit.jsonl"
    return _read_jsonl_tail(audit_path, limit)


def recent_engine_events(workflow_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    workflow_root = Path(workflow_root)
    workflow = _workflow_name(workflow_root)
    if not workflow:
        return []
    return [
        {**event, "source": "engine-events"}
        for event in read_engine_events(
            runtime_paths(workflow_root)["db_path"],
            workflow=workflow,
            limit=limit,
            order="desc",
        )
    ]


def active_lanes(workflow_root: Path) -> list[dict[str, Any]]:
    workflow_root = Path(workflow_root)
    workflow_name = _workflow_name(workflow_root)
    if not workflow_name:
        return []
    if workflow_name == "issue-runner":
        scheduler = _engine_scheduler(workflow_root, workflow_name)
        out: list[dict[str, Any]] = []
        for row in scheduler.get("running") or []:
            if not isinstance(row, dict):
                continue
            identifier = row.get("identifier") or row.get("issue_id")
            work_item = work_item_from_issue(
                {
                    "id": row.get("issue_id") or identifier or "unknown",
                    "identifier": identifier,
                    "state": row.get("state") or "running",
                },
                source="issue-runner",
            ).to_dict()
            out.append(
                {
                    "lane_id": row.get("issue_id"),
                    "state": row.get("state") or "running",
                    "workflow_state": row.get("state") or "running",
                    "issue_identifier": identifier,
                    "lane_status": "active",
                    "kind": "running",
                    "work_item": work_item,
                }
            )
        for row in scheduler.get("retry_queue") or []:
            if not isinstance(row, dict):
                continue
            identifier = row.get("identifier") or row.get("issue_id")
            work_item = work_item_from_issue(
                {
                    "id": row.get("issue_id") or identifier or "unknown",
                    "identifier": identifier,
                    "state": "retrying",
                },
                source="issue-runner",
            ).to_dict()
            out.append(
                {
                    "lane_id": row.get("issue_id"),
                    "state": "retrying",
                    "workflow_state": "retrying",
                    "issue_identifier": identifier,
                    "lane_status": "retrying",
                    "kind": "retrying",
                    "work_item": work_item,
                }
            )
        return out

    if workflow_name != "change-delivery":
        scheduler = _engine_scheduler(workflow_root, workflow_name)
        return [
            {
                "lane_id": row.get("issue_id"),
                "state": row.get("state") or row.get("worker_status") or "running",
                "workflow_state": row.get("state")
                or row.get("worker_status")
                or "running",
                "issue_identifier": row.get("identifier") or row.get("issue_id"),
                "lane_status": row.get("worker_status") or "running",
                "kind": "running",
            }
            for row in (scheduler.get("running") or [])
            if isinstance(row, dict)
        ]

    state_lanes = _state_lanes_by_id(workflow_root, "change-delivery")
    runtime_sessions = _engine_runtime_sessions(workflow_root, "change-delivery")
    engine_entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for work_item in _engine_work_items(workflow_root, "change-delivery"):
        if not isinstance(work_item, dict):
            continue
        lane_id = str(work_item.get("work_id") or "").strip()
        if not lane_id:
            continue
        entry = _engine_lane_entry(
            work_item,
            workflow_name="change-delivery",
            state_lane=state_lanes.get(lane_id),
            runtime_session=runtime_sessions.get(lane_id),
        )
        seen.add(lane_id)
        if not _lane_is_terminal({"status": entry.get("status")}):
            engine_entries.append(entry)
    for lane in _active_state_lanes(workflow_root, "change-delivery"):
        lane_id = str(lane.get("lane_id") or "").strip()
        if lane_id and lane_id not in seen:
            entry = _state_lane_entry(lane, workflow_name="change-delivery")
            entry["lane_status_source"] = "workflow_state"
            entry["state_json_present"] = True
            engine_entries.append(entry)
    return engine_entries


def alert_state(workflow_root: Path) -> dict[str, Any]:
    paths = runtime_paths(Path(workflow_root))
    alert_path = paths["alert_state_path"]
    if not alert_path.exists():
        return {}
    try:
        return json.loads(alert_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _runtime_session_entries(scheduler: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for issue_id, raw_entry in (scheduler.get("runtime_sessions") or {}).items():
        if not isinstance(raw_entry, dict):
            continue
        issue_number = raw_entry.get("issue_number")
        entries.append(
            {
                "issue_id": raw_entry.get("issue_id") or issue_id,
                "issue_number": issue_number,
                "issue_identifier": raw_entry.get("identifier")
                or (f"#{issue_number}" if issue_number else issue_id),
                "thread_id": raw_entry.get("thread_id"),
                "turn_id": raw_entry.get("turn_id"),
                "status": raw_entry.get("status"),
                "cancel_requested": bool(raw_entry.get("cancel_requested") or False),
                "cancel_reason": raw_entry.get("cancel_reason"),
                "updated_at": raw_entry.get("updated_at"),
            }
        )
    return sorted(entries, key=lambda item: str(item.get("issue_id") or ""))


def workflow_status(workflow_root: Path) -> dict[str, Any]:
    workflow_root = Path(workflow_root)
    workflow_name = _workflow_name(workflow_root)
    if not workflow_name:
        return {}
    if workflow_name == "change-delivery":
        scheduler_payload = _engine_scheduler(workflow_root, "change-delivery")
        retry_wakeup = EngineStore(
            db_path=runtime_paths(workflow_root)["db_path"],
            workflow="change-delivery",
        ).retry_wakeup()
        totals = scheduler_payload.get("runtime_totals") or {}
        runtime_sessions = _runtime_session_entries(scheduler_payload)
        latest_runs = _engine_runs(workflow_root, "change-delivery")
        active = _active_state_lanes(workflow_root, "change-delivery")
        active_entries = [
            _state_lane_entry(lane, workflow_name="change-delivery") for lane in active
        ]
        running_count = len(
            [lane for lane in active if str(lane.get("status") or "") == "running"]
        )
        retry_count = len(
            [lane for lane in active if str(lane.get("status") or "") == "retry_queued"]
        )
        attention_count = len(
            [
                lane
                for lane in active
                if str(lane.get("status") or "") == "operator_attention"
            ]
        )
        decision_ready_count = len(
            [
                lane
                for lane in active
                if str(lane.get("status") or "") in {"claimed", "waiting"}
            ]
        )
        return {
            "workflow": "change-delivery",
            "health": None,
            "updated_at": scheduler_payload.get("updated_at"),
            "active_lane_count": len(active),
            "decision_ready_count": decision_ready_count,
            "running_count": running_count,
            "retry_count": retry_count,
            "retry_wakeup": retry_wakeup,
            "operator_attention_count": attention_count,
            "canceling_count": len(
                [
                    entry
                    for entry in runtime_sessions
                    if entry.get("status") == "canceling"
                ]
            ),
            "selected_issue": None,
            "runtime_sessions": runtime_sessions,
            "latest_runs": latest_runs,
            "total_tokens": int(totals.get("total_tokens") or 0),
            "rate_limits": totals.get("rate_limits"),
            "active_lanes": active_entries,
            "operator_attention_lanes": [
                lane
                for lane in active_entries
                if lane.get("status") == "operator_attention"
            ],
            "retry_lanes": [
                lane for lane in active_entries if lane.get("status") == "retry_queued"
            ],
        }
    if workflow_name != "issue-runner":
        scheduler_payload = _engine_scheduler(workflow_root, workflow_name)
        retry_wakeup = EngineStore(
            db_path=runtime_paths(workflow_root)["db_path"],
            workflow=workflow_name,
        ).retry_wakeup()
        totals = scheduler_payload.get("runtime_totals") or {}
        running = scheduler_payload.get("running") or []
        retry_queue = scheduler_payload.get("retry_queue") or []
        return {
            "workflow": workflow_name,
            "health": None,
            "updated_at": scheduler_payload.get("updated_at"),
            "running_count": len(running),
            "retry_count": len(retry_queue),
            "retry_wakeup": retry_wakeup,
            "selected_issue": None,
            "latest_runs": _engine_runs(workflow_root, workflow_name),
            "total_tokens": int(totals.get("total_tokens") or 0),
            "rate_limits": totals.get("rate_limits"),
        }

    status_path = _resolve_issue_runner_storage_path(
        workflow_root, "status", "memory/workflow-status.json"
    )
    status_payload = _load_optional_json(status_path) or {}
    scheduler_payload = _engine_scheduler(workflow_root, "issue-runner")
    scheduler = {
        "running": scheduler_payload.get("running") or [],
        "retry_queue": scheduler_payload.get("retry_queue") or [],
        "runtime_totals": scheduler_payload.get("runtime_totals") or {},
    }
    last_run = status_payload.get("lastRun") or {}
    latest_runs = _engine_runs(workflow_root, "issue-runner")
    return {
        "workflow": "issue-runner",
        "health": status_payload.get("health"),
        "updated_at": scheduler_payload.get("updated_at") or last_run.get("updatedAt"),
        "running_count": len(scheduler["running"]),
        "retry_count": len(scheduler["retry_queue"]),
        "selected_issue": (
            (last_run.get("issue") or {}).get("identifier")
            or (last_run.get("issue") or {}).get("id")
        ),
        "latest_runs": latest_runs,
        "total_tokens": int((scheduler["runtime_totals"].get("total_tokens") or 0)),
        "rate_limits": scheduler["runtime_totals"].get("rate_limits"),
    }
