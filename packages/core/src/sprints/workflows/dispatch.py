"""Actor runtime dispatch, background workers, and output parsing."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

from sprints.workflows.actors import (
    actor_runtime_plan,
    append_actor_skill_docs,
    build_actor_runtime,
)
from sprints.core.config import WorkflowConfig, WorkflowConfigError
from sprints.core.contracts import WorkflowPolicy
from sprints.core.loader import load_workflow_policy
from sprints.workflows.orchestrator import build_actor_prompt
from sprints.core.paths import plugin_entrypoint_path, runtime_paths
from sprints.workflows.state_io import (
    WorkflowState,
    load_state,
    persist_runtime_state,
    update_state_locked,
    validate_state,
    with_state_lock,
)
from sprints.workflows.variables import actor_variables
from sprints.workflows.worktrees import ensure_lane_worktree
from sprints.workflows.lanes import (
    apply_actor_output_status,
    consume_lane_retry,
    guard_actor_dispatch,
    lane_actor_runtime_session,
    lane_by_id,
    lane_recovery_artifacts,
    lane_retry_inputs,
    record_actor_dispatch_planned,
    record_actor_output,
    record_actor_runtime_progress,
    record_actor_runtime_result,
    record_actor_runtime_start,
    set_lane_operator_attention,
    set_lane_status,
    lane_stage,
)

_ACTOR_HEARTBEAT_INTERVAL_SECONDS = 5.0


def parse_actor_output(raw_output: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        candidates: list[dict[str, Any]] = []
        for index, char in enumerate(raw_output):
            if char != "{":
                continue
            try:
                value, end = decoder.raw_decode(raw_output[index:])
            except json.JSONDecodeError:
                continue
            if raw_output[index + end :].strip():
                continue
            if isinstance(value, dict):
                candidates.append(value)
        if not candidates:
            raise original_error
        parsed = candidates[-1]
    if not isinstance(parsed, dict):
        raise TypeError("actor output must be a JSON object")
    return parsed


class _ActorOutputError(RuntimeError):
    def __init__(
        self,
        *,
        reason: str,
        message: str,
        runtime_meta: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.runtime_meta = runtime_meta
        self.artifacts = artifacts


def run_stage_actor(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    lane: dict[str, Any],
    actor_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    stage_name = lane_stage(lane)
    actor = config.actors[actor_name]
    actor_policy = policy.actors.get(actor_name)
    if actor_policy is None:
        raise RuntimeError(f"missing actor policy section for {actor_name}")
    lane_id = str(lane.get("lane_id") or "")
    guard = guard_actor_dispatch(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
    )
    if not guard.get("allowed"):
        persist_runtime_state(config=config, state=state)
        return guard
    inputs = lane_retry_inputs(lane=lane, inputs=inputs)
    worktree = ensure_lane_worktree(config=config, lane=lane)
    runtime_plan = actor_runtime_plan(
        config=config,
        actor=actor,
        stage_name=stage_name,
        lane_id=lane_id,
        resume_session_id=_resume_session_id(
            lane, actor_name=actor_name, stage_name=stage_name
        ),
    )
    prompt = build_actor_prompt(
        actor_policy=actor_policy,
        variables=actor_variables(config=config, state=state, lane=lane, inputs=inputs),
    )
    prompt = append_actor_skill_docs(config=config, actor=actor, prompt=prompt)
    dispatch_meta = _dispatch_plan_meta(
        runtime_plan=runtime_plan,
        dispatch_mode="inline",
        prompt=prompt,
        inputs=inputs,
        extra={"worktree": str(worktree)},
    )
    record_actor_dispatch_planned(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        runtime_meta=dispatch_meta,
    )
    persist_runtime_state(config=config, state=state)
    record_actor_runtime_start(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        runtime_meta=dispatch_meta,
    )
    persist_runtime_state(config=config, state=state)
    consume_lane_retry(config=config, lane=lane)
    set_lane_status(
        config=config,
        lane=lane,
        status="running",
        actor=actor_name,
        reason=f"{actor_name} dispatched",
    )
    persist_runtime_state(config=config, state=state)

    def on_session_ready(session_handle: Any) -> None:
        record_actor_runtime_progress(
            config=config,
            lane=lane,
            runtime_meta={
                **_runtime_plan_meta(runtime_plan),
                "dispatch_id": dispatch_meta.get("dispatch_id"),
                **_session_handle_meta(session_handle),
                "last_event": "session/ready",
            },
        )
        persist_runtime_state(config=config, state=state)

    progress_checkpoint = {"at": 0.0, "thread_id": None, "turn_id": None}

    def on_progress(progress: Any) -> None:
        runtime_meta = {
            **_runtime_plan_meta(runtime_plan),
            "dispatch_id": dispatch_meta.get("dispatch_id"),
            **_runtime_result_meta(progress),
        }
        thread_id = str(runtime_meta.get("thread_id") or "")
        turn_id = str(runtime_meta.get("turn_id") or "")
        now = time.monotonic()
        ids_changed = bool(
            (thread_id and thread_id != progress_checkpoint["thread_id"])
            or (turn_id and turn_id != progress_checkpoint["turn_id"])
        )
        if not ids_changed and now - float(progress_checkpoint["at"] or 0) < 5:
            return
        record_actor_runtime_progress(
            config=config,
            lane=lane,
            runtime_meta=runtime_meta,
        )
        progress_checkpoint["at"] = now
        progress_checkpoint["thread_id"] = thread_id or progress_checkpoint["thread_id"]
        progress_checkpoint["turn_id"] = turn_id or progress_checkpoint["turn_id"]
        persist_runtime_state(config=config, state=state)

    try:
        runtime_result = build_actor_runtime(config=config, actor=actor).run(
            actor=actor,
            prompt=prompt,
            stage_name=stage_name,
            worktree=worktree,
            lane_id=lane_id,
            resume_session_id=runtime_plan.resume_session_id,
            on_session_ready=on_session_ready,
            on_progress=on_progress,
        )
    except Exception as exc:
        record_actor_runtime_result(
            config=config,
            lane=lane,
            runtime_meta={
                **_runtime_plan_meta(runtime_plan),
                "dispatch_id": dispatch_meta.get("dispatch_id"),
                **_runtime_result_meta(getattr(exc, "result", None)),
                "last_message": str(exc),
            },
            status="failed",
        )
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="actor_runtime_failed",
            message=str(exc),
            artifacts=lane_recovery_artifacts(
                lane,
                {
                    "actor": actor_name,
                    "stage": stage_name,
                    "error": str(exc),
                },
            ),
        )
        persist_runtime_state(config=config, state=state)
        raise
    runtime_meta = {
        **_runtime_plan_meta(runtime_plan),
        "dispatch_id": dispatch_meta.get("dispatch_id"),
        **_runtime_result_meta(runtime_result),
    }
    raw_output = runtime_result.output
    try:
        parsed = parse_actor_output(raw_output)
    except json.JSONDecodeError as exc:
        record_actor_runtime_result(
            config=config,
            lane=lane,
            runtime_meta={**runtime_meta, "last_message": str(exc)},
            status="failed",
        )
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="actor_output_invalid_json",
            message=f"actor {actor_name} returned invalid JSON: {exc}",
            artifacts=lane_recovery_artifacts(
                lane,
                {
                    "actor": actor_name,
                    "stage": stage_name,
                    "error": str(exc),
                    "raw_output": raw_output,
                },
            ),
        )
        persist_runtime_state(config=config, state=state)
        raise RuntimeError(f"actor {actor_name} returned invalid JSON: {exc}") from exc
    except TypeError as exc:
        record_actor_runtime_result(
            config=config,
            lane=lane,
            runtime_meta={**runtime_meta, "last_message": str(exc)},
            status="failed",
        )
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="actor_output_contract_failed",
            message=f"actor {actor_name} output was not an object",
            artifacts=lane_recovery_artifacts(
                lane,
                {
                    "actor": actor_name,
                    "stage": stage_name,
                    "error": str(exc),
                    "raw_output": raw_output,
                },
            ),
        )
        persist_runtime_state(config=config, state=state)
        raise RuntimeError(str(exc)) from exc
    record_actor_output(config=config, lane=lane, actor_name=actor_name, output=parsed)
    apply_actor_output_status(
        config=config, lane=lane, actor_name=actor_name, output=parsed
    )
    record_actor_runtime_result(
        config=config,
        lane=lane,
        runtime_meta=runtime_meta,
        status=_actor_output_runtime_status(
            actor_name=actor_name,
            output=parsed,
            lane=lane,
        ),
    )
    persist_runtime_state(config=config, state=state)
    return parsed


def dispatch_stage_actor_background(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    lane: dict[str, Any],
    actor_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    stage_name = lane_stage(lane)
    actor = config.actors[actor_name]
    if policy.actors.get(actor_name) is None:
        raise RuntimeError(f"missing actor policy section for {actor_name}")
    lane_id = str(lane.get("lane_id") or "")
    guard = guard_actor_dispatch(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
    )
    if not guard.get("allowed"):
        persist_runtime_state(config=config, state=state)
        return guard

    actor_inputs = lane_retry_inputs(lane=lane, inputs=inputs)
    worktree = ensure_lane_worktree(config=config, lane=lane)
    runtime_plan = actor_runtime_plan(
        config=config,
        actor=actor,
        stage_name=stage_name,
        lane_id=lane_id,
        resume_session_id=_resume_session_id(
            lane, actor_name=actor_name, stage_name=stage_name
        ),
    )
    dispatch_meta = _dispatch_plan_meta(
        runtime_plan=runtime_plan,
        dispatch_mode="background",
        inputs=actor_inputs,
        extra={"worktree": str(worktree), "prompt_deferred": True},
    )
    record_actor_dispatch_planned(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        runtime_meta=dispatch_meta,
    )
    persist_runtime_state(config=config, state=state)
    record_actor_runtime_start(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        runtime_meta={
            **dispatch_meta,
            "last_event": "actor/background-dispatching",
        },
    )
    persist_runtime_state(config=config, state=state)
    consume_lane_retry(config=config, lane=lane)
    set_lane_status(
        config=config,
        lane=lane,
        status="running",
        actor=actor_name,
        reason=f"{actor_name} dispatched",
    )
    dispatch_file = _write_actor_dispatch_file(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        inputs=actor_inputs,
        runtime_meta=dispatch_meta,
    )
    heartbeat_file = _actor_heartbeat_file(dispatch_file)
    record_actor_runtime_progress(
        config=config,
        lane=lane,
        runtime_meta={
            **dispatch_meta,
            "last_event": "actor/background-prepared",
            "dispatch_mode": "background",
            "inputs_file": str(dispatch_file),
            "heartbeat_path": str(heartbeat_file),
            "log_path": str(_actor_log_file(dispatch_file)),
        },
    )
    persist_runtime_state(config=config, state=state)
    try:
        process = _spawn_actor_worker(
            config=config,
            lane_id=lane_id,
            actor_name=actor_name,
            stage_name=stage_name,
            inputs_file=dispatch_file,
        )
    except Exception as exc:
        record_actor_runtime_result(
            config=config,
            lane=lane,
            runtime_meta={
                **dispatch_meta,
                "last_message": str(exc),
                "inputs_file": str(dispatch_file),
            },
            status="failed",
        )
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason="actor_dispatch_failed",
            message=str(exc),
            artifacts={"actor": actor_name, "stage": stage_name},
        )
        persist_runtime_state(config=config, state=state)
        raise
    record_actor_runtime_progress(
        config=config,
        lane=lane,
        runtime_meta={
            **dispatch_meta,
            "last_event": "actor/background-dispatched",
            "dispatch_mode": "background",
            "process_id": process.pid,
            "inputs_file": str(dispatch_file),
            "heartbeat_path": str(heartbeat_file),
            "log_path": str(_actor_log_file(dispatch_file)),
        },
    )
    persist_runtime_state(config=config, state=state)
    return {
        "status": "dispatched",
        "mode": "background",
        "process_id": process.pid,
        "inputs_file": str(dispatch_file),
        "heartbeat_path": str(heartbeat_file),
    }


def actor_dispatch_mode(config: WorkflowConfig) -> Literal["inline", "background"]:
    raw = config.raw
    execution = raw.get("execution") if isinstance(raw.get("execution"), dict) else {}
    concurrency = (
        raw.get("concurrency") if isinstance(raw.get("concurrency"), dict) else {}
    )
    configured = (
        str(
            execution.get("actor-dispatch")
            or execution.get("actor_dispatch")
            or concurrency.get("actor-dispatch")
            or concurrency.get("actor_dispatch")
            or "auto"
        )
        .strip()
        .lower()
    )
    if configured in {"inline", "sync", "foreground"}:
        return "inline"
    if configured in {"background", "async", "subprocess"}:
        return "background"
    if configured not in {"", "auto"}:
        raise WorkflowConfigError(
            "execution.actor-dispatch must be one of: auto, inline, background"
        )
    return "background" if _configured_lane_limit(config) > 1 else "inline"


def _configured_lane_limit(config: WorkflowConfig) -> int:
    raw = config.raw.get("concurrency")
    cfg = raw if isinstance(raw, dict) else {}
    for key in ("max-lanes", "max_lanes", "max-active-lanes", "max_active_lanes"):
        value = cfg.get(key)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 1


def _dispatch_dir(config: WorkflowConfig) -> Path:
    return runtime_paths(config.workflow_root)["db_path"].parent / "actor-dispatch"


def _write_actor_dispatch_file(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    actor_name: str,
    stage_name: str,
    inputs: dict[str, Any],
    runtime_meta: dict[str, Any],
) -> Path:
    root = _dispatch_dir(config)
    root.mkdir(parents=True, exist_ok=True)
    lane_id = str(lane.get("lane_id") or "lane")
    filename = (
        f"{_safe_dispatch_segment(lane_id)}."
        f"{_safe_dispatch_segment(stage_name)}."
        f"{_safe_dispatch_segment(actor_name)}."
        f"{uuid.uuid4().hex}.json"
    )
    path = root / filename
    payload = {
        "lane_id": lane_id,
        "stage": stage_name,
        "actor": actor_name,
        "inputs": inputs,
        "runtime_meta": dict(runtime_meta),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _read_actor_dispatch_file(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("actor dispatch file must contain a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise RuntimeError("actor dispatch file is missing object field `inputs`")
    runtime_meta = payload.get("runtime_meta")
    if runtime_meta is not None and not isinstance(runtime_meta, dict):
        raise RuntimeError("actor dispatch file field `runtime_meta` must be an object")
    payload["runtime_meta"] = runtime_meta or {}
    return payload


def _actor_heartbeat_file(inputs_file: Path) -> Path:
    return Path(inputs_file).with_suffix(".heartbeat.json")


def _actor_log_file(inputs_file: Path) -> Path:
    return Path(inputs_file).with_suffix(".log")


def _start_actor_heartbeat(
    *,
    lane_id: str,
    actor_name: str,
    stage_name: str,
    inputs_file: Path,
    interval_seconds: float = _ACTOR_HEARTBEAT_INTERVAL_SECONDS,
) -> Callable[[str], None]:
    path = _actor_heartbeat_file(inputs_file)
    stop = threading.Event()

    def beat(status: str) -> None:
        _write_actor_heartbeat(
            path=path,
            lane_id=lane_id,
            actor_name=actor_name,
            stage_name=stage_name,
            inputs_file=inputs_file,
            status=status,
        )

    def loop() -> None:
        while not stop.wait(interval_seconds):
            beat("running")

    beat("running")
    thread = threading.Thread(
        target=loop,
        name=f"sprints-heartbeat-{_safe_dispatch_segment(lane_id)}",
        daemon=True,
    )
    thread.start()

    def finish(status: str) -> None:
        stop.set()
        beat(status)
        thread.join(timeout=0.2)

    return finish


def _write_actor_heartbeat(
    *,
    path: Path,
    lane_id: str,
    actor_name: str,
    stage_name: str,
    inputs_file: Path,
    status: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    now_epoch = time.time()
    payload = {
        "status": status,
        "updated_at": _utc_now_iso(now_epoch),
        "updated_at_epoch": now_epoch,
        "process_id": os.getpid(),
        "lane_id": lane_id,
        "actor": actor_name,
        "stage": stage_name,
        "inputs_file": str(inputs_file),
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(path)
    except OSError:
        # Heartbeat loss should not kill useful actor work; reconciliation will
        # treat the missing heartbeat as stale if the worker disappears.
        return


def _utc_now_iso(epoch: float | None = None) -> str:
    value = time.time() if epoch is None else epoch
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def _safe_dispatch_segment(value: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-"
        for char in str(value or "").strip()
    ).strip(".-")
    return cleaned[:80] or "item"


def _spawn_actor_worker(
    *,
    config: WorkflowConfig,
    lane_id: str,
    actor_name: str,
    stage_name: str,
    inputs_file: Path,
) -> subprocess.Popen:
    log_path = _actor_log_file(inputs_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable,
        str(plugin_entrypoint_path(config.workflow_root)),
        "--workflow-root",
        str(config.workflow_root),
        "actor-run",
        lane_id,
        "--actor",
        actor_name,
        "--stage",
        stage_name,
        "--inputs-file",
        str(inputs_file),
    ]
    stdout = log_path.open("ab")
    kwargs: dict[str, Any] = {
        "cwd": str(config.workflow_root),
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": subprocess.STDOUT,
        "close_fds": os.name != "nt",
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        return subprocess.Popen(argv, **kwargs)
    finally:
        stdout.close()


def _resume_session_id(
    lane: dict[str, Any], *, actor_name: str, stage_name: str
) -> str | None:
    session = lane_actor_runtime_session(
        lane, actor_name=actor_name, stage_name=stage_name
    )
    value = session.get("thread_id") or session.get("session_id")
    text = str(value or "").strip()
    return text or None


def _runtime_plan_meta(plan: Any) -> dict[str, Any]:
    return {
        "runtime_name": getattr(plan, "runtime_name", None),
        "runtime_kind": getattr(plan, "runtime_kind", None),
        "session_name": getattr(plan, "session_name", None),
        "model": getattr(plan, "model", None),
        "session_id": getattr(plan, "resume_session_id", None),
        "thread_id": getattr(plan, "resume_session_id", None),
    }


def _dispatch_plan_meta(
    *,
    runtime_plan: Any,
    dispatch_mode: str,
    prompt: str | None = None,
    inputs: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = {
        **_runtime_plan_meta(runtime_plan),
        "dispatch_id": uuid.uuid4().hex,
        "dispatch_mode": dispatch_mode,
        "input_keys": sorted(str(key) for key in dict(inputs or {}).keys()),
        **dict(extra or {}),
    }
    if prompt is not None:
        encoded = prompt.encode("utf-8", errors="replace")
        meta["prompt_sha256"] = hashlib.sha256(encoded).hexdigest()
        meta["prompt_bytes"] = len(encoded)
    return {
        key: value for key, value in meta.items() if value not in (None, "", [], {})
    }


def _session_handle_meta(session_handle: Any) -> dict[str, Any]:
    return {
        "session_id": getattr(session_handle, "session_id", None),
        "thread_id": getattr(session_handle, "session_id", None),
        "record_id": getattr(session_handle, "record_id", None),
        "session_name": getattr(session_handle, "name", None),
    }


def _runtime_result_meta(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    plan = getattr(result, "plan", None)
    meta = _runtime_plan_meta(plan) if plan is not None else {}
    for key, value in {
        "session_id": getattr(result, "session_id", None),
        "thread_id": getattr(result, "thread_id", None),
        "turn_id": getattr(result, "turn_id", None),
        "last_event": getattr(result, "last_event", None),
        "last_message": getattr(result, "last_message", None),
        "turn_count": getattr(result, "turn_count", None),
        "tokens": getattr(result, "tokens", None),
        "rate_limits": getattr(result, "rate_limits", None),
        "prompt_path": str(getattr(result, "prompt_path", "") or "") or None,
        "result_path": str(getattr(result, "result_path", "") or "") or None,
        "command_argv": getattr(result, "command_argv", None),
    }.items():
        if value not in (None, "", [], {}):
            meta[key] = value
    return meta


def run_actor_worker(
    config: WorkflowConfig,
    *,
    lane_id: str,
    actor_name: str,
    stage_name: str,
    inputs_file: Path,
) -> int:
    policy = load_workflow_policy(config.workflow_root)
    dispatch_payload = _read_actor_dispatch_file(inputs_file)
    inputs = dispatch_payload["inputs"]
    dispatch_meta = dict(dispatch_payload.get("runtime_meta") or {})
    state, lane = _load_background_actor_snapshot(
        config=config,
        lane_id=lane_id,
        actor_name=actor_name,
        stage_name=stage_name,
    )
    finish_heartbeat = _start_actor_heartbeat(
        lane_id=lane_id,
        actor_name=actor_name,
        stage_name=stage_name,
        inputs_file=inputs_file,
    )
    heartbeat_status = "failed"
    try:
        try:
            parsed, runtime_meta, _raw_output = _run_actor_runtime_for_worker(
                config=config,
                policy=policy,
                state=state,
                lane=lane,
                actor_name=actor_name,
                inputs=inputs,
                dispatch_meta=dispatch_meta,
            )
        except _ActorOutputError as exc:
            _finalize_background_actor_failure(
                config=config,
                lane_id=lane_id,
                actor_name=actor_name,
                stage_name=stage_name,
                runtime_meta=exc.runtime_meta,
                reason=exc.reason,
                message=str(exc),
                artifacts=exc.artifacts,
            )
            return 1
        except Exception as exc:
            _finalize_background_actor_failure(
                config=config,
                lane_id=lane_id,
                actor_name=actor_name,
                stage_name=stage_name,
                runtime_meta={
                    **dispatch_meta,
                    "last_message": str(exc),
                    **_runtime_result_meta(getattr(exc, "result", None)),
                },
                reason="actor_runtime_failed",
                message=str(exc),
                artifacts={"error": str(exc)},
            )
            return 1

        _finalize_background_actor_success(
            config=config,
            lane_id=lane_id,
            actor_name=actor_name,
            stage_name=stage_name,
            output=parsed,
            runtime_meta=runtime_meta,
        )
        heartbeat_status = "completed"
        return 0
    finally:
        finish_heartbeat(heartbeat_status)


def _load_background_actor_snapshot(
    *,
    config: WorkflowConfig,
    lane_id: str,
    actor_name: str,
    stage_name: str,
) -> tuple[WorkflowState, dict[str, Any]]:
    snapshot: dict[str, Any] = {}

    def callback() -> int:
        state = load_state(
            config.storage.state_path,
            workflow=config.workflow_name,
            first_stage=config.first_stage,
        )
        validate_state(config, state)
        lane = lane_by_id(state, lane_id)
        _validate_background_lane(lane, actor_name=actor_name, stage_name=stage_name)
        snapshot["state"] = state
        snapshot["lane"] = lane
        return 0

    with_state_lock(
        config=config,
        owner_role="actor-worker-read",
        callback=callback,
    )
    return snapshot["state"], snapshot["lane"]


def _run_actor_runtime_for_worker(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    lane: dict[str, Any],
    actor_name: str,
    inputs: dict[str, Any],
    dispatch_meta: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    actor = config.actors[actor_name]
    actor_policy = policy.actors.get(actor_name)
    if actor_policy is None:
        raise RuntimeError(f"missing actor policy section for {actor_name}")
    stage_name = lane_stage(lane)
    lane_id = str(lane.get("lane_id") or "")
    worktree = ensure_lane_worktree(config=config, lane=lane)
    runtime_plan = actor_runtime_plan(
        config=config,
        actor=actor,
        stage_name=stage_name,
        lane_id=lane_id,
        resume_session_id=_resume_session_id(
            lane, actor_name=actor_name, stage_name=stage_name
        ),
    )
    prompt = build_actor_prompt(
        actor_policy=actor_policy,
        variables=actor_variables(config=config, state=state, lane=lane, inputs=inputs),
    )
    prompt = append_actor_skill_docs(config=config, actor=actor, prompt=prompt)
    runtime_result = build_actor_runtime(config=config, actor=actor).run(
        actor=actor,
        prompt=prompt,
        stage_name=stage_name,
        worktree=worktree,
        lane_id=lane_id,
        resume_session_id=runtime_plan.resume_session_id,
    )
    runtime_meta = {
        **dispatch_meta,
        **_runtime_plan_meta(runtime_plan),
        **_runtime_result_meta(runtime_result),
    }
    raw_output = runtime_result.output
    try:
        return parse_actor_output(raw_output), runtime_meta, raw_output
    except json.JSONDecodeError as exc:
        raise _ActorOutputError(
            reason="actor_output_invalid_json",
            message=f"actor {actor_name} returned invalid JSON: {exc}",
            runtime_meta={
                **runtime_meta,
                "last_message": str(exc),
                "raw_output": raw_output,
            },
            artifacts={
                "actor": actor_name,
                "stage": stage_name,
                "error": str(exc),
                "raw_output": raw_output,
            },
        ) from exc
    except TypeError as exc:
        raise _ActorOutputError(
            reason="actor_output_contract_failed",
            message=f"actor {actor_name} output was not an object",
            runtime_meta={
                **runtime_meta,
                "last_message": str(exc),
                "raw_output": raw_output,
            },
            artifacts={
                "actor": actor_name,
                "stage": stage_name,
                "error": str(exc),
                "raw_output": raw_output,
            },
        ) from exc


def _finalize_background_actor_success(
    *,
    config: WorkflowConfig,
    lane_id: str,
    actor_name: str,
    stage_name: str,
    output: dict[str, Any],
    runtime_meta: dict[str, Any],
) -> None:
    def update(state: WorkflowState) -> None:
        lane = lane_by_id(state, lane_id)
        _validate_background_lane(lane, actor_name=actor_name, stage_name=stage_name)
        record_actor_output(
            config=config, lane=lane, actor_name=actor_name, output=output
        )
        apply_actor_output_status(
            config=config, lane=lane, actor_name=actor_name, output=output
        )
        record_actor_runtime_result(
            config=config,
            lane=lane,
            runtime_meta=runtime_meta,
            status=_actor_output_runtime_status(
                actor_name=actor_name,
                output=output,
                lane=lane,
            ),
        )

    update_state_locked(
        config=config,
        event="actor.completed",
        extra={"lane_id": lane_id, "actor": actor_name, "stage": stage_name},
        update=update,
    )


def _finalize_background_actor_failure(
    *,
    config: WorkflowConfig,
    lane_id: str,
    actor_name: str,
    stage_name: str,
    runtime_meta: dict[str, Any],
    reason: str,
    message: str,
    artifacts: dict[str, Any],
) -> None:
    def update(state: WorkflowState) -> None:
        lane = lane_by_id(state, lane_id)
        _validate_background_lane(lane, actor_name=actor_name, stage_name=stage_name)
        record_actor_runtime_result(
            config=config,
            lane=lane,
            runtime_meta=runtime_meta,
            status="failed",
        )
        set_lane_operator_attention(
            config=config,
            lane=lane,
            reason=reason,
            message=message,
            artifacts={"actor": actor_name, "stage": stage_name, **artifacts},
        )

    update_state_locked(
        config=config,
        event="actor.failed",
        extra={
            "lane_id": lane_id,
            "actor": actor_name,
            "stage": stage_name,
            "reason": reason,
            "message": message,
        },
        update=update,
    )


def _validate_background_lane(
    lane: dict[str, Any], *, actor_name: str, stage_name: str
) -> None:
    if lane_stage(lane) != stage_name:
        raise RuntimeError(
            f"background actor result targets stage {stage_name!r}, "
            f"but lane is at {lane_stage(lane)!r}"
        )
    if str(lane.get("status") or "") != "running":
        raise RuntimeError(
            f"background actor result targets a non-running lane: {lane.get('status')!r}"
        )
    if str(lane.get("actor") or "") != actor_name:
        raise RuntimeError(
            f"background actor result targets actor {actor_name!r}, "
            f"but lane actor is {lane.get('actor')!r}"
        )


def _actor_output_runtime_status(
    *, actor_name: str, output: dict[str, Any], lane: dict[str, Any]
) -> str:
    status = str(output.get("status") or "").strip().lower()
    blockers = (
        output.get("blockers") if isinstance(output.get("blockers"), list) else []
    )
    if status == "failed":
        return "failed"
    if (
        status == "blocked"
        or blockers
        or str(lane.get("status") or "").strip() == "operator_attention"
    ):
        return "blocked"
    if actor_name == "implementer" and status == "done":
        return "completed"
    if actor_name == "reviewer" and status in {
        "approved",
        "changes_requested",
        "needs_changes",
    }:
        return "completed"
    return "failed" if not status else "completed"
