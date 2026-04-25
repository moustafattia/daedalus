import argparse
import calendar
import importlib.util
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from workflows.code_review.paths import (
    plugin_entrypoint_path,
    runtime_paths,
    runtime_paths as yoyopod_runtime_paths,
    yoyopod_cli_argv,
)
from workflows.code_review.status import build_status as build_yoyopod_core_status
import sys

def _load_migration_module():
    """Load the sibling migration.py module via file path.

    Mirrors tools.py::_load_daedalus_module / alerts.py::_load_tools_module
    so runtime.py works whether loaded as part of the hermes_relay package
    or directly via spec_from_file_location.
    """
    module_path = Path(__file__).resolve().parent / "migration.py"
    spec = importlib.util.spec_from_file_location("daedalus_migration_for_runtime", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load migration module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RELAY_SCHEMA_VERSION = 2
RUNTIME_LEASE_KEY = "primary-orchestrator"
RUNTIME_LEASE_SCOPE = "runtime"
EXECUTION_CONTROL_ID = "primary"
RELAY_OWNER = "relay"
WORKFLOW_ERROR_ANALYST_ROLE = "Workflow_Error_Analyst"
STALLED_RECOVERY_AGE_THRESHOLD_SECONDS = 600
STALLED_RECOVERY_DETECTION_THRESHOLD = 2
DISPATCHED_ACTION_TIMEOUT_SECONDS = 1800
SCHEMA_MIGRATIONS_TABLE = "relay_schema_migrations"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_to_epoch(value: str | None) -> int | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return int(calendar.timegm(time.strptime(value, fmt)))
        except Exception:
            continue
    return None


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _parse_json_blob(value: Any) -> Any:
    if value in (None, "", "null"):
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _connect(db_path: Path) -> sqlite3.Connection:
    _ensure_parent(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return any(row[1] == column_name for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def _create_execution_controls_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_controls (
          control_id TEXT PRIMARY KEY,
          active_execution_enabled INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL,
          metadata_json TEXT
        )
        """
    )



def _migrate_execution_control_table(conn: sqlite3.Connection, *, now_iso: str) -> None:
    expected_columns = [
        ("control_id", "TEXT", 0, None, 1),
        ("active_execution_enabled", "INTEGER", 1, "1", 0),
        ("updated_at", "TEXT", 1, None, 0),
        ("metadata_json", "TEXT", 0, None, 0),
    ]
    if _table_exists(conn, "execution_controls"):
        current_columns = [tuple(row[1:6]) for row in conn.execute("PRAGMA table_info(execution_controls)").fetchall()]
        if current_columns != expected_columns:
            raise RuntimeError(f"unsupported execution_controls schema: {current_columns}")
        if _table_exists(conn, "ownership_controls"):
            conn.execute("DROP TABLE ownership_controls")
        return
    if not _table_exists(conn, "ownership_controls"):
        _create_execution_controls_table(conn)
        return
    legacy_rows = conn.execute(
        "SELECT control_id, active_execution_enabled, updated_at, metadata_json FROM ownership_controls"
    ).fetchall()
    _create_execution_controls_table(conn)
    for control_id, active_execution_enabled, updated_at, metadata_json in legacy_rows:
        conn.execute(
            "INSERT OR REPLACE INTO execution_controls (control_id, active_execution_enabled, updated_at, metadata_json) VALUES (?, ?, ?, ?)",
            (
                control_id,
                1 if active_execution_enabled is None else int(bool(active_execution_enabled)),
                updated_at or now_iso,
                metadata_json,
            ),
        )
    conn.execute("DROP TABLE ownership_controls")





def _create_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} (
          migration_version INTEGER PRIMARY KEY,
          from_version INTEGER NOT NULL,
          to_version INTEGER NOT NULL,
          applied_at TEXT NOT NULL,
          details_json TEXT
        )
        """
    )



def _migrate_relay_schema_v1_to_v2(*, conn: sqlite3.Connection, now_iso: str) -> None:
    _create_schema_migrations_table(conn)
    if not _column_exists(conn, "lane_actions", "recovery_attempt_count"):
        conn.execute(
            "ALTER TABLE lane_actions ADD COLUMN recovery_attempt_count INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        f"""
        INSERT OR REPLACE INTO {SCHEMA_MIGRATIONS_TABLE} (
          migration_version, from_version, to_version, applied_at, details_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            2,
            1,
            2,
            now_iso,
            json.dumps(
                {
                    "from_version": 1,
                    "to_version": 2,
                    "changes": ["lane_actions.recovery_attempt_count"],
                },
                sort_keys=True,
            ),
        ),
    )



def _migrate_schema_identity(conn) -> None:
    """Rename relay-era schema artifacts to daedalus equivalents.

    Idempotent: no-op on a fresh DB, no-op on an already-migrated DB.

    Operations performed when relay-era artifacts are detected:
    - ALTER TABLE relay_runtime RENAME TO daedalus_runtime
    - UPDATE daedalus_runtime SET runtime_id='daedalus' WHERE runtime_id='relay'

    Must be called before CREATE TABLE IF NOT EXISTS daedalus_runtime so
    the rename happens cleanly without producing two tables.
    """
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='relay_runtime'"
    )
    if cur.fetchone() is not None:
        conn.execute("ALTER TABLE relay_runtime RENAME TO daedalus_runtime")

    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='daedalus_runtime'"
    )
    if cur.fetchone() is not None:
        conn.execute(
            "UPDATE daedalus_runtime SET runtime_id='daedalus' WHERE runtime_id='relay'"
        )


