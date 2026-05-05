import time
from pathlib import Path
from typing import Any

from sprints.engine.retention import normalize_event_retention
from sprints.engine.state import (
    read_engine_events,
    read_engine_events_for_run,
    read_engine_run,
    read_engine_runs,
)
from sprints.engine.store import EngineStore
from sprints.core.contracts import load_workflow_contract
from sprints.core.paths import runtime_paths


class EngineReportError(Exception):
    pass


def _workflow_name_for_root(workflow_root: Path) -> str:
    contract = load_workflow_contract(workflow_root)
    workflow_name = str(contract.config.get("workflow") or "").strip()
    if not workflow_name:
        raise EngineReportError(
            f"{contract.source_path} is missing top-level `workflow:` field"
        )
    return workflow_name


def _run_timeline_for_cli(
    workflow_root: Path, workflow_name: str, run_id: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    paths = runtime_paths(workflow_root)
    return read_engine_events_for_run(
        paths["db_path"], workflow=workflow_name, run_id=run_id, limit=max(limit, 1)
    )


def build_runs_report(
    *,
    workflow_root: Path,
    action: str = "list",
    run_id: str | None = None,
    limit: int = 20,
    stale_seconds: int = 600,
) -> dict[str, Any]:
    workflow_root = Path(workflow_root).resolve()
    workflow_name = _workflow_name_for_root(workflow_root)
    db_path = runtime_paths(workflow_root)["db_path"]
    now_epoch = time.time()
    if action == "show":
        if not run_id:
            raise EngineReportError("runs show requires a run_id")
        run = read_engine_run(db_path, workflow=workflow_name, run_id=run_id)
        if run is None:
            raise EngineReportError(f"unknown engine run: {run_id}")
        age_seconds = max(
            int(now_epoch - float(run.get("started_at_epoch") or now_epoch)), 0
        )
        return {
            "mode": "show",
            "workflow": workflow_name,
            "run": {
                **run,
                "age_seconds": age_seconds,
                "stale": run.get("status") == "running" and age_seconds > stale_seconds,
            },
            "timeline": _run_timeline_for_cli(
                workflow_root, workflow_name, run_id, limit=max(limit, 1)
            ),
        }

    runs = read_engine_runs(db_path, workflow=workflow_name, limit=max(limit, 1) * 5)
    enriched = []
    for run in runs:
        age_seconds = max(
            int(now_epoch - float(run.get("started_at_epoch") or now_epoch)), 0
        )
        item = {
            **run,
            "age_seconds": age_seconds,
            "stale": run.get("status") == "running" and age_seconds > stale_seconds,
        }
        if action == "failed" and item.get("status") != "failed":
            continue
        if action == "stale" and not item.get("stale"):
            continue
        enriched.append(item)
        if len(enriched) >= limit:
            break
    return {
        "mode": action,
        "workflow": workflow_name,
        "counts": {
            "shown": len(enriched),
            "failed": len([run for run in enriched if run.get("status") == "failed"]),
            "running": len([run for run in enriched if run.get("status") == "running"]),
            "stale": len([run for run in enriched if run.get("stale")]),
        },
        "runs": enriched,
    }


def _workflow_event_retention(workflow_root: Path) -> dict[str, Any]:
    contract = load_workflow_contract(workflow_root)
    retention = contract.config.get("retention") or {}
    if not isinstance(retention, dict):
        return {}
    events = retention.get("events") or {}
    return events if isinstance(events, dict) else {}


def build_events_report(
    *,
    workflow_root: Path,
    action: str = "list",
    run_id: str | None = None,
    work_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 50,
    order: str = "desc",
    max_age_days: float | None = None,
    max_rows: int | None = None,
) -> dict[str, Any]:
    workflow_root = Path(workflow_root).resolve()
    workflow_name = _workflow_name_for_root(workflow_root)
    store = EngineStore(
        db_path=runtime_paths(workflow_root)["db_path"], workflow=workflow_name
    )
    filters = {
        "run_id": run_id,
        "work_id": work_id,
        "event_type": event_type,
        "severity": severity,
    }
    retention_cfg = normalize_event_retention(_workflow_event_retention(workflow_root))
    if max_age_days is not None:
        retention_cfg["configured"] = True
        retention_cfg["max_age_days"] = max_age_days
        retention_cfg["max_age_seconds"] = max_age_days * 86400
    if max_rows is not None:
        retention_cfg["configured"] = True
        retention_cfg["max_rows"] = max_rows
    if action == "stats":
        return {
            "mode": "stats",
            "workflow": workflow_name,
            "stats": store.event_stats(retention_cfg),
        }
    if action == "prune":
        if not retention_cfg.get("configured"):
            raise EngineReportError(
                "events prune requires --max-age-days, --max-rows, or retention.events in WORKFLOW.md"
            )
        result = store.prune_events(
            max_age_seconds=retention_cfg.get("max_age_seconds"),
            max_rows=retention_cfg.get("max_rows"),
        )
        return {
            "mode": "prune",
            "workflow": workflow_name,
            "retention": retention_cfg,
            **result,
        }
    events = read_engine_events(
        runtime_paths(workflow_root)["db_path"],
        workflow=workflow_name,
        run_id=run_id,
        work_id=work_id,
        event_type=event_type,
        severity=severity,
        limit=max(limit, 1),
        order=order,
    )
    return {
        "mode": "list",
        "workflow": workflow_name,
        "filters": {
            key: value for key, value in filters.items() if value not in (None, "")
        },
        "counts": {"shown": len(events)},
        "events": events,
    }
