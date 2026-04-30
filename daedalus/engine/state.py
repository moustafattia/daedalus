from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .scheduler import build_scheduler_payload
from .sqlite import connect_daedalus_db


ENGINE_STATE_TABLES = (
    "engine_work_items",
    "engine_running_work",
    "engine_retry_queue",
    "engine_runtime_sessions",
    "engine_runtime_totals",
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


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


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

        CREATE INDEX IF NOT EXISTS idx_engine_running_workflow_status
          ON engine_running_work(workflow, worker_status);
        CREATE INDEX IF NOT EXISTS idx_engine_retry_workflow_due
          ON engine_retry_queue(workflow, due_at_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_runtime_sessions_thread
          ON engine_runtime_sessions(workflow, thread_id);
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
              cancel_requested, cancel_reason, thread_id, turn_id, updated_at, updated_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow,
                work_id,
                entry.get("worker_id"),
                int(entry.get("attempt") or 0),
                entry.get("worker_status") or "running",
                float(entry.get("started_at_epoch") or now_epoch),
                float(entry.get("heartbeat_at_epoch") or entry.get("started_at_epoch") or now_epoch),
                1 if entry.get("cancel_requested") else 0,
                entry.get("cancel_reason"),
                entry.get("thread_id"),
                entry.get("turn_id"),
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
              workflow, work_id, attempt, due_at_epoch, error, current_attempt, delay_type, updated_at, updated_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow,
                work_id,
                int(entry.get("attempt") or 0),
                float(entry.get("due_at_epoch") or now_epoch),
                entry.get("error"),
                entry.get("current_attempt"),
                entry.get("delay_type") or "failure",
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
                "updated_at",
                "updatedAt",
            }
        }
        conn.execute(
            """
            INSERT INTO engine_runtime_sessions (
              workflow, work_id, session_name, runtime_name, runtime_kind, session_id, thread_id, turn_id,
              status, cancel_requested, cancel_reason, metadata_json, updated_at, updated_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    for row in conn.execute(
        """
        SELECT r.work_id, w.identifier, w.state, r.worker_id, r.attempt, r.worker_status,
               r.started_at_epoch, r.heartbeat_at_epoch, r.cancel_requested, r.cancel_reason,
               r.thread_id, r.turn_id
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
        ) = row
        running_entries[str(work_id)] = {
            "issue_id": str(work_id),
            "identifier": identifier,
            "state": state,
            "worker_id": worker_id,
            "attempt": int(attempt or 0),
            "worker_status": worker_status or "running",
            "started_at_epoch": float(started_at_epoch or now_epoch),
            "heartbeat_at_epoch": float(heartbeat_at_epoch or started_at_epoch or now_epoch),
            "cancel_requested": bool(cancel_requested),
            "cancel_reason": cancel_reason,
            "thread_id": thread_id,
            "turn_id": turn_id,
        }

    retry_entries: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        """
        SELECT q.work_id, w.identifier, q.attempt, q.due_at_epoch, q.error, q.current_attempt, q.delay_type
        FROM engine_retry_queue q
        LEFT JOIN engine_work_items w ON w.workflow = q.workflow AND w.work_id = q.work_id
        WHERE q.workflow=?
        """,
        (workflow,),
    ).fetchall():
        work_id, identifier, attempt, due_at_epoch, error, current_attempt, delay_type = row
        retry_entries[str(work_id)] = {
            "issue_id": str(work_id),
            "identifier": identifier,
            "attempt": int(attempt or 0),
            "due_at_epoch": float(due_at_epoch or now_epoch),
            "error": error,
            "current_attempt": current_attempt,
            "delay_type": delay_type or "failure",
        }

    codex_threads: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        """
        SELECT s.work_id, w.identifier, s.session_name, s.runtime_name, s.runtime_kind, s.session_id,
               s.thread_id, s.turn_id, s.status, s.cancel_requested, s.cancel_reason, s.metadata_json, s.updated_at
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
        if not engine_state_tables_exist(conn):
            return None
        return _scheduler_state_from_connection(conn, workflow=workflow, now_iso=now_iso, now_epoch=now_epoch)
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
