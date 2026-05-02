"""CLI for the generic agentic workflow."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from workflows.agentic.actors import build_actor_runtime
from workflows.agentic.config import AgenticConfig
from workflows.agentic.contract import AgenticPolicy, parse_agentic_policy
from workflows.agentic.orchestrator import OrchestratorDecision
from workflows.agentic.prompts import build_orchestrator_prompt
from workflows.agentic.stages import (
    apply_action_result,
    run_stage_actor,
    validate_current_stage,
)
from workflows.agentic.state import WorkflowState, append_audit, load_state, save_state
from workflows.contract import load_workflow_contract


def main(workspace: object, argv: list[str]) -> int:
    if not isinstance(workspace, AgenticConfig):
        raise TypeError(f"agentic CLI expected AgenticConfig, got {type(workspace).__name__}")
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


def _load_policy(config: AgenticConfig):
    contract = load_workflow_contract(config.workflow_root)
    return parse_agentic_policy(contract.prompt_template)


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
    output = _read_output_arg(orchestrator_output) or _run_local_orchestrator(
        config=config,
        policy=policy,
        state=state,
    )
    decision = OrchestratorDecision.from_output(output)
    _apply_decision(config=config, policy=policy, state=state, decision=decision)
    save_state(config.storage.state_path, state)
    append_audit(
        config.storage.audit_log_path,
        {"event": "agentic.tick", "decision": decision.to_dict(), "state": state.to_dict()},
    )
    print(json.dumps(state.to_dict(), indent=2, sort_keys=True))
    return 0


def _run_local_orchestrator(
    *,
    config: AgenticConfig,
    policy: AgenticPolicy,
    state: WorkflowState,
) -> str:
    prompt = build_orchestrator_prompt(config=config, policy=policy, state=state, facts={})
    actor = config.actors[config.orchestrator_actor]
    runtime = build_actor_runtime(config=config, actor=actor)
    default_output = json.dumps(
        {
            "decision": "complete",
            "stage": state.current_stage,
            "target": None,
            "reason": "local smoke complete",
            "inputs": {},
            "operator_message": None,
        },
        sort_keys=True,
    )
    if actor.raw.get("output") or config.runtimes[actor.runtime].raw.get("output"):
        return runtime.run(actor=actor, prompt=prompt)
    return default_output


def _read_output_arg(value: str) -> str:
    if not value:
        return ""
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8-sig")
    return value


def _apply_decision(
    *,
    config: AgenticConfig,
    policy: AgenticPolicy,
    state: WorkflowState,
    decision: OrchestratorDecision,
) -> None:
    if decision.stage != state.current_stage:
        raise RuntimeError(
            f"orchestrator decision stage {decision.stage!r} does not match "
            f"current stage {state.current_stage!r}"
        )
    state.orchestrator_decisions.append(decision.to_dict())
    if decision.decision == "complete":
        state.status = "complete"
        return
    if decision.decision == "operator_attention":
        state.status = "operator_attention"
        state.operator_attention = {
            "message": decision.operator_message,
            "reason": decision.reason,
        }
        return
    if decision.decision == "retry":
        state.attempt += 1
        return
    if decision.decision == "advance":
        _advance(config=config, state=state, target=decision.target)
        return
    if decision.decision == "run_actor":
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
        return
    if decision.decision == "run_action":
        action_name = _target_or_single(
            target=decision.target,
            values=config.stages[state.current_stage].actions,
            kind="action",
        )
        apply_action_result(
            config=config,
            state=state,
            action_name=action_name,
            inputs=decision.inputs,
        )
        return
    raise RuntimeError(f"unhandled orchestrator decision {decision.decision}")


def _advance(*, config: AgenticConfig, state: WorkflowState, target: str | None) -> None:
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
            raise RuntimeError(f"orchestrator selected {kind} {target!r}, not declared on current stage")
        return target
    if len(values) == 1:
        return values[0]
    raise RuntimeError(f"orchestrator decision must target one {kind}")
