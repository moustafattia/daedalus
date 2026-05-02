"""Read-only aggregation of state from Sprints event sources for /sprints watch.

This module never writes â€” it only reads from:

  - ``<workflow_root>/runtime/memory/sprints-events.jsonl``
  - ``<workflow_root>/runtime/memory/workflow-audit.jsonl``
  - ``<workflow_root>/runtime/state/sprints/sprints.db`` (lanes table)
  - ``<workflow_root>/runtime/memory/sprints-alert-state.json``

Each function tolerates the source being absent / corrupt and returns an
empty result rather than raising. The TUI must keep rendering even if
one source is unavailable.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from engine.state import (
    read_engine_events,
    read_engine_runs,
    read_engine_scheduler_state,
)
from engine.work_items import work_item_from_issue
from workflows.loader import WorkflowContractError, load_workflow_contract

# Sibling-import boilerplate.
try:
    from workflows.paths import runtime_paths
except ImportError:
    import importlib.util as _ilu

    _here = Path(__file__).resolve().parent
    _spec = _ilu.spec_from_file_location(
        "sprints_workflows_shared_paths_for_watch",
        _here / "workflows" / "shared" / "paths.py",
    )
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    runtime_paths = _mod.runtime_paths


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

    paths = runtime_paths(Path(workflow_root))
    db_path = paths["db_path"]
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return []
    try:
        # Real columns per lane schema:
        #   lane_id (PK), issue_number, workflow_state, lane_status, ...
        # An earlier draft queried generic display aliases instead of the
        # real lanes schema, which
        # raised sqlite3.OperationalError against any real db, silently
        # returning [] and making /sprints watch falsely report
        # "no active lanes" even when lanes existed.
        cur = conn.execute(
            "SELECT lane_id, workflow_state, issue_number, lane_status "
            "FROM lanes "
            "WHERE lane_status NOT IN ('merged', 'closed', 'archived')"
        )
        out = []
        for row in cur.fetchall():
            lane = {
                "lane_id": row[0],
                # `state` is the key the renderer (observe/watch.py) consumes; we
                # source it from workflow_state. Both names are exposed for
                # consumers that care.
                "state": row[1],
                "workflow_state": row[1],
                "issue_number": row[2],
                "issue_identifier": f"#{row[2]}",
                "lane_status": row[3],
            }
            lane["work_item"] = work_item_from_issue(
                {
                    "id": str(
                        lane.get("issue_id")
                        or lane.get("issue_identifier")
                        or lane.get("id")
                        or ""
                    ),
                    "identifier": str(
                        lane.get("issue_identifier")
                        or lane.get("issue_number")
                        or lane.get("id")
                        or ""
                    ),
                    "title": str(lane.get("title") or ""),
                    "url": lane.get("url"),
                    "state": lane.get("state"),
                },
                source="agentic",
            ).to_dict()
            out.append(lane)
    except sqlite3.OperationalError:
        out = []
    finally:
        conn.close()
    return out


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
        totals = scheduler_payload.get("runtime_totals") or {}
        runtime_sessions = _runtime_session_entries(scheduler_payload)
        latest_runs = _engine_runs(workflow_root, "change-delivery")
        return {
            "workflow": "change-delivery",
            "health": None,
            "updated_at": scheduler_payload.get("updated_at"),
            "running_count": len(
                [
                    entry
                    for entry in runtime_sessions
                    if entry.get("status") == "running"
                ]
            ),
            "retry_count": 0,
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
        }
    if workflow_name != "issue-runner":
        scheduler_payload = _engine_scheduler(workflow_root, workflow_name)
        totals = scheduler_payload.get("runtime_totals") or {}
        running = scheduler_payload.get("running") or []
        retry_queue = scheduler_payload.get("retry_queue") or []
        return {
            "workflow": workflow_name,
            "health": None,
            "updated_at": scheduler_payload.get("updated_at"),
            "running_count": len(running),
            "retry_count": len(retry_queue),
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
