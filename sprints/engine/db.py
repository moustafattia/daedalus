from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

ENGINE_SCHEDULER_TABLES = (
    "engine_work_items",
    "engine_running_work",
    "engine_retry_queue",
    "engine_runtime_sessions",
    "engine_runtime_totals",
)

ENGINE_STATE_TABLES = (
    *ENGINE_SCHEDULER_TABLES,
    "engine_runs",
    "engine_events",
)


def connect_sprints_db(db_path: Path) -> sqlite3.Connection:
    """Open the Sprints SQLite state store with production pragmas."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.OperationalError:
        return False
    return any(str(row[1]) == column_name for row in rows)


def engine_state_tables_exist(conn: sqlite3.Connection) -> bool:
    return all(table_exists(conn, name) for name in ENGINE_STATE_TABLES)


def init_engine_state(conn: sqlite3.Connection) -> None:
    """Create shared Sprints engine state tables and compatibility indexes."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS engine_work_items (
          workflow TEXT NOT NULL,
          work_id TEXT NOT NULL,
          identifier TEXT,
          state TEXT,
          title TEXT,
          url TEXT,
          source TEXT,
          metadata_json TEXT,
          updated_at TEXT NOT NULL,
          updated_at_epoch REAL NOT NULL,
          PRIMARY KEY (workflow, work_id)
        );

        CREATE TABLE IF NOT EXISTS engine_running_work (
          workflow TEXT NOT NULL,
          work_id TEXT NOT NULL,
          worker_id TEXT,
          attempt INTEGER NOT NULL DEFAULT 0,
          worker_status TEXT NOT NULL DEFAULT 'running',
          started_at_epoch REAL NOT NULL,
          heartbeat_at_epoch REAL NOT NULL,
          cancel_requested INTEGER NOT NULL DEFAULT 0,
          cancel_reason TEXT,
          thread_id TEXT,
          turn_id TEXT,
          run_id TEXT,
          updated_at TEXT NOT NULL,
          updated_at_epoch REAL NOT NULL,
          PRIMARY KEY (workflow, work_id),
          FOREIGN KEY (workflow, work_id) REFERENCES engine_work_items(workflow, work_id)
        );

        CREATE TABLE IF NOT EXISTS engine_retry_queue (
          workflow TEXT NOT NULL,
          work_id TEXT NOT NULL,
          attempt INTEGER NOT NULL DEFAULT 0,
          due_at_epoch REAL NOT NULL,
          error TEXT,
          current_attempt INTEGER,
          delay_type TEXT NOT NULL DEFAULT 'failure',
          run_id TEXT,
          updated_at TEXT NOT NULL,
          updated_at_epoch REAL NOT NULL,
          PRIMARY KEY (workflow, work_id),
          FOREIGN KEY (workflow, work_id) REFERENCES engine_work_items(workflow, work_id)
        );

        CREATE TABLE IF NOT EXISTS engine_runtime_sessions (
          workflow TEXT NOT NULL,
          work_id TEXT NOT NULL,
          session_name TEXT,
          runtime_name TEXT,
          runtime_kind TEXT,
          session_id TEXT,
          thread_id TEXT,
          turn_id TEXT,
          status TEXT,
          cancel_requested INTEGER NOT NULL DEFAULT 0,
          cancel_reason TEXT,
          run_id TEXT,
          metadata_json TEXT,
          updated_at TEXT NOT NULL,
          updated_at_epoch REAL NOT NULL,
          PRIMARY KEY (workflow, work_id),
          FOREIGN KEY (workflow, work_id) REFERENCES engine_work_items(workflow, work_id)
        );

        CREATE TABLE IF NOT EXISTS engine_runtime_totals (
          workflow TEXT PRIMARY KEY,
          input_tokens INTEGER NOT NULL DEFAULT 0,
          output_tokens INTEGER NOT NULL DEFAULT 0,
          total_tokens INTEGER NOT NULL DEFAULT 0,
          turn_count INTEGER NOT NULL DEFAULT 0,
          rate_limits_json TEXT,
          updated_at TEXT NOT NULL,
          updated_at_epoch REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS engine_runs (
          workflow TEXT NOT NULL,
          run_id TEXT NOT NULL,
          mode TEXT NOT NULL,
          status TEXT NOT NULL,
          started_at TEXT NOT NULL,
          started_at_epoch REAL NOT NULL,
          completed_at TEXT,
          completed_at_epoch REAL,
          selected_count INTEGER NOT NULL DEFAULT 0,
          completed_count INTEGER NOT NULL DEFAULT 0,
          error TEXT,
          metadata_json TEXT,
          PRIMARY KEY (workflow, run_id)
        );

        CREATE TABLE IF NOT EXISTS engine_events (
          workflow TEXT NOT NULL,
          event_id TEXT NOT NULL,
          run_id TEXT,
          work_id TEXT,
          event_type TEXT NOT NULL,
          severity TEXT NOT NULL DEFAULT 'info',
          created_at TEXT NOT NULL,
          created_at_epoch REAL NOT NULL,
          payload_json TEXT,
          PRIMARY KEY (workflow, event_id)
        );

        CREATE INDEX IF NOT EXISTS idx_engine_running_workflow_status
          ON engine_running_work(workflow, worker_status);
        CREATE INDEX IF NOT EXISTS idx_engine_retry_workflow_due
          ON engine_retry_queue(workflow, due_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_runtime_sessions_thread
          ON engine_runtime_sessions(workflow, thread_id);
        CREATE INDEX IF NOT EXISTS idx_engine_runs_workflow_started
          ON engine_runs(workflow, started_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_runs_workflow_status
          ON engine_runs(workflow, status);
        CREATE INDEX IF NOT EXISTS idx_engine_events_workflow_run
          ON engine_events(workflow, run_id, created_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_events_workflow_work
          ON engine_events(workflow, work_id, created_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_events_workflow_type
          ON engine_events(workflow, event_type, created_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_events_workflow_created
          ON engine_events(workflow, created_at_epoch);
        """
    )
    _rebuild_table_for_primary_key(
        conn,
        table_name="engine_runs",
        expected_primary_key=["workflow", "run_id"],
        create_sql="""
        CREATE TABLE engine_runs (
          workflow TEXT NOT NULL,
          run_id TEXT NOT NULL,
          mode TEXT NOT NULL,
          status TEXT NOT NULL,
          started_at TEXT NOT NULL,
          started_at_epoch REAL NOT NULL,
          completed_at TEXT,
          completed_at_epoch REAL,
          selected_count INTEGER NOT NULL DEFAULT 0,
          completed_count INTEGER NOT NULL DEFAULT 0,
          error TEXT,
          metadata_json TEXT,
          PRIMARY KEY (workflow, run_id)
        )
        """,
        copy_columns=[
            "workflow",
            "run_id",
            "mode",
            "status",
            "started_at",
            "started_at_epoch",
            "completed_at",
            "completed_at_epoch",
            "selected_count",
            "completed_count",
            "error",
            "metadata_json",
        ],
        indexes=["idx_engine_runs_workflow_started", "idx_engine_runs_workflow_status"],
    )
    _rebuild_table_for_primary_key(
        conn,
        table_name="engine_events",
        expected_primary_key=["workflow", "event_id"],
        create_sql="""
        CREATE TABLE engine_events (
          workflow TEXT NOT NULL,
          event_id TEXT NOT NULL,
          run_id TEXT,
          work_id TEXT,
          event_type TEXT NOT NULL,
          severity TEXT NOT NULL DEFAULT 'info',
          created_at TEXT NOT NULL,
          created_at_epoch REAL NOT NULL,
          payload_json TEXT,
          PRIMARY KEY (workflow, event_id)
        )
        """,
        copy_columns=[
            "workflow",
            "event_id",
            "run_id",
            "work_id",
            "event_type",
            "severity",
            "created_at",
            "created_at_epoch",
            "payload_json",
        ],
        indexes=[
            "idx_engine_events_workflow_run",
            "idx_engine_events_workflow_work",
            "idx_engine_events_workflow_type",
            "idx_engine_events_workflow_created",
        ],
    )
    _ensure_column(conn, "engine_running_work", "run_id TEXT")
    _ensure_column(conn, "engine_retry_queue", "run_id TEXT")
    _ensure_column(conn, "engine_runtime_sessions", "run_id TEXT")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_engine_running_workflow_status
          ON engine_running_work(workflow, worker_status);
        CREATE INDEX IF NOT EXISTS idx_engine_retry_workflow_due
          ON engine_retry_queue(workflow, due_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_runtime_sessions_thread
          ON engine_runtime_sessions(workflow, thread_id);
        CREATE INDEX IF NOT EXISTS idx_engine_runs_workflow_started
          ON engine_runs(workflow, started_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_runs_workflow_status
          ON engine_runs(workflow, status);
        CREATE INDEX IF NOT EXISTS idx_engine_events_workflow_run
          ON engine_events(workflow, run_id, created_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_events_workflow_work
          ON engine_events(workflow, work_id, created_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_events_workflow_type
          ON engine_events(workflow, event_type, created_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_events_workflow_created
          ON engine_events(workflow, created_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_running_workflow_run
          ON engine_running_work(workflow, run_id);
        CREATE INDEX IF NOT EXISTS idx_engine_retry_workflow_run
          ON engine_retry_queue(workflow, run_id);
        CREATE INDEX IF NOT EXISTS idx_engine_runtime_sessions_run
          ON engine_runtime_sessions(workflow, run_id);
        """
    )


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_sql: str) -> None:
    column_name = column_sql.split()[0]
    if table_exists(conn, table_name) and not column_exists(
        conn, table_name, column_name
    ):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _primary_key_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.OperationalError:
        return []
    pk_rows = sorted(
        (row for row in rows if int(row[5] or 0) > 0), key=lambda row: int(row[5] or 0)
    )
    return [str(row[1]) for row in pk_rows]


def _rebuild_table_for_primary_key(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    expected_primary_key: list[str],
    create_sql: str,
    copy_columns: list[str],
    indexes: list[str],
) -> None:
    if not table_exists(conn, table_name):
        return
    if _primary_key_columns(conn, table_name) == expected_primary_key:
        return
    for index_name in indexes:
        conn.execute(f"DROP INDEX IF EXISTS {index_name}")
    old_table_name = f"{table_name}__old_primary_key_{uuid.uuid4().hex}"
    conn.execute(f"ALTER TABLE {table_name} RENAME TO {old_table_name}")
    conn.execute(create_sql)
    columns = ", ".join(copy_columns)
    conn.execute(
        f"INSERT OR IGNORE INTO {table_name} ({columns}) SELECT {columns} FROM {old_table_name}"
    )
    conn.execute(f"DROP TABLE {old_table_name}")
