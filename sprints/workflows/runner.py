"""Workflow execution mechanics, state persistence, status, and stall hooks."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from workflows.actions import run_action
from workflows.actors import build_actor_runtime
from workflows.config import AgenticConfig, AgenticConfigError
from workflows.contracts import (
    WorkflowPolicy,
    load_workflow_contract,
    parse_workflow_policy,
)
from workflows.orchestrator import (
    OrchestratorDecision,
    build_actor_prompt,
    build_orchestrator_prompt,
)

SPRINTS_STALL_DETECTED = "sprints.stall.detected"
SPRINTS_STALL_TERMINATED = "sprints.stall.terminated"
_DEFAULT_TIMEOUT_MS = 300_000


@dataclass
class WorkflowState:
    workflow: str = "agentic"
    current_stage: str = ""
    status: str = "running"
    attempt: int = 1
    stage_outputs: dict[str, Any] = field(default_factory=dict)
    actor_outputs: dict[str, Any] = field(default_factory=dict)
    action_results: dict[str, Any] = field(default_factory=dict)
    orchestrator_decisions: list[dict[str, Any]] = field(default_factory=list)
    operator_attention: dict[str, Any] | None = None

    @classmethod
    def initial(cls, first_stage: str) -> "WorkflowState":
        return cls(current_stage=first_stage)

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
    if not isinstance(workspace, AgenticConfig):
        raise TypeError(
            f"agentic CLI expected AgenticConfig, got {type(workspace).__name__}"
        )
    parser = argparse.ArgumentParser(prog="agentic")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("validate")
    subcommands.add_parser("show")
    tick_parser = subcommands.add_parser("tick")
    tick_parser.add_argument("--orchestrator-output", default="")
    args = parser.parse_args(argv)

    if args.command == "validate":
        return _validate(workspace)
    if args.command == "show":
        return _show(workspace)
    if args.command == "tick":
        return _tick(workspace, orchestrator_output=args.orchestrator_output)
    raise RuntimeError(f"unhandled command {args.command}")


def load_state(path: Path, *, first_stage: str) -> WorkflowState:
    if not path.exists():
        return WorkflowState.initial(first_stage)
    return WorkflowState.from_dict(json.loads(path.read_text(encoding="utf-8")))


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
    *, config: AgenticConfig, state: WorkflowState, inputs: dict[str, Any]
) -> dict[str, Any]:
    return {"workflow": state.to_dict(), "config": config.raw, **inputs}


def run_stage_actor(
    *,
    config: AgenticConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    actor_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    actor = config.actors[actor_name]
    actor_policy = policy.actors.get(actor_name)
    if actor_policy is None:
        raise RuntimeError(f"missing actor policy section for {actor_name}")
    prompt = build_actor_prompt(
        actor_policy=actor_policy,
        variables=actor_variables(config=config, state=state, inputs=inputs),
    )
    raw_output = build_actor_runtime(config=config, actor=actor).run(
        actor=actor, prompt=prompt, stage_name=state.current_stage
    )
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"actor {actor_name} returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"actor {actor_name} output must be a JSON object")
    state.actor_outputs[actor_name] = parsed
    state.stage_outputs[state.current_stage] = {
        **dict(state.stage_outputs.get(state.current_stage) or {}),
        "last_actor": actor_name,
    }
    return parsed


def apply_action_result(
    *,
    config: AgenticConfig,
    state: WorkflowState,
    action_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    result = run_action(config.actions[action_name], inputs)
    payload = {"ok": result.ok, "output": result.output}
    state.action_results[action_name] = payload
    state.stage_outputs[state.current_stage] = {
        **dict(state.stage_outputs.get(state.current_stage) or {}),
        "last_action": action_name,
    }
    return payload


def validate_current_stage(config: AgenticConfig, state: WorkflowState) -> None:
    if state.current_stage not in config.stages:
        raise RuntimeError(f"unknown current stage: {state.current_stage}")
    validate_stage_gates(config, state.current_stage)


def validate_stage_gates(config: AgenticConfig, stage_name: str) -> None:
    stage = config.stages[stage_name]
    for gate_name in stage.gates:
        gate = config.gates[gate_name]
        if gate.type != "orchestrator-evaluated":
            raise AgenticConfigError(
                f"unsupported gate type for {gate_name}: {gate.type}"
            )


def ensure_workflow_state_files(
    workflow_root: Path, config: dict[str, Any] | None = None
) -> dict[str, str]:
    root = Path(workflow_root).expanduser().resolve()
    raw = config if config is not None else load_workflow_contract(root).config
    typed = AgenticConfig.from_raw(raw=raw, workflow_root=root)
    if not typed.storage.state_path.exists():
        save_state(typed.storage.state_path, WorkflowState.initial(typed.first_stage))
    typed.storage.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    typed.storage.audit_log_path.touch(exist_ok=True)
    return {
        "state": str(typed.storage.state_path),
        "audit_log": str(typed.storage.audit_log_path),
    }


def build_status(workflow_root: Path) -> dict[str, Any]:
    root = Path(workflow_root).expanduser().resolve()
    contract = load_workflow_contract(root)
    config = AgenticConfig.from_raw(raw=contract.config, workflow_root=root)
    state: dict[str, Any] = {}
    if config.storage.state_path.exists():
        try:
            state = json.loads(config.storage.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    return {
        "workflow": "agentic",
        "health": "ok" if state else "unknown",
        "workflow_root": str(root),
        "contract_path": str(contract.source_path),
        "state_path": str(config.storage.state_path),
        "audit_log_path": str(config.storage.audit_log_path),
        "current_stage": state.get("current_stage"),
        "status": state.get("status"),
        "running_count": 1 if state.get("status") == "running" else 0,
        "retry_count": int(state.get("attempt") or 0),
        "canceling_count": 0,
        "total_tokens": 0,
        "latest_runs": [],
        "runtime_sessions": [],
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


def _load_policy(config: AgenticConfig) -> WorkflowPolicy:
    contract = load_workflow_contract(config.workflow_root)
    return parse_workflow_policy(contract.prompt_template)


def _validate(config: AgenticConfig) -> int:
    policy = _load_policy(config)
    missing = [
        actor
        for stage in config.stages.values()
        for actor in stage.actors
        if actor != config.orchestrator_actor and actor not in policy.actors
    ]
    if missing:
        raise RuntimeError(f"missing actor policy sections: {sorted(set(missing))}")
    state = load_state(config.storage.state_path, first_stage=config.first_stage)
    validate_current_stage(config, state)
    print("agentic workflow valid")
    return 0


def _show(config: AgenticConfig) -> int:
    print(json.dumps(config.raw, indent=2, sort_keys=True))
    return 0


def _tick(config: AgenticConfig, *, orchestrator_output: str) -> int:
    policy = _load_policy(config)
    state = load_state(config.storage.state_path, first_stage=config.first_stage)
    validate_current_stage(config, state)
    output = _read_output_arg(orchestrator_output) or _run_orchestrator(
        config=config,
        policy=policy,
        state=state,
    )
    decision = OrchestratorDecision.from_output(output)
    _apply_decision(config=config, policy=policy, state=state, decision=decision)
    save_state(config.storage.state_path, state)
    append_audit(
        config.storage.audit_log_path,
        {
            "event": "agentic.tick",
            "decision": decision.to_dict(),
            "state": state.to_dict(),
        },
    )
    print(json.dumps(state.to_dict(), indent=2, sort_keys=True))
    return 0


def _run_orchestrator(
    *, config: AgenticConfig, policy: WorkflowPolicy, state: WorkflowState
) -> str:
    prompt = build_orchestrator_prompt(
        config=config, policy=policy, state=state, facts={}
    )
    actor = config.actors[config.orchestrator_actor]
    return build_actor_runtime(config=config, actor=actor).run(
        actor=actor, prompt=prompt, stage_name=state.current_stage
    )


def _read_output_arg(value: str) -> str:
    if not value:
        return ""
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8-sig")
    return value


def _apply_decision(
    *,
    config: AgenticConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    decision: OrchestratorDecision,
) -> None:
    if decision.stage != state.current_stage:
        raise RuntimeError(
            f"orchestrator decision stage {decision.stage!r} does not match current stage {state.current_stage!r}"
        )
    state.orchestrator_decisions.append(decision.to_dict())
    if decision.decision == "complete":
        state.status = "complete"
    elif decision.decision == "operator_attention":
        state.status = "operator_attention"
        state.operator_attention = {
            "message": decision.operator_message,
            "reason": decision.reason,
        }
    elif decision.decision == "retry":
        state.attempt += 1
    elif decision.decision == "advance":
        _advance(config=config, state=state, target=decision.target)
    elif decision.decision == "run_actor":
        actor_name = _target_or_single(
            target=decision.target,
            values=config.stages[state.current_stage].actors,
            kind="actor",
        )
        run_stage_actor(
            config=config,
            policy=policy,
            state=state,
            actor_name=actor_name,
            inputs=decision.inputs,
        )
    elif decision.decision == "run_action":
        action_name = _target_or_single(
            target=decision.target,
            values=config.stages[state.current_stage].actions,
            kind="action",
        )
        apply_action_result(
            config=config, state=state, action_name=action_name, inputs=decision.inputs
        )
    else:
        raise RuntimeError(f"unhandled orchestrator decision {decision.decision}")


def _advance(
    *, config: AgenticConfig, state: WorkflowState, target: str | None
) -> None:
    next_stage = target or config.stages[state.current_stage].next_stage
    if not next_stage:
        raise RuntimeError(f"stage {state.current_stage} has no next stage")
    if next_stage == "done":
        state.status = "complete"
        return
    if next_stage not in config.stages:
        raise RuntimeError(f"unknown target stage: {next_stage}")
    state.current_stage = next_stage


def _target_or_single(*, target: str | None, values: tuple[str, ...], kind: str) -> str:
    if target:
        if target not in values:
            raise RuntimeError(
                f"orchestrator selected {kind} {target!r}, not declared on current stage"
            )
        return target
    if len(values) == 1:
        return values[0]
    raise RuntimeError(f"orchestrator decision must target one {kind}")