def init_relay_db(*, workflow_root: Path, project_key: str) -> dict[str, Any]:
    # 1. Filesystem-level migration (renames relay-era files if present).
    #    Done before opening the DB so we don't open a stale empty file.
    _load_migration_module().migrate_filesystem_state(workflow_root)

    # 2. Resolve canonical paths and open the DB.
    paths = runtime_paths(workflow_root)
    db_path = paths["db_path"]
    conn = _connect(db_path)
    try:
        # 3. SQL identity migration (rename relay_runtime -> daedalus_runtime
        #    if needed). Must run BEFORE the CREATE TABLE statements below.
        _migrate_schema_identity(conn)

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS relay_runtime (
              runtime_id TEXT PRIMARY KEY,
              project_key TEXT NOT NULL,
              schema_version INTEGER NOT NULL,
              runtime_status TEXT NOT NULL,
              engine_name TEXT NOT NULL,
              engine_owner TEXT NOT NULL,
              active_orchestrator_instance_id TEXT,
              current_mode TEXT NOT NULL,
              current_epoch TEXT NOT NULL,
              latest_checkpoint_path TEXT,
              latest_checkpoint_sha256 TEXT,
              latest_boot_at TEXT,
              latest_heartbeat_at TEXT,
              latest_reconcile_at TEXT,
              latest_error_at TEXT,
              latest_error_summary TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS leases (
              lease_id TEXT PRIMARY KEY,
              lease_scope TEXT NOT NULL,
              lease_key TEXT NOT NULL,
              owner_instance_id TEXT NOT NULL,
              owner_role TEXT NOT NULL,
              acquired_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              released_at TEXT,
              release_reason TEXT,
              metadata_json TEXT,
              UNIQUE (lease_scope, lease_key)
            );

            CREATE TABLE IF NOT EXISTS lanes (
              lane_id TEXT PRIMARY KEY,
              issue_number INTEGER NOT NULL,
              issue_url TEXT NOT NULL,
              issue_title TEXT NOT NULL,
              repo_path TEXT NOT NULL,
              worktree_path TEXT,
              branch_name TEXT,
              priority_hint TEXT,
              effort_label TEXT,
              actor_backend TEXT NOT NULL,
              lane_status TEXT NOT NULL,
              workflow_state TEXT NOT NULL,
              review_state TEXT NOT NULL,
              merge_state TEXT NOT NULL,
              current_head_sha TEXT,
              last_published_head_sha TEXT,
              active_pr_number INTEGER,
              active_pr_url TEXT,
              active_pr_head_sha TEXT,
              required_internal_review INTEGER NOT NULL DEFAULT 0,
              required_external_review INTEGER NOT NULL DEFAULT 0,
              merge_blocked INTEGER NOT NULL DEFAULT 0,
              merge_blockers_json TEXT,
              repair_brief_json TEXT,
              active_actor_id TEXT,
              current_action_id TEXT,
              last_completed_action_id TEXT,
              last_meaningful_progress_at TEXT,
              last_meaningful_progress_kind TEXT,
              operator_attention_required INTEGER NOT NULL DEFAULT 0,
              operator_attention_reason TEXT,
              archived_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (issue_number)
            );

            CREATE TABLE IF NOT EXISTS lane_actors (
              actor_id TEXT PRIMARY KEY,
              lane_id TEXT NOT NULL,
              actor_role TEXT NOT NULL,
              actor_backend TEXT NOT NULL,
              backend_identity TEXT,
              backend_session_id TEXT,
              backend_process_id TEXT,
              backend_endpoint TEXT,
              backend_command TEXT,
              backend_extra_json TEXT,
              model_name TEXT,
              status TEXT NOT NULL,
              last_seen_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (lane_id) REFERENCES lanes(lane_id)
            );

            CREATE TABLE IF NOT EXISTS lane_actions (
              action_id TEXT PRIMARY KEY,
              lane_id TEXT NOT NULL,
              action_type TEXT NOT NULL,
              action_reason TEXT,
              action_mode TEXT,
              requested_by TEXT NOT NULL,
              target_actor_role TEXT,
              target_actor_id TEXT,
              target_head_sha TEXT,
              idempotency_key TEXT NOT NULL,
              status TEXT NOT NULL,
              requested_at TEXT NOT NULL,
              dispatched_at TEXT,
              completed_at TEXT,
              failed_at TEXT,
              result_code TEXT,
              result_summary TEXT,
              request_payload_json TEXT,
              result_payload_json TEXT,
              error_payload_json TEXT,
              retry_count INTEGER NOT NULL DEFAULT 0,
              recovery_attempt_count INTEGER NOT NULL DEFAULT 0,
              superseded_by_action_id TEXT,
              causal_event_id TEXT,
              FOREIGN KEY (lane_id) REFERENCES lanes(lane_id),
              UNIQUE (idempotency_key)
            );

            CREATE TABLE IF NOT EXISTS lane_reviews (
              review_id TEXT PRIMARY KEY,
              lane_id TEXT NOT NULL,
              reviewer_scope TEXT NOT NULL,
              reviewer_role TEXT NOT NULL,
              reviewer_name TEXT NOT NULL,
              backend_type TEXT,
              model_name TEXT,
              status TEXT NOT NULL,
              verdict TEXT,
              requested_head_sha TEXT,
              reviewed_head_sha TEXT,
              review_scope TEXT,
              open_finding_count INTEGER NOT NULL DEFAULT 0,
              blockers_json TEXT,
              concerns_json TEXT,
              suggestions_json TEXT,
              summary_text TEXT,
              requested_at TEXT,
              completed_at TEXT,
              source_event_id TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (lane_id) REFERENCES lanes(lane_id)
            );

            CREATE TABLE IF NOT EXISTS failures (
              failure_id TEXT PRIMARY KEY,
              lane_id TEXT,
              related_action_id TEXT,
              related_actor_id TEXT,
              failure_scope TEXT NOT NULL,
              failure_class TEXT NOT NULL,
              severity TEXT NOT NULL,
              detected_at TEXT NOT NULL,
              evidence_json TEXT,
              analyst_status TEXT NOT NULL,
              analyst_recommended_action TEXT,
              analyst_confidence REAL,
              analyst_summary TEXT,
              escalated INTEGER NOT NULL DEFAULT 0,
              resolved_at TEXT,
              resolution_action_id TEXT,
              metadata_json TEXT,
              FOREIGN KEY (lane_id) REFERENCES lanes(lane_id)
            );

            CREATE TABLE IF NOT EXISTS state_projections (
              projection_id TEXT PRIMARY KEY,
              lane_id TEXT,
              projection_type TEXT NOT NULL,
              target_path TEXT NOT NULL,
              source_version TEXT NOT NULL,
              last_written_at TEXT NOT NULL,
              checksum_sha256 TEXT,
              metadata_json TEXT,
              FOREIGN KEY (lane_id) REFERENCES lanes(lane_id),
              UNIQUE (projection_type, target_path)
            );

            CREATE TABLE IF NOT EXISTS relay_schema_migrations (
              migration_version INTEGER PRIMARY KEY,
              from_version INTEGER NOT NULL,
              to_version INTEGER NOT NULL,
              applied_at TEXT NOT NULL,
              details_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_leases_scope_key ON leases(lease_scope, lease_key);
            CREATE INDEX IF NOT EXISTS idx_leases_owner ON leases(owner_instance_id);
            CREATE INDEX IF NOT EXISTS idx_lanes_status ON lanes(lane_status, workflow_state);
            CREATE INDEX IF NOT EXISTS idx_lanes_active_pr ON lanes(active_pr_number);
            CREATE INDEX IF NOT EXISTS idx_lane_actors_lane_role ON lane_actors(lane_id, actor_role);
            CREATE INDEX IF NOT EXISTS idx_lane_actions_lane_status ON lane_actions(lane_id, status, requested_at);
            CREATE INDEX IF NOT EXISTS idx_lane_actions_actor ON lane_actions(target_actor_id, status);
            CREATE INDEX IF NOT EXISTS idx_lane_reviews_lane_scope ON lane_reviews(lane_id, reviewer_scope, status);
            CREATE INDEX IF NOT EXISTS idx_failures_lane_detected ON failures(lane_id, detected_at);
            CREATE INDEX IF NOT EXISTS idx_state_projections_lane_type ON state_projections(lane_id, projection_type);
            """
        )
        now_iso = _now_iso()
        runtime_row = conn.execute(
            "SELECT schema_version FROM relay_runtime WHERE runtime_id='relay'"
        ).fetchone()
        current_schema_version = int(runtime_row[0]) if runtime_row else RELAY_SCHEMA_VERSION
        if runtime_row is None:
            conn.execute(
                """
                INSERT INTO relay_runtime (
                  runtime_id, project_key, schema_version, runtime_status, engine_name, engine_owner,
                  active_orchestrator_instance_id, current_mode, current_epoch,
                  latest_checkpoint_path, latest_checkpoint_sha256,
                  latest_boot_at, latest_heartbeat_at, latest_reconcile_at,
                  latest_error_at, latest_error_summary, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    "relay",
                    project_key,
                    RELAY_SCHEMA_VERSION,
                    "initialized",
                    "Hermes Relay",
                    "Workflow_Orchestrator",
                    "shadow",
                    "relay-shadow-v1",
                    now_iso,
                    now_iso,
                ),
            )
        else:
            if current_schema_version > RELAY_SCHEMA_VERSION:
                raise RuntimeError(f"unsupported relay schema version: {current_schema_version}")
            conn.execute(
                """
                UPDATE relay_runtime
                SET project_key=?, updated_at=?
                WHERE runtime_id='relay'
                """,
                (project_key, now_iso),
            )
            if current_schema_version < RELAY_SCHEMA_VERSION:
                _migrate_relay_schema_v1_to_v2(conn=conn, now_iso=now_iso)
                conn.execute(
                    """
                    UPDATE relay_runtime
                    SET schema_version=?, updated_at=?
                    WHERE runtime_id='relay'
                    """,
                    (RELAY_SCHEMA_VERSION, now_iso),
                )
        _migrate_execution_control_table(conn, now_iso=now_iso)
        conn.execute(
            """
            INSERT INTO execution_controls (
              control_id, active_execution_enabled, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(control_id) DO NOTHING
            """,
            (
                EXECUTION_CONTROL_ID,
                1,
                now_iso,
                json.dumps({"source": "init"}, sort_keys=True),
            ),
        )
        conn.commit()
        return {"ok": True, "db_path": str(db_path), "project_key": project_key}
    finally:
        conn.close()

def append_relay_event(*, event_log_path: Path, event: dict[str, Any]) -> dict[str, Any]:
    _ensure_parent(event_log_path)
    with event_log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return {"ok": True, "event_log_path": str(event_log_path), "event_id": event.get("event_id")}


def acquire_lease(
    *,
    db_path: Path,
    lease_scope: str,
    lease_key: str,
    owner_instance_id: str,
    owner_role: str,
    now_iso: str | None = None,
    ttl_seconds: int = 60,
) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    now_epoch = _iso_to_epoch(now_iso)
    expires_epoch = (now_epoch or int(time.time())) + ttl_seconds
    expires_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires_epoch))
    lease_id = f"lease:{lease_scope}:{lease_key}"
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT owner_instance_id, expires_at, released_at FROM leases WHERE lease_scope=? AND lease_key=?",
            (lease_scope, lease_key),
        ).fetchone()
        if row:
            current_owner, expires_at, released_at = row
            expires_at_epoch = _iso_to_epoch(expires_at)
            if not released_at and expires_at_epoch and expires_at_epoch > (now_epoch or 0) and current_owner != owner_instance_id:
                return {"acquired": False, "lease_id": lease_id, "owner_instance_id": current_owner}
            conn.execute(
                """
                UPDATE leases
                SET owner_instance_id=?, owner_role=?, acquired_at=?, expires_at=?, released_at=NULL, release_reason=NULL
                WHERE lease_scope=? AND lease_key=?
                """,
                (owner_instance_id, owner_role, now_iso, expires_iso, lease_scope, lease_key),
            )
        else:
            conn.execute(
                """
                INSERT INTO leases (
                  lease_id, lease_scope, lease_key, owner_instance_id, owner_role,
                  acquired_at, expires_at, released_at, release_reason, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (lease_id, lease_scope, lease_key, owner_instance_id, owner_role, now_iso, expires_iso),
            )
        conn.commit()
        return {"acquired": True, "lease_id": lease_id, "owner_instance_id": owner_instance_id, "expires_at": expires_iso}
    finally:
        conn.close()


def release_lease(
    *,
    db_path: Path,
    lease_scope: str,
    lease_key: str,
    owner_instance_id: str,
    now_iso: str | None = None,
    release_reason: str | None = None,
) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT owner_instance_id FROM leases WHERE lease_scope=? AND lease_key=?",
            (lease_scope, lease_key),
        ).fetchone()
        if not row or row[0] != owner_instance_id:
            return {"released": False, "reason": "not-owner"}
        conn.execute(
            """
            UPDATE leases
            SET released_at=?, release_reason=?
            WHERE lease_scope=? AND lease_key=?
            """,
            (now_iso, release_reason, lease_scope, lease_key),
        )
        conn.commit()
        return {"released": True, "lease_id": f"lease:{lease_scope}:{lease_key}", "owner_instance_id": owner_instance_id}
    finally:
        conn.close()


def _runtime_paths(workflow_root: Path) -> dict[str, Path]:
    return yoyopod_runtime_paths(workflow_root)


def _default_execution_control(*, now_iso: str | None = None) -> dict[str, Any]:
    return {
        "control_id": EXECUTION_CONTROL_ID,
        "active_execution_enabled": True,
        "updated_at": now_iso or _now_iso(),
        "metadata": {},
    }


def get_execution_control(*, workflow_root: Path) -> dict[str, Any]:
    paths = _runtime_paths(workflow_root)
    init_relay_db(workflow_root=workflow_root, project_key="yoyopod")
    conn = _connect(paths["db_path"])
    try:
        row = conn.execute(
            "SELECT control_id, active_execution_enabled, updated_at, metadata_json FROM execution_controls WHERE control_id=?",
            (EXECUTION_CONTROL_ID,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return _default_execution_control()
    return {
        "control_id": row[0],
        "active_execution_enabled": bool(row[1]),
        "updated_at": row[2],
        "metadata": _parse_json_blob(row[3]) or {},
    }


def set_execution_control(
    *,
    workflow_root: Path,
    active_execution_enabled: bool,
    metadata: dict[str, Any] | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    metadata = metadata or {}
    paths = _runtime_paths(workflow_root)
    init_relay_db(workflow_root=workflow_root, project_key="yoyopod")
    conn = _connect(paths["db_path"])
    try:
        conn.execute(
            """
            INSERT INTO execution_controls (
              control_id, active_execution_enabled, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(control_id) DO UPDATE SET
              active_execution_enabled=excluded.active_execution_enabled,
              updated_at=excluded.updated_at,
              metadata_json=excluded.metadata_json
            """,
            (
                EXECUTION_CONTROL_ID,
                1 if active_execution_enabled else 0,
                now_iso,
                json.dumps(metadata, sort_keys=True),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    append_relay_event(
        event_log_path=paths["event_log_path"],
        event={
            "event_id": f"evt:active_execution_control_updated:{int(bool(active_execution_enabled))}:{now_iso}",
            "event_type": "active_execution_control_updated",
            "event_version": 1,
            "created_at": now_iso,
            "producer": "Workflow_Orchestrator",
            "project_key": "yoyopod",
            "lane_id": None,
            "issue_number": None,
            "head_sha": None,
            "causal_event_id": None,
            "causal_action_id": None,
            "dedupe_key": f"active_execution_control_updated:{int(bool(active_execution_enabled))}",
            "payload": {
                "primary_owner": RELAY_OWNER,
                "active_execution_enabled": bool(active_execution_enabled),
                "metadata": metadata,
            },
        },
    )
    return get_execution_control(workflow_root=workflow_root)


def evaluate_active_execution_gate(
    *,
    workflow_root: Path,
    legacy_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = get_runtime_status(workflow_root=workflow_root)
    control = get_execution_control(workflow_root=workflow_root)
    reasons: list[str] = []
    if not control.get("active_execution_enabled"):
        reasons.append("active-execution-disabled")
    if runtime.get("runtime_status") != "running":
        reasons.append("runtime-not-running")
    if runtime.get("current_mode") != "active":
        reasons.append("runtime-not-active-mode")
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "execution": control,
        "primary_owner": RELAY_OWNER,
        "runtime": runtime,
        "legacy_health": (legacy_status or {}).get("health"),
    }


def bootstrap_runtime(
    *,
    workflow_root: Path,
    project_key: str,
    instance_id: str,
    mode: str = "shadow",
    now_iso: str | None = None,
) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    paths = _runtime_paths(workflow_root)
    init_relay_db(workflow_root=workflow_root, project_key=project_key)
    lease = acquire_lease(
        db_path=paths["db_path"],
        lease_scope=RUNTIME_LEASE_SCOPE,
        lease_key=RUNTIME_LEASE_KEY,
        owner_instance_id=instance_id,
        owner_role="Workflow_Orchestrator",
        now_iso=now_iso,
        ttl_seconds=60,
    )
    if not lease.get("acquired"):
        return {
            "runtime_status": "blocked",
            "reason": "lease-held",
            "owner_instance_id": lease.get("owner_instance_id"),
            "db_path": str(paths["db_path"]),
            "event_log_path": str(paths["event_log_path"]),
        }
    conn = _connect(paths["db_path"])
    try:
        conn.execute(
            """
            UPDATE relay_runtime
            SET runtime_status=?, active_orchestrator_instance_id=?, current_mode=?, latest_boot_at=?, latest_heartbeat_at=?, updated_at=?
            WHERE runtime_id='relay'
            """,
            ("running", instance_id, mode, now_iso, now_iso, now_iso),
        )
        conn.commit()
    finally:
        conn.close()
    event = {
        "event_id": f"evt:relay_runtime_started:{instance_id}:{now_iso}",
        "event_type": "relay_runtime_started",
        "event_version": 1,
        "created_at": now_iso,
        "producer": "Relay_Runtime",
        "project_key": project_key,
        "lane_id": None,
        "issue_number": None,
        "head_sha": None,
        "causal_event_id": None,
        "causal_action_id": None,
        "dedupe_key": f"relay_runtime_started:{instance_id}:{mode}",
        "payload": {
            "instance_id": instance_id,
            "mode": mode,
            "checkpoint_path": None,
        },
    }
    append_relay_event(event_log_path=paths["event_log_path"], event=event)
    return {
        "runtime_status": "running",
        "instance_id": instance_id,
        "mode": mode,
        "db_path": str(paths["db_path"]),
        "event_log_path": str(paths["event_log_path"]),
    }


def get_runtime_status(*, workflow_root: Path) -> dict[str, Any]:
    paths = _runtime_paths(workflow_root)
    conn = _connect(paths["db_path"])
    try:
        runtime = conn.execute(
            """
            SELECT runtime_id, project_key, schema_version, runtime_status,
                   active_orchestrator_instance_id, current_mode,
                   latest_boot_at, latest_heartbeat_at, updated_at
            FROM relay_runtime
            WHERE runtime_id='relay'
            """
        ).fetchone()
        lane_count = conn.execute("SELECT COUNT(*) FROM lanes").fetchone()[0]
    finally:
        conn.close()
    if not runtime:
        return {
            "runtime_id": "relay",
            "runtime_status": "missing",
            "current_mode": None,
            "active_orchestrator_instance_id": None,
            "lane_count": 0,
            "db_path": str(paths["db_path"]),
            "event_log_path": str(paths["event_log_path"]),
        }
    return {
        "runtime_id": runtime[0],
        "project_key": runtime[1],
        "schema_version": runtime[2],
        "runtime_status": runtime[3],
        "active_orchestrator_instance_id": runtime[4],
        "current_mode": runtime[5],
        "latest_boot_at": runtime[6],
        "latest_heartbeat_at": runtime[7],
        "updated_at": runtime[8],
        "lane_count": lane_count,
        "db_path": str(paths["db_path"]),
        "event_log_path": str(paths["event_log_path"]),
    }


def _lane_id(issue_number: int) -> str:
    return f"lane:{issue_number}"


def _actor_id(lane_id: str, role_slug: str) -> str:
    return f"actor:{lane_id}:{role_slug}"


def _merge_state_from_status(legacy_status: dict[str, Any]) -> str:
    if legacy_status.get("derivedMergeBlocked"):
        return "blocked"
    if legacy_status.get("openPr"):
        return "ready"
    return "not_ready"


def _required_review_flags(legacy_status: dict[str, Any]) -> tuple[int, int]:
    reviews = legacy_status.get("reviews") or {}
    internal_required = 1 if (reviews.get("claudeCode") or {}).get("required") else 0
    external_required = 1 if (reviews.get("codexCloud") or {}).get("required") else 0
    return internal_required, external_required


def ingest_legacy_status(*, workflow_root: Path, legacy_status: dict[str, Any], now_iso: str | None = None) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    active_lane = legacy_status.get("activeLane") or {}
    issue_number = active_lane.get("number")
    if not issue_number:
        return {"ingested": False, "reason": "no-active-lane"}
    lane_id = _lane_id(issue_number)
    impl = legacy_status.get("implementation") or {}
    lane_state = (impl.get("laneState") or {}).get("implementation") or {}
    reviews = legacy_status.get("reviews") or {}
    internal_required, external_required = _required_review_flags(legacy_status)
    repo_path = legacy_status.get("repo") or ""
    merge_blockers_json = json.dumps(legacy_status.get("derivedMergeBlockers") or [])
    repair_brief_json = json.dumps((legacy_status.get("ledger") or {}).get("repairBrief"))
    actor_id = _actor_id(lane_id, "coder")
    effort_label = next((label.get("name") for label in active_lane.get("labels") or [] if str(label.get("name", "")).startswith("effort:")), None)
    legacy_attention_required = bool((legacy_status.get("ledger") or {}).get("workflowState") == "operator_attention_required")
    legacy_attention_reason = None
    if legacy_attention_required:
        next_action = legacy_status.get("nextAction") or {}
        stale_lane_reasons = legacy_status.get("staleLaneReasons") or []
        legacy_attention_reason = (
            next_action.get("reason")
            or legacy_status.get("activeLaneError")
            or (stale_lane_reasons[0] if stale_lane_reasons else None)
            or "legacy-operator-attention"
        )
    paths = _runtime_paths(workflow_root)
    conn = _connect(paths["db_path"])
    try:
        conn.execute(
            """
            INSERT INTO lanes (
              lane_id, issue_number, issue_url, issue_title, repo_path, worktree_path, branch_name,
              priority_hint, effort_label, actor_backend, lane_status, workflow_state, review_state,
              merge_state, current_head_sha, last_published_head_sha, active_pr_number, active_pr_url,
              active_pr_head_sha, required_internal_review, required_external_review, merge_blocked,
              merge_blockers_json, repair_brief_json, active_actor_id, current_action_id,
              last_completed_action_id, last_meaningful_progress_at, last_meaningful_progress_kind,
              operator_attention_required, operator_attention_reason, archived_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(lane_id) DO UPDATE SET
              issue_url=excluded.issue_url,
              issue_title=excluded.issue_title,
              repo_path=excluded.repo_path,
              worktree_path=excluded.worktree_path,
              branch_name=excluded.branch_name,
              priority_hint=excluded.priority_hint,
              effort_label=excluded.effort_label,
              actor_backend=excluded.actor_backend,
              lane_status=excluded.lane_status,
              workflow_state=excluded.workflow_state,
              review_state=excluded.review_state,
              merge_state=excluded.merge_state,
              current_head_sha=excluded.current_head_sha,
              last_published_head_sha=excluded.last_published_head_sha,
              active_pr_number=excluded.active_pr_number,
              active_pr_url=excluded.active_pr_url,
              active_pr_head_sha=excluded.active_pr_head_sha,
              required_internal_review=excluded.required_internal_review,
              required_external_review=excluded.required_external_review,
              merge_blocked=excluded.merge_blocked,
              merge_blockers_json=excluded.merge_blockers_json,
              repair_brief_json=excluded.repair_brief_json,
              active_actor_id=excluded.active_actor_id,
              last_meaningful_progress_at=excluded.last_meaningful_progress_at,
              last_meaningful_progress_kind=excluded.last_meaningful_progress_kind,
              operator_attention_required=CASE
                WHEN lanes.operator_attention_reason LIKE 'active-action-failed:%' AND excluded.operator_attention_required=0
                THEN lanes.operator_attention_required
                ELSE excluded.operator_attention_required
              END,
              operator_attention_reason=CASE
                WHEN lanes.operator_attention_reason LIKE 'active-action-failed:%' AND excluded.operator_attention_required=0
                THEN lanes.operator_attention_reason
                WHEN excluded.operator_attention_required=1
                THEN excluded.operator_attention_reason
                ELSE NULL
              END,
              updated_at=excluded.updated_at
            """,
            (
                lane_id, issue_number, active_lane.get("url") or "", active_lane.get("title") or "",
                repo_path, impl.get("worktree"), impl.get("branch"), None, effort_label,
                "acpx-codex", "active", (legacy_status.get("ledger") or {}).get("workflowState") or "unknown",
                legacy_status.get("derivedReviewLoopState") or (legacy_status.get("ledger") or {}).get("reviewState") or "unknown",
                _merge_state_from_status(legacy_status), impl.get("localHeadSha"), ((impl.get("laneState") or {}).get("pr") or {}).get("lastPublishedHeadSha"),
                (legacy_status.get("openPr") or {}).get("number"), (legacy_status.get("openPr") or {}).get("url"),
                (legacy_status.get("openPr") or {}).get("headRefOid"), internal_required, external_required,
                1 if legacy_status.get("derivedMergeBlocked") else 0, merge_blockers_json, repair_brief_json, actor_id,
                lane_state.get("lastMeaningfulProgressAt") or now_iso,
                lane_state.get("lastMeaningfulProgressKind") or ((legacy_status.get("ledger") or {}).get("workflowState") or "unknown"),
                1 if legacy_attention_required else 0,
                legacy_attention_reason,
                now_iso, now_iso,
            ),
        )
        conn.execute(
            """
            INSERT INTO lane_actors (
              actor_id, lane_id, actor_role, actor_name, backend_type, backend_identity,
              backend_session_id, backend_thread_id, backend_record_id, model_name,
              runtime_status, session_action_recommendation, last_seen_at, last_used_at,
              can_continue, can_nudge, restart_count, failure_count, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(actor_id) DO UPDATE SET
              backend_identity=excluded.backend_identity,
              backend_session_id=excluded.backend_session_id,
              model_name=excluded.model_name,
              runtime_status=excluded.runtime_status,
              session_action_recommendation=excluded.session_action_recommendation,
              last_seen_at=excluded.last_seen_at,
              last_used_at=excluded.last_used_at,
              can_continue=excluded.can_continue,
              can_nudge=excluded.can_nudge,
              restart_count=excluded.restart_count,
              failure_count=excluded.failure_count,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (
                actor_id, lane_id, "Internal_Coder_Agent", "Internal_Coder_Agent", "acpx-codex",
                impl.get("sessionName"), impl.get("resumeSessionId"), impl.get("codexModel"),
                "healthy" if (impl.get("activeSessionHealth") or {}).get("healthy") else "unhealthy",
                (impl.get("sessionActionRecommendation") or {}).get("action"), now_iso,
                (impl.get("activeSessionHealth") or {}).get("lastUsedAt") or now_iso,
                1 if (impl.get("sessionActionRecommendation") or {}).get("action") == "continue-session" else 0,
                1 if (impl.get("sessionActionRecommendation") or {}).get("action") == "poke-session" else 0,
                ((impl.get("laneState") or {}).get("restart") or {}).get("count") or 0,
                (((impl.get("laneState") or {}).get("failure") or {}).get("retryCount") or 0),
                json.dumps({
                    "source": "legacy-status-ingest",
                    "sessionControl": (impl.get("laneState") or {}).get("sessionControl") or {},
                }, sort_keys=True), now_iso, now_iso,
            ),
        )
        for reviewer_scope, legacy_key, reviewer_role in (
            ("internal", "claudeCode", "Internal_Reviewer_Agent"),
            ("external", "codexCloud", "External_Reviewer_Agent"),
        ):
            review = reviews.get(legacy_key) or {}
            if not review:
                continue
            review_id = f"review:{lane_id}:{reviewer_scope}"
            conn.execute(
                """
                INSERT INTO lane_reviews (
                  review_id, lane_id, reviewer_scope, reviewer_role, reviewer_name, backend_type,
                  model_name, status, verdict, requested_head_sha, reviewed_head_sha, review_scope,
                  open_finding_count, blockers_json, concerns_json, suggestions_json, summary_text,
                  requested_at, completed_at, source_event_id, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                  reviewer_name=excluded.reviewer_name,
                  backend_type=excluded.backend_type,
                  model_name=excluded.model_name,
                  status=excluded.status,
                  verdict=excluded.verdict,
                  requested_head_sha=excluded.requested_head_sha,
                  reviewed_head_sha=excluded.reviewed_head_sha,
                  review_scope=excluded.review_scope,
                  open_finding_count=excluded.open_finding_count,
                  blockers_json=excluded.blockers_json,
                  concerns_json=excluded.concerns_json,
                  suggestions_json=excluded.suggestions_json,
                  summary_text=excluded.summary_text,
                  requested_at=excluded.requested_at,
                  completed_at=excluded.completed_at,
                  metadata_json=excluded.metadata_json,
                  updated_at=excluded.updated_at
                """,
                (
                    review_id,
                    lane_id,
                    reviewer_scope,
                    reviewer_role,
                    review.get("agentName") or reviewer_role,
                    legacy_key,
                    review.get("model"),
                    review.get("status") or "not_started",
                    review.get("verdict"),
                    review.get("requestedHeadSha"),
                    review.get("reviewedHeadSha"),
                    review.get("reviewScope"),
                    review.get("openFindingCount") or 0,
                    json.dumps(review.get("blockingFindings") or []),
                    json.dumps(review.get("majorConcerns") or []),
                    json.dumps(review.get("minorSuggestions") or []),
                    review.get("summary"),
                    review.get("requestedAt"),
                    review.get("updatedAt") if review.get("status") == "completed" else None,
                    json.dumps({"source": "legacy-status-ingest", "legacyKey": legacy_key}, sort_keys=True),
                    now_iso,
                    now_iso,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    append_relay_event(
        event_log_path=paths["event_log_path"],
        event={
            "event_id": f"evt:lane_promoted:{lane_id}:{now_iso}",
            "event_type": "lane_promoted",
            "event_version": 1,
            "created_at": now_iso,
            "producer": "Legacy_Watchdog_Shadow",
            "project_key": "yoyopod",
            "lane_id": lane_id,
            "issue_number": issue_number,
            "head_sha": impl.get("localHeadSha"),
            "causal_event_id": None,
            "causal_action_id": None,
            "dedupe_key": f"lane_promoted:{lane_id}",
            "payload": {
                "issue_number": issue_number,
                "issue_title": active_lane.get("title"),
                "issue_url": active_lane.get("url"),
                "priority_hint": None,
                "effort_label": effort_label,
            },
        },
    )
    return {"ingested": True, "lane_id": lane_id, "actor_id": actor_id}


def derive_shadow_actions_for_lane(*, lane_row: dict[str, Any], reviews: list[dict[str, Any]], actor_row: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    workflow_state = lane_row.get("workflow_state")
    current_head_sha = lane_row.get("current_head_sha")
    active_pr_number = lane_row.get("active_pr_number")
    active_pr_head_sha = lane_row.get("active_pr_head_sha")
    internal_review = next((r for r in reviews if r.get("reviewer_scope") == "internal"), None)
    external_review = next((r for r in reviews if r.get("reviewer_scope") == "external"), None)
    actor_row = actor_row or {}
    actor_metadata = _parse_json_blob(actor_row.get("metadata_json")) or {}
    session_control = actor_metadata.get("sessionControl") or {}
    repair_brief = _parse_json_blob(lane_row.get("repair_brief_json")) or {}
    has_actionable_repair_brief = bool(
        current_head_sha
        and repair_brief.get("forHeadSha") == current_head_sha
        and (repair_brief.get("mustFix") or repair_brief.get("shouldFix"))
    )
    last_codex_cloud_handoff = session_control.get("lastCodexCloudRepairHandoff") or {}
    repair_handoff_already_sent = bool(
        last_codex_cloud_handoff.get("sessionName") == actor_row.get("backend_identity")
        and last_codex_cloud_handoff.get("headSha") == current_head_sha
        and external_review
        and last_codex_cloud_handoff.get("reviewedAt") == external_review.get("completed_at")
    )
    if (
        workflow_state in {"implementing_local", "implementing"}
        and not active_pr_number
        and actor_row.get("runtime_status") == "healthy"
        and actor_row.get("session_action_recommendation") == "continue-session"
        and current_head_sha
    ):
        return [{
            "action_type": "noop",
            "lane_id": lane_row.get("lane_id"),
            "issue_number": lane_row.get("issue_number"),
            "target_head_sha": current_head_sha,
            "reason": "fresh-session-still-working",
        }]
    if (
        workflow_state in {"implementing_local", "implementing"}
        and not active_pr_number
        and (
            not current_head_sha
            or actor_row.get("runtime_status") != "healthy"
            or actor_row.get("session_action_recommendation") not in {"continue-session", "poke-session"}
        )
    ):
        return [{
            "action_type": "dispatch_implementation_turn",
            "lane_id": lane_row.get("lane_id"),
            "issue_number": lane_row.get("issue_number"),
            "target_head_sha": current_head_sha,
            "reason": "implementation-in-progress",
        }]
    if (
        workflow_state in {"claude_prepublish_findings", "rework_required"}
        and not active_pr_number
        and internal_review
        and internal_review.get("status") == "completed"
        and internal_review.get("verdict") in {"PASS_WITH_FINDINGS", "REWORK"}
        and current_head_sha
        and (
            actor_row.get("runtime_status") != "healthy"
            or actor_row.get("session_action_recommendation") not in {"continue-session", "poke-session"}
        )
    ):
        return [{
            "action_type": "dispatch_implementation_turn",
            "lane_id": lane_row.get("lane_id"),
            "issue_number": lane_row.get("issue_number"),
            "target_head_sha": current_head_sha,
            "reason": "local-review-findings-need-repair",
        }]
    if (
        active_pr_number
        and current_head_sha
        and active_pr_head_sha
        and current_head_sha != active_pr_head_sha
    ):
        return [{
            "action_type": "push_pr_update",
            "lane_id": lane_row.get("lane_id"),
            "issue_number": lane_row.get("issue_number"),
            "target_head_sha": current_head_sha,
            "reason": "local-repair-head-ahead-of-published-pr",
        }]
    if (
        workflow_state == "awaiting_claude_prepublish"
        and not active_pr_number
        and lane_row.get("required_internal_review")
        and internal_review
        and internal_review.get("status") == "pending"
        and current_head_sha
    ):
        return [{
            "action_type": "request_internal_review",
            "lane_id": lane_row.get("lane_id"),
            "issue_number": lane_row.get("issue_number"),
            "target_head_sha": current_head_sha,
            "reason": "internal-review-pending",
        }]
    if (
        active_pr_number
        and workflow_state in {"findings_open", "rework_required", "under_review"}
        and external_review
        and external_review.get("status") == "completed"
        and external_review.get("verdict") in {"PASS_WITH_FINDINGS", "REWORK"}
        and current_head_sha
        and has_actionable_repair_brief
        and (
            actor_row.get("runtime_status") not in {None, "", "healthy"}
            or actor_row.get("session_action_recommendation") not in {None, "", "continue-session", "poke-session"}
        )
    ):
        return [{
            "action_type": "dispatch_implementation_turn",
            "lane_id": lane_row.get("lane_id"),
            "issue_number": lane_row.get("issue_number"),
            "target_head_sha": current_head_sha,
            "reason": "external-review-findings-open",
        }]
    if (
        active_pr_number
        and workflow_state in {"findings_open", "rework_required", "under_review"}
        and external_review
        and external_review.get("status") == "completed"
        and external_review.get("verdict") in {"PASS_WITH_FINDINGS", "REWORK"}
        and current_head_sha
        and has_actionable_repair_brief
        and not repair_handoff_already_sent
    ):
        return [{
            "action_type": "dispatch_repair_handoff",
            "lane_id": lane_row.get("lane_id"),
            "issue_number": lane_row.get("issue_number"),
            "target_head_sha": current_head_sha,
            "reason": "external-review-findings-open",
        }]
    if workflow_state == "ready_to_publish" and not active_pr_number and current_head_sha:
        return [{
            "action_type": "publish_pr",
            "lane_id": lane_row.get("lane_id"),
            "issue_number": lane_row.get("issue_number"),
            "target_head_sha": current_head_sha,
            "reason": "local-head-cleared-for-publish",
        }]
    if active_pr_number and lane_row.get("review_state") == "clean" and lane_row.get("merge_state") == "ready" and not lane_row.get("merge_blocked"):
        return [{
            "action_type": "merge_pr",
            "lane_id": lane_row.get("lane_id"),
            "issue_number": lane_row.get("issue_number"),
            "target_head_sha": active_pr_head_sha or current_head_sha,
            "reason": "published-pr-approved",
        }]
    return []


def persist_shadow_actions(*, workflow_root: Path, lane_id: str, now_iso: str | None = None) -> list[dict[str, Any]]:
    now_iso = now_iso or _now_iso()
    paths = _runtime_paths(workflow_root)
    conn = _connect(paths["db_path"])
    try:
        conn.row_factory = sqlite3.Row
        lane_row = conn.execute("SELECT * FROM lanes WHERE lane_id=?", (lane_id,)).fetchone()
        if not lane_row:
            return []
        lane = dict(lane_row)
        actor = conn.execute("SELECT * FROM lane_actors WHERE actor_id=?", (lane.get("active_actor_id"),)).fetchone()
        actor_dict = dict(actor) if actor else {}
        reviews = [dict(r) for r in conn.execute("SELECT * FROM lane_reviews WHERE lane_id=?", (lane_id,)).fetchall()]
        actions = derive_shadow_actions_for_lane(lane_row=lane, reviews=reviews, actor_row=actor_dict)
        persisted = []
        events_to_emit = []
        for idx, action in enumerate(actions, start=1):
            action_id = f"act:{lane_id}:{action['action_type']}:{now_iso}:{idx}"
            idempotency_key = f"shadow:{action['action_type']}:{lane_id}:{action.get('target_head_sha') or 'none'}"
            conn.execute(
                """
                INSERT INTO lane_actions (
                  action_id, lane_id, action_type, action_reason, action_mode, requested_by,
                  target_actor_role, target_actor_id, target_head_sha, idempotency_key, status,
                  requested_at, dispatched_at, completed_at, failed_at, result_code, result_summary,
                  request_payload_json, result_payload_json, error_payload_json, retry_count,
                  superseded_by_action_id, causal_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL, NULL, 0, NULL, NULL)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    action_id,
                    lane_id,
                    action["action_type"],
                    action.get("reason"),
                    "shadow",
                    "Workflow_Orchestrator",
                    "Internal_Coder_Agent" if action["action_type"] in {"noop", "request_internal_review", "publish_pr", "merge_pr"} else None,
                    lane.get("active_actor_id"),
                    action.get("target_head_sha"),
                    idempotency_key,
                    "requested",
                    now_iso,
                    json.dumps(action, sort_keys=True),
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                persisted.append({**action, "action_id": action_id})
                events_to_emit.append({
                    "event_id": f"evt:shadow_action_requested:{lane_id}:{action['action_type']}:{now_iso}:{idx}",
                    "event_type": "shadow_action_requested",
                    "event_version": 1,
                    "created_at": now_iso,
                    "producer": "Workflow_Orchestrator",
                    "project_key": "yoyopod",
                    "lane_id": lane_id,
                    "issue_number": lane.get("issue_number"),
                    "head_sha": action.get("target_head_sha"),
                    "causal_event_id": None,
                    "causal_action_id": action_id,
                    "dedupe_key": f"shadow_action_requested:{idempotency_key}",
                    "payload": {
                        "action_type": action["action_type"],
                        "reason": action.get("reason"),
                        "mode": "shadow",
                    },
                })
        conn.commit()
    finally:
        conn.close()
    for event in events_to_emit:
        append_relay_event(event_log_path=paths["event_log_path"], event=event)
    return persisted


def request_active_actions_for_lane(*, workflow_root: Path, lane_id: str, now_iso: str | None = None) -> list[dict[str, Any]]:
    now_iso = now_iso or _now_iso()
    paths = _runtime_paths(workflow_root)
    conn = _connect(paths["db_path"])
    try:
        conn.row_factory = sqlite3.Row
        existing_requested = [
            dict(row)
            for row in conn.execute(
                """
                SELECT action_id, lane_id, action_type, action_reason AS reason, target_head_sha, retry_count, recovery_attempt_count, requested_at
                FROM lane_actions
                WHERE lane_id=? AND action_mode='active' AND status='requested'
                ORDER BY requested_at ASC
                """,
                (lane_id,),
            ).fetchall()
        ]
        if existing_requested:
            return existing_requested
        lane_row = conn.execute("SELECT * FROM lanes WHERE lane_id=?", (lane_id,)).fetchone()
        if not lane_row:
            return []
        lane = dict(lane_row)
        actor = conn.execute("SELECT * FROM lane_actors WHERE actor_id=?", (lane.get("active_actor_id"),)).fetchone()
        actor_dict = dict(actor) if actor else {}
        reviews = [dict(r) for r in conn.execute("SELECT * FROM lane_reviews WHERE lane_id=?", (lane_id,)).fetchall()]
        internal_review = next((review for review in reviews if review.get("reviewer_scope") == "internal"), {})
        actions = derive_shadow_actions_for_lane(lane_row=lane, reviews=reviews, actor_row=actor_dict)
        persisted = []
        events_to_emit = []
        active_action_types = _active_action_types() - {"restart_actor_session"}
        for idx, action in enumerate(actions, start=1):
            if action["action_type"] not in active_action_types:
                continue
            base_idempotency_key = f"active:{action['action_type']}:{lane_id}:{action.get('target_head_sha') or 'none'}"
            failed_predecessor = conn.execute(
                """
                SELECT action_id, retry_count, recovery_attempt_count
                FROM lane_actions
                WHERE lane_id=?
                  AND action_mode='active'
                  AND action_type=?
                  AND COALESCE(target_head_sha, '') = COALESCE(?, '')
                  AND status='failed'
                ORDER BY retry_count DESC, requested_at DESC
                LIMIT 1
                """,
                (lane_id, action["action_type"], action.get("target_head_sha")),
            ).fetchone()
            retry_count = int((dict(failed_predecessor).get("retry_count") if failed_predecessor else 0) or 0)
            recovery_attempt_count = int((dict(failed_predecessor).get("recovery_attempt_count") if failed_predecessor else 0) or 0)
            idempotency_key = base_idempotency_key
            if failed_predecessor is not None:
                retry_count += 1
                recovery_attempt_count += 1
                idempotency_key = f"{base_idempotency_key}:retry:{retry_count}"
            action_id = f"act:active:{lane_id}:{action['action_type']}:{now_iso}:{idx}"
            target_actor_role = _target_actor_role_for_active_action(action["action_type"])
            target_actor_id = _target_actor_id_for_active_action(action_type=action["action_type"], lane=lane)
            conn.execute(
                """
                INSERT INTO lane_actions (
                  action_id, lane_id, action_type, action_reason, action_mode, requested_by,
                  target_actor_role, target_actor_id, target_head_sha, idempotency_key, status,
                  requested_at, dispatched_at, completed_at, failed_at, result_code, result_summary,
                  request_payload_json, result_payload_json, error_payload_json, retry_count, recovery_attempt_count,
                  superseded_by_action_id, causal_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL, NULL, ?, ?, NULL, NULL)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    action_id,
                    lane_id,
                    action["action_type"],
                    action.get("reason"),
                    "active",
                    "Workflow_Orchestrator",
                    target_actor_role,
                    target_actor_id,
                    action.get("target_head_sha"),
                    idempotency_key,
                    "requested",
                    now_iso,
                    json.dumps(action, sort_keys=True),
                    retry_count,
                    recovery_attempt_count,
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                if failed_predecessor is not None:
                    conn.execute(
                        "UPDATE lane_actions SET superseded_by_action_id=? WHERE action_id=?",
                        (action_id, dict(failed_predecessor)["action_id"]),
                    )
                persisted_action = {
                    **action,
                    "action_id": action_id,
                    "lane_id": lane_id,
                    "target_actor_id": target_actor_id,
                    "target_actor_role": target_actor_role,
                    "action_reason": action.get("reason"),
                    "retry_count": retry_count,
                    "recovery_attempt_count": recovery_attempt_count,
                    "requested_at": now_iso,
                }
                persisted.append(persisted_action)
                events_to_emit.append({
                    "event_id": f"evt:active_action_requested:{lane_id}:{action['action_type']}:{now_iso}:{idx}",
                    "event_type": "active_action_requested",
                    "event_version": 1,
                    "created_at": now_iso,
                    "producer": "Workflow_Orchestrator",
                    "project_key": "yoyopod",
                    "lane_id": lane_id,
                    "issue_number": lane.get("issue_number"),
                    "head_sha": action.get("target_head_sha"),
                    "causal_event_id": None,
                    "causal_action_id": action_id,
                    "dedupe_key": f"active_action_requested:{idempotency_key}",
                    "payload": {
                        "action_type": action["action_type"],
                        "reason": action.get("reason"),
                        "mode": "active",
                        "retry_count": retry_count,
                        "recovery_attempt_count": recovery_attempt_count,
                    },
                })
                events_to_emit.extend(
                    _semantic_request_events_for_action(
                        action=persisted_action,
                        lane=lane,
                        actor=actor_dict,
                        review=internal_review,
                        now_iso=now_iso,
                    )
                )
        conn.commit()
    finally:
        conn.close()
    for event in events_to_emit:
        append_relay_event(event_log_path=paths["event_log_path"], event=event)
    return persisted


def _run_yoyopod_cli_json(*, workflow_root: Path, command: str) -> dict[str, Any]:
    """Spawn the YoYoPod CLI (plugin entrypoint preferred, wrapper fallback) and parse JSON output."""
    argv = yoyopod_cli_argv(workflow_root, command, "--json")
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=workflow_root,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"{command} exited {completed.returncode}"
        )
    return json.loads(completed.stdout)


def _run_legacy_dispatch_implementation_turn(*, workflow_root: Path) -> dict[str, Any]:
    return _run_yoyopod_cli_json(workflow_root=workflow_root, command="dispatch-implementation-turn")


def _run_legacy_push_pr_update(*, workflow_root: Path) -> dict[str, Any]:
    return _run_yoyopod_cli_json(workflow_root=workflow_root, command="push-pr-update")


def _run_legacy_publish_pr(*, workflow_root: Path) -> dict[str, Any]:
    return _run_yoyopod_cli_json(workflow_root=workflow_root, command="publish-ready-pr")


def _run_legacy_request_internal_review(*, workflow_root: Path) -> dict[str, Any]:
    return _run_yoyopod_cli_json(workflow_root=workflow_root, command="dispatch-claude-review")


def _run_legacy_merge_pr(*, workflow_root: Path) -> dict[str, Any]:
    return _run_yoyopod_cli_json(workflow_root=workflow_root, command="merge-and-promote")


def _run_legacy_dispatch_repair_handoff(*, workflow_root: Path) -> dict[str, Any]:
    return _run_yoyopod_cli_json(workflow_root=workflow_root, command="dispatch-repair-handoff")


def _run_legacy_restart_actor_session(*, workflow_root: Path) -> dict[str, Any]:
    return _run_yoyopod_cli_json(workflow_root=workflow_root, command="restart-actor-session")


def _default_active_action_runners(*, workflow_root: Path) -> dict[str, Any]:
    return {
        "dispatch_implementation_turn": lambda: _run_legacy_dispatch_implementation_turn(workflow_root=workflow_root),
        "dispatch_repair_handoff": lambda: _run_legacy_dispatch_repair_handoff(workflow_root=workflow_root),
        "restart_actor_session": lambda: _run_legacy_restart_actor_session(workflow_root=workflow_root),
        "push_pr_update": lambda: _run_legacy_push_pr_update(workflow_root=workflow_root),
        "publish_pr": lambda: _run_legacy_publish_pr(workflow_root=workflow_root),
        "request_internal_review": lambda: _run_legacy_request_internal_review(workflow_root=workflow_root),
        "merge_pr": lambda: _run_legacy_merge_pr(workflow_root=workflow_root),
    }


def _summarize_active_action_result(*, action_type: str, result: dict[str, Any]) -> str:
    if action_type == "dispatch_implementation_turn":
        return "implementation dispatch completed" if result.get("dispatched") else "implementation dispatch returned not-dispatched"
    if action_type == "dispatch_repair_handoff":
        return "repair handoff dispatched" if result.get("dispatched") else "repair handoff returned not-dispatched"
    if action_type == "restart_actor_session":
        return "actor session restart completed" if result.get("dispatched") else "actor session restart returned not-dispatched"
    if action_type == "push_pr_update":
        return "push PR update completed" if result.get("pushed") else "push PR update returned not-pushed"
    if action_type == "publish_pr":
        return "publish PR completed" if result.get("published") else "publish PR returned not-published"
    if action_type == "request_internal_review":
        return "internal review request completed" if result.get("dispatched") else "internal review request returned not-dispatched"
    if action_type == "merge_pr":
        return "merge PR completed" if result.get("merged") else "merge PR returned not-merged"
    return f"{action_type} completed"


def _failure_scope_for_action(action_type: str | None) -> str:
    if action_type in {"dispatch_implementation_turn", "dispatch_repair_handoff", "restart_actor_session"}:
        return "actor"
    if action_type == "request_internal_review":
        return "review"
    if action_type in {"publish_pr", "push_pr_update"}:
        return "publish"
    if action_type == "merge_pr":
        return "merge"
    return "lane"


def _failure_age_seconds(*, detected_at: str | None, now_iso: str | None = None) -> int | None:
    now_epoch = _iso_to_epoch(now_iso or _now_iso())
    detected_epoch = _iso_to_epoch(detected_at)
    if now_epoch is None or detected_epoch is None:
        return None
    return max(0, now_epoch - detected_epoch)



def _recovery_age_seconds(*, recovery_requested_at: str | None, now_iso: str | None = None) -> int | None:
    return _failure_age_seconds(detected_at=recovery_requested_at, now_iso=now_iso)



def _failure_urgency(*, recovery_state: str | None, failure_age_seconds: int | None) -> str:
    if recovery_state in {"operator_attention_required", "recovery_failed", "recovery_stalled"}:
        return "critical"
    if recovery_state == "queued_recovery":
        return "warning"
    if recovery_state in {"resolved", "recovery_completed"}:
        return "info"
    if failure_age_seconds is not None and failure_age_seconds >= STALLED_RECOVERY_AGE_THRESHOLD_SECONDS:
        return "critical"
    return "warning"



def query_recent_failures(*, workflow_root: Path, limit: int = 5, unresolved_only: bool = True, now_iso: str | None = None, lane_id: str | None = None) -> list[dict[str, Any]]:
    paths = _runtime_paths(workflow_root)
    conn = _connect(paths["db_path"])
    conn.row_factory = sqlite3.Row
    try:
        where_clauses = []
        params: list[Any] = []
        if unresolved_only:
            where_clauses.append("f.resolved_at IS NULL")
        if lane_id:
            where_clauses.append("f.lane_id=?")
            params.append(lane_id)
        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        rows = conn.execute(
            f"""
            SELECT f.failure_id,
                   f.lane_id,
                   l.issue_number,
                   f.related_action_id,
                   f.related_actor_id,
                   f.failure_scope,
                   f.failure_class,
                   f.severity,
                   f.detected_at,
                   f.analyst_status,
                   f.analyst_recommended_action,
                   f.analyst_confidence,
                   f.analyst_summary,
                   f.escalated,
                   f.resolved_at,
                   f.resolution_action_id,
                   f.evidence_json,
                   f.metadata_json,
                   failed_action.superseded_by_action_id AS recovery_action_id,
                   recovery_action.action_type AS recovery_action_type,
                   recovery_action.status AS recovery_action_status,
                   recovery_action.requested_at AS recovery_requested_at
            FROM failures f
            LEFT JOIN lanes l ON l.lane_id = f.lane_id
            LEFT JOIN lane_actions failed_action ON failed_action.action_id = f.related_action_id
            LEFT JOIN lane_actions recovery_action ON recovery_action.action_id = failed_action.superseded_by_action_id
            {where}
            ORDER BY f.detected_at DESC
            LIMIT ?
            """,
            (tuple(params) + (limit,)),
        ).fetchall()
    finally:
        conn.close()
    failures = []
    for row in rows:
        failure = dict(row)
        evidence = _parse_json_blob(failure.pop("evidence_json", None)) or {}
        metadata = _parse_json_blob(failure.pop("metadata_json", None)) or {}
        failure["evidence"] = evidence
        failure["metadata"] = metadata
        failure["root_cause"] = metadata.get("root_cause")
        failure["evidence_refs"] = metadata.get("evidence_refs") or []
        recovery_age_seconds = _recovery_age_seconds(
            recovery_requested_at=failure.get("recovery_requested_at"),
            now_iso=now_iso,
        )
        if failure.get("resolved_at"):
            recovery_state = "resolved"
        elif failure.get("escalated"):
            recovery_state = "operator_attention_required"
        elif (
            failure.get("recovery_action_id")
            and failure.get("recovery_action_status") in {"requested", "dispatched"}
            and recovery_age_seconds is not None
            and recovery_age_seconds >= STALLED_RECOVERY_AGE_THRESHOLD_SECONDS
        ):
            recovery_state = "recovery_stalled"
        elif failure.get("recovery_action_id") and failure.get("recovery_action_status") in {"requested", "dispatched"}:
            recovery_state = "queued_recovery"
        elif failure.get("recovery_action_id") and failure.get("recovery_action_status") == "completed":
            recovery_state = "recovery_completed"
        elif failure.get("recovery_action_id") and failure.get("recovery_action_status") == "failed":
            recovery_state = "recovery_failed"
        else:
            recovery_state = "unresolved"
        failure_age_seconds = _failure_age_seconds(detected_at=failure.get("detected_at"), now_iso=now_iso)
        failure["recovery_state"] = recovery_state
        failure["failure_age_seconds"] = failure_age_seconds
        failure["recovery_age_seconds"] = recovery_age_seconds
        failure["urgency"] = _failure_urgency(
            recovery_state=recovery_state,
            failure_age_seconds=failure_age_seconds,
        )
        failures.append(failure)
    return failures



def query_stuck_dispatched_actions(*, workflow_root: Path, lane_id: str | None = None, now_iso: str | None = None, timeout_seconds: int = DISPATCHED_ACTION_TIMEOUT_SECONDS, limit: int = 25) -> list[dict[str, Any]]:
    now_iso = now_iso or _now_iso()
    cutoff_epoch = max(0, (_iso_to_epoch(now_iso) or int(time.time())) - timeout_seconds)
    cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff_epoch))
    paths = _runtime_paths(workflow_root)
    conn = _connect(paths["db_path"])
    conn.row_factory = sqlite3.Row
    try:
        where_clauses = ["status='dispatched'", "dispatched_at IS NOT NULL", "dispatched_at <= ?"]
        params: list[Any] = [cutoff_iso]
        if lane_id:
            where_clauses.append("lane_id=?")
            params.append(lane_id)
        rows = conn.execute(
            f"""
            SELECT action_id,
                   lane_id,
                   action_type,
                   action_reason,
                   action_mode,
                   requested_by,
                   target_actor_role,
                   target_actor_id,
                   target_head_sha,
                   status,
                   requested_at,
                   dispatched_at,
                   retry_count,
                   recovery_attempt_count,
                   idempotency_key,
                   superseded_by_action_id,
                   causal_event_id
            FROM lane_actions
            WHERE {' AND '.join(where_clauses)}
            ORDER BY dispatched_at ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()
    stuck_actions: list[dict[str, Any]] = []
    for row in rows:
        stuck_action = dict(row)
        stuck_action["dispatched_age_seconds"] = _failure_age_seconds(detected_at=stuck_action.get("dispatched_at"), now_iso=now_iso)
        stuck_action["timeout_seconds"] = timeout_seconds
        stuck_actions.append(stuck_action)
    return stuck_actions



def reap_stuck_dispatched_actions(*, workflow_root: Path, lane_id: str, now_iso: str | None = None, timeout_seconds: int = DISPATCHED_ACTION_TIMEOUT_SECONDS) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    paths = _runtime_paths(workflow_root)
    stuck_actions = query_stuck_dispatched_actions(
        workflow_root=workflow_root,
        lane_id=lane_id,
        now_iso=now_iso,
        timeout_seconds=timeout_seconds,
    )
    if not stuck_actions:
        return {"checked": 0, "reaped": 0, "failures": [], "recovery_actions": []}

    conn = _connect(paths["db_path"])
    conn.row_factory = sqlite3.Row
    events_to_emit: list[dict[str, Any]] = []
    reaped_failures: list[dict[str, Any]] = []
    recovery_actions: list[dict[str, Any]] = []
    try:
        for stuck_action in stuck_actions:
            action_row = conn.execute("SELECT * FROM lane_actions WHERE action_id=?", (stuck_action["action_id"],)).fetchone()
            if not action_row:
                continue
            action = dict(action_row)
            if action.get("status") != "dispatched":
                continue
            failure_id = f"failure:{action['action_id']}"
            failure_scope = _failure_scope_for_action(action.get("action_type"))
            failure_summary = (
                f"dispatcher lost after {timeout_seconds} seconds waiting for {action.get('action_type')} to complete"
            )
            recovery = _deterministic_recovery_for_failure(action) or {
                "analyst_status": "completed",
                "recommended_action": "mark_operator_attention",
                "confidence": 0.0,
                "escalated": 1,
                "queue_recovery_action": None,
                "summary": failure_summary,
                "failure_class": "dispatcher_lost",
            }
            recovery_metadata = {
                **(recovery.get("metadata") or {}),
                "source": "dispatch_reaper",
                "timeout_seconds": timeout_seconds,
                "dispatched_at": action.get("dispatched_at"),
                "dispatched_age_seconds": stuck_action.get("dispatched_age_seconds"),
            }
            recovery = {
                **recovery,
                "failure_class": "dispatcher_lost",
                "metadata": recovery_metadata,
            }
            evidence = {
                "action_type": action.get("action_type"),
                "error": "dispatcher lost",
                "timeout_seconds": timeout_seconds,
                "dispatched_at": action.get("dispatched_at"),
                "dispatched_age_seconds": stuck_action.get("dispatched_age_seconds"),
                "target_head_sha": action.get("target_head_sha"),
            }
            conn.execute(
                """
                UPDATE lane_actions
                SET status='failed', failed_at=?, result_code=?, result_summary=?, error_payload_json=?
                WHERE action_id=?
                """,
                (now_iso, "timeout", failure_summary, json.dumps(evidence, sort_keys=True), action["action_id"]),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO failures (
                  failure_id, lane_id, related_action_id, related_actor_id, failure_scope,
                  failure_class, severity, detected_at, evidence_json, analyst_status,
                  analyst_recommended_action, analyst_confidence, analyst_summary,
                  escalated, resolved_at, resolution_action_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    failure_id,
                    action.get("lane_id"),
                    action["action_id"],
                    action.get("target_actor_id"),
                    failure_scope,
                    "dispatcher_lost",
                    "error",
                    now_iso,
                    json.dumps(evidence, sort_keys=True),
                    recovery.get("analyst_status"),
                    recovery.get("recommended_action"),
                    recovery.get("confidence"),
                    recovery.get("summary") or failure_summary,
                    recovery.get("escalated"),
                    json.dumps(recovery.get("metadata") or {"source": "dispatch_reaper"}, sort_keys=True),
                ),
            )
            conn.execute(
                """
                UPDATE relay_runtime
                SET latest_error_at=?, latest_error_summary=?, updated_at=?
                WHERE runtime_id='relay'
                """,
                (now_iso, failure_summary, now_iso),
            )
            reaped_record = {
                "failure_id": failure_id,
                "action_id": action["action_id"],
                "lane_id": action.get("lane_id"),
                "action_type": action.get("action_type"),
                "failure_class": "dispatcher_lost",
                "failure_summary": failure_summary,
                "timeout_seconds": timeout_seconds,
            }
            if recovery.get("queue_recovery_action"):
                recovery_action = _queue_recovery_action(
                    conn=conn,
                    action=action,
                    now_iso=now_iso,
                    recovery_action_type=recovery["queue_recovery_action"],
                )
                recovery_actions.append(recovery_action)
                reaped_record["recovery_action"] = recovery_action
                events_to_emit.append(
                    {
                        "event_id": f"evt:recovery_requested:dispatch-timeout:{failure_id}:{now_iso}",
                        "event_type": "recovery_requested",
                        "event_version": 1,
                        "created_at": now_iso,
                        "producer": "Workflow_Orchestrator",
                        "project_key": "yoyopod",
                        "lane_id": action.get("lane_id"),
                        "issue_number": None,
                        "head_sha": action.get("target_head_sha"),
                        "causal_event_id": None,
                        "causal_action_id": action["action_id"],
                        "dedupe_key": f"recovery_requested:dispatch-timeout:{failure_id}",
                        "payload": {
                            "failure_id": failure_id,
                            "recovery_action_type": recovery_action["action_type"],
                            "action_id": recovery_action["action_id"],
                            "reason": recovery_action["action_reason"],
                        },
                    }
                )
            else:
                attention_reason = f"dispatcher_lost:{action.get('action_type')}"
                conn.execute(
                    """
                    UPDATE lanes
                    SET operator_attention_required=1,
                        operator_attention_reason=?,
                        updated_at=?
                    WHERE lane_id=?
                    """,
                    (attention_reason, now_iso, action.get("lane_id")),
                )
                events_to_emit.append(
                    {
                        "event_id": f"evt:operator_attention_required:dispatch-timeout:{failure_id}:{now_iso}",
                        "event_type": "operator_attention_required",
                        "event_version": 1,
                        "created_at": now_iso,
                        "producer": "Workflow_Orchestrator",
                        "project_key": "yoyopod",
                        "lane_id": action.get("lane_id"),
                        "issue_number": None,
                        "head_sha": action.get("target_head_sha"),
                        "causal_event_id": None,
                        "causal_action_id": action["action_id"],
                        "dedupe_key": f"operator_attention_required:dispatch-timeout:{failure_id}",
                        "payload": {
                            "reason": attention_reason,
                            "failure_id": failure_id,
                            "summary": failure_summary,
                        },
                    }
                )
            events_to_emit.append(
                {
                    "event_id": f"evt:active_action_failed:dispatch-timeout:{failure_id}:{now_iso}",
                    "event_type": "active_action_failed",
                    "event_version": 1,
                    "created_at": now_iso,
                    "producer": "Workflow_Orchestrator",
                    "project_key": "yoyopod",
                    "lane_id": action.get("lane_id"),
                    "issue_number": None,
                    "head_sha": action.get("target_head_sha"),
                    "causal_event_id": None,
                    "causal_action_id": action["action_id"],
                    "dedupe_key": f"active_action_failed:dispatch-timeout:{failure_id}",
                    "payload": {
                        "action_id": action["action_id"],
                        "action_type": action.get("action_type"),
                        "failure_class": "dispatcher_lost",
                        "reason": failure_summary,
                        "timeout_seconds": timeout_seconds,
                    },
                }
            )
            reaped_failures.append(reaped_record)
        conn.commit()
    finally:
        conn.close()
    for event in events_to_emit:
        append_relay_event(event_log_path=paths["event_log_path"], event=event)
    return {
        "checked": len(stuck_actions),
        "reaped": len(reaped_failures),
        "failures": reaped_failures,
        "recovery_actions": recovery_actions,
    }



def _active_action_types() -> set[str]:
    return {
        "dispatch_implementation_turn",
        "dispatch_repair_handoff",
        "restart_actor_session",
        "push_pr_update",
        "publish_pr",
        "request_internal_review",
        "merge_pr",
    }



def _target_actor_role_for_active_action(action_type: str | None) -> str | None:
    if action_type == "request_internal_review":
        return "Internal_Reviewer_Agent"
    if action_type in {"publish_pr", "push_pr_update", "merge_pr"}:
        return "Workflow_Orchestrator"
    if action_type in {"dispatch_implementation_turn", "dispatch_repair_handoff", "restart_actor_session"}:
        return "Internal_Coder_Agent"
    return None



def _target_actor_id_for_active_action(*, action_type: str | None, lane: dict[str, Any] | None = None, action: dict[str, Any] | None = None) -> str | None:
    role = _target_actor_role_for_active_action(action_type)
    if role != "Internal_Coder_Agent":
        return None
    if action and action.get("target_actor_id"):
        return action.get("target_actor_id")
    return (lane or {}).get("active_actor_id")


def _make_relay_event(
    *,
    event_type: str,
    now_iso: str,
    lane_id: str | None,
    issue_number: int | None,
    head_sha: str | None,
    dedupe_key: str,
    payload: dict[str, Any],
    causal_action_id: str | None = None,
    causal_event_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": f"evt:{event_type}:{lane_id or 'global'}:{now_iso}",
        "event_type": event_type,
        "event_version": 1,
        "created_at": now_iso,
        "producer": "Workflow_Orchestrator",
        "project_key": "yoyopod",
        "lane_id": lane_id,
        "issue_number": issue_number,
        "head_sha": head_sha,
        "causal_event_id": causal_event_id,
        "causal_action_id": causal_action_id,
        "dedupe_key": dedupe_key,
        "payload": payload,
    }


def _lane_promoted_payload(*, lane: dict[str, Any] | None, issue_number: int | None = None) -> dict[str, Any]:
    lane = lane or {}
    return {
        "issue_number": issue_number if issue_number is not None else lane.get("issue_number"),
        "issue_title": lane.get("issue_title"),
        "issue_url": lane.get("issue_url"),
        "priority_hint": lane.get("priority_hint"),
        "effort_label": lane.get("effort_label"),
    }


def _dispatch_mode_for_action(*, action_type: str | None, lane: dict[str, Any] | None) -> str:
    lane = lane or {}
    if action_type == "dispatch_repair_handoff":
        return "postpublish_repair" if lane.get("active_pr_number") else "repair"
    if action_type == "restart_actor_session":
        if lane.get("active_pr_number"):
            return "postpublish_repair"
        if lane.get("review_state") in {"rework_required", "findings_open"}:
            return "repair"
    return "implementation"


def _semantic_request_events_for_action(
    *,
    action: dict[str, Any],
    lane: dict[str, Any] | None,
    actor: dict[str, Any] | None,
    review: dict[str, Any] | None,
    now_iso: str,
) -> list[dict[str, Any]]:
    lane = lane or {}
    actor = actor or {}
    review = review or {}
    action_type = action.get("action_type")
    lane_id = action.get("lane_id")
    issue_number = lane.get("issue_number")
    head_sha = action.get("target_head_sha") or lane.get("current_head_sha")
    causal_action_id = action.get("action_id")
    if action_type in {"dispatch_implementation_turn", "dispatch_repair_handoff", "restart_actor_session"}:
        return [
            _make_relay_event(
                event_type="implementation_requested",
                now_iso=now_iso,
                lane_id=lane_id,
                issue_number=issue_number,
                head_sha=head_sha,
                causal_action_id=causal_action_id,
                dedupe_key=f"implementation_requested:{lane_id}:{head_sha or 'none'}:{action_type}",
                payload={
                    "actor_role": "Internal_Coder_Agent",
                    "actor_id": action.get("target_actor_id") or lane.get("active_actor_id"),
                    "backend_type": actor.get("backend_type") or lane.get("actor_backend") or "acpx-codex",
                    "dispatch_mode": _dispatch_mode_for_action(action_type=action_type, lane=lane),
                    "branch_name": lane.get("branch_name"),
                    "worktree_path": lane.get("worktree_path"),
                    "session_name": actor.get("backend_identity"),
                    "resume_session_id": actor.get("backend_session_id"),
                    "model_name": actor.get("model_name") or lane.get("actor_backend") or "unknown",
                    "reason": action.get("action_reason") or action.get("reason"),
                },
            )
        ]
    if action_type == "request_internal_review":
        return [
            _make_relay_event(
                event_type="internal_review_requested",
                now_iso=now_iso,
                lane_id=lane_id,
                issue_number=issue_number,
                head_sha=head_sha,
                causal_action_id=causal_action_id,
                dedupe_key=f"internal_review_requested:{lane_id}:{head_sha or 'none'}",
                payload={
                    "reviewer_role": "Internal_Reviewer_Agent",
                    "model_name": review.get("model_name") or review.get("backend_type") or "unknown",
                    "requested_head_sha": head_sha,
                    "review_scope": review.get("review_scope") or "local-prepublish",
                },
            )
        ]
    if action_type == "merge_pr":
        return [
            _make_relay_event(
                event_type="merge_requested",
                now_iso=now_iso,
                lane_id=lane_id,
                issue_number=issue_number,
                head_sha=head_sha,
                causal_action_id=causal_action_id,
                dedupe_key=f"merge_requested:{lane_id}:{lane.get('active_pr_number') or 'none'}:{head_sha or 'none'}",
                payload={
                    "pr_number": lane.get("active_pr_number"),
                    "pr_url": lane.get("active_pr_url"),
                    "head_sha": head_sha,
                    "reason": action.get("action_reason") or "published-pr-approved",
                },
            )
        ]
    return []


def _semantic_completion_events_for_action(
    *,
    action: dict[str, Any],
    lane_before: dict[str, Any] | None,
    lane_after: dict[str, Any] | None,
    result: dict[str, Any],
    post_legacy_status: dict[str, Any] | None,
    now_iso: str,
) -> list[dict[str, Any]]:
    lane_before = lane_before or {}
    lane_after = lane_after or lane_before
    action_type = action.get("action_type")
    lane_id = action.get("lane_id")
    issue_number = lane_after.get("issue_number") or lane_before.get("issue_number")
    causal_action_id = action.get("action_id")
    head_sha = action.get("target_head_sha") or lane_after.get("current_head_sha") or lane_before.get("current_head_sha")
    impl_status = (post_legacy_status or {}).get("implementation") or {}
    open_pr_status = (post_legacy_status or {}).get("openPr") or {}
    reviews = (post_legacy_status or {}).get("reviews") or {}
    internal_review = reviews.get("claudeCode") or {}
    events: list[dict[str, Any]] = []
    if action_type in {"dispatch_implementation_turn", "dispatch_repair_handoff", "restart_actor_session"} and result.get("dispatched"):
        local_head_sha = impl_status.get("localHeadSha") or lane_after.get("current_head_sha") or lane_before.get("current_head_sha")
        completion_kind = "repair_ready" if action_type == "dispatch_repair_handoff" else ("head_ready" if local_head_sha else "no_change")
        events.append(
            _make_relay_event(
                event_type="implementation_completed",
                now_iso=now_iso,
                lane_id=lane_id,
                issue_number=issue_number,
                head_sha=local_head_sha,
                causal_action_id=causal_action_id,
                dedupe_key=f"implementation_completed:{lane_id}:{local_head_sha or 'none'}:{action_type}",
                payload={
                    "actor_id": action.get("target_actor_id") or lane_after.get("active_actor_id") or lane_before.get("active_actor_id"),
                    "completion_kind": completion_kind,
                    "local_head_sha": local_head_sha,
                    "commits_ahead": 1 if local_head_sha else 0,
                    "summary": _summarize_active_action_result(action_type=action_type, result=result),
                },
            )
        )
    if action_type == "request_internal_review" and result.get("dispatched"):
        requested_head_sha = internal_review.get("requestedHeadSha") or head_sha
        reviewed_head_sha = internal_review.get("reviewedHeadSha") or requested_head_sha
        verdict = internal_review.get("verdict") or "PASS_CLEAN"
        events.append(
            _make_relay_event(
                event_type="internal_review_completed",
                now_iso=now_iso,
                lane_id=lane_id,
                issue_number=issue_number,
                head_sha=reviewed_head_sha,
                causal_action_id=causal_action_id,
                dedupe_key=f"internal_review_completed:{lane_id}:{reviewed_head_sha or 'none'}",
                payload={
                    "reviewer_role": "Internal_Reviewer_Agent",
                    "model_name": internal_review.get("model") or internal_review.get("model_name") or "unknown",
                    "requested_head_sha": requested_head_sha,
                    "reviewed_head_sha": reviewed_head_sha,
                    "verdict": verdict,
                    "open_finding_count": internal_review.get("openFindingCount") or 0,
                    "summary": internal_review.get("summary") or _summarize_active_action_result(action_type=action_type, result=result),
                },
            )
        )
    if action_type == "publish_pr" and result.get("published"):
        pr_number = open_pr_status.get("number") or result.get("prNumber") or lane_after.get("active_pr_number")
        pr_url = open_pr_status.get("url") or lane_after.get("active_pr_url")
        published_head_sha = open_pr_status.get("headRefOid") or impl_status.get("localHeadSha") or lane_after.get("current_head_sha") or head_sha
        events.append(
            _make_relay_event(
                event_type="pr_published",
                now_iso=now_iso,
                lane_id=lane_id,
                issue_number=issue_number,
                head_sha=published_head_sha,
                causal_action_id=causal_action_id,
                dedupe_key=f"pr_published:{lane_id}:pr:{pr_number or 'none'}:{published_head_sha or 'none'}",
                payload={
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "branch_name": impl_status.get("branch") or lane_after.get("branch_name") or lane_before.get("branch_name"),
                    "published_head_sha": published_head_sha,
                    "draft": bool(open_pr_status.get("isDraft")),
                },
            )
        )
    if action_type == "push_pr_update" and result.get("pushed"):
        pr_number = result.get("prNumber") or lane_after.get("active_pr_number") or lane_before.get("active_pr_number")
        current_head_sha = result.get("headSha") or open_pr_status.get("headRefOid") or lane_after.get("current_head_sha") or head_sha
        events.append(
            _make_relay_event(
                event_type="pr_updated",
                now_iso=now_iso,
                lane_id=lane_id,
                issue_number=issue_number,
                head_sha=current_head_sha,
                causal_action_id=causal_action_id,
                dedupe_key=f"pr_updated:{lane_id}:pr:{pr_number or 'none'}:{current_head_sha or 'none'}",
                payload={
                    "pr_number": pr_number,
                    "pr_url": open_pr_status.get("url") or lane_after.get("active_pr_url") or lane_before.get("active_pr_url"),
                    "previous_head_sha": lane_before.get("active_pr_head_sha"),
                    "current_head_sha": current_head_sha,
                },
            )
        )
    if action_type == "merge_pr" and result.get("merged"):
        merged_pr_number = result.get("mergedPrNumber") or lane_before.get("active_pr_number")
        events.append(
            _make_relay_event(
                event_type="merge_completed",
                now_iso=now_iso,
                lane_id=lane_id,
                issue_number=issue_number,
                head_sha=head_sha,
                causal_action_id=causal_action_id,
                dedupe_key=f"merge_completed:{lane_id}:pr:{merged_pr_number or 'none'}:{head_sha or 'none'}",
                payload={
                    "pr_number": merged_pr_number,
                    "merge_commit_sha": None,
                    "head_sha": head_sha,
                    "closeout_comment_posted": True,
                },
            )
        )
        next_issue_number = result.get("nextIssueNumber")
        if next_issue_number:
            next_lane = {
                "issue_title": None,
                "issue_url": None,
                "priority_hint": None,
                "effort_label": None,
            }
            events.append(
                _make_relay_event(
                    event_type="next_lane_promoted",
                    now_iso=now_iso,
                    lane_id=f"lane:{next_issue_number}",
                    issue_number=next_issue_number,
                    head_sha=None,
                    causal_action_id=causal_action_id,
                    dedupe_key=f"next_lane_promoted:lane:{next_issue_number}",
                    payload=_lane_promoted_payload(lane=next_lane, issue_number=next_issue_number),
                )
            )
    return events


def _semantic_failure_events_for_action(*, action: dict[str, Any] | None, failure_class: str, failure_summary: str, now_iso: str) -> list[dict[str, Any]]:
    action = action or {}
    action_type = action.get("action_type")
    if action_type not in {"dispatch_implementation_turn", "dispatch_repair_handoff", "restart_actor_session"}:
        return []
    return [
        _make_relay_event(
            event_type="implementation_failed",
            now_iso=now_iso,
            lane_id=action.get("lane_id"),
            issue_number=None,
            head_sha=action.get("target_head_sha"),
            causal_action_id=action.get("action_id"),
            dedupe_key=f"implementation_failed:{action.get('lane_id')}:{action.get('action_id')}",
            payload={
                "actor_id": action.get("target_actor_id"),
                "failure_class": failure_class,
                "summary": failure_summary,
                "stderr_excerpt": failure_summary,
                "session_action_recommendation": "restart-session" if action_type != "restart_actor_session" else "no-action",
            },
        )
    ]


def _deterministic_recovery_for_failure(action: dict[str, Any]) -> dict[str, Any] | None:
    action_type = action.get("action_type")
    retry_count = int(action.get("retry_count") or 0)
    if action_type in {"dispatch_implementation_turn", "dispatch_repair_handoff"} and retry_count < 1:
        return {
            "analyst_status": "completed",
            "recommended_action": "retry_same_action",
            "escalated": 0,
            "queue_recovery_action": action_type,
            "summary": f"queued one automatic retry for {action_type}",
            "failure_class": f"{action_type}_failed",
        }
    if action_type in {"dispatch_implementation_turn", "dispatch_repair_handoff"}:
        return {
            "analyst_status": "completed",
            "recommended_action": "restart_actor_session",
            "escalated": 0,
            "queue_recovery_action": "restart_actor_session",
            "summary": f"queued forced actor session restart after repeated {action_type} failure",
            "failure_class": f"{action_type}_failed",
        }
    if action_type == "restart_actor_session":
        return {
            "analyst_status": "completed",
            "recommended_action": "mark_operator_attention",
            "escalated": 1,
            "queue_recovery_action": None,
            "summary": f"no automatic recovery available for {action_type}",
            "failure_class": f"{action_type}_failed",
        }
    return None



def _allowed_analyst_actions_for_failure(action: dict[str, Any]) -> list[str]:
    action_type = action.get("action_type")
    if action_type == "publish_pr":
        return ["retry_same_action", "request_internal_review", "mark_operator_attention"]
    if action_type == "push_pr_update":
        return ["retry_same_action", "dispatch_repair_handoff", "request_internal_review", "mark_operator_attention"]
    if action_type in {"request_internal_review", "merge_pr"}:
        return ["retry_same_action", "mark_operator_attention"]
    return ["mark_operator_attention"]



def _recent_relay_events_for_lane(*, event_log_path: Path, lane_id: str | None, limit: int = 5) -> list[dict[str, Any]]:
    if not event_log_path.exists():
        return []
    selected: list[dict[str, Any]] = []
    for raw_line in reversed(event_log_path.read_text(encoding="utf-8").splitlines()):
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except Exception:
            continue
        if lane_id and entry.get("lane_id") != lane_id:
            continue
        selected.append(
            {
                "event_id": entry.get("event_id"),
                "event_type": entry.get("event_type"),
                "created_at": entry.get("created_at"),
                "causal_action_id": entry.get("causal_action_id"),
            }
        )
        if len(selected) >= limit:
            break
    selected.reverse()
    return selected



def _build_failure_analysis_input(
    *,
    conn: sqlite3.Connection,
    event_log_path: Path,
    action: dict[str, Any],
    failure_id: str,
    failure_scope: str,
    failure_class: str,
    failure_summary: str,
) -> dict[str, Any]:
    lane_row = conn.execute(
        """
        SELECT lane_id, issue_number, workflow_state, review_state, merge_state,
               current_head_sha, active_pr_number, active_pr_head_sha,
               operator_attention_required, operator_attention_reason
        FROM lanes WHERE lane_id=?
        """,
        (action.get("lane_id"),),
    ).fetchone()
    actor_row = None
    if action.get("target_actor_id"):
        actor_row = conn.execute(
            """
            SELECT actor_id, actor_role, runtime_status, session_action_recommendation,
                   restart_count, failure_count, last_seen_at
            FROM lane_actors WHERE actor_id=?
            """,
            (action.get("target_actor_id"),),
        ).fetchone()
    unresolved_failure_count = conn.execute(
        "SELECT COUNT(*) FROM failures WHERE lane_id=? AND resolved_at IS NULL",
        (action.get("lane_id"),),
    ).fetchone()[0]
    request_payload = _parse_json_blob(action.get("request_payload_json")) or {}
    lane_snapshot = dict(lane_row) if lane_row else {}
    actor_health = dict(actor_row) if actor_row else {}
    recent_events = _recent_relay_events_for_lane(event_log_path=event_log_path, lane_id=action.get("lane_id"), limit=5)
    evidence = {
        "action_type": action.get("action_type"),
        "error": failure_summary,
        "target_head_sha": action.get("target_head_sha"),
        "retry_count": int(action.get("retry_count") or 0),
        "unresolved_failure_count": unresolved_failure_count,
        "lane_snapshot": lane_snapshot,
        "actor_health": actor_health,
        "recent_events": recent_events,
        "request_payload": request_payload,
    }
    return {
        "failure_id": failure_id,
        "failure_scope": failure_scope,
        "failure_class": failure_class,
        "failure_summary": failure_summary,
        "allowed_actions": _allowed_analyst_actions_for_failure(action),
        "lane_snapshot": lane_snapshot,
        "actor_health": actor_health,
        "recent_events": recent_events,
        "last_action": {
            "action_id": action.get("action_id"),
            "action_type": action.get("action_type"),
            "action_reason": action.get("action_reason"),
            "target_actor_role": action.get("target_actor_role"),
            "target_actor_id": action.get("target_actor_id"),
            "target_head_sha": action.get("target_head_sha"),
            "retry_count": int(action.get("retry_count") or 0),
            "request_payload": request_payload,
        },
        "evidence": evidence,
    }



def _validate_failure_analyst_output(*, output: Any, allowed_actions: list[str]) -> list[str]:
    if not isinstance(output, dict):
        return ["output-not-object"]
    errors = []
    if not isinstance(output.get("failure_class"), str) or not output.get("failure_class", "").strip():
        errors.append("missing failure_class")
    if not isinstance(output.get("root_cause"), str) or not output.get("root_cause", "").strip():
        errors.append("missing root_cause")
    confidence = output.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        errors.append("invalid confidence")
    recommended_action = output.get("recommended_action")
    if not isinstance(recommended_action, str) or recommended_action not in allowed_actions:
        errors.append("invalid recommended_action")
    if not isinstance(output.get("reasoning_summary"), str) or not output.get("reasoning_summary", "").strip():
        errors.append("missing reasoning_summary")
    evidence_refs = output.get("evidence_refs")
    if not isinstance(evidence_refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
        errors.append("missing evidence_refs")
    if not isinstance(output.get("escalate"), bool):
        errors.append("missing escalate")
    return errors



def _queueable_recovery_action_type(*, action: dict[str, Any], recommended_action: str | None, escalate: bool) -> str | None:
    if escalate or not recommended_action:
        return None
    if recommended_action == "retry_same_action":
        return action.get("action_type")
    if recommended_action in _active_action_types():
        return recommended_action
    return None



def _is_transient_failure_summary(summary: str) -> bool:
    lowered = (summary or "").lower()
    transient_tokens = (
        "timeout",
        "timed out",
        "temporar",
        "connection reset",
        "connection refused",
        "connection aborted",
        "connection error",
        "network",
        "econn",
        "502",
        "503",
        "504",
        "429",
        "rate limit",
        "try again",
    )
    return any(token in lowered for token in transient_tokens)



def _mark_operator_attention_analysis(*, failure_class: str, root_cause: str, reasoning_summary: str, evidence_refs: list[str] | None = None, confidence: float = 0.91) -> dict[str, Any]:
    return {
        "failure_class": failure_class,
        "root_cause": root_cause,
        "confidence": confidence,
        "recommended_action": "mark_operator_attention",
        "reasoning_summary": reasoning_summary,
        "evidence_refs": evidence_refs or ["evidence.error"],
        "escalate": True,
    }



def _default_failure_analyst(analysis_input: dict[str, Any]) -> dict[str, Any]:
    allowed_actions = analysis_input.get("allowed_actions") or []
    allowed = set(allowed_actions)
    failure_summary = analysis_input.get("failure_summary") or ""
    action = analysis_input.get("last_action") or {}
    lane = analysis_input.get("lane_snapshot") or {}
    evidence = analysis_input.get("evidence") or {}
    action_type = action.get("action_type")
    workflow_state = lane.get("workflow_state") or "unknown"
    review_state = lane.get("review_state") or "unknown"
    merge_state = lane.get("merge_state") or "unknown"
    current_head_sha = lane.get("current_head_sha")
    repair_brief = _parse_json_blob(lane.get("repair_brief_json")) or {}

    if action_type == "publish_pr":
        if "retry_same_action" in allowed and _is_transient_failure_summary(failure_summary):
            return {
                "failure_class": "publish_pr_transient_failure",
                "root_cause": "publish path failed with a transient transport or rate-limit style error",
                "confidence": 0.74,
                "recommended_action": "retry_same_action",
                "reasoning_summary": "retry publish once because the failure looks transient",
                "evidence_refs": ["evidence.error"],
                "escalate": False,
            }
        if "request_internal_review" in allowed and current_head_sha:
            return {
                "failure_class": "publish_pr_requires_review",
                "root_cause": "publish failed after the lane reached a locally ready state, so re-validating the current head is safer than blind republish",
                "confidence": 0.82,
                "recommended_action": "request_internal_review",
                "reasoning_summary": "request internal review before another publish attempt",
                "evidence_refs": ["lane_snapshot.workflow_state", "lane_snapshot.current_head_sha", "evidence.error"],
                "escalate": False,
            }
        return _mark_operator_attention_analysis(
            failure_class="publish_pr_blocked",
            root_cause="publish failed without a safe bounded recovery path",
            reasoning_summary="publish failure needs operator attention",
        )

    if action_type == "push_pr_update":
        if "retry_same_action" in allowed and _is_transient_failure_summary(failure_summary):
            return {
                "failure_class": "push_pr_update_transient_failure",
                "root_cause": "PR update failed with a transient network or platform error",
                "confidence": 0.72,
                "recommended_action": "retry_same_action",
                "reasoning_summary": "retry the PR update once because the failure looks transient",
                "evidence_refs": ["evidence.error"],
                "escalate": False,
            }
        if (
            "dispatch_repair_handoff" in allowed
            and current_head_sha
            and repair_brief.get("forHeadSha") == current_head_sha
            and (repair_brief.get("mustFix") or repair_brief.get("shouldFix"))
        ):
            return {
                "failure_class": "push_pr_update_repair_handoff_needed",
                "root_cause": "the branch update failed while the lane still has actionable repair work for the current head",
                "confidence": 0.77,
                "recommended_action": "dispatch_repair_handoff",
                "reasoning_summary": "dispatch repair handoff to refresh the coder-side repair loop before another PR update",
                "evidence_refs": ["lane_snapshot.repair_brief_json", "lane_snapshot.current_head_sha", "evidence.error"],
                "escalate": False,
            }
        if "request_internal_review" in allowed:
            return {
                "failure_class": "push_pr_update_requires_review",
                "root_cause": "the PR update failed and a reviewer pass is the safest bounded way to re-check branch state",
                "confidence": 0.68,
                "recommended_action": "request_internal_review",
                "reasoning_summary": "request internal review before another PR update attempt",
                "evidence_refs": ["lane_snapshot.review_state", "evidence.error"],
                "escalate": False,
            }
        return _mark_operator_attention_analysis(
            failure_class="push_pr_update_blocked",
            root_cause="PR update failed without a safe bounded recovery path",
            reasoning_summary="PR update failure needs operator attention",
        )

    if action_type == "request_internal_review":
        if "retry_same_action" in allowed and _is_transient_failure_summary(failure_summary):
            return {
                "failure_class": "request_internal_review_transient_failure",
                "root_cause": "review dispatch failed with a transient runtime or transport error",
                "confidence": 0.71,
                "recommended_action": "retry_same_action",
                "reasoning_summary": "retry the internal review request once because the failure looks transient",
                "evidence_refs": ["evidence.error"],
                "escalate": False,
            }
        return _mark_operator_attention_analysis(
            failure_class="request_internal_review_blocked",
            root_cause="internal review dispatch failed in a non-transient way",
            reasoning_summary="internal review failure needs operator attention",
        )

    if action_type == "merge_pr":
        if "retry_same_action" in allowed and _is_transient_failure_summary(failure_summary):
            return {
                "failure_class": "merge_pr_transient_failure",
                "root_cause": "merge failed with a transient platform or transport error",
                "confidence": 0.73,
                "recommended_action": "retry_same_action",
                "reasoning_summary": "retry the merge once because the failure looks transient",
                "evidence_refs": ["evidence.error"],
                "escalate": False,
            }
        return _mark_operator_attention_analysis(
            failure_class="merge_pr_blocked",
            root_cause=f"merge failed while lane state was {workflow_state}/{review_state}/{merge_state}",
            reasoning_summary="merge failure needs operator attention",
            evidence_refs=["lane_snapshot.workflow_state", "lane_snapshot.review_state", "lane_snapshot.merge_state", "evidence.error"],
        )

    return _mark_operator_attention_analysis(
        failure_class=analysis_input.get("failure_class") or f"{action_type or 'unknown_action'}_failed",
        root_cause="no bounded default analysis rule matched this failure",
        reasoning_summary="operator attention required because no bounded failure-analysis rule matched",
        evidence_refs=["last_action.action_type", "evidence.error"],
    )



def _invoke_failure_analyst(*, analysis_input: dict[str, Any], failure_analyst: Any | None) -> dict[str, Any]:
    allowed_actions = analysis_input.get("allowed_actions") or []
    analyst = failure_analyst or _default_failure_analyst
    raw_output = None
    validation_errors: list[str] = []
    try:
        raw_output = analyst(analysis_input)
    except Exception as exc:
        validation_errors = [f"failure analyst execution failed: {exc}"]
    else:
        validation_errors = _validate_failure_analyst_output(output=raw_output, allowed_actions=allowed_actions)

    if validation_errors:
        analysis = {
            "failure_class": analysis_input.get("failure_class"),
            "root_cause": "failure analysis output was invalid",
            "confidence": 0.0,
            "recommended_action": "mark_operator_attention",
            "reasoning_summary": f"invalid failure analyst output: {', '.join(validation_errors)}",
            "evidence_refs": [],
            "escalate": True,
        }
        return {
            "ok": False,
            "analysis": analysis,
            "raw_output": raw_output,
            "validation_errors": validation_errors,
        }

    assert isinstance(raw_output, dict)
    analysis = {
        "failure_class": raw_output.get("failure_class"),
        "root_cause": raw_output.get("root_cause"),
        "confidence": float(raw_output.get("confidence") or 0.0),
        "recommended_action": raw_output.get("recommended_action"),
        "reasoning_summary": raw_output.get("reasoning_summary"),
        "evidence_refs": raw_output.get("evidence_refs") or [],
        "escalate": bool(raw_output.get("escalate")),
    }
    return {
        "ok": True,
        "analysis": analysis,
        "raw_output": raw_output,
        "validation_errors": [],
    }



def _analyze_ambiguous_failure(
    *,
    conn: sqlite3.Connection,
    event_log_path: Path,
    action: dict[str, Any],
    failure_id: str,
    failure_scope: str,
    failure_class: str,
    failure_summary: str,
    now_iso: str,
    failure_analyst: Any | None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    analysis_input = _build_failure_analysis_input(
        conn=conn,
        event_log_path=event_log_path,
        action=action,
        failure_id=failure_id,
        failure_scope=failure_scope,
        failure_class=failure_class,
        failure_summary=failure_summary,
    )
    allowed_actions = analysis_input["allowed_actions"]
    events = [
        {
            "event_id": f"evt:error_analysis_requested:{failure_id}:{now_iso}",
            "event_type": "error_analysis_requested",
            "event_version": 1,
            "created_at": now_iso,
            "producer": "Workflow_Orchestrator",
            "project_key": "yoyopod",
            "lane_id": action.get("lane_id"),
            "issue_number": None,
            "head_sha": action.get("target_head_sha"),
            "causal_event_id": None,
            "causal_action_id": action.get("action_id"),
            "dedupe_key": f"error_analysis_requested:{failure_id}",
            "payload": {
                "failure_id": failure_id,
                "analyst_role": WORKFLOW_ERROR_ANALYST_ROLE,
                "allowed_actions": allowed_actions,
            },
        }
    ]
    invocation = _invoke_failure_analyst(analysis_input=analysis_input, failure_analyst=failure_analyst)
    analysis = invocation["analysis"]
    recommended_action = analysis.get("recommended_action")
    escalate = bool(analysis.get("escalate")) or recommended_action == "mark_operator_attention"
    recovery = {
        "analyst_status": "completed" if invocation.get("ok") else "failed",
        "recommended_action": recommended_action,
        "confidence": analysis.get("confidence") or 0.0,
        "escalated": 1 if escalate else 0,
        "queue_recovery_action": _queueable_recovery_action_type(
            action=action,
            recommended_action=recommended_action,
            escalate=escalate,
        ),
        "summary": analysis.get("reasoning_summary"),
        "failure_class": analysis.get("failure_class") or failure_class,
        "metadata": {
            "allowed_actions": allowed_actions,
            "analysis_input": analysis_input,
            "analyst_output": invocation.get("raw_output"),
            "root_cause": analysis.get("root_cause"),
            "evidence_refs": analysis.get("evidence_refs") or [],
            "validation_errors": invocation.get("validation_errors") or [],
        },
    }
    events.append(
        {
            "event_id": f"evt:error_analysis_completed:{failure_id}:{now_iso}",
            "event_type": "error_analysis_completed",
            "event_version": 1,
            "created_at": now_iso,
            "producer": "Workflow_Orchestrator",
            "project_key": "yoyopod",
            "lane_id": action.get("lane_id"),
            "issue_number": None,
            "head_sha": action.get("target_head_sha"),
            "causal_event_id": None,
            "causal_action_id": action.get("action_id"),
            "dedupe_key": f"error_analysis_completed:{failure_id}",
            "payload": {
                "failure_id": failure_id,
                "failure_class": recovery.get("failure_class") or failure_class,
                "recommended_action": recovery.get("recommended_action"),
                "confidence": recovery.get("confidence") or 0.0,
                "escalation_needed": bool(recovery.get("escalated")),
                "summary": recovery.get("summary") or failure_summary,
            },
        }
    )
    return recovery, analysis_input["evidence"], events


def _queue_recovery_action(*, conn: sqlite3.Connection, action: dict[str, Any], now_iso: str, recovery_action_type: str) -> dict[str, Any]:
    retry_count = int(action.get("retry_count") or 0) + 1 if recovery_action_type == action.get("action_type") else 0
    recovery_attempt_count = int(action.get("recovery_attempt_count") or 0) + 1
    action_id = f"act:recovery:{action.get('lane_id')}:{recovery_action_type}:{now_iso}:{recovery_attempt_count}"
    idempotency_key = (
        f"active-recovery:{recovery_action_type}:{action.get('lane_id')}:"
        f"{action.get('target_head_sha') or 'none'}:{recovery_attempt_count}"
    )
    payload = _parse_json_blob(action.get("request_payload_json")) or {
        "action_type": action.get("action_type"),
        "reason": action.get("action_reason"),
        "target_head_sha": action.get("target_head_sha"),
    }
    action_reason = "automatic-retry-after-failure" if recovery_action_type == action.get("action_type") else "automatic-session-restart-after-failure"
    target_actor_role = _target_actor_role_for_active_action(recovery_action_type)
    target_actor_id = _target_actor_id_for_active_action(action_type=recovery_action_type, action=action)
    conn.execute(
        """
        INSERT INTO lane_actions (
          action_id, lane_id, action_type, action_reason, action_mode, requested_by,
          target_actor_role, target_actor_id, target_head_sha, idempotency_key, status,
          requested_at, dispatched_at, completed_at, failed_at, result_code, result_summary,
          request_payload_json, result_payload_json, error_payload_json, retry_count,
          recovery_attempt_count, superseded_by_action_id, causal_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL, NULL, ?, ?, NULL, NULL)
        """,
        (
            action_id,
            action.get("lane_id"),
            recovery_action_type,
            action_reason,
            action.get("action_mode"),
            action.get("requested_by") or "Workflow_Orchestrator",
            target_actor_role,
            target_actor_id,
            action.get("target_head_sha"),
            idempotency_key,
            "requested",
            now_iso,
            json.dumps({**payload, "recovery": recovery_action_type, "prior_action_id": action.get("action_id")}, sort_keys=True),
            retry_count,
            recovery_attempt_count,
        ),
    )
    conn.execute(
        "UPDATE lane_actions SET superseded_by_action_id=? WHERE action_id=?",
        (action_id, action.get("action_id")),
    )
    conn.execute(
        "UPDATE lanes SET current_action_id=?, updated_at=? WHERE lane_id=?",
        (action_id, now_iso, action.get("lane_id")),
    )
    return {
        "action_id": action_id,
        "lane_id": action.get("lane_id"),
        "action_type": recovery_action_type,
        "action_reason": action_reason,
        "target_head_sha": action.get("target_head_sha"),
        "retry_count": retry_count,
        "recovery_attempt_count": recovery_attempt_count,
        "requested_at": now_iso,
    }



def analyze_failure(*, workflow_root: Path, failure_id: str, failure_analyst: Any | None = None) -> dict[str, Any]:
    paths = _runtime_paths(workflow_root)
    conn = _connect(paths["db_path"])
    conn.row_factory = sqlite3.Row
    try:
        failure_row = conn.execute("SELECT * FROM failures WHERE failure_id=?", (failure_id,)).fetchone()
        if not failure_row:
            return {"ok": False, "reason": "missing-failure", "failure_id": failure_id}
        failure = dict(failure_row)
        action_id = failure.get("related_action_id")
        if not action_id:
            return {"ok": False, "reason": "failure-missing-related-action", "failure_id": failure_id}
        action_row = conn.execute("SELECT * FROM lane_actions WHERE action_id=?", (action_id,)).fetchone()
        if not action_row:
            return {"ok": False, "reason": "missing-related-action", "failure_id": failure_id, "action_id": action_id}
        action = dict(action_row)
        evidence = _parse_json_blob(failure.get("evidence_json")) or {}
        failure_summary = evidence.get("error") or failure.get("analyst_summary") or failure.get("failure_class")
        analysis_input = _build_failure_analysis_input(
            conn=conn,
            event_log_path=paths["event_log_path"],
            action=action,
            failure_id=failure_id,
            failure_scope=failure.get("failure_scope") or _failure_scope_for_action(action.get("action_type")),
            failure_class=failure.get("failure_class") or f"{action.get('action_type')}_failed",
            failure_summary=failure_summary,
        )
        invocation = _invoke_failure_analyst(analysis_input=analysis_input, failure_analyst=failure_analyst)
        return {
            "ok": bool(invocation.get("ok")),
            "failure_id": failure_id,
            "action_id": action_id,
            "analysis_input": analysis_input,
            "analysis": invocation.get("analysis"),
            "validation_errors": invocation.get("validation_errors") or [],
        }
    finally:
        conn.close()



def execute_requested_action(
    *,
    workflow_root: Path,
    action_id: str,
    now_iso: str | None = None,
    action_runners: dict[str, Any] | None = None,
    failure_analyst: Any | None = None,
) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    runners = _default_active_action_runners(workflow_root=workflow_root)
    if action_runners:
        runners.update(action_runners)
    paths = _runtime_paths(workflow_root)
    conn = _connect(paths["db_path"])
    conn.row_factory = sqlite3.Row
    action: dict[str, Any] | None = None
    lane_before: dict[str, Any] | None = None
    lane_after: dict[str, Any] | None = None
    post_action_status: dict[str, Any] | None = None
    recovery_action: dict[str, Any] | None = None
    analysis_events: list[dict[str, Any]] = []
    try:
        action_row = conn.execute("SELECT * FROM lane_actions WHERE action_id=?", (action_id,)).fetchone()
        if not action_row:
            return {"executed": False, "reason": "missing-action", "action_id": action_id}
        action = dict(action_row)
        if action.get("lane_id"):
            lane_row = conn.execute("SELECT * FROM lanes WHERE lane_id=?", (action.get("lane_id"),)).fetchone()
            lane_before = dict(lane_row) if lane_row else None
        if action.get("action_mode") != "active":
            return {"executed": False, "reason": "not-active-action", "action_id": action_id}
        if action.get("status") != "requested":
            return {"executed": False, "reason": "action-not-requested", "action_id": action_id, "status": action.get("status")}
        conn.execute(
            "UPDATE lane_actions SET status='dispatched', dispatched_at=? WHERE action_id=?",
            (now_iso, action_id),
        )
        conn.commit()
        runner = runners.get(action.get("action_type"))
        if runner is None:
            raise RuntimeError(f"unsupported action_type: {action.get('action_type')}")
        result = runner()
        post_action_status = result.get("after") if isinstance(result.get("after"), dict) else None
        if post_action_status is None:
            # Post-action status read prefers the plugin-side workspace
            # accessor; falls back to the legacy wrapper module if the plugin
            # is not yet installed under the workflow root.
            try:
                post_action_status = _load_legacy_workflow_module(workflow_root).build_status()
            except Exception:
                post_action_status = None
        if post_action_status is not None:
            ingest_legacy_status(workflow_root=workflow_root, legacy_status=post_action_status, now_iso=now_iso)
        if action.get("lane_id"):
            lane_row = conn.execute("SELECT * FROM lanes WHERE lane_id=?", (action.get("lane_id"),)).fetchone()
            lane_after = dict(lane_row) if lane_row else lane_before
        result_summary = _summarize_active_action_result(action_type=action.get("action_type"), result=result)
        request_payload = _parse_json_blob(action.get("request_payload_json")) or {}
        prior_action_id = request_payload.get("prior_action_id")
        conn.execute(
            """
            UPDATE lane_actions
            SET status='completed', completed_at=?, result_code=?, result_summary=?, result_payload_json=?
            WHERE action_id=?
            """,
            (now_iso, "ok", result_summary, json.dumps(result, sort_keys=True), action_id),
        )
        if prior_action_id:
            conn.execute(
                """
                UPDATE failures
                SET resolved_at=?, resolution_action_id=?
                WHERE related_action_id=? AND resolved_at IS NULL
                """,
                (now_iso, action_id, prior_action_id),
            )
            unresolved_count = conn.execute(
                "SELECT COUNT(*) FROM failures WHERE lane_id=? AND resolved_at IS NULL",
                (action.get("lane_id"),),
            ).fetchone()[0]
            if unresolved_count == 0:
                conn.execute(
                    """
                    UPDATE lanes
                    SET operator_attention_required=0,
                        operator_attention_reason=NULL,
                        updated_at=?
                    WHERE lane_id=? AND operator_attention_reason LIKE 'active-action-failed:%'
                    """,
                    (now_iso, action.get("lane_id")),
                )
        conn.commit()
    except Exception as exc:
        failure_scope = _failure_scope_for_action((action or {}).get("action_type"))
        raw_failure_class = f"{(action or {}).get('action_type') or 'unknown_action'}_failed"
        failure_summary = str(exc)
        failure_id = f"failure:{action_id}"
        recovery = {
            "analyst_status": "failed",
            "recommended_action": "mark_operator_attention",
            "confidence": 0.0,
            "escalated": 1,
            "queue_recovery_action": None,
            "summary": failure_summary,
            "failure_class": raw_failure_class,
            "metadata": {"source": "execute_requested_action"},
        }
        evidence = {
            "action_type": (action or {}).get("action_type"),
            "error": failure_summary,
            "target_head_sha": (action or {}).get("target_head_sha"),
        }
        conn.execute(
            """
            UPDATE lane_actions
            SET status='failed', failed_at=?, result_code=?, result_summary=?, error_payload_json=?
            WHERE action_id=?
            """,
            (now_iso, "error", failure_summary, json.dumps({"error": failure_summary}, sort_keys=True), action_id),
        )
        if action:
            deterministic_recovery = _deterministic_recovery_for_failure(action)
            if deterministic_recovery is not None:
                recovery = {
                    **recovery,
                    **deterministic_recovery,
                    "metadata": {"source": "execute_requested_action", "recovery_type": "deterministic"},
                }
            else:
                recovery, evidence, analysis_events = _analyze_ambiguous_failure(
                    conn=conn,
                    event_log_path=paths["event_log_path"],
                    action=action,
                    failure_id=failure_id,
                    failure_scope=failure_scope,
                    failure_class=raw_failure_class,
                    failure_summary=failure_summary,
                    now_iso=now_iso,
                    failure_analyst=failure_analyst,
                )
                recovery["metadata"] = {
                    "source": "execute_requested_action",
                    **(recovery.get("metadata") or {}),
                }
            failure_class = recovery.get("failure_class") or raw_failure_class
            conn.execute(
                """
                INSERT OR REPLACE INTO failures (
                  failure_id, lane_id, related_action_id, related_actor_id, failure_scope,
                  failure_class, severity, detected_at, evidence_json, analyst_status,
                  analyst_recommended_action, analyst_confidence, analyst_summary,
                  escalated, resolved_at, resolution_action_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    failure_id,
                    action.get("lane_id"),
                    action_id,
                    action.get("target_actor_id"),
                    failure_scope,
                    failure_class,
                    "error",
                    now_iso,
                    json.dumps(evidence, sort_keys=True),
                    recovery.get("analyst_status"),
                    recovery.get("recommended_action"),
                    recovery.get("confidence"),
                    recovery.get("summary") or failure_summary,
                    recovery.get("escalated"),
                    json.dumps(recovery.get("metadata") or {"source": "execute_requested_action"}, sort_keys=True),
                ),
            )
            if recovery.get("queue_recovery_action"):
                recovery_action = _queue_recovery_action(
                    conn=conn,
                    action=action,
                    now_iso=now_iso,
                    recovery_action_type=recovery.get("queue_recovery_action"),
                )
            else:
                conn.execute(
                    """
                    UPDATE lanes
                    SET operator_attention_required=1,
                        operator_attention_reason=?,
                        updated_at=?
                    WHERE lane_id=?
                    """,
                    (f"active-action-failed:{action.get('action_type')}", now_iso, action.get("lane_id")),
                )
            conn.execute(
                """
                UPDATE relay_runtime
                SET latest_error_at=?, latest_error_summary=?, updated_at=?
                WHERE runtime_id='relay'
                """,
                (now_iso, failure_summary, now_iso),
            )
        else:
            failure_class = raw_failure_class
        conn.commit()
        append_relay_event(
            event_log_path=paths["event_log_path"],
            event={
                "event_id": f"evt:active_action_failed:{action_id}:{now_iso}",
                "event_type": "active_action_failed",
                "event_version": 1,
                "created_at": now_iso,
                "producer": "Workflow_Orchestrator",
                "project_key": "yoyopod",
                "lane_id": (action or {}).get("lane_id"),
                "issue_number": None,
                "head_sha": (action or {}).get("target_head_sha"),
                "causal_event_id": None,
                "causal_action_id": action_id,
                "dedupe_key": f"active_action_failed:{action_id}:{now_iso}",
                "payload": {"error": failure_summary, "action_type": (action or {}).get("action_type")},
            },
        )
        append_relay_event(
            event_log_path=paths["event_log_path"],
            event={
                "event_id": f"evt:failure_detected:{action_id}:{now_iso}",
                "event_type": "failure_detected",
                "event_version": 1,
                "created_at": now_iso,
                "producer": "Workflow_Orchestrator",
                "project_key": "yoyopod",
                "lane_id": (action or {}).get("lane_id"),
                "issue_number": None,
                "head_sha": (action or {}).get("target_head_sha"),
                "causal_event_id": None,
                "causal_action_id": action_id,
                "dedupe_key": f"failure_detected:{action_id}:{now_iso}",
                "payload": {
                    "failure_id": failure_id,
                    "failure_scope": failure_scope,
                    "failure_class": failure_class,
                    "severity": "error",
                    "summary": failure_summary,
                    "recommended_action": recovery.get("recommended_action"),
                },
            },
        )
        for event in _semantic_failure_events_for_action(
            action=action,
            failure_class=failure_class,
            failure_summary=failure_summary,
            now_iso=now_iso,
        ):
            append_relay_event(event_log_path=paths["event_log_path"], event=event)
        for event in analysis_events:
            append_relay_event(event_log_path=paths["event_log_path"], event=event)
        if recovery_action:
            append_relay_event(
                event_log_path=paths["event_log_path"],
                event={
                    "event_id": f"evt:active_action_requested:{recovery_action['action_id']}:{now_iso}",
                    "event_type": "active_action_requested",
                    "event_version": 1,
                    "created_at": now_iso,
                    "producer": "Workflow_Orchestrator",
                    "project_key": "yoyopod",
                    "lane_id": recovery_action.get("lane_id"),
                    "issue_number": None,
                    "head_sha": recovery_action.get("target_head_sha"),
                    "causal_event_id": None,
                    "causal_action_id": recovery_action.get("action_id"),
                    "dedupe_key": f"active_action_requested:{recovery_action.get('action_id')}",
                    "payload": {
                        "action_type": recovery_action.get("action_type"),
                        "reason": recovery_action.get("action_reason"),
                        "mode": "active",
                        "retry_count": recovery_action.get("retry_count"),
                    },
                },
            )
        elif action:
            append_relay_event(
                event_log_path=paths["event_log_path"],
                event={
                    "event_id": f"evt:operator_attention_required:{action_id}:{now_iso}",
                    "event_type": "operator_attention_required",
                    "event_version": 1,
                    "created_at": now_iso,
                    "producer": "Workflow_Orchestrator",
                    "project_key": "yoyopod",
                    "lane_id": action.get("lane_id"),
                    "issue_number": None,
                    "head_sha": action.get("target_head_sha"),
                    "causal_event_id": None,
                    "causal_action_id": action_id,
                    "dedupe_key": f"operator_attention_required:{action_id}:{now_iso}",
                    "payload": {
                        "reason": f"active-action-failed:{action.get('action_type')}",
                        "failure_id": failure_id,
                        "summary": failure_summary,
                    },
                },
            )
        return {"executed": False, "action_id": action_id, "reason": "execution-failed", "error": failure_summary}
    finally:
        conn.close()
    append_relay_event(
        event_log_path=paths["event_log_path"],
        event={
            "event_id": f"evt:active_action_completed:{action_id}:{now_iso}",
            "event_type": "active_action_completed",
            "event_version": 1,
            "created_at": now_iso,
            "producer": "Workflow_Orchestrator",
            "project_key": "yoyopod",
            "lane_id": action.get("lane_id"),
            "issue_number": None,
            "head_sha": action.get("target_head_sha"),
            "causal_event_id": None,
            "causal_action_id": action_id,
            "dedupe_key": f"active_action_completed:{action_id}:{now_iso}",
            "payload": {"action_type": action.get("action_type"), "result_code": "ok"},
        },
    )
    for event in _semantic_completion_events_for_action(
        action=action,
        lane_before=lane_before,
        lane_after=lane_after,
        result=result,
        post_legacy_status=post_action_status,
        now_iso=now_iso,
    ):
        append_relay_event(event_log_path=paths["event_log_path"], event=event)
    return {"executed": True, "action_id": action_id, "action_type": action.get("action_type"), "result": result}


def compare_with_legacy_status(*, workflow_root: Path, legacy_status: dict[str, Any], now_iso: str | None = None) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    ingest = ingest_legacy_status(workflow_root=workflow_root, legacy_status=legacy_status, now_iso=now_iso)
    lane_id = ingest.get("lane_id")
    if not lane_id:
        return {"compared": False, "reason": "no-lane"}
    shadow_actions = persist_shadow_actions(workflow_root=workflow_root, lane_id=lane_id, now_iso=now_iso)
    relay_action = shadow_actions[0] if shadow_actions else None
    legacy_action = (legacy_status.get("nextAction") or {})
    compatibility = {
        ("publish_ready_pr", "publish_pr"),
        ("merge_and_promote", "merge_pr"),
        ("run_claude_review", "request_internal_review"),
        ("dispatch_codex_turn", "dispatch_implementation_turn"),
        ("noop", "noop"),
        ("noop", None),
        ("push_pr_update", "push_pr_update"),
        ("dispatch_codex_turn", "dispatch_repair_handoff"),
    }
    relay_action_type = relay_action.get("action_type") if relay_action else None
    compatible = (legacy_action.get("type"), relay_action_type) in compatibility
    return {
        "compared": True,
        "lane_id": lane_id,
        "legacy_action_type": legacy_action.get("type"),
        "relay_action_type": relay_action_type,
        "legacy_reason": legacy_action.get("reason"),
        "relay_reason": relay_action.get("reason") if relay_action else None,
        "compatible": compatible,
    }


def refresh_runtime_lease(*, workflow_root: Path, instance_id: str, now_iso: str | None = None, ttl_seconds: int = 60) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    paths = _runtime_paths(workflow_root)
    refreshed = acquire_lease(
        db_path=paths["db_path"],
        lease_scope=RUNTIME_LEASE_SCOPE,
        lease_key=RUNTIME_LEASE_KEY,
        owner_instance_id=instance_id,
        owner_role="Workflow_Orchestrator",
        now_iso=now_iso,
        ttl_seconds=ttl_seconds,
    )
    if not refreshed.get("acquired"):
        return {"refreshed": False, "reason": "lease-held", "owner_instance_id": refreshed.get("owner_instance_id")}
    conn = _connect(paths["db_path"])
    try:
        conn.execute(
            "UPDATE relay_runtime SET latest_heartbeat_at=?, updated_at=? WHERE runtime_id='relay'",
            (now_iso, now_iso),
        )
        conn.commit()
    finally:
        conn.close()
    append_relay_event(
        event_log_path=paths["event_log_path"],
        event={
            "event_id": f"evt:relay_runtime_heartbeat:{instance_id}:{now_iso}",
            "event_type": "relay_runtime_heartbeat",
            "event_version": 1,
            "created_at": now_iso,
            "producer": "Relay_Runtime",
            "project_key": "yoyopod",
            "lane_id": None,
            "issue_number": None,
            "head_sha": None,
            "causal_event_id": None,
            "causal_action_id": None,
            "dedupe_key": f"relay_runtime_heartbeat:{instance_id}:{now_iso}",
            "payload": {"instance_id": instance_id, "ttl_seconds": ttl_seconds},
        },
    )
    return {"refreshed": True, "instance_id": instance_id, "heartbeat_at": now_iso}



def reconcile_stalled_recoveries(*, workflow_root: Path, lane_id: str, now_iso: str | None = None) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    paths = _runtime_paths(workflow_root)
    recent_failures = [
        failure
        for failure in query_recent_failures(
            workflow_root=workflow_root,
            limit=50,
            unresolved_only=True,
            now_iso=now_iso,
            lane_id=lane_id,
        )
        if failure.get("recovery_state") == "recovery_stalled"
    ]
    if not recent_failures:
        return {"checked": 0, "stalled": 0, "escalated": []}

    conn = _connect(paths["db_path"])
    conn.row_factory = sqlite3.Row
    events_to_emit = []
    escalated = []
    try:
        lane_row = conn.execute(
            "SELECT operator_attention_required, operator_attention_reason FROM lanes WHERE lane_id=?",
            (lane_id,),
        ).fetchone()
        current_attention_required = bool((lane_row or [0, None])[0])
        current_attention_reason = (lane_row or [0, None])[1]
        for failure in recent_failures:
            metadata = dict(failure.get("metadata") or {})
            detection_count = int(metadata.get("stalled_detection_count") or 0) + 1
            metadata["stalled_detection_count"] = detection_count
            metadata.setdefault("first_stalled_at", now_iso)
            metadata["last_stalled_at"] = now_iso
            reason = f"recovery-stalled:{failure.get('recovery_action_type') or failure.get('analyst_recommended_action') or 'unknown'}"
            should_escalate = detection_count >= STALLED_RECOVERY_DETECTION_THRESHOLD
            already_set_at = metadata.get("operator_attention_set_at")
            if should_escalate and not already_set_at:
                metadata["operator_attention_set_at"] = now_iso
                if not current_attention_required:
                    conn.execute(
                        """
                        UPDATE lanes
                        SET operator_attention_required=1,
                            operator_attention_reason=?,
                            updated_at=?
                        WHERE lane_id=?
                        """,
                        (reason, now_iso, lane_id),
                    )
                    current_attention_required = True
                    current_attention_reason = reason
                elif current_attention_reason == reason:
                    conn.execute(
                        "UPDATE lanes SET updated_at=? WHERE lane_id=?",
                        (now_iso, lane_id),
                    )
                conn.execute(
                    """
                    UPDATE relay_runtime
                    SET latest_error_at=?, latest_error_summary=?, updated_at=?
                    WHERE runtime_id='relay'
                    """,
                    (now_iso, f"stalled recovery detected for {reason}", now_iso),
                )
                events_to_emit.append(
                    {
                        "event_id": f"evt:operator_attention_required:stalled:{failure.get('failure_id')}:{now_iso}",
                        "event_type": "operator_attention_required",
                        "event_version": 1,
                        "created_at": now_iso,
                        "producer": "Workflow_Orchestrator",
                        "project_key": "yoyopod",
                        "lane_id": lane_id,
                        "issue_number": failure.get("issue_number"),
                        "head_sha": failure.get("evidence", {}).get("target_head_sha"),
                        "causal_event_id": None,
                        "causal_action_id": failure.get("related_action_id"),
                        "dedupe_key": f"operator_attention_required:stalled:{failure.get('failure_id')}",
                        "payload": {
                            "reason": reason,
                            "failure_id": failure.get("failure_id"),
                            "summary": failure.get("analyst_summary") or "stalled recovery detected",
                        },
                    }
                )
                escalated.append({"failure_id": failure.get("failure_id"), "reason": reason})
            conn.execute(
                "UPDATE failures SET metadata_json=? WHERE failure_id=?",
                (json.dumps(metadata, sort_keys=True), failure.get("failure_id")),
            )
        conn.commit()
    finally:
        conn.close()
    for event in events_to_emit:
        append_relay_event(event_log_path=paths["event_log_path"], event=event)
    return {"checked": len(recent_failures), "stalled": len(recent_failures), "escalated": escalated}



def run_shadow_iteration(*, workflow_root: Path, instance_id: str, legacy_status: dict[str, Any] | None = None, now_iso: str | None = None) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    heartbeat = refresh_runtime_lease(workflow_root=workflow_root, instance_id=instance_id, now_iso=now_iso)
    if not heartbeat.get("refreshed"):
        return {"iteration_status": "blocked", "reason": heartbeat.get("reason"), "owner_instance_id": heartbeat.get("owner_instance_id")}
    if legacy_status is None:
        legacy = _load_legacy_workflow_module(workflow_root)
        legacy_status = legacy.build_status()
    comparison = compare_with_legacy_status(workflow_root=workflow_root, legacy_status=legacy_status, now_iso=now_iso)
    return {
        "iteration_status": "ok",
        "heartbeat": heartbeat,
        "comparison": comparison,
    }


def run_shadow_loop(
    *,
    workflow_root: Path,
    project_key: str,
    instance_id: str,
    interval_seconds: int = 30,
    max_iterations: int | None = None,
    legacy_status_provider=None,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    start_now = _now_iso()
    status = get_runtime_status(workflow_root=workflow_root)
    if status.get("runtime_status") != "running" or status.get("active_orchestrator_instance_id") != instance_id:
        bootstrap = bootstrap_runtime(
            workflow_root=workflow_root,
            project_key=project_key,
            instance_id=instance_id,
            mode="shadow",
            now_iso=start_now,
        )
        if bootstrap.get("runtime_status") not in {"running"}:
            return {
                "loop_status": "blocked",
                "reason": bootstrap.get("reason"),
                "owner_instance_id": bootstrap.get("owner_instance_id"),
            }
    iterations = 0
    last_result = None
    try:
        while True:
            legacy_status = legacy_status_provider() if legacy_status_provider else None
            last_result = run_shadow_iteration(
                workflow_root=workflow_root,
                instance_id=instance_id,
                legacy_status=legacy_status,
                now_iso=_now_iso(),
            )
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            sleep_fn(interval_seconds)
    except KeyboardInterrupt:
        return {
            "loop_status": "interrupted",
            "instance_id": instance_id,
            "iterations": iterations,
            "last_result": last_result,
        }
    return {
        "loop_status": "completed",
        "instance_id": instance_id,
        "iterations": iterations,
        "last_result": last_result,
    }


def run_active_iteration(*, workflow_root: Path, instance_id: str, legacy_status: dict[str, Any] | None = None, now_iso: str | None = None, action_runners: dict[str, Any] | None = None) -> dict[str, Any]:
    now_iso = now_iso or _now_iso()
    heartbeat = refresh_runtime_lease(workflow_root=workflow_root, instance_id=instance_id, now_iso=now_iso)
    if not heartbeat.get("refreshed"):
        return {"iteration_status": "blocked", "reason": heartbeat.get("reason"), "owner_instance_id": heartbeat.get("owner_instance_id")}
    if legacy_status is None:
        legacy = _load_legacy_workflow_module(workflow_root)
        legacy_status = legacy.build_status()
    comparison = compare_with_legacy_status(workflow_root=workflow_root, legacy_status=legacy_status, now_iso=now_iso)
    gate = evaluate_active_execution_gate(workflow_root=workflow_root, legacy_status=legacy_status)
    if not comparison.get("compatible"):
        gate = {
            **gate,
            "allowed": False,
            "reasons": [*gate.get("reasons", []), "shadow-parity-mismatch"],
        }
    ingested = ingest_legacy_status(workflow_root=workflow_root, legacy_status=legacy_status, now_iso=now_iso)
    if not gate.get("allowed"):
        return {
            "iteration_status": "blocked",
            "heartbeat": heartbeat,
            "gate": gate,
            "comparison": comparison,
            "ingested": ingested,
            "dispatched_reap": {"checked": 0, "reaped": 0, "failures": [], "recovery_actions": []},
        }
    lane_id = ingested.get("lane_id")
    if not lane_id:
        return {
            "iteration_status": "noop",
            "reason": ingested.get("reason") or "no-active-lane",
            "heartbeat": heartbeat,
            "gate": gate,
            "comparison": comparison,
            "ingested": ingested,
            "requested_actions": [],
            "executed_action": None,
            "stalled_recoveries": {"checked": 0, "stalled": 0, "escalated": []},
            "dispatched_reap": {"checked": 0, "reaped": 0, "failures": [], "recovery_actions": []},
        }
    dispatched_reap = reap_stuck_dispatched_actions(
        workflow_root=workflow_root,
        lane_id=lane_id,
        now_iso=now_iso,
    )
    stalled_recoveries = reconcile_stalled_recoveries(
        workflow_root=workflow_root,
        lane_id=lane_id,
        now_iso=now_iso,
    )
    requested_actions = request_active_actions_for_lane(
        workflow_root=workflow_root,
        lane_id=lane_id,
        now_iso=now_iso,
    )
    if not requested_actions:
        return {
            "iteration_status": "noop",
            "reason": "no-active-actions",
            "heartbeat": heartbeat,
            "gate": gate,
            "comparison": comparison,
            "ingested": ingested,
            "requested_actions": [],
            "executed_action": None,
            "stalled_recoveries": stalled_recoveries,
            "dispatched_reap": dispatched_reap,
        }
    executed_action = execute_requested_action(
        workflow_root=workflow_root,
        action_id=requested_actions[0]["action_id"],
        now_iso=now_iso,
        action_runners=action_runners,
    )
    return {
        "iteration_status": "executed",
        "heartbeat": heartbeat,
        "gate": gate,
        "comparison": comparison,
        "ingested": ingested,
        "requested_actions": requested_actions,
        "executed_action": executed_action,
        "stalled_recoveries": stalled_recoveries,
        "dispatched_reap": dispatched_reap,
    }


def run_active_loop(
    *,
    workflow_root: Path,
    project_key: str,
    instance_id: str,
    interval_seconds: int = 30,
    max_iterations: int | None = None,
    legacy_status_provider=None,
    sleep_fn=time.sleep,
    action_runners: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start_now = _now_iso()
    status = get_runtime_status(workflow_root=workflow_root)
    if status.get("runtime_status") != "running" or status.get("active_orchestrator_instance_id") != instance_id or status.get("current_mode") != "active":
        bootstrap = bootstrap_runtime(
            workflow_root=workflow_root,
            project_key=project_key,
            instance_id=instance_id,
            mode="active",
            now_iso=start_now,
        )
        if bootstrap.get("runtime_status") not in {"running"}:
            return {
                "loop_status": "blocked",
                "reason": bootstrap.get("reason"),
                "owner_instance_id": bootstrap.get("owner_instance_id"),
            }
    iterations = 0
    last_result = None
    try:
        while True:
            legacy_status = legacy_status_provider() if legacy_status_provider else None
            last_result = run_active_iteration(
                workflow_root=workflow_root,
                instance_id=instance_id,
                legacy_status=legacy_status,
                now_iso=_now_iso(),
                action_runners=action_runners,
            )
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            sleep_fn(interval_seconds)
    except KeyboardInterrupt:
        return {
            "loop_status": "interrupted",
            "instance_id": instance_id,
            "iterations": iterations,
            "last_result": last_result,
        }
    return {
        "loop_status": "completed",
        "instance_id": instance_id,
        "iterations": iterations,
        "last_result": last_result,
    }


def _load_legacy_workflow_module(workflow_root: Path):
    """Build the plugin's workspace accessor for the given workflow root.

    Returns an object that exposes the full YoYoPod workflow attribute
    surface (``build_status``, ``reconcile``, ``doctor``, ``dispatch_*``,
    config constants, helper methods). Historically this function used to
    prefer a workspace-scoped ``yoyopod_workflow.py`` wrapper script; that
    wrapper has been retired, so the only supported resolution is via
    ``workflows.code_review.workspace.load_workspace_from_config``.
    """
    plugin_main = plugin_entrypoint_path(workflow_root)
    if not plugin_main.exists():
        raise RuntimeError(
            f"hermes-relay plugin not installed under {workflow_root}; "
            f"expected entrypoint at {plugin_main}. Run ./scripts/install.sh "
            "from the hermes-relay repo to install it."
        )
    plugin_root = plugin_main.parents[2]
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    workspace_mod = importlib.import_module("workflows.code_review.workspace")
    return workspace_mod.load_workspace_from_config(workspace_root=workflow_root)


def ingest_live_legacy_status(*, workflow_root: Path, now_iso: str | None = None) -> dict[str, Any]:
    legacy_status = build_yoyopod_core_status(workflow_root)
    return ingest_legacy_status(workflow_root=workflow_root, legacy_status=legacy_status, now_iso=now_iso)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Relay side-by-side runtime bootstrap.")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init", help="Initialize Relay DB and filesystem paths.")
    init_cmd.add_argument("--workflow-root", required=True)
    init_cmd.add_argument("--project-key", default="yoyopod")
    init_cmd.add_argument("--json", action="store_true")

    start_cmd = sub.add_parser("start", help="Bootstrap Relay runtime and acquire runtime lease.")
    start_cmd.add_argument("--workflow-root", required=True)
    start_cmd.add_argument("--project-key", default="yoyopod")
    start_cmd.add_argument("--instance-id", required=True)
    start_cmd.add_argument("--mode", default="shadow", choices=["shadow", "active", "maintenance"])
    start_cmd.add_argument("--json", action="store_true")

    status_cmd = sub.add_parser("status", help="Show Relay runtime status.")
    status_cmd.add_argument("--workflow-root", required=True)
    status_cmd.add_argument("--json", action="store_true")

    ingest_cmd = sub.add_parser("ingest-live", help="Ingest current legacy YoYoPod workflow status into Relay shadow state.")
    ingest_cmd.add_argument("--workflow-root", required=True)
    ingest_cmd.add_argument("--json", action="store_true")

    heartbeat_cmd = sub.add_parser("heartbeat", help="Refresh Relay runtime lease and heartbeat timestamp.")
    heartbeat_cmd.add_argument("--workflow-root", required=True)
    heartbeat_cmd.add_argument("--instance-id", required=True)
    heartbeat_cmd.add_argument("--ttl-seconds", type=int, default=60)
    heartbeat_cmd.add_argument("--json", action="store_true")

    iterate_cmd = sub.add_parser("iterate-shadow", help="Run one shadow-mode loop iteration against live or provided legacy state.")
    iterate_cmd.add_argument("--workflow-root", required=True)
    iterate_cmd.add_argument("--instance-id", required=True)
    iterate_cmd.add_argument("--json", action="store_true")

    run_cmd = sub.add_parser("run-shadow", help="Run the shadow-mode loop shell for one or more iterations.")
    run_cmd.add_argument("--workflow-root", required=True)
    run_cmd.add_argument("--project-key", default="yoyopod")
    run_cmd.add_argument("--instance-id", required=True)
    run_cmd.add_argument("--interval-seconds", type=int, default=30)
    run_cmd.add_argument("--max-iterations", type=int)
    run_cmd.add_argument("--json", action="store_true")

    active_gate_status_cmd = sub.add_parser("active-gate-status", help="Show Relay active-execution gate state.")
    active_gate_status_cmd.add_argument("--workflow-root", required=True)
    active_gate_status_cmd.add_argument("--json", action="store_true")

    set_active_execution_cmd = sub.add_parser("set-active-execution", help="Enable or disable Relay active execution.")
    set_active_execution_cmd.add_argument("--workflow-root", required=True)
    set_active_execution_cmd.add_argument("--enabled", choices=["true", "false"], required=True)
    set_active_execution_cmd.add_argument("--json", action="store_true")

    iterate_active_cmd = sub.add_parser("iterate-active", help="Run one guarded active-mode loop iteration against live or provided legacy state.")
    iterate_active_cmd.add_argument("--workflow-root", required=True)
    iterate_active_cmd.add_argument("--instance-id", required=True)
    iterate_active_cmd.add_argument("--json", action="store_true")

    run_active_cmd = sub.add_parser("run-active", help="Run the guarded active-mode loop shell for one or more iterations.")
    run_active_cmd.add_argument("--workflow-root", required=True)
    run_active_cmd.add_argument("--project-key", default="yoyopod")
    run_active_cmd.add_argument("--instance-id", required=True)
    run_active_cmd.add_argument("--interval-seconds", type=int, default=30)
    run_active_cmd.add_argument("--max-iterations", type=int)
    run_active_cmd.add_argument("--json", action="store_true")

    request_active_cmd = sub.add_parser("request-active-actions", help="Derive and persist active requested actions for one lane.")
    request_active_cmd.add_argument("--workflow-root", required=True)
    request_active_cmd.add_argument("--lane-id", required=True)
    request_active_cmd.add_argument("--json", action="store_true")

    execute_action_cmd = sub.add_parser("execute-action", help="Execute one active requested action by action id.")
    execute_action_cmd.add_argument("--workflow-root", required=True)
    execute_action_cmd.add_argument("--action-id", required=True)
    execute_action_cmd.add_argument("--json", action="store_true")

    analyze_failure_cmd = sub.add_parser("analyze-failure", help="Run bounded failure analysis for a recorded failure id.")
    analyze_failure_cmd.add_argument("--workflow-root", required=True)
    analyze_failure_cmd.add_argument("--failure-id", required=True)
    analyze_failure_cmd.add_argument("--json", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    workflow_root = Path(args.workflow_root)
    paths = _runtime_paths(workflow_root)
    if args.command == "init":
        result = init_relay_db(workflow_root=workflow_root, project_key=args.project_key)
        print(json.dumps(result, indent=2) if args.json else f"initialized {result['db_path']}")
        return 0
    if args.command == "start":
        result = bootstrap_runtime(
            workflow_root=workflow_root,
            project_key=args.project_key,
            instance_id=args.instance_id,
            mode=args.mode,
        )
        print(json.dumps(result, indent=2) if args.json else f"{result['runtime_status']} {result.get('instance_id', '')}".strip())
        return 0
    if args.command == "status":
        result = get_runtime_status(workflow_root=workflow_root)
        print(json.dumps(result, indent=2) if args.json else f"{result['runtime_status']} mode={result.get('current_mode')} lanes={result.get('lane_count')}")
        return 0
    if args.command == "ingest-live":
        result = ingest_live_legacy_status(workflow_root=workflow_root)
        print(json.dumps(result, indent=2) if args.json else f"ingested {result.get('lane_id', '')}".strip())
        return 0
    if args.command == "heartbeat":
        result = refresh_runtime_lease(
            workflow_root=workflow_root,
            instance_id=args.instance_id,
            ttl_seconds=args.ttl_seconds,
        )
        print(json.dumps(result, indent=2) if args.json else f"heartbeat {result.get('instance_id', '')}".strip())
        return 0
    if args.command == "iterate-shadow":
        result = run_shadow_iteration(
            workflow_root=workflow_root,
            instance_id=args.instance_id,
        )
        print(json.dumps(result, indent=2) if args.json else f"{result['iteration_status']} {result.get('comparison', {}).get('lane_id', '')}".strip())
        return 0
    if args.command == "run-shadow":
        result = run_shadow_loop(
            workflow_root=workflow_root,
            project_key=args.project_key,
            instance_id=args.instance_id,
            interval_seconds=args.interval_seconds,
            max_iterations=args.max_iterations,
        )
        print(json.dumps(result, indent=2) if args.json else f"{result['loop_status']} iterations={result.get('iterations', 0)}")
        return 0
    if args.command == "active-gate-status":
        result = evaluate_active_execution_gate(workflow_root=workflow_root)
        print(json.dumps(result, indent=2) if args.json else f"allowed={result.get('allowed')} active_execution_enabled={((result.get('execution') or {}).get('active_execution_enabled'))} reasons={','.join(result.get('reasons') or [])}")
        return 0
    if args.command == "set-active-execution":
        result = set_execution_control(
            workflow_root=workflow_root,
            active_execution_enabled=(args.enabled == "true"),
        )
        print(json.dumps(result, indent=2) if args.json else f"active_execution_enabled={result.get('active_execution_enabled')}")
        return 0
    if args.command == "iterate-active":
        result = run_active_iteration(
            workflow_root=workflow_root,
            instance_id=args.instance_id,
        )
        print(json.dumps(result, indent=2) if args.json else f"{result['iteration_status']} action={((result.get('executed_action') or {}).get('action_type'))}")
        return 0
    if args.command == "run-active":
        result = run_active_loop(
            workflow_root=workflow_root,
            project_key=args.project_key,
            instance_id=args.instance_id,
            interval_seconds=args.interval_seconds,
            max_iterations=args.max_iterations,
        )
        print(json.dumps(result, indent=2) if args.json else f"{result['loop_status']} iterations={result.get('iterations', 0)}")
        return 0
    if args.command == "request-active-actions":
        result = request_active_actions_for_lane(
            workflow_root=workflow_root,
            lane_id=args.lane_id,
        )
        print(json.dumps(result, indent=2) if args.json else f"requested {len(result)} active actions")
        return 0
    if args.command == "execute-action":
        result = execute_requested_action(
            workflow_root=workflow_root,
            action_id=args.action_id,
        )
        print(json.dumps(result, indent=2) if args.json else f"executed={result.get('executed')} action={result.get('action_id')}")
        return 0
    if args.command == "analyze-failure":
        result = analyze_failure(
            workflow_root=workflow_root,
            failure_id=args.failure_id,
        )
        print(json.dumps(result, indent=2) if args.json else f"ok={result.get('ok')} failure={result.get('failure_id')} action={result.get('action_id')} recommendation={(result.get('analysis') or {}).get('recommended_action')}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
