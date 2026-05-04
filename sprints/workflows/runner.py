"""Workflow execution mechanics, lane state, status, and stall hooks."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol

from engine import EngineStore
from workflows.actions import run_action
from workflows.actors import (
    actor_runtime_plan,
    append_actor_skill_docs,
    build_actor_runtime,
)
from workflows.config import WorkflowConfig, WorkflowConfigError
from workflows.contracts import (
    WorkflowPolicy,
    load_workflow_contract,
    parse_workflow_policy,
)
from workflows.orchestrator import (
    OrchestratorDecision,
    build_actor_prompt,
    build_orchestrator_prompt,
    parse_orchestrator_decisions,
)
from workflows.paths import plugin_entrypoint_path, runtime_paths
from workflows.worktrees import ensure_lane_worktree
from workflows.lanes import (
    active_lanes,
    advance_lane,
    apply_actor_output_status,
    actor_concurrency_usage,
    build_lane_status,
    build_workflow_facts,
    claim_new_lanes,
    complete_lane,
    consume_lane_retry,
    decision_ready_lanes,
    guard_actor_dispatch,
    lane_actor_runtime_session,
    lane_by_id,
    lane_for_decision,
    lane_mapping,
    lane_recovery_artifacts,
    lane_retry_inputs,
    lane_summary,
    lane_stage,
    reconcile_lanes,
    record_actor_output,
    record_actor_runtime_progress,
    record_actor_runtime_result,
    record_actor_runtime_start,
    record_action_result,
    queue_lane_retry,
    release_lane,
    save_scheduler_snapshot,
    set_lane_operator_attention,
    set_lane_status,
    target_or_single,
    validate_actor_capacity,
    validate_decision_for_lane,
)

SPRINTS_STALL_DETECTED = "sprints.stall.detected"
SPRINTS_STALL_TERMINATED = "sprints.stall.terminated"
_DEFAULT_TIMEOUT_MS = 300_000
_STATE_LOCK_SCOPE = "workflow-state"
_STATE_LOCK_TTL_SECONDS = 120
_STATE_LOCK_RENEW_INTERVAL_SECONDS = 30.0
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


@dataclass
class WorkflowState:
    workflow: str = ""
    status: str = "idle"
    lanes: dict[str, dict[str, Any]] = field(default_factory=dict)
    orchestrator_decisions: list[dict[str, Any]] = field(default_factory=list)
    idle_reason: str | None = None

    @classmethod
    def initial(cls, *, workflow: str, first_stage: str) -> "WorkflowState":
        del first_stage
        return cls(workflow=workflow)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkflowState":
        names = {item.name for item in fields(cls)}
        return cls(**{name: raw[name] for name in names if name in raw})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StallVerdict:
    issue_id: str
    elapsed_seconds: float
    threshold_seconds: float
    action: Literal["terminate", "warn", "noop"]


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


class _RunningEntry(Protocol):
    started_at_monotonic: float

    def runtime(self): ...


def main(workspace: object, argv: list[str]) -> int:
    if not isinstance(workspace, WorkflowConfig):
        raise TypeError(
            f"workflow CLI expected WorkflowConfig, got {type(workspace).__name__}"
        )
    parser = argparse.ArgumentParser(prog=workspace.workflow_name)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("validate")
    subcommands.add_parser("show")
    status_parser = subcommands.add_parser("status")
    status_parser.add_argument("--json", action="store_true")
    lanes_parser = subcommands.add_parser("lanes")
    lanes_parser.add_argument("lane_id", nargs="?")
    lanes_parser.add_argument(
        "--attention",
        action="store_true",
        help="Show only lanes requiring operator attention.",
    )
    retry_parser = subcommands.add_parser("retry")
    retry_parser.add_argument("lane_id")
    retry_parser.add_argument("--reason", default="operator requested retry")
    retry_parser.add_argument("--target")
    release_parser = subcommands.add_parser("release")
    release_parser.add_argument("lane_id")
    release_parser.add_argument("--reason", default="operator released lane")
    complete_parser = subcommands.add_parser("complete")
    complete_parser.add_argument("lane_id")
    complete_parser.add_argument("--reason", default="operator completed lane")
    tick_parser = subcommands.add_parser("tick")
    tick_parser.add_argument("--orchestrator-output", default="")
    actor_run_parser = subcommands.add_parser("actor-run")
    actor_run_parser.add_argument("lane_id")
    actor_run_parser.add_argument("--actor", required=True)
    actor_run_parser.add_argument("--stage", required=True)
    actor_run_parser.add_argument("--inputs-file", required=True)
    args = parser.parse_args(argv)

    if args.command == "validate":
        return _validate(workspace)
    if args.command == "show":
        return _show(workspace)
    if args.command == "status":
        return _status(workspace)
    if args.command == "lanes":
        return _lanes(
            workspace, lane_id=args.lane_id, attention_only=bool(args.attention)
        )
    if args.command == "retry":
        return _operator_retry(
            workspace,
            lane_id=args.lane_id,
            reason=args.reason,
            target=args.target,
        )
    if args.command == "release":
        return _operator_release(workspace, lane_id=args.lane_id, reason=args.reason)
    if args.command == "complete":
        return _operator_complete(workspace, lane_id=args.lane_id, reason=args.reason)
    if args.command == "tick":
        return _tick(workspace, orchestrator_output=args.orchestrator_output)
    if args.command == "actor-run":
        return _actor_run_worker(
            workspace,
            lane_id=args.lane_id,
            actor_name=args.actor,
            stage_name=args.stage,
            inputs_file=Path(args.inputs_file),
        )
    raise RuntimeError(f"unhandled command {args.command}")


def load_state(path: Path, *, workflow: str, first_stage: str) -> WorkflowState:
    if not path.exists():
        return WorkflowState.initial(workflow=workflow, first_stage=first_stage)
    state = WorkflowState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if not state.workflow:
        state.workflow = workflow
    return state


def save_state(path: Path, state: WorkflowState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def append_audit(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def actor_variables(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    lane: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    actor_outputs = lane_mapping(lane, "actor_outputs")
    attempt = int(inputs.get("attempt") or lane.get("attempt") or 1)
    return {
        **inputs,
        "attempt": attempt,
        "workflow": state.to_dict(),
        "lane": lane,
        "config": config.raw,
        "issue": lane.get("issue") or {},
        "implementation": actor_outputs.get("implementer") or {},
        "review": actor_outputs.get("reviewer") or {},
        "review_feedback": _review_feedback(lane=lane, inputs=inputs),
        "pull_request": lane.get("pull_request") or {},
        "retry": lane.get("pending_retry") or inputs.get("retry") or {},
    }


def action_variables(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    lane: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    actor_outputs = lane_mapping(lane, "actor_outputs")
    return {
        **inputs,
        "workflow": state.to_dict(),
        "lane": lane,
        "workflow_root": str(config.workflow_root),
        "config": config.raw,
        "issue": lane.get("issue") or {},
        "actor_outputs": actor_outputs,
        "stage_outputs": lane_mapping(lane, "stage_outputs"),
        "action_results": lane_mapping(lane, "action_results"),
        "implementation": actor_outputs.get("implementer") or {},
        "review": actor_outputs.get("reviewer") or {},
        "review_feedback": _review_feedback(lane=lane, inputs=inputs),
        "pull_request": lane.get("pull_request") or {},
        "retry": lane.get("pending_retry") or inputs.get("retry") or {},
    }


def _review_feedback(*, lane: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    actor_outputs = lane_mapping(lane, "actor_outputs")
    review = inputs.get("review")
    if not isinstance(review, dict):
        stored_review = actor_outputs.get("reviewer")
        review = stored_review if isinstance(stored_review, dict) else {}
    retry = inputs.get("retry") if isinstance(inputs.get("retry"), dict) else {}
    feedback = {
        "review": review,
        "required_fixes": inputs.get("required_fixes")
        or review.get("required_fixes")
        or retry.get("required_fixes"),
        "findings": inputs.get("findings")
        or review.get("findings")
        or retry.get("findings"),
        "verification_gaps": inputs.get("verification_gaps")
        or review.get("verification_gaps")
        or retry.get("verification_gaps"),
        "feedback": inputs.get("feedback") or retry.get("reason"),
    }
    return {
        key: value
        for key, value in feedback.items()
        if value not in (None, "", [], {})
    }


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
        _persist_runtime_state(config=config, state=state)
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
    record_actor_runtime_start(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        runtime_meta=_runtime_plan_meta(runtime_plan),
    )
    consume_lane_retry(config=config, lane=lane)
    set_lane_status(
        config=config,
        lane=lane,
        status="running",
        actor=actor_name,
        reason=f"{actor_name} dispatched",
    )
    _persist_runtime_state(config=config, state=state)

    def on_session_ready(session_handle: Any) -> None:
        record_actor_runtime_progress(
            config=config,
            lane=lane,
            runtime_meta={
                **_runtime_plan_meta(runtime_plan),
                **_session_handle_meta(session_handle),
                "last_event": "session/ready",
            },
        )
        _persist_runtime_state(config=config, state=state)

    progress_checkpoint = {"at": 0.0, "thread_id": None, "turn_id": None}

    def on_progress(progress: Any) -> None:
        runtime_meta = {
            **_runtime_plan_meta(runtime_plan),
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
        _persist_runtime_state(config=config, state=state)

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
        _persist_runtime_state(config=config, state=state)
        raise
    runtime_meta = {
        **_runtime_plan_meta(runtime_plan),
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
        _persist_runtime_state(config=config, state=state)
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
        _persist_runtime_state(config=config, state=state)
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
    _persist_runtime_state(config=config, state=state)
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
        _persist_runtime_state(config=config, state=state)
        return guard

    actor_inputs = lane_retry_inputs(lane=lane, inputs=inputs)
    ensure_lane_worktree(config=config, lane=lane)
    runtime_plan = actor_runtime_plan(
        config=config,
        actor=actor,
        stage_name=stage_name,
        lane_id=lane_id,
        resume_session_id=_resume_session_id(
            lane, actor_name=actor_name, stage_name=stage_name
        ),
    )
    record_actor_runtime_start(
        config=config,
        lane=lane,
        actor_name=actor_name,
        stage_name=stage_name,
        runtime_meta={
            **_runtime_plan_meta(runtime_plan),
            "last_event": "actor/background-dispatching",
        },
    )
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
    )
    heartbeat_file = _actor_heartbeat_file(dispatch_file)
    record_actor_runtime_progress(
        config=config,
        lane=lane,
        runtime_meta={
            **_runtime_plan_meta(runtime_plan),
            "last_event": "actor/background-prepared",
            "dispatch_mode": "background",
            "inputs_file": str(dispatch_file),
            "heartbeat_path": str(heartbeat_file),
            "log_path": str(_actor_log_file(dispatch_file)),
        },
    )
    _persist_runtime_state(config=config, state=state)
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
                **_runtime_plan_meta(runtime_plan),
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
        _persist_runtime_state(config=config, state=state)
        raise
    record_actor_runtime_progress(
        config=config,
        lane=lane,
        runtime_meta={
            **_runtime_plan_meta(runtime_plan),
            "last_event": "actor/background-dispatched",
            "dispatch_mode": "background",
            "process_id": process.pid,
            "inputs_file": str(dispatch_file),
            "heartbeat_path": str(heartbeat_file),
            "log_path": str(_actor_log_file(dispatch_file)),
        },
    )
    _persist_runtime_state(config=config, state=state)
    return {
        "status": "dispatched",
        "mode": "background",
        "process_id": process.pid,
        "inputs_file": str(dispatch_file),
        "heartbeat_path": str(heartbeat_file),
    }


def _persist_runtime_state(*, config: WorkflowConfig, state: WorkflowState) -> None:
    save_state(config.storage.state_path, state)
    save_scheduler_snapshot(config=config, state=state)


def _save_state_event(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    event: str,
    extra: dict[str, Any] | None = None,
) -> None:
    save_state(config.storage.state_path, state)
    save_scheduler_snapshot(config=config, state=state)
    append_audit(
        config.storage.audit_log_path,
        {
            "event": f"{config.workflow_name}.{event}",
            "state": state.to_dict(),
            **dict(extra or {}),
        },
    )


def _with_state_lock(
    *,
    config: WorkflowConfig,
    owner_role: str,
    callback: Callable[[], int | None],
    timeout_seconds: float = 60.0,
) -> int:
    store = _runner_engine_store(config)
    lease_key = str(config.workflow_root)
    owner_instance_id = f"{owner_role}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    stop_renewing: Callable[[], None] | None = None
    try:
        while True:
            lease = store.acquire_lease(
                lease_scope=_STATE_LOCK_SCOPE,
                lease_key=lease_key,
                owner_instance_id=owner_instance_id,
                owner_role=owner_role,
                ttl_seconds=_STATE_LOCK_TTL_SECONDS,
                metadata={"workflow_root": str(config.workflow_root)},
            )
            acquired = bool(lease.get("acquired"))
            if acquired:
                break
            if time.monotonic() >= deadline:
                current_owner = lease.get("owner_instance_id") or "unknown"
                raise TimeoutError(
                    f"timed out waiting for workflow state lock held by {current_owner}"
                )
            time.sleep(0.2)
        stop_renewing = _start_state_lock_renewer(
            store=store,
            config=config,
            lease_key=lease_key,
            owner_instance_id=owner_instance_id,
            owner_role=owner_role,
        )
        result = callback()
        return int(result or 0)
    finally:
        if stop_renewing is not None:
            stop_renewing()
        if acquired:
            store.release_lease(
                lease_scope=_STATE_LOCK_SCOPE,
                lease_key=lease_key,
                owner_instance_id=owner_instance_id,
                release_reason="complete",
            )


def _start_state_lock_renewer(
    *,
    store: EngineStore,
    config: WorkflowConfig,
    lease_key: str,
    owner_instance_id: str,
    owner_role: str,
) -> Callable[[], None]:
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(_STATE_LOCK_RENEW_INTERVAL_SECONDS):
            try:
                store.acquire_lease(
                    lease_scope=_STATE_LOCK_SCOPE,
                    lease_key=lease_key,
                    owner_instance_id=owner_instance_id,
                    owner_role=owner_role,
                    ttl_seconds=_STATE_LOCK_TTL_SECONDS,
                    metadata={"workflow_root": str(config.workflow_root)},
                )
            except Exception:
                return

    thread = threading.Thread(
        target=renew,
        name=f"sprints-state-lock-{_safe_dispatch_segment(owner_role)}",
        daemon=True,
    )
    thread.start()

    def stop_thread() -> None:
        stop.set()
        thread.join(timeout=0.2)

    return stop_thread


def _update_state_locked(
    *,
    config: WorkflowConfig,
    event: str,
    extra: dict[str, Any],
    update: Callable[[WorkflowState], None],
) -> None:
    def callback() -> int:
        state = load_state(
            config.storage.state_path,
            workflow=config.workflow_name,
            first_stage=config.first_stage,
        )
        validate_state(config, state)
        update(state)
        _refresh_state_status(state, idle_reason="no active lanes")
        _save_state_event(config=config, state=state, event=event, extra=extra)
        return 0

    _with_state_lock(
        config=config,
        owner_role="actor-worker",
        callback=callback,
    )


def _runner_engine_store(config: WorkflowConfig) -> EngineStore:
    return EngineStore(
        db_path=runtime_paths(config.workflow_root)["db_path"],
        workflow=config.workflow_name,
    )


def _actor_dispatch_mode(config: WorkflowConfig) -> Literal["inline", "background"]:
    raw = config.raw
    execution = raw.get("execution") if isinstance(raw.get("execution"), dict) else {}
    concurrency = (
        raw.get("concurrency") if isinstance(raw.get("concurrency"), dict) else {}
    )
    configured = str(
        execution.get("actor-dispatch")
        or execution.get("actor_dispatch")
        or concurrency.get("actor-dispatch")
        or concurrency.get("actor_dispatch")
        or "auto"
    ).strip().lower()
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
    return inputs


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


def _actor_run_worker(
    config: WorkflowConfig,
    *,
    lane_id: str,
    actor_name: str,
    stage_name: str,
    inputs_file: Path,
) -> int:
    policy = _load_policy(config)
    inputs = _read_actor_dispatch_file(inputs_file)
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

    _with_state_lock(
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
        record_actor_output(config=config, lane=lane, actor_name=actor_name, output=output)
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

    _update_state_locked(
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

    _update_state_locked(
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
    blockers = output.get("blockers") if isinstance(output.get("blockers"), list) else []
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


def apply_action_result(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    lane: dict[str, Any],
    action_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    result = run_action(
        config.actions[action_name],
        action_variables(config=config, state=state, lane=lane, inputs=inputs),
    )
    payload = {"ok": result.ok, "output": result.output}
    record_action_result(
        config=config, lane=lane, action_name=action_name, result=payload
    )
    return payload


def validate_state(config: WorkflowConfig, state: WorkflowState) -> None:
    for lane in state.lanes.values():
        stage_name = lane_stage(lane)
        if stage_name not in config.stages:
            raise RuntimeError(
                f"lane {lane.get('lane_id')} references unknown stage: {stage_name}"
            )
        validate_stage_gates(config, stage_name)


def validate_stage_gates(config: WorkflowConfig, stage_name: str) -> None:
    stage = config.stages[stage_name]
    for gate_name in stage.gates:
        gate = config.gates[gate_name]
        if gate.type != "orchestrator-evaluated":
            raise WorkflowConfigError(
                f"unsupported gate type for {gate_name}: {gate.type}"
            )


def ensure_workflow_state_files(
    workflow_root: Path, config: dict[str, Any] | None = None
) -> dict[str, str]:
    root = Path(workflow_root).expanduser().resolve()
    raw = config if config is not None else load_workflow_contract(root).config
    typed = WorkflowConfig.from_raw(raw=raw, workflow_root=root)
    if not typed.storage.state_path.exists():
        save_state(
            typed.storage.state_path,
            WorkflowState.initial(
                workflow=typed.workflow_name, first_stage=typed.first_stage
            ),
        )
    typed.storage.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    typed.storage.audit_log_path.touch(exist_ok=True)
    return {
        "state": str(typed.storage.state_path),
        "audit_log": str(typed.storage.audit_log_path),
    }


def build_status(workflow_root: Path) -> dict[str, Any]:
    root = Path(workflow_root).expanduser().resolve()
    contract = load_workflow_contract(root)
    config = WorkflowConfig.from_raw(raw=contract.config, workflow_root=root)
    state: dict[str, Any] = {}
    if config.storage.state_path.exists():
        try:
            state = json.loads(config.storage.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    return {
        "workflow": config.workflow_name,
        "health": "ok" if state else "unknown",
        "workflow_root": str(root),
        "contract_path": str(contract.source_path),
        "state_path": str(config.storage.state_path),
        "audit_log_path": str(config.storage.audit_log_path),
        **build_lane_status(config=config, state=state),
        "canceling_count": 0,
    }


def reconcile_stalls(
    snapshot: Any, running: Mapping[str, object], now: float
) -> list[StallVerdict]:
    stall_cfg = (snapshot.config or {}).get("stall") or {}
    threshold_ms = stall_cfg.get("timeout_ms", _DEFAULT_TIMEOUT_MS)
    if threshold_ms <= 0:
        return []
    threshold_s = threshold_ms / 1000.0
    out: list[StallVerdict] = []
    for issue_id, entry in running.items():
        rt = getattr(entry, "runtime", None)
        if rt is None or not hasattr(rt, "last_activity_ts"):
            continue
        last = rt.last_activity_ts()
        baseline = last if last is not None else entry.started_at_monotonic
        elapsed = now - baseline
        if elapsed > threshold_s:
            out.append(
                StallVerdict(
                    issue_id=issue_id,
                    elapsed_seconds=elapsed,
                    threshold_seconds=threshold_s,
                    action="terminate",
                )
            )
    return out


def canonicalize(event_type: str) -> str:
    return str(event_type or "").strip()


def _load_policy(config: WorkflowConfig) -> WorkflowPolicy:
    contract = load_workflow_contract(config.workflow_root)
    return parse_workflow_policy(contract.prompt_template)


def _validate(config: WorkflowConfig) -> int:
    policy = _load_policy(config)
    missing = [
        actor
        for stage in config.stages.values()
        for actor in stage.actors
        if actor != config.orchestrator_actor and actor not in policy.actors
    ]
    if missing:
        raise RuntimeError(f"missing actor policy sections: {sorted(set(missing))}")
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    validate_state(config, state)
    print(f"{config.workflow_name} workflow valid")
    return 0


def _show(config: WorkflowConfig) -> int:
    print(json.dumps(config.raw, indent=2, sort_keys=True))
    return 0


def _status(config: WorkflowConfig) -> int:
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    payload = {
        "workflow": config.workflow_name,
        "workflow_root": str(config.workflow_root),
        "state_path": str(config.storage.state_path),
        "audit_log_path": str(config.storage.audit_log_path),
        **build_lane_status(config=config, state=state.to_dict()),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _lanes(config: WorkflowConfig, *, lane_id: str | None, attention_only: bool) -> int:
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    if lane_id:
        print(json.dumps(lane_by_id(state, lane_id), indent=2, sort_keys=True))
        return 0
    lanes = [
        lane_summary(lane)
        for lane in state.lanes.values()
        if isinstance(lane, dict)
        and (
            not attention_only or str(lane.get("status") or "") == "operator_attention"
        )
    ]
    print(
        json.dumps(
            {
                "workflow": config.workflow_name,
                "workflow_root": str(config.workflow_root),
                "attention_only": attention_only,
                "lanes": lanes,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _operator_retry(
    config: WorkflowConfig, *, lane_id: str, reason: str, target: str | None
) -> int:
    return _with_state_lock(
        config=config,
        owner_role="operator-retry",
        callback=lambda: _operator_retry_locked(
            config, lane_id=lane_id, reason=reason, target=target
        ),
    )


def _operator_retry_locked(
    config: WorkflowConfig, *, lane_id: str, reason: str, target: str | None
) -> int:
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    lane = lane_by_id(state, lane_id)
    status = str(lane.get("status") or "").strip()
    if status == "running":
        raise RuntimeError(f"lane {lane_id} is running; refusing duplicate work")
    if status == "retry_queued":
        raise RuntimeError(f"lane {lane_id} already has a queued retry")
    if status in {"complete", "released"}:
        raise RuntimeError(f"lane {lane_id} is terminal")
    decision = OrchestratorDecision(
        decision="retry",
        stage=lane_stage(lane) or config.first_stage,
        lane_id=lane_id,
        target=target,
        reason=reason,
        inputs={"feedback": reason, "operator_requested": True},
    )
    result = queue_lane_retry(config=config, lane=lane, decision=decision)
    _refresh_state_status(state, idle_reason="no active lanes")
    _save_tick(
        config=config,
        state=state,
        event="operator.retry",
        extra={"lane_id": lane_id, "result": result},
    )
    return 0


def _operator_release(config: WorkflowConfig, *, lane_id: str, reason: str) -> int:
    return _with_state_lock(
        config=config,
        owner_role="operator-release",
        callback=lambda: _operator_release_locked(
            config, lane_id=lane_id, reason=reason
        ),
    )


def _operator_release_locked(
    config: WorkflowConfig, *, lane_id: str, reason: str
) -> int:
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    lane = lane_by_id(state, lane_id)
    if str(lane.get("status") or "") == "running":
        raise RuntimeError(f"lane {lane_id} is running; refusing release")
    if str(lane.get("status") or "") in {"complete", "released"}:
        raise RuntimeError(f"lane {lane_id} is already terminal")
    release_lane(config=config, lane=lane, reason=reason)
    _refresh_state_status(state, idle_reason="no active lanes")
    _save_tick(
        config=config,
        state=state,
        event="operator.release",
        extra={"lane_id": lane_id, "reason": reason},
    )
    return 0


def _operator_complete(config: WorkflowConfig, *, lane_id: str, reason: str) -> int:
    return _with_state_lock(
        config=config,
        owner_role="operator-complete",
        callback=lambda: _operator_complete_locked(
            config, lane_id=lane_id, reason=reason
        ),
    )


def _operator_complete_locked(
    config: WorkflowConfig, *, lane_id: str, reason: str
) -> int:
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    lane = lane_by_id(state, lane_id)
    if str(lane.get("status") or "") == "running":
        raise RuntimeError(f"lane {lane_id} is running; refusing completion")
    if str(lane.get("status") or "") in {"complete", "released"}:
        raise RuntimeError(f"lane {lane_id} is already terminal")
    stage = config.stages.get(lane_stage(lane))
    if stage is None or stage.next_stage != "done":
        raise RuntimeError(
            f"lane {lane_id} is at stage {lane_stage(lane)!r}; "
            "operator completion is only allowed at a terminal handoff stage"
        )
    complete_lane(config=config, lane=lane, reason=reason)
    _refresh_state_status(state, idle_reason="no active lanes")
    _save_tick(
        config=config,
        state=state,
        event="operator.complete",
        extra={"lane_id": lane_id, "reason": reason},
    )
    return 0


def _tick(config: WorkflowConfig, *, orchestrator_output: str) -> int:
    return _with_state_lock(
        config=config,
        owner_role="workflow-tick",
        callback=lambda: _tick_locked(config, orchestrator_output=orchestrator_output),
    )


def _tick_locked(config: WorkflowConfig, *, orchestrator_output: str) -> int:
    policy = _load_policy(config)
    state = load_state(
        config.storage.state_path,
        workflow=config.workflow_name,
        first_stage=config.first_stage,
    )
    validate_state(config, state)
    reconcile = reconcile_lanes(config=config, state=state)
    intake = claim_new_lanes(config=config, state=state)
    if not active_lanes(state):
        state.status = "idle"
        state.idle_reason = intake.get("reason") or "no active lanes"
        _save_tick(
            config=config,
            state=state,
            event="idle",
            extra={"intake": intake, "reconcile": reconcile},
        )
        return 0

    state.status = "running"
    state.idle_reason = None
    _persist_runtime_state(config=config, state=state)
    output_override = _read_output_arg(orchestrator_output)
    ready_lanes = decision_ready_lanes(state)
    if not ready_lanes and not output_override:
        _save_tick(
            config=config,
            state=state,
            event="no_decision_ready",
            extra={
                "intake": intake,
                "reconcile": reconcile,
                "active_lane_count": len(active_lanes(state)),
                "reason": "active lanes are running, blocked, or waiting for retry time",
            },
        )
        return 0
    try:
        output = output_override or _run_orchestrator(
            config=config,
            policy=policy,
            state=state,
        )
        decisions = parse_orchestrator_decisions(output)
        results = _apply_decisions(
            config=config, policy=policy, state=state, decisions=decisions
        )
    except Exception as exc:
        _save_failed_tick(
            config=config,
            state=state,
            intake=intake,
            reconcile=reconcile,
            error=exc,
        )
        raise
    _refresh_state_status(state, idle_reason="no active lanes")
    _save_tick(
        config=config,
        state=state,
        event="tick",
        extra={
            "intake": intake,
            "reconcile": reconcile,
            "decisions": [decision.to_dict() for decision in decisions],
            "results": results,
        },
    )
    return 0


def _refresh_state_status(state: WorkflowState, *, idle_reason: str) -> None:
    if active_lanes(state):
        state.status = "running"
        state.idle_reason = None
        return
    state.status = "idle"
    state.idle_reason = idle_reason


def _save_failed_tick(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    intake: dict[str, Any],
    reconcile: dict[str, Any],
    error: Exception,
) -> None:
    _persist_runtime_state(config=config, state=state)
    append_audit(
        config.storage.audit_log_path,
        {
            "event": f"{config.workflow_name}.tick_failed",
            "state": state.to_dict(),
            "intake": intake,
            "reconcile": reconcile,
            "error": str(error),
            "error_type": type(error).__name__,
        },
    )


def _save_tick(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    event: str,
    extra: dict[str, Any] | None = None,
) -> None:
    _save_state_event(config=config, state=state, event=event, extra=extra)
    print(json.dumps(state.to_dict(), indent=2, sort_keys=True))


def _run_orchestrator(
    *, config: WorkflowConfig, policy: WorkflowPolicy, state: WorkflowState
) -> str:
    prompt = build_orchestrator_prompt(
        config=config,
        policy=policy,
        state=state,
        facts=build_workflow_facts(config, state),
    )
    actor = config.actors[config.orchestrator_actor]
    return (
        build_actor_runtime(config=config, actor=actor)
        .run(actor=actor, prompt=prompt, stage_name="orchestrator")
        .output
    )


def _read_output_arg(value: str) -> str:
    if not value:
        return ""
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8-sig")
    return value


def _apply_decisions(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    decisions: list[OrchestratorDecision],
) -> list[dict[str, Any]]:
    actor_usage = actor_concurrency_usage(config=config, state=state)
    planned = _plan_decisions(
        config=config,
        state=state,
        decisions=decisions,
        actor_usage=actor_usage,
    )
    dispatch_counts = dict(actor_usage)
    results: list[dict[str, Any]] = []
    for decision, lane in planned:
        state.orchestrator_decisions.append(decision.to_dict())
        result = _apply_decision(
            config=config,
            policy=policy,
            state=state,
            lane=lane,
            decision=decision,
            dispatch_counts=dispatch_counts,
        )
        results.append(result)
    return results


def _plan_decisions(
    *,
    config: WorkflowConfig,
    state: WorkflowState,
    decisions: list[OrchestratorDecision],
    actor_usage: dict[str, int] | None = None,
) -> list[tuple[OrchestratorDecision, dict[str, Any]]]:
    planned: list[tuple[OrchestratorDecision, dict[str, Any]]] = []
    seen_lanes: set[str] = set()
    dispatch_counts: dict[str, int] = dict(actor_usage or {})
    for decision in decisions:
        lane = lane_for_decision(state=state, decision=decision)
        lane_id = str(lane.get("lane_id") or "").strip()
        if not lane_id:
            raise RuntimeError("orchestrator selected a lane without lane_id")
        if lane_id in seen_lanes:
            raise RuntimeError(
                f"orchestrator returned multiple decisions for lane {lane_id}; "
                "return at most one decision per lane per tick"
            )
        seen_lanes.add(lane_id)
        validate_decision_for_lane(config=config, lane=lane, decision=decision)
        if decision.decision == "run_actor":
            actor_name = target_or_single(
                target=decision.target,
                values=config.stages[decision.stage].actors,
                kind="actor",
            )
            validate_actor_capacity(
                config=config, actor_name=actor_name, dispatch_counts=dispatch_counts
            )
            dispatch_counts[actor_name] = dispatch_counts.get(actor_name, 0) + 1
        planned.append((decision, lane))
    return planned


def _apply_decision(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    lane: dict[str, Any],
    decision: OrchestratorDecision,
    dispatch_counts: dict[str, int],
) -> dict[str, Any]:
    if decision.decision == "complete":
        complete_lane(config=config, lane=lane, reason=decision.reason or "completed")
        return {"lane_id": lane["lane_id"], "decision": "complete"}
    if decision.decision == "operator_attention":
        lane["operator_attention"] = {
            "message": decision.operator_message,
            "reason": decision.reason,
        }
        set_lane_status(
            config=config,
            lane=lane,
            status="operator_attention",
            reason=decision.reason or "operator attention",
        )
        return {"lane_id": lane["lane_id"], "decision": "operator_attention"}
    if decision.decision == "retry":
        return queue_lane_retry(config=config, lane=lane, decision=decision)
    if decision.decision == "advance":
        target_stage = (
            decision.target if decision.target in config.stages else decision.stage
        )
        advance_lane(config=config, lane=lane, target=target_stage)
        return {"lane_id": lane["lane_id"], "decision": "advance"}
    if decision.decision == "run_actor":
        if decision.stage != lane_stage(lane):
            advance_lane(config=config, lane=lane, target=decision.stage)
        actor_name = target_or_single(
            target=decision.target,
            values=config.stages[lane_stage(lane)].actors,
            kind="actor",
        )
        validate_actor_capacity(
            config=config, actor_name=actor_name, dispatch_counts=dispatch_counts
        )
        dispatch_counts[actor_name] = dispatch_counts.get(actor_name, 0) + 1
        if _actor_dispatch_mode(config) == "background":
            result = dispatch_stage_actor_background(
                config=config,
                policy=policy,
                state=state,
                lane=lane,
                actor_name=actor_name,
                inputs=decision.inputs,
            )
        else:
            result = run_stage_actor(
                config=config,
                policy=policy,
                state=state,
                lane=lane,
                actor_name=actor_name,
                inputs=decision.inputs,
            )
        return {
            "lane_id": lane["lane_id"],
            "decision": "run_actor",
            "target": actor_name,
            "result": result,
        }
    if decision.decision == "run_action":
        if decision.stage != lane_stage(lane):
            advance_lane(config=config, lane=lane, target=decision.stage)
        inputs = lane_retry_inputs(lane=lane, inputs=decision.inputs)
        action_name = target_or_single(
            target=decision.target,
            values=config.stages[lane_stage(lane)].actions,
            kind="action",
        )
        result = apply_action_result(
            config=config,
            state=state,
            lane=lane,
            action_name=action_name,
            inputs=inputs,
        )
        return {
            "lane_id": lane["lane_id"],
            "decision": "run_action",
            "target": action_name,
            "result": result,
        }
    raise RuntimeError(f"unhandled orchestrator decision {decision.decision}")
