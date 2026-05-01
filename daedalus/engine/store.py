from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .leases import (
    acquire_engine_lease,
    init_engine_leases,
    read_engine_lease,
    release_engine_lease,
)
from .sqlite import connect_daedalus_db
from .state import (
    ENGINE_STATE_TABLES,
    engine_state_tables_exist,
    engine_run_from_connection,
    finish_engine_run_to_connection,
    init_engine_state,
    latest_engine_runs_from_connection,
    load_engine_scheduler_state_from_connection,
    read_engine_scheduler_state,
    save_engine_scheduler_state_to_connection,
    start_engine_run_to_connection,
)


def _default_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class EngineStore:
    """Workflow-scoped API for shared Daedalus engine state.

    Workflows should depend on this class instead of reaching directly into
    SQLite tables. That keeps engine-owned state changes transactional and
    leaves workflow packages focused on policy.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        workflow: str,
        now_iso: Callable[[], str] = _default_now_iso,
        now_epoch: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = Path(db_path)
        self.workflow = workflow
        self._now_iso = now_iso
        self._now_epoch = now_epoch

    def connect(self) -> sqlite3.Connection:
        conn = connect_daedalus_db(self.db_path)
        init_engine_state(conn)
        init_engine_leases(conn)
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def load_scheduler(self) -> dict[str, Any]:
        conn = self.connect()
        try:
            return load_engine_scheduler_state_from_connection(
                conn,
                workflow=self.workflow,
                now_iso=self._now_iso(),
                now_epoch=self._now_epoch(),
            )
        finally:
            conn.close()

    def read_scheduler(self) -> dict[str, Any] | None:
        return read_engine_scheduler_state(
            self.db_path,
            workflow=self.workflow,
            now_iso=self._now_iso(),
            now_epoch=self._now_epoch(),
        )

    def save_scheduler(
        self,
        *,
        retry_entries: dict[str, dict[str, Any]],
        running_entries: dict[str, dict[str, Any]],
        codex_totals: dict[str, Any] | None,
        codex_threads: dict[str, dict[str, Any]],
        now_iso: str | None = None,
        now_epoch: float | None = None,
    ) -> None:
        with self.transaction() as conn:
            save_engine_scheduler_state_to_connection(
                conn,
                workflow=self.workflow,
                retry_entries=retry_entries,
                running_entries=running_entries,
                codex_totals=codex_totals,
                codex_threads=codex_threads,
                now_iso=now_iso or self._now_iso(),
                now_epoch=self._now_epoch() if now_epoch is None else now_epoch,
            )

    def acquire_lease(
        self,
        *,
        lease_scope: str,
        lease_key: str,
        owner_instance_id: str,
        owner_role: str,
        ttl_seconds: int = 60,
        now_iso: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            return acquire_engine_lease(
                conn,
                lease_scope=lease_scope,
                lease_key=lease_key,
                owner_instance_id=owner_instance_id,
                owner_role=owner_role,
                now_iso=now_iso or self._now_iso(),
                ttl_seconds=ttl_seconds,
                metadata=metadata,
            )

    def release_lease(
        self,
        *,
        lease_scope: str,
        lease_key: str,
        owner_instance_id: str,
        now_iso: str | None = None,
        release_reason: str | None = None,
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            return release_engine_lease(
                conn,
                lease_scope=lease_scope,
                lease_key=lease_key,
                owner_instance_id=owner_instance_id,
                now_iso=now_iso or self._now_iso(),
                release_reason=release_reason,
            )

    def lease_status(
        self,
        *,
        lease_scope: str,
        lease_key: str,
        heartbeat_at: str | None = None,
        active_owner_instance_id: str | None = None,
        stale_after_seconds: int = 120,
    ) -> dict[str, Any]:
        conn = self.connect()
        try:
            return read_engine_lease(
                conn,
                lease_scope=lease_scope,
                lease_key=lease_key,
                now_epoch=self._now_epoch(),
                heartbeat_at=heartbeat_at,
                active_owner_instance_id=active_owner_instance_id,
                stale_after_seconds=stale_after_seconds,
            )
        finally:
            conn.close()

    def start_run(
        self,
        *,
        mode: str,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
        selected_count: int = 0,
        completed_count: int = 0,
        now_iso: str | None = None,
        now_epoch: float | None = None,
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            return start_engine_run_to_connection(
                conn,
                workflow=self.workflow,
                mode=mode,
                now_iso=now_iso or self._now_iso(),
                now_epoch=self._now_epoch() if now_epoch is None else now_epoch,
                run_id=run_id,
                selected_count=selected_count,
                completed_count=completed_count,
                metadata=metadata,
            )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        selected_count: int | None = None,
        completed_count: int | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        now_iso: str | None = None,
        now_epoch: float | None = None,
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            return finish_engine_run_to_connection(
                conn,
                workflow=self.workflow,
                run_id=run_id,
                status=status,
                now_iso=now_iso or self._now_iso(),
                now_epoch=self._now_epoch() if now_epoch is None else now_epoch,
                selected_count=selected_count,
                completed_count=completed_count,
                error=error,
                metadata=metadata,
            )

    def complete_run(
        self,
        run_id: str,
        *,
        selected_count: int | None = None,
        completed_count: int | None = None,
        metadata: dict[str, Any] | None = None,
        now_iso: str | None = None,
        now_epoch: float | None = None,
    ) -> dict[str, Any]:
        return self.finish_run(
            run_id,
            status="completed",
            selected_count=selected_count,
            completed_count=completed_count,
            metadata=metadata,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )

    def fail_run(
        self,
        run_id: str,
        *,
        error: str,
        selected_count: int | None = None,
        completed_count: int | None = None,
        metadata: dict[str, Any] | None = None,
        now_iso: str | None = None,
        now_epoch: float | None = None,
    ) -> dict[str, Any]:
        return self.finish_run(
            run_id,
            status="failed",
            selected_count=selected_count,
            completed_count=completed_count,
            error=error,
            metadata=metadata,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )

    def latest_runs(self, *, limit: int = 10) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            return latest_engine_runs_from_connection(conn, workflow=self.workflow, limit=limit)
        finally:
            conn.close()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        conn = self.connect()
        try:
            return engine_run_from_connection(conn, workflow=self.workflow, run_id=run_id)
        finally:
            conn.close()

    def doctor(self, *, stale_running_seconds: int = 600) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        try:
            conn = self.connect()
        except Exception as exc:
            return [{"name": "engine-db", "status": "fail", "detail": f"{type(exc).__name__}: {exc}"}]
        try:
            missing_tables = [
                table
                for table in (*ENGINE_STATE_TABLES, "leases")
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
            ]
            checks.append(
                {
                    "name": "engine-schema",
                    "status": "pass" if not missing_tables and engine_state_tables_exist(conn) else "fail",
                    "detail": "ok" if not missing_tables else "missing: " + ", ".join(missing_tables),
                }
            )

            now_epoch = self._now_epoch()
            stale_running = conn.execute(
                """
                SELECT work_id, heartbeat_at_epoch
                FROM engine_running_work
                WHERE workflow=? AND heartbeat_at_epoch < ?
                ORDER BY heartbeat_at_epoch ASC
                LIMIT 10
                """,
                (self.workflow, now_epoch - stale_running_seconds),
            ).fetchall()
            checks.append(
                {
                    "name": "engine-running-work",
                    "status": "warn" if stale_running else "pass",
                    "detail": (
                        f"{len(stale_running)} stale running work item(s)"
                        if stale_running
                        else "no stale running work"
                    ),
                    "items": [row[0] for row in stale_running],
                }
            )

            retry_count = conn.execute(
                "SELECT COUNT(*) FROM engine_retry_queue WHERE workflow=?",
                (self.workflow,),
            ).fetchone()[0]
            checks.append(
                {
                    "name": "engine-retry-queue",
                    "status": "pass",
                    "detail": f"{int(retry_count or 0)} queued retry item(s)",
                }
            )

            stale_runs = conn.execute(
                """
                SELECT run_id, mode, started_at, started_at_epoch
                FROM engine_runs
                WHERE workflow=? AND status='running' AND completed_at IS NULL AND started_at_epoch < ?
                ORDER BY started_at_epoch ASC
                LIMIT 10
                """,
                (self.workflow, now_epoch - stale_running_seconds),
            ).fetchall()
            stale_run_details = [
                {
                    "run_id": row[0],
                    "mode": row[1],
                    "started_at": row[2],
                    "age_seconds": max(int(now_epoch - float(row[3] or now_epoch)), 0),
                    "suggested_recovery": f"inspect with `hermes daedalus runs show {row[0]}`",
                }
                for row in stale_runs
            ]
            checks.append(
                {
                    "name": "engine-runs",
                    "status": "warn" if stale_runs else "pass",
                    "detail": (
                        f"{len(stale_runs)} stale running engine run(s); "
                        f"oldest_age_seconds={stale_run_details[0]['age_seconds'] if stale_run_details else 0}"
                        if stale_runs
                        else "no stale running engine runs"
                    ),
                    "items": [row[0] for row in stale_runs],
                    "details": stale_run_details,
                }
            )

            invalid_sessions = conn.execute(
                """
                SELECT work_id
                FROM engine_runtime_sessions
                WHERE workflow=? AND (thread_id IS NULL OR thread_id = '')
                LIMIT 10
                """,
                (self.workflow,),
            ).fetchall()
            checks.append(
                {
                    "name": "engine-runtime-sessions",
                    "status": "fail" if invalid_sessions else "pass",
                    "detail": (
                        f"{len(invalid_sessions)} runtime session(s) missing thread_id"
                        if invalid_sessions
                        else "runtime sessions have valid thread mappings"
                    ),
                    "items": [row[0] for row in invalid_sessions],
                }
            )
            return checks
        except Exception as exc:
            checks.append({"name": "engine-state", "status": "fail", "detail": f"{type(exc).__name__}: {exc}"})
            return checks
        finally:
            conn.close()
