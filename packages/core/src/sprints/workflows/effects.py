"""Idempotency keys and ledger entries for workflow side effects."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from sprints.engine import EngineStore
from sprints.core.config import WorkflowConfig
from sprints.core.paths import runtime_paths

_TERMINAL_EFFECT_STATUSES = {"succeeded", "skipped"}


def side_effect_key(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    operation: str,
    target: str,
    payload: dict[str, Any] | None = None,
) -> str:
    lane_id = str(lane.get("lane_id") or "").strip()
    canonical = {
        "workflow": config.workflow_name,
        "lane_id": lane_id,
        "operation": operation,
        "target": target,
        "payload": _stable(payload or {}),
    }
    digest = hashlib.sha256(
        json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()[:16]
    return (
        "sprints:"
        f"{_slug(config.workflow_name)}:"
        f"{_slug(lane_id)}:"
        f"{_slug(operation)}:"
        f"{_slug(target)}:"
        f"{digest}"
    )


def completed_side_effect(
    *, config: WorkflowConfig, lane: dict[str, Any], key: str
) -> dict[str, Any] | None:
    ledger = lane_mapping(lane, "side_effects")
    entry = ledger.get(key)
    if isinstance(entry, dict) and str(entry.get("status") or "") in {
        "succeeded",
        "skipped",
    }:
        return entry
    for status in ("succeeded", "skipped"):
        event = _engine_store(config).event(_side_effect_event_id(key, status))
        if not event:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        entry = payload.get("side_effect") if isinstance(payload, dict) else {}
        if isinstance(entry, dict):
            _record_lane_side_effect(lane=lane, entry=entry)
            return entry
    return None


def side_effect_completed(
    *, config: WorkflowConfig, lane: dict[str, Any], key: str
) -> bool:
    return completed_side_effect(config=config, lane=lane, key=key) is not None


def record_side_effect_started(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    key: str,
    operation: str,
    target: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _record_side_effect(
        config=config,
        lane=lane,
        key=key,
        operation=operation,
        target=target,
        status="started",
        payload=payload,
    )


def record_side_effect_succeeded(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    key: str,
    operation: str,
    target: str,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _record_side_effect(
        config=config,
        lane=lane,
        key=key,
        operation=operation,
        target=target,
        status="succeeded",
        payload=payload,
        result=result,
    )


def record_side_effect_failed(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    key: str,
    operation: str,
    target: str,
    payload: dict[str, Any] | None = None,
    error: str,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _record_side_effect(
        config=config,
        lane=lane,
        key=key,
        operation=operation,
        target=target,
        status="failed",
        payload=payload,
        result=result,
        error=error,
        severity="warning",
    )


def record_side_effect_skipped(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    key: str,
    operation: str,
    target: str,
    payload: dict[str, Any] | None = None,
    reason: str,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _record_side_effect(
        config=config,
        lane=lane,
        key=key,
        operation=operation,
        target=target,
        status="skipped",
        payload=payload,
        result={**dict(result or {}), "reason": reason},
    )


def side_effect_marker(key: str) -> str:
    return f"<!-- sprints:idempotency-key:{key} -->"


def with_side_effect_marker(body: str, key: str) -> str:
    marker = side_effect_marker(key)
    text = str(body or "").strip()
    if marker in text:
        return text
    return f"{text}\n\n{marker}".strip()


def side_effects_summary(
    lane: dict[str, Any], *, limit: int = 5
) -> list[dict[str, Any]]:
    effects = lane_mapping(lane, "side_effects")
    entries = [entry for entry in effects.values() if isinstance(entry, dict)]
    entries.sort(
        key=lambda entry: str(entry.get("updated_at") or entry.get("created_at") or "")
    )
    return [
        {
            key: entry.get(key)
            for key in (
                "key",
                "operation",
                "target",
                "status",
                "updated_at",
                "error",
            )
            if entry.get(key) not in (None, "", [], {})
        }
        for entry in entries[-limit:]
    ]


def _record_side_effect(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    key: str,
    operation: str,
    target: str,
    status: str,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    severity: str = "info",
) -> dict[str, Any]:
    now = _now_iso()
    current = lane_mapping(lane, "side_effects").get(key)
    created_at = (
        str(current.get("created_at") or "").strip()
        if isinstance(current, dict)
        else ""
    )
    entry = {
        "key": key,
        "operation": operation,
        "target": target,
        "status": status,
        "payload": _stable(payload or {}),
        "result": _stable(result or {}),
        "error": error,
        "created_at": created_at or now,
        "updated_at": now,
    }
    entry = {
        item_key: value
        for item_key, value in entry.items()
        if value not in (None, "", [], {})
    }
    _record_lane_side_effect(lane=lane, entry=entry)
    _engine_store(config).append_event(
        event_type=f"{config.workflow_name}.side_effect.{status}",
        event_id=_side_effect_event_id(key, status)
        if status in {"started", "succeeded", "skipped"}
        else None,
        work_id=str(lane.get("lane_id") or ""),
        run_id=_lane_run_id(lane),
        severity=severity,
        payload={
            "idempotency_key": key,
            "side_effect": entry,
        },
    )
    return entry


def _record_lane_side_effect(*, lane: dict[str, Any], entry: dict[str, Any]) -> None:
    lane_mapping(lane, "side_effects")[str(entry.get("key") or "")] = entry


def _side_effect_event_id(key: str, status: str) -> str:
    return f"side-effect:{key}:{status}"


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable(value[key])
            for key in sorted(value)
            if value[key] not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [_stable(item) for item in value if item not in (None, "", [], {})]
    if isinstance(value, tuple):
        return [_stable(item) for item in value if item not in (None, "", [], {})]
    if isinstance(value, set):
        return sorted(_stable(item) for item in value if item not in (None, "", [], {}))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return text[:80] or "item"


def _engine_store(config: WorkflowConfig) -> EngineStore:
    return EngineStore(
        db_path=runtime_paths(config.workflow_root)["db_path"],
        workflow=config.workflow_name,
    )


def _lane_run_id(lane: dict[str, Any]) -> str | None:
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    value = session.get("run_id")
    text = str(value or "").strip()
    return text or None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def lane_mapping(lane: dict[str, Any], key: str) -> dict[str, Any]:
    value = lane.get(key)
    if isinstance(value, dict):
        return value
    lane[key] = {}
    return lane[key]
