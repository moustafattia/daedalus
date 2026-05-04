from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .db import (
    ENGINE_SCHEDULER_TABLES,
    connect_sprints_db,
    init_engine_state,
    table_exists as _table_exists,
)
from .scheduler import build_scheduler_payload


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


def _open_engine_state_db(db_path: Path) -> sqlite3.Connection:
    conn = connect_sprints_db(db_path)
    init_engine_state(conn)
    return conn


def _work_item_from_entry(
    *, workflow: str, work_id: str, entry: dict[str, Any]
) -> dict[str, Any]:
    metadata = dict(entry.get("metadata") or {})
    for key in (
        "issue_number",
        "issueNumber",
        "worktree",
        "last_event",
        "last_message",
    ):
        if entry.get(key) is not None:
            metadata[key] = entry.get(key)
    return {
        "workflow": workflow,
        "work_id": work_id,
        "identifier": entry.get("identifier") or work_id,
        "state": entry.get("state")
        or entry.get("workflow_state")
        or entry.get("status"),
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


def upsert_engine_work_item_to_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    work_id: str,
    entry: dict[str, Any],
    now_iso: str,
    now_epoch: float,
) -> dict[str, Any]:
    init_engine_state(conn)
    normalized_work_id = str(
        entry.get("work_id") or entry.get("issue_id") or work_id or ""
    ).strip()
    if not normalized_work_id:
        raise ValueError("engine work item requires work_id")
    _upsert_work_item(
        conn,
        workflow=workflow,
        work_id=normalized_work_id,
        entry=entry,
        now_iso=now_iso,
        now_epoch=now_epoch,
    )
    item = _work_item_from_entry(
        workflow=workflow, work_id=normalized_work_id, entry=entry
    )
    return {
        **item,
        "metadata": dict(item.get("metadata") or {}),
        "updated_at": now_iso,
        "updated_at_epoch": now_epoch,
    }


