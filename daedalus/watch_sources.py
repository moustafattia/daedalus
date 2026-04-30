"""Read-only aggregation of state from Daedalus event sources for /daedalus watch.

This module never writes — it only reads from:

  - ``<workflow_root>/runtime/memory/daedalus-events.jsonl``
  - ``<workflow_root>/runtime/memory/workflow-audit.jsonl``
  - ``<workflow_root>/runtime/state/daedalus/daedalus.db`` (lanes table)
  - ``<workflow_root>/runtime/memory/daedalus-alert-state.json``

Each function tolerates the source being absent / corrupt and returns an
empty result rather than raising. The TUI must keep rendering even if
one source is unavailable.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from engine.work_items import work_item_from_change_delivery_lane, work_item_from_issue
from workflows.contract import WorkflowContractError, load_workflow_contract

# Sibling-import boilerplate.
try:
    from workflows.shared.paths import runtime_paths
except ImportError:
    import importlib.util as _ilu
    _here = Path(__file__).resolve().parent
    _spec = _ilu.spec_from_file_location(
        "daedalus_workflows_shared_paths_for_watch",
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
        return str(load_workflow_contract(Path(workflow_root)).config.get("workflow") or "").strip() or None
    except (FileNotFoundError, WorkflowContractError, OSError):
        return None


def _resolve_issue_runner_storage_path(workflow_root: Path, key: str, default: str) -> Path | None:
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


def recent_daedalus_events(workflow_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    paths = runtime_paths(Path(workflow_root))
    return _read_jsonl_tail(paths["event_log_path"], limit)


def recent_workflow_audit(workflow_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    base = Path(workflow_root)
    if _workflow_name(base) == "issue-runner":
        audit_path = _resolve_issue_runner_storage_path(base, "audit-log", "memory/workflow-audit.jsonl")
        return _read_jsonl_tail(audit_path, limit) if audit_path is not None else []
    # workflow-audit.jsonl lives under <root>/runtime/memory/ in the project layout
    # and under <root>/memory/ in the legacy layout — match runtime_paths logic.
    runtime_event_log = runtime_paths(base)["event_log_path"]
    audit_path = runtime_event_log.parent / "workflow-audit.jsonl"
    return _read_jsonl_tail(audit_path, limit)


def active_lanes(workflow_root: Path) -> list[dict[str, Any]]:
    workflow_root = Path(workflow_root)
    if _workflow_name(workflow_root) == "issue-runner":
        scheduler_path = _resolve_issue_runner_storage_path(
            workflow_root, "scheduler", "memory/workflow-scheduler.json"
        )
        scheduler = _load_optional_json(scheduler_path) or {}
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
                    "github_issue_number": identifier,
                    "issue_number": identifier,
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
                    "github_issue_number": identifier,
                    "issue_number": identifier,
                    "issue_identifier": identifier,
                    "lane_status": "retrying",
                    "kind": "retrying",
                    "work_item": work_item,
                }
            )
        return out

    paths = runtime_paths(Path(workflow_root))
    db_path = paths["db_path"]
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return []
    try:
        # Real columns per runtime.py lanes schema:
        #   lane_id (PK), issue_number, workflow_state, lane_status, ...
        # An earlier draft queried `state` and `github_issue_number` which
        # raised sqlite3.OperationalError against any real db, silently
        # returning [] and making /daedalus watch falsely report
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
                # `state` is the key the renderer (watch.py) consumes; we
                # source it from workflow_state. Both names are exposed for
                # consumers that care.
                "state": row[1],
                "workflow_state": row[1],
                "github_issue_number": row[2],
                "issue_number": row[2],
                "lane_status": row[3],
            }
            lane["work_item"] = work_item_from_change_delivery_lane(lane).to_dict()
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


def _codex_turn_entries(scheduler: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for issue_id, raw_entry in (scheduler.get("codex_threads") or scheduler.get("codexThreads") or {}).items():
        if not isinstance(raw_entry, dict):
            continue
        issue_number = raw_entry.get("issue_number") or raw_entry.get("issueNumber")
        entries.append(
            {
                "issue_id": raw_entry.get("issue_id") or issue_id,
                "issue_number": issue_number,
                "issue_identifier": raw_entry.get("identifier") or (f"#{issue_number}" if issue_number else issue_id),
                "thread_id": raw_entry.get("thread_id") or raw_entry.get("threadId"),
                "turn_id": raw_entry.get("turn_id") or raw_entry.get("turnId"),
                "status": raw_entry.get("status"),
                "cancel_requested": bool(raw_entry.get("cancel_requested") or raw_entry.get("cancelRequested") or False),
                "cancel_reason": raw_entry.get("cancel_reason") or raw_entry.get("cancelReason"),
                "updated_at": raw_entry.get("updated_at") or raw_entry.get("updatedAt"),
            }
        )
    return sorted(entries, key=lambda item: str(item.get("issue_id") or ""))


def workflow_status(workflow_root: Path) -> dict[str, Any]:
    workflow_root = Path(workflow_root)
    workflow_name = _workflow_name(workflow_root)
    if workflow_name not in {"issue-runner", "change-delivery"}:
        return {}
    if workflow_name == "change-delivery":
        scheduler_path = _resolve_issue_runner_storage_path(workflow_root, "scheduler", "memory/workflow-scheduler.json")
        scheduler_payload = _load_optional_json(scheduler_path) or {}
        totals = scheduler_payload.get("codex_totals") or scheduler_payload.get("codexTotals") or {}
        codex_turns = _codex_turn_entries(scheduler_payload)
        return {
            "workflow": "change-delivery",
            "health": None,
            "updated_at": scheduler_payload.get("updatedAt"),
            "running_count": len([entry for entry in codex_turns if entry.get("status") == "running"]),
            "retry_count": 0,
            "canceling_count": len([entry for entry in codex_turns if entry.get("status") == "canceling"]),
            "selected_issue": None,
            "codex_turns": codex_turns,
            "total_tokens": int(totals.get("total_tokens") or 0),
            "rate_limits": totals.get("rate_limits"),
        }
    status_path = _resolve_issue_runner_storage_path(workflow_root, "status", "memory/workflow-status.json")
    scheduler_path = _resolve_issue_runner_storage_path(workflow_root, "scheduler", "memory/workflow-scheduler.json")
    status_payload = _load_optional_json(status_path) or {}
    scheduler_payload = _load_optional_json(scheduler_path) or {}
    scheduler = {
        "running": scheduler_payload.get("running") or [],
        "retry_queue": scheduler_payload.get("retry_queue") or scheduler_payload.get("retryQueue") or [],
        "codex_totals": scheduler_payload.get("codex_totals") or scheduler_payload.get("codexTotals") or {},
    }
    last_run = status_payload.get("lastRun") or {}
    return {
        "workflow": "issue-runner",
        "health": status_payload.get("health"),
        "updated_at": scheduler_payload.get("updatedAt") or last_run.get("updatedAt"),
        "running_count": len(scheduler["running"]),
        "retry_count": len(scheduler["retry_queue"]),
        "selected_issue": ((last_run.get("issue") or {}).get("identifier") or (last_run.get("issue") or {}).get("id")),
        "total_tokens": int((scheduler["codex_totals"].get("total_tokens") or 0)),
        "rate_limits": scheduler["codex_totals"].get("rate_limits"),
    }
