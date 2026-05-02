from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .scheduler import build_scheduler_payload
from .sqlite import connect_daedalus_db


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


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _json_loads(value: Any) -> Any:
    if value in (None, "", "null"):
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _value_or_default(value: Any, default: Any) -> Any:
    return default if value in (None, "") else value


def _first_value_or_default(default: Any, *values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return default


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.OperationalError:
        return False
    return any(str(row[1]) == column_name for row in rows)


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_sql: str) -> None:
    column_name = column_sql.split()[0]
    if _table_exists(conn, table_name) and not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _primary_key_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.OperationalError:
        return []
    pk_rows = sorted((row for row in rows if int(row[5] or 0) > 0), key=lambda row: int(row[5] or 0))
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
    if not _table_exists(conn, table_name):
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


def engine_state_tables_exist(conn: sqlite3.Connection) -> bool:
    return all(_table_exists(conn, name) for name in ENGINE_STATE_TABLES)


def init_engine_state(conn: sqlite3.Connection) -> None:
    """Create shared Daedalus engine state tables.

    Workflow-specific tables still own workflow policy and domain state. These
    tables own the neutral execution state used by scheduler, watch, doctor,
    retry, and runtime-session surfaces.
    """
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


def _open_engine_state_db(db_path: Path) -> sqlite3.Connection:
    conn = connect_daedalus_db(db_path)
    init_engine_state(conn)
    return conn


