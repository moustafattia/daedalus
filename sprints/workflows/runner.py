"""Workflow execution mechanics, lane state, status, and stall hooks."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

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
from workflows.lanes import (
    active_lanes,
    advance_lane,
    apply_actor_output_status,
    build_lane_status,
    build_workflow_facts,
    claim_new_lanes,
    complete_lane,
    lane_by_id,
    lane_for_decision,
    lane_mapping,
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
        "pull_request": lane.get("pull_request") or {},
        "retry": lane.get("pending_retry") or inputs.get("retry") or {},
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
    inputs = lane_retry_inputs(lane=lane, inputs=inputs)
    stage_name = lane_stage(lane)
    actor = config.actors[actor_name]
    actor_policy = policy.actors.get(actor_name)
    if actor_policy is None:
        raise RuntimeError(f"missing actor policy section for {actor_name}")
    lane_id = str(lane.get("lane_id") or "")
    runtime_plan = actor_runtime_plan(
        config=config,
        actor=actor,
        stage_name=stage_name,
        lane_id=lane_id,
        resume_session_id=_resume_session_id(lane),
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
            artifacts={"actor": actor_name, "stage": stage_name},
        )
        _persist_runtime_state(config=config, state=state)
        raise
    record_actor_runtime_result(
        config=config,
        lane=lane,
        runtime_meta={
            **_runtime_plan_meta(runtime_plan),
            **_runtime_result_meta(runtime_result),
        },
        status="completed",
    )
    raw_output = runtime_result.output
    try:
        parsed = parse_actor_output(raw_output)
    except json.JSONDecodeError as exc:
        set_lane_status(
            config=config,
            lane=lane,
            status="operator_attention",
            reason=f"actor {actor_name} returned invalid JSON: {exc}",
        )
        _persist_runtime_state(config=config, state=state)
        raise RuntimeError(f"actor {actor_name} returned invalid JSON: {exc}") from exc
    except TypeError as exc:
        set_lane_status(
            config=config,
            lane=lane,
            status="operator_attention",
            reason=f"actor {actor_name} output was not an object",
        )
        _persist_runtime_state(config=config, state=state)
        raise RuntimeError(str(exc)) from exc
    record_actor_output(config=config, lane=lane, actor_name=actor_name, output=parsed)
    apply_actor_output_status(
        config=config, lane=lane, actor_name=actor_name, output=parsed
    )
    _persist_runtime_state(config=config, state=state)
    return parsed


def _persist_runtime_state(*, config: WorkflowConfig, state: WorkflowState) -> None:
    save_state(config.storage.state_path, state)
    save_scheduler_snapshot(config=config, state=state)


def _resume_session_id(lane: dict[str, Any]) -> str | None:
    session = (
        lane.get("runtime_session")
        if isinstance(lane.get("runtime_session"), dict)
        else {}
    )
    value = (
        session.get("thread_id") or lane.get("thread_id") or session.get("session_id")
    )
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
    _save_tick(
        config=config,
        state=state,
        event="operator.retry",
        extra={"lane_id": lane_id, "result": result},
    )
    return 0


def _operator_release(config: WorkflowConfig, *, lane_id: str, reason: str) -> int:
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
    _save_tick(
        config=config,
        state=state,
        event="operator.release",
        extra={"lane_id": lane_id, "reason": reason},
    )
    return 0


def _operator_complete(config: WorkflowConfig, *, lane_id: str, reason: str) -> int:
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
    _save_tick(
        config=config,
        state=state,
        event="operator.complete",
        extra={"lane_id": lane_id, "reason": reason},
    )
    return 0


def _tick(config: WorkflowConfig, *, orchestrator_output: str) -> int:
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
    output = _read_output_arg(orchestrator_output) or _run_orchestrator(
        config=config,
        policy=policy,
        state=state,
    )
    decisions = parse_orchestrator_decisions(output)
    results = _apply_decisions(
        config=config, policy=policy, state=state, decisions=decisions
    )
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


def _save_tick(
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
    planned = _plan_decisions(config=config, state=state, decisions=decisions)
    dispatch_counts = {"implementer": 0, "reviewer": 0}
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
) -> list[tuple[OrchestratorDecision, dict[str, Any]]]:
    planned: list[tuple[OrchestratorDecision, dict[str, Any]]] = []
    seen_lanes: set[str] = set()
    dispatch_counts: dict[str, int] = {}
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
