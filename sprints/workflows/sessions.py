"""Runtime session, actor run, heartbeat, and scheduler projection mechanics."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from engine import EngineStore
from workflows.config import WorkflowConfig
from workflows.paths import runtime_paths

_TERMINAL_LANE_STATUSES = {"complete", "released"}
_RUNTIME_RUNNING_STATUSES = {"running"}
_RUNTIME_FINAL_STATUSES = {"completed", "failed", "interrupted", "blocked"}


def actor_concurrency_usage(
    *, config: WorkflowConfig, lanes: list[dict[str, Any]]
) -> dict[str, int]:
    usage: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()

    def add(
        *,
        actor_name: Any,
        lane_id: Any,
        stage_name: Any,
        run_id: Any = "",
        source: str,
    ) -> None:
        actor = str(actor_name or "").strip()
        if not actor:
            return
        lane_key = str(lane_id or "").strip()
        stage_key = str(stage_name or "").strip()
        run_key = str(run_id or "").strip()
        identity = (
            f"lane:{lane_key}:{stage_key}:{actor}"
            if lane_key
            else f"run:{run_key or source}:{actor}"
        )
        key = (actor, identity)
        if key in seen:
            return
        seen.add(key)
        usage[actor] = usage.get(actor, 0) + 1

    for lane in lanes:
        if not isinstance(lane, dict) or _lane_is_terminal(lane):
            continue
        lane_id = lane.get("lane_id")
        stage_name = _lane_stage(lane)
        if str(lane.get("status") or "").strip() == "running":
            add(
                actor_name=lane.get("actor"),
                lane_id=lane_id,
                stage_name=stage_name,
                source="lane",
            )
        session = (
            lane.get("runtime_session")
            if isinstance(lane.get("runtime_session"), dict)
            else {}
        )
        if runtime_session_is_running(session):
            add(
                actor_name=session.get("actor") or lane.get("actor"),
                lane_id=lane_id,
                stage_name=session.get("stage") or stage_name,
                run_id=session.get("run_id"),
                source="lane_runtime_session",
            )

    for engine_session in _engine_store(config).runtime_sessions(limit=500):
        if not runtime_session_is_running(engine_session):
            continue
        metadata = (
            engine_session.get("metadata")
            if isinstance(engine_session.get("metadata"), dict)
            else {}
        )
        add(
            actor_name=engine_session.get("actor") or metadata.get("actor"),
            lane_id=engine_session.get("work_id"),
            stage_name=engine_session.get("stage") or metadata.get("stage"),
            run_id=engine_session.get("run_id"),
            source="engine_runtime_session",
        )

    for run in _engine_store(config).running_runs(mode="actor", limit=500):
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        add(
            actor_name=metadata.get("actor"),
            lane_id=metadata.get("lane_id"),
            stage_name=metadata.get("stage"),
            run_id=run.get("run_id"),
            source="engine_run",
        )

    return usage


def actor_dispatch_conflicts(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    lane_id: str,
    actor_name: str,
    stage_name: str,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    if not lane_id:
        return [{"source": "lane", "status": "missing_lane_id"}]
    lane_status = str(lane.get("status") or "").strip()
    if lane_status == "running":
        conflicts.append({"source": "lane", "status": lane_status})
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    if runtime_session_is_running(session):
        conflicts.append(
            {
                "source": "lane_runtime_session",
                "run_id": session.get("run_id"),
                "thread_id": session.get("thread_id"),
                "turn_id": session.get("turn_id"),
                "status": session.get("status"),
            }
        )
    for engine_session in _engine_store(config).runtime_sessions(
        work_id=lane_id, limit=5
    ):
        if runtime_session_is_running(engine_session):
            conflicts.append(
                {
                    "source": "engine_runtime_session",
                    "run_id": engine_session.get("run_id"),
                    "thread_id": engine_session.get("thread_id"),
                    "turn_id": engine_session.get("turn_id"),
                    "status": engine_session.get("status"),
                    "updated_at": engine_session.get("updated_at"),
                }
            )
    for run in _engine_store(config).running_runs(mode="actor", limit=200):
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        if str(metadata.get("lane_id") or "").strip() != lane_id:
            continue
        if str(metadata.get("actor") or "").strip() != actor_name:
            continue
        if str(metadata.get("stage") or "").strip() != stage_name:
            continue
        conflicts.append(
            {
                "source": "engine_run",
                "run_id": run.get("run_id"),
                "status": run.get("status"),
                "started_at": run.get("started_at"),
            }
        )
    return _dedupe_conflicts(conflicts)


def record_actor_runtime_start(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    stage_name: str,
    runtime_meta: dict[str, Any],
) -> None:
    started_at = _now_iso()
    session_key, session = _mutable_actor_runtime_session(
        lane=lane, actor_name=actor_name, stage_name=stage_name
    )
    run = _start_engine_actor_run(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        runtime_meta=runtime_meta,
        started_at=started_at,
    )
    session.update(
        {
            **_runtime_meta_payload(runtime_meta),
            "run_id": run["run_id"],
            "session_key": session_key,
            "actor": actor_name,
            "stage": stage_name,
            "attempt": int(lane.get("attempt") or 0),
            "status": "running",
            "started_at": started_at,
            "updated_at": started_at,
        }
    )
    _apply_runtime_session_ids(lane=lane, session=session)
    _set_latest_runtime_session(lane=lane, session_key=session_key, session=session)
    _upsert_engine_runtime_session(config=config, lane=lane)
    append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.runtime_started",
        payload={"runtime_session": session},
    )


def record_actor_runtime_progress(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    runtime_meta: dict[str, Any],
) -> None:
    session_key, session = _current_runtime_session(lane)
    session.update(_runtime_meta_payload(runtime_meta))
    session["status"] = "running"
    session["updated_at"] = _now_iso()
    _apply_runtime_session_ids(lane=lane, session=session)
    _set_latest_runtime_session(lane=lane, session_key=session_key, session=session)
    _upsert_engine_runtime_session(config=config, lane=lane)
    lane["last_progress_at"] = _now_iso()
    append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.runtime_progress",
        payload={"runtime_session": session},
    )


def record_actor_runtime_result(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    runtime_meta: dict[str, Any],
    status: str,
) -> None:
    updated_at = _now_iso()
    session_key, session = _current_runtime_session(lane)
    session.update(_runtime_meta_payload(runtime_meta))
    session["status"] = normalize_runtime_session_status(status)
    session["updated_at"] = updated_at
    _apply_runtime_session_ids(lane=lane, session=session)
    _set_latest_runtime_session(lane=lane, session_key=session_key, session=session)
    _upsert_engine_runtime_session(config=config, lane=lane)
    _finish_engine_actor_run(
        config=config,
        lane=lane,
        status=session["status"],
        error=str(runtime_meta.get("last_message") or "") or None,
        metadata=_runtime_meta_payload(runtime_meta),
        completed_at=updated_at,
    )
    lane["last_progress_at"] = updated_at
    append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.runtime_{status}",
        payload={"runtime_session": session},
    )


def record_actor_runtime_interrupted(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    reason: str,
    message: str,
    age_seconds: int,
) -> None:
    interrupted_at = _now_iso()
    session_key, session = _current_runtime_session(lane)
    session.update(
        {
            "status": "interrupted",
            "interrupted_at": interrupted_at,
            "updated_at": interrupted_at,
            "last_event": reason,
            "last_message": message,
            "age_seconds": age_seconds,
        }
    )
    _apply_runtime_session_ids(lane=lane, session=session)
    _set_latest_runtime_session(lane=lane, session_key=session_key, session=session)
    _upsert_engine_runtime_session(config=config, lane=lane)
    _finish_engine_actor_run(
        config=config,
        lane=lane,
        status="interrupted",
        error=message,
        metadata={"reason": reason, "age_seconds": age_seconds},
        completed_at=interrupted_at,
    )
    lane["last_progress_at"] = interrupted_at
    append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.runtime_interrupted",
        payload={"runtime_session": session, "reason": reason},
        severity="warning",
    )


def save_scheduler_snapshot(*, config: WorkflowConfig, lanes: Any) -> None:
    running_entries: dict[str, dict[str, Any]] = {}
    runtime_entries: dict[str, dict[str, Any]] = {}
    runtime_totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "turn_count": 0,
    }
    last_rate_limits: dict[str, Any] | None = None

    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        lane_id = str(lane.get("lane_id") or "").strip()
        if not lane_id:
            continue
        entry = scheduler_entry(lane)
        status = str(lane.get("status") or "")
        if status == "running":
            running_entries[lane_id] = entry
        session = lane.get("runtime_session")
        if isinstance(session, dict) and runtime_session_has_identity(session):
            runtime_entries[lane_id] = entry
            tokens = (
                session.get("tokens") if isinstance(session.get("tokens"), dict) else {}
            )
            runtime_totals["input_tokens"] += int(tokens.get("input_tokens") or 0)
            runtime_totals["output_tokens"] += int(tokens.get("output_tokens") or 0)
            runtime_totals["total_tokens"] += int(tokens.get("total_tokens") or 0)
            runtime_totals["turn_count"] += int(session.get("turn_count") or 0)
            rate_limits = session.get("rate_limits")
            if isinstance(rate_limits, dict):
                last_rate_limits = rate_limits
    if last_rate_limits is not None:
        runtime_totals["rate_limits"] = last_rate_limits

    _engine_store(config).save_scheduler(
        retry_entries=None,
        running_entries=running_entries,
        runtime_totals=runtime_totals,
        runtime_sessions=runtime_entries,
    )


def runtime_session_key(*, actor_name: str, stage_name: str) -> str:
    actor = str(actor_name or "").strip()
    stage = str(stage_name or "").strip()
    return f"{stage}:{actor}" if actor and stage else actor or stage


def lane_actor_runtime_session(
    lane: dict[str, Any], *, actor_name: str, stage_name: str
) -> dict[str, Any]:
    key = runtime_session_key(actor_name=actor_name, stage_name=stage_name)
    sessions = lane.get("runtime_sessions")
    if isinstance(sessions, dict):
        session = sessions.get(key)
        if isinstance(session, dict):
            return session
    latest = lane.get("runtime_session")
    if isinstance(latest, dict) and _runtime_session_matches(
        latest, actor_name=actor_name, stage_name=stage_name
    ):
        return latest
    return {}


def lane_runtime_session_summaries(lanes: Any) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        sessions = lane.get("runtime_sessions")
        if isinstance(sessions, dict) and sessions:
            for key, session in sessions.items():
                if not isinstance(session, dict):
                    continue
                summaries.append(
                    {
                        **dict(session),
                        "session_key": session.get("session_key") or key,
                        "lane_id": lane.get("lane_id"),
                        "lane_stage": lane.get("stage"),
                        "lane_actor": lane.get("actor"),
                    }
                )
            continue
        latest = lane.get("runtime_session")
        if isinstance(latest, dict):
            summaries.append(
                {
                    **dict(latest),
                    "lane_id": lane.get("lane_id"),
                    "lane_stage": lane.get("stage"),
                    "lane_actor": lane.get("actor"),
                }
            )
    return summaries


def lane_run_id(lane: dict[str, Any]) -> str | None:
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    value = session.get("run_id")
    text = str(value or "").strip()
    return text or None


def runtime_session_has_identity(session: dict[str, Any]) -> bool:
    return bool(
        str(
            session.get("run_id")
            or session.get("thread_id")
            or session.get("session_id")
            or ""
        ).strip()
    )


def runtime_session_is_running(session: dict[str, Any]) -> bool:
    return (
        normalize_runtime_session_status(str(session.get("status") or ""))
        in _RUNTIME_RUNNING_STATUSES
    )


def normalize_runtime_session_status(status: str) -> str:
    text = str(status or "").strip().lower()
    if text in _RUNTIME_RUNNING_STATUSES:
        return "running"
    if text in _RUNTIME_FINAL_STATUSES:
        return text
    return "failed" if text else ""


def runtime_heartbeat(lane: dict[str, Any]) -> dict[str, Any]:
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    path_text = str(session.get("heartbeat_path") or "").strip()
    if not path_text:
        return {}
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        try:
            stat = path.stat()
        except OSError:
            return {"path": path_text, "status": "missing"}
        return {
            "path": path_text,
            "status": "unreadable",
            "updated_at": _epoch_to_iso(stat.st_mtime),
            "updated_at_epoch": stat.st_mtime,
        }
    if not isinstance(payload, dict):
        return {"path": path_text, "status": "invalid"}
    updated_at = str(payload.get("updated_at") or "").strip()
    if not updated_at:
        try:
            stat = path.stat()
        except OSError:
            stat = None
        if stat is not None:
            payload["updated_at"] = _epoch_to_iso(stat.st_mtime)
            payload["updated_at_epoch"] = stat.st_mtime
    payload["path"] = path_text
    return payload


def runtime_process_is_missing(session: dict[str, Any]) -> bool:
    value = session.get("process_id") if isinstance(session, dict) else None
    try:
        process_id = int(value)
    except (TypeError, ValueError):
        return False
    if process_id <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
        except ImportError:
            return False
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, process_id)
        if handle:
            kernel32.CloseHandle(handle)
            return False
        return kernel32.GetLastError() == 87
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False
    return False


def runtime_updated_at(lane: dict[str, Any]) -> str:
    heartbeat = runtime_heartbeat(lane)
    heartbeat_updated_at = str(heartbeat.get("updated_at") or "").strip()
    if heartbeat_updated_at:
        return heartbeat_updated_at
    session = lane.get("runtime_session")
    if isinstance(session, dict):
        return str(session.get("updated_at") or session.get("started_at") or "")
    return ""


def scheduler_entry(lane: dict[str, Any]) -> dict[str, Any]:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    runtime_updated = runtime_updated_at(lane)
    return {
        "issue_id": lane.get("lane_id"),
        "identifier": issue.get("identifier") or lane.get("lane_id"),
        "state": lane.get("status"),
        "title": issue.get("title"),
        "url": issue.get("url"),
        "worker_id": lane.get("actor"),
        "attempt": int(lane.get("attempt") or 0),
        "worker_status": lane.get("status"),
        "started_at_epoch": _iso_to_epoch(
            str(session.get("started_at") or lane.get("last_progress_at") or ""),
            default=time.time(),
        ),
        "heartbeat_at_epoch": _iso_to_epoch(
            str(runtime_updated or lane.get("last_progress_at") or ""),
            default=time.time(),
        ),
        "thread_id": lane.get("thread_id") or session.get("thread_id"),
        "turn_id": lane.get("turn_id") or session.get("turn_id"),
        "session_name": session.get("session_name"),
        "runtime_name": session.get("runtime_name"),
        "runtime_kind": session.get("runtime_kind"),
        "session_id": session.get("session_id"),
        "status": session.get("status") or lane.get("status"),
        "run_id": session.get("run_id"),
        "actor": session.get("actor") or lane.get("actor"),
        "stage": session.get("stage") or lane.get("stage"),
        "branch": lane.get("branch"),
        "pull_request": lane.get("pull_request"),
        "last_event": session.get("last_event"),
        "last_message": session.get("last_message"),
        "tokens": session.get("tokens"),
        "rate_limits": session.get("rate_limits"),
        "turn_count": session.get("turn_count"),
        "process_id": session.get("process_id"),
        "heartbeat_path": session.get("heartbeat_path"),
    }


def runtime_session_entry(lane: dict[str, Any]) -> dict[str, Any]:
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    heartbeat = runtime_heartbeat(lane)
    return {
        **scheduler_entry(lane),
        "issue_id": lane.get("lane_id"),
        "identifier": issue.get("identifier") or lane.get("lane_id"),
        "session_name": session.get("session_name"),
        "runtime_name": session.get("runtime_name"),
        "runtime_kind": session.get("runtime_kind"),
        "session_id": session.get("session_id"),
        "thread_id": session.get("thread_id") or lane.get("thread_id"),
        "turn_id": session.get("turn_id") or lane.get("turn_id"),
        "status": session.get("status") or lane.get("status"),
        "run_id": session.get("run_id"),
        "updated_at": session.get("updated_at") or lane.get("last_progress_at"),
        "actor": session.get("actor") or lane.get("actor"),
        "stage": session.get("stage") or lane.get("stage"),
        "attempt": session.get("attempt") or lane.get("attempt"),
        "tokens": session.get("tokens"),
        "rate_limits": session.get("rate_limits"),
        "turn_count": session.get("turn_count"),
        "last_event": session.get("last_event"),
        "last_message": session.get("last_message"),
        "prompt_path": session.get("prompt_path"),
        "result_path": session.get("result_path"),
        "command_argv": session.get("command_argv"),
        "dispatch_mode": session.get("dispatch_mode"),
        "process_id": session.get("process_id"),
        "inputs_file": session.get("inputs_file"),
        "heartbeat_path": session.get("heartbeat_path"),
        "heartbeat_status": heartbeat.get("status"),
        "heartbeat_updated_at": heartbeat.get("updated_at"),
        "log_path": session.get("log_path"),
    }


def append_engine_event(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    severity: str = "info",
) -> None:
    _engine_store(config).append_event(
        event_type=event_type,
        payload=payload,
        work_id=str(lane.get("lane_id") or ""),
        run_id=lane_run_id(lane),
        severity=severity,
    )


def _upsert_engine_runtime_session(
    *, config: WorkflowConfig, lane: dict[str, Any]
) -> None:
    lane_id = str(lane.get("lane_id") or "").strip()
    session = lane.get("runtime_session")
    if not lane_id or not isinstance(session, dict):
        return
    _engine_store(config).upsert_runtime_session(
        work_id=lane_id,
        entry=runtime_session_entry(lane),
        now_iso=_now_iso(),
    )


def _dedupe_conflicts(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for conflict in conflicts:
        key = (
            str(conflict.get("source") or ""),
            str(conflict.get("run_id") or ""),
            str(conflict.get("thread_id") or ""),
            str(conflict.get("status") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(conflict)
    return deduped


def _start_engine_actor_run(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    stage_name: str,
    runtime_meta: dict[str, Any],
    started_at: str,
) -> dict[str, Any]:
    return _engine_store(config).start_run(
        mode="actor",
        selected_count=1,
        metadata={
            **_runtime_run_metadata(lane=lane, runtime_meta=runtime_meta),
            "actor": actor_name,
            "stage": stage_name,
            "attempt": int(lane.get("attempt") or 0),
            "started_at": started_at,
        },
        now_iso=started_at,
    )


def _finish_engine_actor_run(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    status: str,
    error: str | None,
    metadata: dict[str, Any],
    completed_at: str,
) -> None:
    run_id = lane_run_id(lane)
    if not run_id:
        return
    final_status = normalize_runtime_session_status(status) or "failed"
    completed_count = 1 if final_status == "completed" else 0
    run_error = None if final_status == "completed" else error
    try:
        _engine_store(config).finish_run(
            run_id,
            status=final_status,
            selected_count=1,
            completed_count=completed_count,
            error=run_error,
            metadata={
                **_runtime_run_metadata(lane=lane, runtime_meta=metadata),
                "final_status": final_status,
            },
            now_iso=completed_at,
        )
    except KeyError:
        _engine_store(config).append_event(
            event_type=f"{config.workflow_name}.runtime_run_missing",
            payload={"run_id": run_id, "status": final_status},
            work_id=str(lane.get("lane_id") or ""),
            severity="warning",
        )


def _runtime_run_metadata(
    *, lane: dict[str, Any], runtime_meta: dict[str, Any]
) -> dict[str, Any]:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    return {
        "lane_id": lane.get("lane_id"),
        "issue": {
            "id": issue.get("id"),
            "identifier": issue.get("identifier"),
            "title": issue.get("title"),
            "url": issue.get("url"),
        },
        "stage": lane.get("stage"),
        "actor": lane.get("actor") or session.get("actor"),
        "attempt": lane.get("attempt"),
        "branch": lane.get("branch"),
        "pull_request": lane.get("pull_request"),
        "runtime": _runtime_meta_payload(runtime_meta),
        "thread_id": lane.get("thread_id") or session.get("thread_id"),
        "turn_id": lane.get("turn_id") or session.get("turn_id"),
    }


def _runtime_meta_payload(runtime_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(runtime_meta or {}).items()
        if value not in (None, "", [], {})
    }


def _mutable_actor_runtime_session(
    *, lane: dict[str, Any], actor_name: str, stage_name: str
) -> tuple[str, dict[str, Any]]:
    key = runtime_session_key(actor_name=actor_name, stage_name=stage_name)
    sessions = _lane_mapping(lane, "runtime_sessions")
    session = sessions.get(key)
    if not isinstance(session, dict):
        session = {}
        sessions[key] = session
    session["session_key"] = key
    return key, session


def _current_runtime_session(lane: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    latest = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    key = str(latest.get("session_key") or "").strip()
    sessions = _lane_mapping(lane, "runtime_sessions")
    if key and isinstance(sessions.get(key), dict):
        return key, sessions[key]
    if latest:
        key = runtime_session_key(
            actor_name=str(latest.get("actor") or lane.get("actor") or ""),
            stage_name=str(latest.get("stage") or lane.get("stage") or ""),
        )
        if key:
            latest["session_key"] = key
            sessions[key] = latest
            return key, latest
    for candidate_key, candidate in sessions.items():
        if isinstance(candidate, dict) and runtime_session_is_running(candidate):
            candidate["session_key"] = str(
                candidate.get("session_key") or candidate_key
            )
            return str(candidate_key), candidate
    key = "latest"
    session = _lane_mapping(lane, "runtime_session")
    session["session_key"] = key
    sessions[key] = session
    return key, session


def _set_latest_runtime_session(
    *, lane: dict[str, Any], session_key: str, session: dict[str, Any]
) -> None:
    session["session_key"] = session_key
    _lane_mapping(lane, "runtime_sessions")[session_key] = session
    lane["runtime_session"] = session


def _runtime_session_matches(
    session: dict[str, Any], *, actor_name: str, stage_name: str
) -> bool:
    return (
        str(session.get("actor") or "").strip() == str(actor_name or "").strip()
        and str(session.get("stage") or "").strip() == str(stage_name or "").strip()
    )


def _apply_runtime_session_ids(
    *, lane: dict[str, Any], session: dict[str, Any]
) -> None:
    session_id = str(session.get("session_id") or "").strip()
    thread_id = str(session.get("thread_id") or session_id or "").strip()
    turn_id = str(session.get("turn_id") or "").strip()
    if session_id:
        session["session_id"] = session_id
    if thread_id:
        session["thread_id"] = thread_id
        lane["thread_id"] = thread_id
    if turn_id:
        session["turn_id"] = turn_id
        lane["turn_id"] = turn_id


def _lane_mapping(lane: dict[str, Any], key: str) -> dict[str, Any]:
    value = lane.get(key)
    if isinstance(value, dict):
        return value
    lane[key] = {}
    return lane[key]


def _lane_stage(lane: dict[str, Any]) -> str:
    return str(lane.get("stage") or "").strip()


def _lane_is_terminal(lane: dict[str, Any]) -> bool:
    return str(lane.get("status") or "").strip() in _TERMINAL_LANE_STATUSES


def _engine_store(config: WorkflowConfig) -> EngineStore:
    return EngineStore(
        db_path=runtime_paths(config.workflow_root)["db_path"],
        workflow=config.workflow_name,
    )


def _iso_to_epoch(value: str, *, default: float) -> float:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return default


def _epoch_to_iso(value: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
