from __future__ import annotations

import calendar
import json
import sqlite3
import time
from typing import Any


def iso_to_epoch(value: str | None) -> int | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return int(calendar.timegm(time.strptime(value, fmt)))
        except Exception:
            continue
    return None


def epoch_to_iso(value: int | float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def init_engine_leases(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
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

        CREATE INDEX IF NOT EXISTS idx_leases_scope_key ON leases(lease_scope, lease_key);
        CREATE INDEX IF NOT EXISTS idx_leases_owner ON leases(owner_instance_id);
        """
    )


def acquire_engine_lease(
    conn: sqlite3.Connection,
    *,
    lease_scope: str,
    lease_key: str,
    owner_instance_id: str,
    owner_role: str,
    now_iso: str,
    ttl_seconds: int = 60,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_engine_leases(conn)
    now_epoch = iso_to_epoch(now_iso) or int(time.time())
    expires_iso = epoch_to_iso(now_epoch + ttl_seconds)
    lease_id = f"lease:{lease_scope}:{lease_key}"
    row = conn.execute(
        "SELECT owner_instance_id, expires_at, released_at FROM leases WHERE lease_scope=? AND lease_key=?",
        (lease_scope, lease_key),
    ).fetchone()
    if row:
        current_owner, expires_at, released_at = row
        expires_at_epoch = iso_to_epoch(expires_at)
        if not released_at and expires_at_epoch and expires_at_epoch > now_epoch and current_owner != owner_instance_id:
            return {"acquired": False, "lease_id": lease_id, "owner_instance_id": current_owner}
        conn.execute(
            """
            UPDATE leases
            SET owner_instance_id=?, owner_role=?, acquired_at=?, expires_at=?,
                released_at=NULL, release_reason=NULL, metadata_json=?
            WHERE lease_scope=? AND lease_key=?
            """,
            (
                owner_instance_id,
                owner_role,
                now_iso,
                expires_iso,
                json.dumps(metadata, sort_keys=True) if metadata else None,
                lease_scope,
                lease_key,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO leases (
              lease_id, lease_scope, lease_key, owner_instance_id, owner_role,
              acquired_at, expires_at, released_at, release_reason, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (
                lease_id,
                lease_scope,
                lease_key,
                owner_instance_id,
                owner_role,
                now_iso,
                expires_iso,
                json.dumps(metadata, sort_keys=True) if metadata else None,
            ),
        )
    return {
        "acquired": True,
        "lease_id": lease_id,
        "owner_instance_id": owner_instance_id,
        "expires_at": expires_iso,
    }


def release_engine_lease(
    conn: sqlite3.Connection,
    *,
    lease_scope: str,
    lease_key: str,
    owner_instance_id: str,
    now_iso: str,
    release_reason: str | None = None,
) -> dict[str, Any]:
    init_engine_leases(conn)
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
    return {
        "released": True,
        "lease_id": f"lease:{lease_scope}:{lease_key}",
        "owner_instance_id": owner_instance_id,
    }


def read_engine_lease(
    conn: sqlite3.Connection,
    *,
    lease_scope: str,
    lease_key: str,
    now_epoch: int | float,
    heartbeat_at: str | None = None,
    active_owner_instance_id: str | None = None,
    stale_after_seconds: int = 120,
) -> dict[str, Any]:
    init_engine_leases(conn)
    row = conn.execute(
        """
        SELECT lease_scope, lease_key, owner_instance_id, owner_role, acquired_at, expires_at,
               released_at, release_reason, metadata_json
        FROM leases
        WHERE lease_scope=? AND lease_key=?
        """,
        (lease_scope, lease_key),
    ).fetchone()
    if not row:
        return {
            "owner_instance_id": None,
            "owner_role": None,
            "acquired_at": None,
            "expires_at": None,
            "released_at": None,
            "release_reason": None,
            "heartbeat_age_seconds": None,
            "expired": False,
            "stale": True,
            "stale_reasons": ["lease-missing"],
            "metadata": {},
        }
    (
        _scope,
        _key,
        owner_instance_id,
        owner_role,
        acquired_at,
        expires_at,
        released_at,
        release_reason,
        metadata_json,
    ) = row
    expires_epoch = iso_to_epoch(expires_at)
    heartbeat_epoch = iso_to_epoch(heartbeat_at)
    heartbeat_age_seconds = (
        max(0, int(now_epoch) - heartbeat_epoch)
        if now_epoch is not None and heartbeat_epoch is not None
        else None
    )
    stale_reasons: list[str] = []
    if released_at:
        stale_reasons.append("lease-released")
    if expires_epoch is not None and now_epoch is not None and int(now_epoch) > expires_epoch:
        stale_reasons.append("lease-expired")
    if heartbeat_age_seconds is not None and heartbeat_age_seconds > stale_after_seconds:
        stale_reasons.append("heartbeat-old")
    if active_owner_instance_id and owner_instance_id != active_owner_instance_id:
        stale_reasons.append("owner-mismatch")
    try:
        metadata = json.loads(metadata_json) if metadata_json else {}
    except json.JSONDecodeError:
        metadata = {}
    return {
        "owner_instance_id": owner_instance_id,
        "owner_role": owner_role,
        "acquired_at": acquired_at,
        "expires_at": expires_at,
        "released_at": released_at,
        "release_reason": release_reason,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "expired": bool(released_at or (expires_epoch is not None and now_epoch is not None and int(now_epoch) > expires_epoch)),
        "stale": bool(stale_reasons),
        "stale_reasons": stale_reasons,
        "metadata": metadata,
    }