def engine_work_items_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    state: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    init_engine_state(conn)
    where = "WHERE workflow=?"
    params: list[Any] = [workflow]
    if state:
        where += " AND state=?"
        params.append(state)
    params.append(max(int(limit or 200), 1))
    rows = conn.execute(
        f"""
        SELECT workflow, work_id, identifier, state, title, url, source,
               metadata_json, updated_at, updated_at_epoch
        FROM engine_work_items
        {where}
        ORDER BY updated_at_epoch DESC, work_id ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        {
            "workflow": row[0],
            "work_id": row[1],
            "identifier": row[2],
            "state": row[3],
            "title": row[4],
            "url": row[5],
            "source": row[6],
            "metadata": _json_loads(row[7]) or {},
            "updated_at": row[8],
            "updated_at_epoch": row[9],
        }
        for row in rows
    ]


def save_engine_scheduler_state_to_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    retry_entries: dict[str, dict[str, Any]],
    running_entries: dict[str, dict[str, Any]],
    runtime_totals: dict[str, Any] | None,
    runtime_sessions: dict[str, dict[str, Any]],
    now_iso: str,
    now_epoch: float,
) -> None:
    init_engine_state(conn)
    conn.execute("DELETE FROM engine_running_work WHERE workflow=?", (workflow,))
    conn.execute("DELETE FROM engine_retry_queue WHERE workflow=?", (workflow,))
    conn.execute("DELETE FROM engine_runtime_sessions WHERE workflow=?", (workflow,))

    for work_id, entry in sorted(
        running_entries.items(), key=lambda item: str(item[0])
    ):
        work_id = str(entry.get("issue_id") or work_id or "").strip()
        if not work_id:
            continue
        _upsert_work_item(
            conn,
            workflow=workflow,
            work_id=work_id,
            entry=entry,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )
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
                entry.get("run_id"),
                now_iso,
                now_epoch,
            ),
        )

    for work_id, entry in sorted(retry_entries.items(), key=lambda item: str(item[0])):
        upsert_engine_retry_to_connection(
            conn,
            workflow=workflow,
            work_id=work_id,
            entry=entry,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )

    for work_id, entry in sorted(
        runtime_sessions.items(), key=lambda item: str(item[0])
    ):
        if not isinstance(entry, dict):
            continue
        work_id = str(entry.get("issue_id") or work_id or "").strip()
        thread_id = str(entry.get("thread_id") or "").strip()
        if not work_id or not thread_id:
            continue
        _upsert_work_item(
            conn,
            workflow=workflow,
            work_id=work_id,
            entry=entry,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )
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
                "updated_at",
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
                entry.get("session_name"),
                entry.get("runtime_name"),
                entry.get("runtime_kind"),
                entry.get("session_id"),
                thread_id,
                entry.get("turn_id"),
                entry.get("status"),
                1 if entry.get("cancel_requested") else 0,
                entry.get("cancel_reason"),
                entry.get("run_id"),
                _json_dumps(metadata),
                entry.get("updated_at") or now_iso,
                now_epoch,
            ),
        )

    totals = dict(runtime_totals or {})
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


def upsert_engine_retry_to_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    work_id: str,
    entry: dict[str, Any],
    now_iso: str,
    now_epoch: float,
) -> dict[str, Any]:
    init_engine_state(conn)
    normalized_work_id = str(entry.get("issue_id") or work_id or "").strip()
    if not normalized_work_id:
        raise ValueError("engine retry entry requires work_id")
    _upsert_work_item(
        conn,
        workflow=workflow,
        work_id=normalized_work_id,
        entry=entry,
        now_iso=now_iso,
        now_epoch=now_epoch,
    )
    due_at_epoch = float(_value_or_default(entry.get("due_at_epoch"), now_epoch))
    attempt = int(entry.get("attempt") or 0)
    current_attempt = entry.get("current_attempt")
    delay_type = str(entry.get("delay_type") or "failure")
    conn.execute(
        """
        INSERT INTO engine_retry_queue (
          workflow, work_id, attempt, due_at_epoch, error, current_attempt, delay_type, run_id, updated_at, updated_at_epoch
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workflow, work_id) DO UPDATE SET
          attempt=excluded.attempt,
          due_at_epoch=excluded.due_at_epoch,
          error=excluded.error,
          current_attempt=excluded.current_attempt,
          delay_type=excluded.delay_type,
          run_id=excluded.run_id,
          updated_at=excluded.updated_at,
          updated_at_epoch=excluded.updated_at_epoch
        """,
        (
            workflow,
            normalized_work_id,
            attempt,
            due_at_epoch,
            entry.get("error"),
            current_attempt,
            delay_type,
            entry.get("run_id"),
            now_iso,
            now_epoch,
        ),
    )
    return {
        "workflow": workflow,
        "work_id": normalized_work_id,
        "attempt": attempt,
        "due_at_epoch": due_at_epoch,
        "error": entry.get("error"),
        "current_attempt": current_attempt,
        "delay_type": delay_type,
        "run_id": entry.get("run_id"),
    }


def clear_engine_retry_to_connection(
    conn: sqlite3.Connection, *, workflow: str, work_id: str
) -> dict[str, Any]:
    init_engine_state(conn)
    normalized_work_id = str(work_id or "").strip()
    if not normalized_work_id:
        raise ValueError("engine retry clear requires work_id")
    cursor = conn.execute(
        "DELETE FROM engine_retry_queue WHERE workflow=? AND work_id=?",
        (workflow, normalized_work_id),
    )
    return {
        "workflow": workflow,
        "work_id": normalized_work_id,
        "cleared": cursor.rowcount > 0,
    }


def engine_due_retries_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    due_at_epoch: float,
    limit: int = 50,
) -> list[dict[str, Any]]:
    init_engine_state(conn)
    rows = conn.execute(
        """
        SELECT q.work_id, w.identifier, w.state, w.title, w.url,
               q.attempt, q.due_at_epoch, q.error, q.current_attempt, q.delay_type, q.run_id,
               q.updated_at, q.updated_at_epoch
        FROM engine_retry_queue q
        LEFT JOIN engine_work_items w ON w.workflow = q.workflow AND w.work_id = q.work_id
        WHERE q.workflow=? AND q.due_at_epoch <= ?
        ORDER BY q.due_at_epoch ASC, q.work_id ASC
        LIMIT ?
        """,
        (workflow, due_at_epoch, max(int(limit or 50), 1)),
    ).fetchall()
    return [
        {
            "workflow": workflow,
            "work_id": str(row[0]),
            "issue_id": str(row[0]),
            "identifier": row[1],
            "state": row[2],
            "title": row[3],
            "url": row[4],
            "attempt": int(row[5] or 0),
            "due_at_epoch": float(_value_or_default(row[6], due_at_epoch)),
            "error": row[7],
            "current_attempt": row[8],
            "delay_type": row[9] or "failure",
            "run_id": row[10],
            "updated_at": row[11],
            "updated_at_epoch": float(_value_or_default(row[12], due_at_epoch)),
        }
        for row in rows
    ]


def upsert_engine_runtime_session_to_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    work_id: str,
    entry: dict[str, Any],
    now_iso: str,
    now_epoch: float,
) -> dict[str, Any]:
    init_engine_state(conn)
    normalized_work_id = str(entry.get("issue_id") or work_id or "").strip()
    if not normalized_work_id:
        raise ValueError("engine runtime session requires work_id")
    _upsert_work_item(
        conn,
        workflow=workflow,
        work_id=normalized_work_id,
        entry=entry,
        now_iso=now_iso,
        now_epoch=now_epoch,
    )
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
            "updated_at",
        }
    }
    updated_at = str(entry.get("updated_at") or now_iso)
    conn.execute(
        """
        INSERT INTO engine_runtime_sessions (
          workflow, work_id, session_name, runtime_name, runtime_kind, session_id, thread_id, turn_id,
          status, cancel_requested, cancel_reason, run_id, metadata_json, updated_at, updated_at_epoch
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workflow, work_id) DO UPDATE SET
          session_name=excluded.session_name,
          runtime_name=excluded.runtime_name,
          runtime_kind=excluded.runtime_kind,
          session_id=excluded.session_id,
          thread_id=excluded.thread_id,
          turn_id=excluded.turn_id,
          status=excluded.status,
          cancel_requested=excluded.cancel_requested,
          cancel_reason=excluded.cancel_reason,
          run_id=excluded.run_id,
          metadata_json=excluded.metadata_json,
          updated_at=excluded.updated_at,
          updated_at_epoch=excluded.updated_at_epoch
        """,
        (
            workflow,
            normalized_work_id,
            entry.get("session_name"),
            entry.get("runtime_name"),
            entry.get("runtime_kind"),
            entry.get("session_id"),
            entry.get("thread_id"),
            entry.get("turn_id"),
            entry.get("status"),
            1 if entry.get("cancel_requested") else 0,
            entry.get("cancel_reason"),
            entry.get("run_id"),
            _json_dumps(metadata),
            updated_at,
            now_epoch,
        ),
    )
    return {
        "workflow": workflow,
        "work_id": normalized_work_id,
        "session_name": entry.get("session_name"),
        "runtime_name": entry.get("runtime_name"),
        "runtime_kind": entry.get("runtime_kind"),
        "session_id": entry.get("session_id"),
        "thread_id": entry.get("thread_id"),
        "turn_id": entry.get("turn_id"),
        "status": entry.get("status"),
        "run_id": entry.get("run_id"),
        "metadata": metadata,
        "updated_at": updated_at,
        "updated_at_epoch": now_epoch,
    }


def engine_runtime_sessions_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    work_id: str | None = None,
    thread_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    init_engine_state(conn)
    conditions = ["s.workflow=?"]
    params: list[Any] = [workflow]
    if work_id:
        conditions.append("s.work_id=?")
        params.append(work_id)
    if thread_id:
        conditions.append("s.thread_id=?")
        params.append(thread_id)
    params.append(max(int(limit or 200), 1))
    rows = conn.execute(
        f"""
        SELECT s.work_id, w.identifier, s.session_name, s.runtime_name, s.runtime_kind, s.session_id,
               s.thread_id, s.turn_id, s.status, s.cancel_requested, s.cancel_reason, s.run_id,
               s.metadata_json, s.updated_at, s.updated_at_epoch
        FROM engine_runtime_sessions s
        LEFT JOIN engine_work_items w ON w.workflow = s.workflow AND w.work_id = s.work_id
        WHERE {" AND ".join(conditions)}
        ORDER BY s.updated_at_epoch DESC, s.work_id ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        {
            "workflow": workflow,
            "work_id": row[0],
            "issue_id": row[0],
            "identifier": row[1],
            "session_name": row[2],
            "runtime_name": row[3],
            "runtime_kind": row[4],
            "session_id": row[5],
            "thread_id": row[6],
            "turn_id": row[7],
            "status": row[8],
            "cancel_requested": bool(row[9]),
            "cancel_reason": row[10],
            "run_id": row[11],
            "metadata": _json_loads(row[12]) or {},
            "updated_at": row[13],
            "updated_at_epoch": float(_value_or_default(row[14], 0.0)),
        }
        for row in rows
    ]