def _work_item_from_entry(*, workflow: str, work_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(entry.get("metadata") or {})
    for key in ("issue_number", "issueNumber", "worktree", "last_event", "last_message"):
        if entry.get(key) is not None:
            metadata[key] = entry.get(key)
    return {
        "workflow": workflow,
        "work_id": work_id,
        "identifier": entry.get("identifier") or work_id,
        "state": entry.get("state") or entry.get("workflow_state") or entry.get("status"),
        "title": entry.get("title") or entry.get("issue_title"),
        "url": entry.get("url") or entry.get("issue_url"),
        "source": entry.get("source") or workflow,
        "metadata": metadata,
    }


def _upsert_work_item(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    work_id: str,
    entry: dict[str, Any],
    now_iso: str,
    now_epoch: float,
) -> None:
    item = _work_item_from_entry(workflow=workflow, work_id=work_id, entry=entry)
    conn.execute(
        """
        INSERT INTO engine_work_items (
          workflow, work_id, identifier, state, title, url, source, metadata_json, updated_at, updated_at_epoch
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workflow, work_id) DO UPDATE SET
          identifier=excluded.identifier,
          state=excluded.state,
          title=excluded.title,
          url=excluded.url,
          source=excluded.source,
          metadata_json=excluded.metadata_json,
          updated_at=excluded.updated_at,
          updated_at_epoch=excluded.updated_at_epoch
        """,
        (
            item["workflow"],
            item["work_id"],
            item["identifier"],
            item["state"],
            item["title"],
            item["url"],
            item["source"],
            _json_dumps(item["metadata"]),
            now_iso,
            now_epoch,
        ),
    )


def save_engine_scheduler_state_to_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    retry_entries: dict[str, dict[str, Any]],
    running_entries: dict[str, dict[str, Any]],
    codex_totals: dict[str, Any] | None,
    codex_threads: dict[str, dict[str, Any]],
    now_iso: str,
    now_epoch: float,
) -> None:
    init_engine_state(conn)
    conn.execute("DELETE FROM engine_running_work WHERE workflow=?", (workflow,))
    conn.execute("DELETE FROM engine_retry_queue WHERE workflow=?", (workflow,))
    conn.execute("DELETE FROM engine_runtime_sessions WHERE workflow=?", (workflow,))

    for work_id, entry in sorted(running_entries.items(), key=lambda item: str(item[0])):
        work_id = str(entry.get("issue_id") or work_id or "").strip()
        if not work_id:
            continue
        _upsert_work_item(conn, workflow=workflow, work_id=work_id, entry=entry, now_iso=now_iso, now_epoch=now_epoch)
        conn.execute(
            """
            INSERT INTO engine_running_work (
              workflow, work_id, worker_id, attempt, worker_status, started_at_epoch, heartbeat_at_epoch,
              cancel_requested, cancel_reason, thread_id, turn_id, run_id, updated_at, updated_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow,
                work_id,
                entry.get("worker_id"),
                int(entry.get("attempt") or 0),
                entry.get("worker_status") or "running",
                float(_value_or_default(entry.get("started_at_epoch"), now_epoch)),
                float(
                    _first_value_or_default(
                        now_epoch,
                        entry.get("heartbeat_at_epoch"),
                        entry.get("started_at_epoch"),
                    )
                ),
                1 if entry.get("cancel_requested") else 0,
                entry.get("cancel_reason"),
                entry.get("thread_id"),
                entry.get("turn_id"),
                entry.get("run_id") or entry.get("runId"),
                now_iso,
                now_epoch,
            ),
        )

    for work_id, entry in sorted(retry_entries.items(), key=lambda item: str(item[0])):
        work_id = str(entry.get("issue_id") or work_id or "").strip()
        if not work_id:
            continue
        _upsert_work_item(conn, workflow=workflow, work_id=work_id, entry=entry, now_iso=now_iso, now_epoch=now_epoch)
        conn.execute(
            """
            INSERT INTO engine_retry_queue (
              workflow, work_id, attempt, due_at_epoch, error, current_attempt, delay_type, run_id, updated_at, updated_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow,
                work_id,
                int(entry.get("attempt") or 0),
                float(_value_or_default(entry.get("due_at_epoch"), now_epoch)),
                entry.get("error"),
                entry.get("current_attempt"),
                entry.get("delay_type") or "failure",
                entry.get("run_id") or entry.get("runId"),
                now_iso,
                now_epoch,
            ),
        )

    for work_id, entry in sorted(codex_threads.items(), key=lambda item: str(item[0])):
        if not isinstance(entry, dict):
            continue
        work_id = str(entry.get("issue_id") or work_id or "").strip()
        thread_id = str(entry.get("thread_id") or "").strip()
        if not work_id or not thread_id:
            continue
        _upsert_work_item(conn, workflow=workflow, work_id=work_id, entry=entry, now_iso=now_iso, now_epoch=now_epoch)
        metadata = {
            key: value
            for key, value in entry.items()
            if key
            not in {
                "issue_id",
                "identifier",
                "session_name",
                "runtime_name",
                "runtime_kind",
                "session_id",
                "thread_id",
                "turn_id",
                "status",
                "cancel_requested",
                "cancel_reason",
                "run_id",
                "runId",
                "updated_at",
                "updatedAt",
            }
        }
        conn.execute(
            """
            INSERT INTO engine_runtime_sessions (
              workflow, work_id, session_name, runtime_name, runtime_kind, session_id, thread_id, turn_id,
              status, cancel_requested, cancel_reason, run_id, metadata_json, updated_at, updated_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow,
                work_id,
                entry.get("session_name") or entry.get("sessionName"),
                entry.get("runtime_name") or entry.get("runtimeName"),
                entry.get("runtime_kind") or entry.get("runtimeKind"),
                entry.get("session_id") or entry.get("sessionId"),
                thread_id,
                entry.get("turn_id") or entry.get("turnId"),
                entry.get("status"),
                1 if (entry.get("cancel_requested") or entry.get("cancelRequested")) else 0,
                entry.get("cancel_reason") or entry.get("cancelReason"),
                entry.get("run_id") or entry.get("runId"),
                _json_dumps(metadata),
                entry.get("updated_at") or entry.get("updatedAt") or now_iso,
                now_epoch,
            ),
        )

    totals = dict(codex_totals or {})
    conn.execute(
        """
        INSERT INTO engine_runtime_totals (
          workflow, input_tokens, output_tokens, total_tokens, turn_count, rate_limits_json, updated_at, updated_at_epoch
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workflow) DO UPDATE SET
          input_tokens=excluded.input_tokens,
          output_tokens=excluded.output_tokens,
          total_tokens=excluded.total_tokens,
          turn_count=excluded.turn_count,
          rate_limits_json=excluded.rate_limits_json,
          updated_at=excluded.updated_at,
          updated_at_epoch=excluded.updated_at_epoch
        """,
        (
            workflow,
            int(totals.get("input_tokens") or 0),
            int(totals.get("output_tokens") or 0),
            int(totals.get("total_tokens") or 0),
            int(totals.get("turn_count") or 0),
            _json_dumps(totals.get("rate_limits")),
            now_iso,
            now_epoch,
        ),
    )


def save_engine_scheduler_state(
    db_path: Path,
    *,
    workflow: str,
    retry_entries: dict[str, dict[str, Any]],
    running_entries: dict[str, dict[str, Any]],
    codex_totals: dict[str, Any] | None,
    codex_threads: dict[str, dict[str, Any]],
    now_iso: str,
    now_epoch: float,
) -> None:
    conn = _open_engine_state_db(db_path)
    try:
        save_engine_scheduler_state_to_connection(
            conn,
            workflow=workflow,
            retry_entries=retry_entries,
            running_entries=running_entries,
            codex_totals=codex_totals,
            codex_threads=codex_threads,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )
        conn.commit()
    finally:
        conn.close()


def _scheduler_state_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    now_iso: str,
    now_epoch: float,
) -> dict[str, Any]:
    running_entries: dict[str, dict[str, Any]] = {}
    running_run_id_expr = "r.run_id" if _column_exists(conn, "engine_running_work", "run_id") else "NULL"
    for row in conn.execute(
        f"""
        SELECT r.work_id, w.identifier, w.state, r.worker_id, r.attempt, r.worker_status,
               r.started_at_epoch, r.heartbeat_at_epoch, r.cancel_requested, r.cancel_reason,
               r.thread_id, r.turn_id, {running_run_id_expr}
        FROM engine_running_work r
        LEFT JOIN engine_work_items w ON w.workflow = r.workflow AND w.work_id = r.work_id
        WHERE r.workflow=?
        """,
        (workflow,),
    ).fetchall():
        (
            work_id,
            identifier,
            state,
            worker_id,
            attempt,
            worker_status,
            started_at_epoch,
            heartbeat_at_epoch,
            cancel_requested,
            cancel_reason,
            thread_id,
            turn_id,
            run_id,
        ) = row
        running_entries[str(work_id)] = {
            "issue_id": str(work_id),
            "identifier": identifier,
            "state": state,
            "worker_id": worker_id,
            "attempt": int(attempt or 0),
            "worker_status": worker_status or "running",
            "started_at_epoch": float(_value_or_default(started_at_epoch, now_epoch)),
            "heartbeat_at_epoch": float(_first_value_or_default(now_epoch, heartbeat_at_epoch, started_at_epoch)),
            "cancel_requested": bool(cancel_requested),
            "cancel_reason": cancel_reason,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "run_id": run_id,
        }

    retry_entries: dict[str, dict[str, Any]] = {}
    retry_run_id_expr = "q.run_id" if _column_exists(conn, "engine_retry_queue", "run_id") else "NULL"
    for row in conn.execute(
        f"""
        SELECT q.work_id, w.identifier, q.attempt, q.due_at_epoch, q.error, q.current_attempt, q.delay_type, {retry_run_id_expr}
        FROM engine_retry_queue q
        LEFT JOIN engine_work_items w ON w.workflow = q.workflow AND w.work_id = q.work_id
        WHERE q.workflow=?
        """,
        (workflow,),
    ).fetchall():
        work_id, identifier, attempt, due_at_epoch, error, current_attempt, delay_type, run_id = row
        retry_entries[str(work_id)] = {
            "issue_id": str(work_id),
            "identifier": identifier,
            "attempt": int(attempt or 0),
            "due_at_epoch": float(_value_or_default(due_at_epoch, now_epoch)),
            "error": error,
            "current_attempt": current_attempt,
            "delay_type": delay_type or "failure",
            "run_id": run_id,
        }

    codex_threads: dict[str, dict[str, Any]] = {}
    session_run_id_expr = "s.run_id" if _column_exists(conn, "engine_runtime_sessions", "run_id") else "NULL"
    for row in conn.execute(
        f"""
        SELECT s.work_id, w.identifier, s.session_name, s.runtime_name, s.runtime_kind, s.session_id,
               s.thread_id, s.turn_id, s.status, s.cancel_requested, s.cancel_reason, {session_run_id_expr}, s.metadata_json, s.updated_at
        FROM engine_runtime_sessions s
        LEFT JOIN engine_work_items w ON w.workflow = s.workflow AND w.work_id = s.work_id
        WHERE s.workflow=? AND s.thread_id IS NOT NULL AND s.thread_id != ''
        """,
        (workflow,),
    ).fetchall():
        (
            work_id,
            identifier,
            session_name,
            runtime_name,
            runtime_kind,
            session_id,
            thread_id,
            turn_id,
            status,
            cancel_requested,
            cancel_reason,
            run_id,
            metadata_json,
            updated_at,
        ) = row
        metadata = _json_loads(metadata_json) or {}
        entry = {
            **metadata,
            "issue_id": str(work_id),
            "identifier": identifier or metadata.get("identifier") or str(work_id),
            "session_name": session_name,
            "runtime_name": runtime_name,
            "runtime_kind": runtime_kind,
            "session_id": session_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "status": status,
            "cancel_requested": bool(cancel_requested),
            "cancel_reason": cancel_reason,
            "run_id": run_id,
            "updated_at": updated_at,
        }
        codex_threads[str(work_id)] = {key: value for key, value in entry.items() if value is not None}

    totals_row = conn.execute(
        """
        SELECT input_tokens, output_tokens, total_tokens, turn_count, rate_limits_json
        FROM engine_runtime_totals
        WHERE workflow=?
        """,
        (workflow,),
    ).fetchone()
    codex_totals: dict[str, Any] = {}
    if totals_row is not None:
        input_tokens, output_tokens, total_tokens, turn_count, rate_limits_json = totals_row
        codex_totals = {
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "total_tokens": int(total_tokens or 0),
            "turn_count": int(turn_count or 0),
        }
        rate_limits = _json_loads(rate_limits_json)
        if rate_limits is not None:
            codex_totals["rate_limits"] = rate_limits

    return build_scheduler_payload(
        workflow=workflow,
        retry_entries=retry_entries,
        running_entries=running_entries,
        codex_totals=codex_totals,
        codex_threads=codex_threads,
        now_iso=now_iso,
        now_epoch=now_epoch,
    )


def load_engine_scheduler_state(
    db_path: Path,
    *,
    workflow: str,
    now_iso: str,
    now_epoch: float,
) -> dict[str, Any]:
    conn = _open_engine_state_db(db_path)
    try:
        return _scheduler_state_from_connection(conn, workflow=workflow, now_iso=now_iso, now_epoch=now_epoch)
    finally:
        conn.close()


def load_engine_scheduler_state_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    now_iso: str,
    now_epoch: float,
) -> dict[str, Any]:
    init_engine_state(conn)
    return _scheduler_state_from_connection(conn, workflow=workflow, now_iso=now_iso, now_epoch=now_epoch)


def read_engine_scheduler_state(
    db_path: Path,
    *,
    workflow: str,
    now_iso: str,
    now_epoch: float,
) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        if not all(_table_exists(conn, name) for name in ENGINE_SCHEDULER_TABLES):
            return None
        return _scheduler_state_from_connection(conn, workflow=workflow, now_iso=now_iso, now_epoch=now_epoch)
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _run_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        workflow,
        run_id,
        mode,
        status,
        started_at,
        started_at_epoch,
        completed_at,
        completed_at_epoch,
        selected_count,
        completed_count,
        error,
        metadata_json,
    ) = row
    return {
        "workflow": workflow,
        "run_id": run_id,
        "mode": mode,
        "status": status,
        "started_at": started_at,
        "started_at_epoch": float(started_at_epoch or 0),
        "completed_at": completed_at,
        "completed_at_epoch": None if completed_at_epoch is None else float(completed_at_epoch),
        "selected_count": int(selected_count or 0),
        "completed_count": int(completed_count or 0),
        "error": error,
        "metadata": _json_loads(metadata_json) or {},
    }


def start_engine_run_to_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    mode: str,
    now_iso: str,
    now_epoch: float,
    run_id: str | None = None,
    selected_count: int = 0,
    completed_count: int = 0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_engine_state(conn)
    safe_mode = str(mode or "run").strip() or "run"
    safe_run_id = str(run_id or "").strip() or (
        f"{workflow}:{safe_mode}:{int(now_epoch * 1000)}:{uuid.uuid4().hex[:8]}"
    )
    conn.execute(
        """
        INSERT INTO engine_runs (
          workflow, run_id, mode, status, started_at, started_at_epoch,
          selected_count, completed_count, metadata_json
        ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?)
        """,
        (
            workflow,
            safe_run_id,
            safe_mode,
            now_iso,
            float(now_epoch),
            int(selected_count or 0),
            int(completed_count or 0),
            _json_dumps(metadata or {}),
        ),
    )
    return {
        "workflow": workflow,
        "run_id": safe_run_id,
        "mode": safe_mode,
        "status": "running",
        "started_at": now_iso,
        "started_at_epoch": float(now_epoch),
        "completed_at": None,
        "completed_at_epoch": None,
        "selected_count": int(selected_count or 0),
        "completed_count": int(completed_count or 0),
        "error": None,
        "metadata": dict(metadata or {}),
    }


def finish_engine_run_to_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    run_id: str,
    status: str,
    now_iso: str,
    now_epoch: float,
    selected_count: int | None = None,
    completed_count: int | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_engine_state(conn)
    row = conn.execute(
        """
        SELECT workflow, run_id, mode, status, started_at, started_at_epoch,
               completed_at, completed_at_epoch, selected_count, completed_count,
               error, metadata_json
        FROM engine_runs
        WHERE workflow=? AND run_id=?
        """,
        (workflow, run_id),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown engine run: {run_id}")
    current = _run_row_to_dict(row)
    merged_metadata = dict(current.get("metadata") or {})
    if metadata:
        merged_metadata.update(metadata)
    final_status = str(status or "completed").strip() or "completed"
    final_selected_count = current["selected_count"] if selected_count is None else int(selected_count or 0)
    final_completed_count = current["completed_count"] if completed_count is None else int(completed_count or 0)
    final_error = error if error is not None else current.get("error")
    conn.execute(
        """
        UPDATE engine_runs
           SET status=?,
               completed_at=?,
               completed_at_epoch=?,
               selected_count=?,
               completed_count=?,
               error=?,
               metadata_json=?
         WHERE workflow=? AND run_id=?
        """,
        (
            final_status,
            now_iso,
            float(now_epoch),
            final_selected_count,
            final_completed_count,
            final_error,
            _json_dumps(merged_metadata),
            workflow,
            run_id,
        ),
    )
    return {
        **current,
        "status": final_status,
        "completed_at": now_iso,
        "completed_at_epoch": float(now_epoch),
        "selected_count": final_selected_count,
        "completed_count": final_completed_count,
        "error": final_error,
        "metadata": merged_metadata,
    }


def latest_engine_runs_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    init_engine_state(conn)
    rows = conn.execute(
        """
        SELECT workflow, run_id, mode, status, started_at, started_at_epoch,
               completed_at, completed_at_epoch, selected_count, completed_count,
               error, metadata_json
        FROM engine_runs
        WHERE workflow=?
        ORDER BY started_at_epoch DESC, run_id DESC
        LIMIT ?
        """,
        (workflow, max(int(limit or 10), 1)),
    ).fetchall()
    return [_run_row_to_dict(row) for row in rows]


def engine_run_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    run_id: str,
) -> dict[str, Any] | None:
    init_engine_state(conn)
    row = conn.execute(
        """
        SELECT workflow, run_id, mode, status, started_at, started_at_epoch,
               completed_at, completed_at_epoch, selected_count, completed_count,
               error, metadata_json
        FROM engine_runs
        WHERE workflow=? AND run_id=?
        """,
        (workflow, run_id),
    ).fetchone()
    return _run_row_to_dict(row) if row is not None else None


def read_engine_run(
    db_path: Path,
    *,
    workflow: str,
    run_id: str,
) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        if not _table_exists(conn, "engine_runs"):
            return None
        row = conn.execute(
            """
            SELECT workflow, run_id, mode, status, started_at, started_at_epoch,
                   completed_at, completed_at_epoch, selected_count, completed_count,
                   error, metadata_json
            FROM engine_runs
            WHERE workflow=? AND run_id=?
            """,
            (workflow, run_id),
        ).fetchone()
        return _run_row_to_dict(row) if row is not None else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def read_engine_runs(
    db_path: Path,
    *,
    workflow: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return []
    try:
        if not _table_exists(conn, "engine_runs"):
            return []
        rows = conn.execute(
            """
            SELECT workflow, run_id, mode, status, started_at, started_at_epoch,
                   completed_at, completed_at_epoch, selected_count, completed_count,
                   error, metadata_json
            FROM engine_runs
            WHERE workflow=?
            ORDER BY started_at_epoch DESC, run_id DESC
            LIMIT ?
            """,
            (workflow, max(int(limit or 10), 1)),
        ).fetchall()
        return [_run_row_to_dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _event_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        workflow,
        event_id,
        run_id,
        work_id,
        event_type,
        severity,
        created_at,
        created_at_epoch,
        payload_json,
    ) = row
    payload = _json_loads(payload_json) or {}
    return {
        "workflow": workflow,
        "event_id": event_id,
        "run_id": run_id,
        "work_id": work_id,
        "event_type": event_type,
        "severity": severity,
        "created_at": created_at,
        "created_at_epoch": float(created_at_epoch or 0),
        "payload": payload,
    }


def append_engine_event_to_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    event_type: str,
    payload: dict[str, Any],
    created_at: str,
    created_at_epoch: float,
    event_id: str | None = None,
    run_id: str | None = None,
    work_id: str | None = None,
    severity: str = "info",
) -> dict[str, Any]:
    init_engine_state(conn)
    safe_event_type = str(event_type or "event").strip() or "event"
    safe_severity = str(severity or "info").strip() or "info"
    safe_event_id = str(event_id or "").strip() or (
        f"{workflow}:{safe_event_type}:{int(float(created_at_epoch) * 1000)}:{uuid.uuid4().hex[:8]}"
    )
    event_payload = dict(payload or {})
    inserted = conn.execute(
        """
        INSERT OR IGNORE INTO engine_events (
          workflow, event_id, run_id, work_id, event_type, severity,
          created_at, created_at_epoch, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow,
            safe_event_id,
            run_id,
            work_id,
            safe_event_type,
            safe_severity,
            created_at,
            float(created_at_epoch),
            _json_dumps(event_payload),
        ),
    )
    if inserted.rowcount == 0:
        row = conn.execute(
            """
            SELECT workflow, event_id, run_id, work_id, event_type, severity,
                   created_at, created_at_epoch, payload_json
            FROM engine_events
            WHERE workflow=? AND event_id=?
            """,
            (workflow, safe_event_id),
        ).fetchone()
        if row is not None:
            return {**_event_row_to_dict(row), "inserted": False}
    return {
        "workflow": workflow,
        "event_id": safe_event_id,
        "run_id": run_id,
        "work_id": work_id,
        "event_type": safe_event_type,
        "severity": safe_severity,
        "created_at": created_at,
        "created_at_epoch": float(created_at_epoch),
        "payload": event_payload,
        "inserted": True,
    }


