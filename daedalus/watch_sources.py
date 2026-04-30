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

# Sibling-import boilerplate.
try:
    from workflows.change_delivery.paths import runtime_paths
except ImportError:
    import importlib.util as _ilu
    _here = Path(__file__).resolve().parent
    _spec = _ilu.spec_from_file_location(
        "daedalus_workflows_change_delivery_paths_for_watch",
        _here / "workflows" / "change_delivery" / "paths.py",
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


def recent_daedalus_events(workflow_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    paths = runtime_paths(Path(workflow_root))
    return _read_jsonl_tail(paths["event_log_path"], limit)


def recent_workflow_audit(workflow_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    base = Path(workflow_root)
    # workflow-audit.jsonl lives under <root>/runtime/memory/ in the project layout
    # and under <root>/memory/ in the legacy layout — match runtime_paths logic.
    runtime_event_log = runtime_paths(base)["event_log_path"]
    audit_path = runtime_event_log.parent / "workflow-audit.jsonl"
    return _read_jsonl_tail(audit_path, limit)


def active_lanes(workflow_root: Path) -> list[dict[str, Any]]:
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
            out.append({
                "lane_id": row[0],
                # `state` is the key the renderer (watch.py) consumes; we
                # source it from workflow_state. Both names are exposed for
                # consumers that care.
                "state": row[1],
                "workflow_state": row[1],
                "github_issue_number": row[2],
                "issue_number": row[2],
                "lane_status": row[3],
            })
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
