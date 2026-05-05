"""Workflow CLI command router."""

from __future__ import annotations

import argparse
from pathlib import Path

from sprints.core.config import WorkflowConfig
from sprints.workflows.dispatch import run_actor_worker
from sprints.workflows.inspection import (
    lanes_command,
    show_command,
    status_command,
    validate_command,
)
from sprints.workflows.operator import operator_complete, operator_release, operator_retry
from sprints.workflows.ticks import tick


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
        return validate_command(workspace)
    if args.command == "show":
        return show_command(workspace)
    if args.command == "status":
        return status_command(workspace)
    if args.command == "lanes":
        return lanes_command(
            workspace, lane_id=args.lane_id, attention_only=bool(args.attention)
        )
    if args.command == "retry":
        return operator_retry(
            workspace,
            lane_id=args.lane_id,
            reason=args.reason,
            target=args.target,
        )
    if args.command == "release":
        return operator_release(workspace, lane_id=args.lane_id, reason=args.reason)
    if args.command == "complete":
        return operator_complete(workspace, lane_id=args.lane_id, reason=args.reason)
    if args.command == "tick":
        return tick(workspace, orchestrator_output=args.orchestrator_output)
    if args.command == "actor-run":
        return run_actor_worker(
            workspace,
            lane_id=args.lane_id,
            actor_name=args.actor,
            stage_name=args.stage,
            inputs_file=Path(args.inputs_file),
        )
    raise RuntimeError(f"unhandled command {args.command}")