def engine_events_for_run_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    run_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_engine_state(conn)
    rows = conn.execute(
        """
        SELECT workflow, event_id, run_id, work_id, event_type, severity,
               created_at, created_at_epoch, payload_json
        FROM engine_events
        WHERE workflow=? AND run_id=?
        ORDER BY created_at_epoch ASC, event_id ASC
        LIMIT ?
        """,
        (workflow, run_id, max(int(limit or 100), 1)),
    ).fetchall()
    return [_event_row_to_dict(row) for row in rows]


def engine_events_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    run_id: str | None = None,
    work_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 100,
    order: str = "desc",
) -> list[dict[str, Any]]:
    conditions = ["workflow=?"]
    params: list[Any] = [workflow]
    if run_id:
        conditions.append("run_id=?")
        params.append(run_id)
    if work_id:
        conditions.append("work_id=?")
        params.append(work_id)
    if event_type:
        conditions.append("event_type=?")
        params.append(event_type)
    if severity:
        conditions.append("severity=?")
        params.append(severity)
    order_sql = "ASC" if str(order).lower() == "asc" else "DESC"
    params.append(max(int(limit or 100), 1))
    rows = conn.execute(
        f"""
        SELECT workflow, event_id, run_id, work_id, event_type, severity,
               created_at, created_at_epoch, payload_json
        FROM engine_events
        WHERE {" AND ".join(conditions)}
        ORDER BY created_at_epoch {order_sql}, event_id {order_sql}
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_event_row_to_dict(row) for row in rows]


def read_engine_events_for_run(
    db_path: Path,
    *,
    workflow: str,
    run_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return []
    try:
        if not _table_exists(conn, "engine_events"):
            return []
        rows = conn.execute(
            """
            SELECT workflow, event_id, run_id, work_id, event_type, severity,
                   created_at, created_at_epoch, payload_json
            FROM engine_events
            WHERE workflow=? AND run_id=?
            ORDER BY created_at_epoch ASC, event_id ASC
            LIMIT ?
            """,
            (workflow, run_id, max(int(limit or 100), 1)),
        ).fetchall()
        return [_event_row_to_dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def read_engine_events(
    db_path: Path,
    *,
    workflow: str,
    run_id: str | None = None,
    work_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 100,
    order: str = "desc",
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return []
    try:
        if not _table_exists(conn, "engine_events"):
            return []
        return engine_events_from_connection(
            conn,
            workflow=workflow,
            run_id=run_id,
            work_id=work_id,
            event_type=event_type,
            severity=severity,
            limit=limit,
            order=order,
        )
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def engine_event_stats_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    now_epoch: float,
    retention: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_engine_state(conn)
    retention_cfg = dict(retention or {})
    row = conn.execute(
        """
        SELECT COUNT(*), MIN(created_at_epoch), MAX(created_at_epoch)
        FROM engine_events
        WHERE workflow=?
        """,
        (workflow,),
    ).fetchone()
    total = int((row or [0])[0] or 0)
    oldest_epoch = None if row is None or row[1] is None else float(row[1])
    newest_epoch = None if row is None or row[2] is None else float(row[2])
    oldest_at = None
    newest_at = None
    if oldest_epoch is not None:
        oldest_at = conn.execute(
            """
            SELECT created_at
            FROM engine_events
            WHERE workflow=? AND created_at_epoch=?
            ORDER BY event_id ASC
            LIMIT 1
            """,
            (workflow, oldest_epoch),
        ).fetchone()[0]
    if newest_epoch is not None:
        newest_at = conn.execute(
            """
            SELECT created_at
            FROM engine_events
            WHERE workflow=? AND created_at_epoch=?
            ORDER BY event_id DESC
            LIMIT 1
            """,
            (workflow, newest_epoch),
        ).fetchone()[0]
    by_type = {
        str(event_type): int(count or 0)
        for event_type, count in conn.execute(
            """
            SELECT event_type, COUNT(*)
            FROM engine_events
            WHERE workflow=?
            GROUP BY event_type
            ORDER BY COUNT(*) DESC, event_type ASC
            """,
            (workflow,),
        ).fetchall()
    }
    by_severity = {
        str(severity): int(count or 0)
        for severity, count in conn.execute(
            """
            SELECT severity, COUNT(*)
            FROM engine_events
            WHERE workflow=?
            GROUP BY severity
            ORDER BY COUNT(*) DESC, severity ASC
            """,
            (workflow,),
        ).fetchall()
    }
    oldest_age_seconds = None
    if oldest_epoch is not None:
        oldest_age_seconds = max(float(now_epoch) - oldest_epoch, 0)
    max_age_seconds = retention_cfg.get("max_age_seconds")
    max_rows = retention_cfg.get("max_rows")
    excess_rows = max(total - int(max_rows), 0) if max_rows is not None else 0
    age_overdue = bool(
        max_age_seconds is not None
        and oldest_age_seconds is not None
        and oldest_age_seconds > float(max_age_seconds)
    )
    overdue = bool(excess_rows > 0 or age_overdue)
    return {
        "workflow": workflow,
        "total_events": total,
        "oldest_event_at": oldest_at,
        "oldest_event_epoch": oldest_epoch,
        "oldest_age_seconds": oldest_age_seconds,
        "newest_event_at": newest_at,
        "newest_event_epoch": newest_epoch,
        "by_type": by_type,
        "by_severity": by_severity,
        "retention": {
            **retention_cfg,
            "excess_rows": excess_rows,
            "age_overdue": age_overdue,
            "overdue": overdue,
        },
    }


def read_engine_event_stats(
    db_path: Path,
    *,
    workflow: str,
    now_epoch: float,
    retention: dict[str, Any] | None = None,
) -> dict[str, Any]:
    empty_retention = dict(retention or {})
    empty = {
        "workflow": workflow,
        "total_events": 0,
        "oldest_event_at": None,
        "oldest_event_epoch": None,
        "oldest_age_seconds": None,
        "newest_event_at": None,
        "newest_event_epoch": None,
        "by_type": {},
        "by_severity": {},
        "retention": {**empty_retention, "excess_rows": 0, "age_overdue": False, "overdue": False},
    }
    if not db_path.exists():
        return empty
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return empty
    try:
        if not _table_exists(conn, "engine_events"):
            return empty
        return engine_event_stats_from_connection(
            conn,
            workflow=workflow,
            now_epoch=now_epoch,
            retention=retention,
        )
    except sqlite3.OperationalError:
        return empty
    finally:
        conn.close()


def prune_engine_events_to_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    now_epoch: float,
    max_age_seconds: float | None = None,
    max_rows: int | None = None,
) -> dict[str, Any]:
    init_engine_state(conn)
    before_changes = conn.total_changes
    if max_age_seconds is not None:
        cutoff_epoch = float(now_epoch) - max(float(max_age_seconds), 0)
        conn.execute(
            "DELETE FROM engine_events WHERE workflow=? AND created_at_epoch < ?",
            (workflow, cutoff_epoch),
        )
    if max_rows is not None:
        keep_rows = max(int(max_rows), 0)
        conn.execute(
            """
            DELETE FROM engine_events
            WHERE workflow=? AND event_id IN (
              SELECT event_id
              FROM engine_events
              WHERE workflow=?
              ORDER BY created_at_epoch DESC, event_id DESC
              LIMIT -1 OFFSET ?
            )
            """,
            (workflow, workflow, keep_rows),
        )
    deleted = conn.total_changes - before_changes
    remaining = conn.execute(
        "SELECT COUNT(*) FROM engine_events WHERE workflow=?",
        (workflow,),
    ).fetchone()[0]
    return {
        "workflow": workflow,
        "deleted": int(deleted or 0),
        "remaining": int(remaining or 0),
        "max_age_seconds": max_age_seconds,
        "max_rows": max_rows,
    }