def save_engine_scheduler_state(
    db_path: Path,
    *,
    workflow: str,
    retry_entries: dict[str, dict[str, Any]],
    running_entries: dict[str, dict[str, Any]],
    runtime_totals: dict[str, Any] | None,
    runtime_sessions: dict[str, dict[str, Any]],
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
            runtime_totals=runtime_totals,
            runtime_sessions=runtime_sessions,
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
               r.thread_id, r.turn_id, r.run_id
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
            "heartbeat_at_epoch": float(
                _first_value_or_default(now_epoch, heartbeat_at_epoch, started_at_epoch)
            ),
            "cancel_requested": bool(cancel_requested),
            "cancel_reason": cancel_reason,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "run_id": run_id,
        }

    retry_entries: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        """
        SELECT q.work_id, w.identifier, q.attempt, q.due_at_epoch, q.error, q.current_attempt, q.delay_type, q.run_id
        FROM engine_retry_queue q
        LEFT JOIN engine_work_items w ON w.workflow = q.workflow AND w.work_id = q.work_id
        WHERE q.workflow=?
        """,
        (workflow,),
    ).fetchall():
        (
            work_id,
            identifier,
            attempt,
            due_at_epoch,
            error,
            current_attempt,
            delay_type,
            run_id,
        ) = row
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

    runtime_sessions: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        """
        SELECT s.work_id, w.identifier, s.session_name, s.runtime_name, s.runtime_kind, s.session_id,
               s.thread_id, s.turn_id, s.status, s.cancel_requested, s.cancel_reason, s.run_id, s.metadata_json, s.updated_at
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
        runtime_sessions[str(work_id)] = {
            key: value for key, value in entry.items() if value is not None
        }

    totals_row = conn.execute(
        """
        SELECT input_tokens, output_tokens, total_tokens, turn_count, rate_limits_json
        FROM engine_runtime_totals
        WHERE workflow=?
        """,
        (workflow,),
    ).fetchone()
    runtime_totals: dict[str, Any] = {}
    if totals_row is not None:
        input_tokens, output_tokens, total_tokens, turn_count, rate_limits_json = (
            totals_row
        )
        runtime_totals = {
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "total_tokens": int(total_tokens or 0),
            "turn_count": int(turn_count or 0),
        }
        rate_limits = _json_loads(rate_limits_json)
        if rate_limits is not None:
            runtime_totals["rate_limits"] = rate_limits

    return build_scheduler_payload(
        workflow=workflow,
        retry_entries=retry_entries,
        running_entries=running_entries,
        runtime_totals=runtime_totals,
        runtime_sessions=runtime_sessions,
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
        return _scheduler_state_from_connection(
            conn, workflow=workflow, now_iso=now_iso, now_epoch=now_epoch
        )
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
    return _scheduler_state_from_connection(
        conn, workflow=workflow, now_iso=now_iso, now_epoch=now_epoch
    )


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
        return _scheduler_state_from_connection(
            conn, workflow=workflow, now_iso=now_iso, now_epoch=now_epoch
        )
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
        "completed_at_epoch": None
        if completed_at_epoch is None
        else float(completed_at_epoch),
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
    final_selected_count = (
        current["selected_count"]
        if selected_count is None
        else int(selected_count or 0)
    )
    final_completed_count = (
        current["completed_count"]
        if completed_count is None
        else int(completed_count or 0)
    )
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


def running_engine_runs_from_connection(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    mode: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    init_engine_state(conn)
    conditions = ["workflow=?", "status='running'", "completed_at IS NULL"]
    params: list[Any] = [workflow]
    if mode:
        conditions.append("mode=?")
        params.append(mode)
    params.append(max(int(limit or 200), 1))
    rows = conn.execute(
        f"""
        SELECT workflow, run_id, mode, status, started_at, started_at_epoch,
               completed_at, completed_at_epoch, selected_count, completed_count,
               error, metadata_json
        FROM engine_runs
        WHERE {" AND ".join(conditions)}
        ORDER BY started_at_epoch DESC, run_id DESC
        LIMIT ?
        """,
        params,
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
        "retention": {
            **empty_retention,
            "excess_rows": 0,
            "age_overdue": False,
            "overdue": False,
        },
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
