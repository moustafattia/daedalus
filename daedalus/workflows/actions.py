"""Deterministic actions available to agentic workflows."""
from __future__ import annotations

from dataclasses import dataclass
from subprocess import run
from typing import Any, Callable

from workflows.config import ActionConfig


@dataclass(frozen=True)
class ActionResult:
    name: str
    ok: bool
    output: dict[str, Any]


ActionHandler = Callable[[ActionConfig, dict[str, Any]], ActionResult]


def run_action(action: ActionConfig, inputs: dict[str, Any]) -> ActionResult:
    handlers: dict[str, ActionHandler] = {
        "noop": _run_noop,
        "command": _run_command,
        "comment": _run_comment,
    }
    handler = handlers.get(action.type)
    if handler is None:
        return ActionResult(
            name=action.name,
            ok=False,
            output={"error": f"unknown action type {action.type}"},
        )
    return handler(action, inputs)


def _run_noop(action: ActionConfig, inputs: dict[str, Any]) -> ActionResult:
    return ActionResult(name=action.name, ok=True, output={"inputs": inputs})


def _run_command(action: ActionConfig, inputs: dict[str, Any]) -> ActionResult:
    command = action.raw.get("command") or inputs.get("command")
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        return ActionResult(
            name=action.name,
            ok=False,
            output={"error": "command action requires a string list"},
        )
    completed = run(command, capture_output=True, text=True, check=False)
    return ActionResult(
        name=action.name,
        ok=completed.returncode == 0,
        output={
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        },
    )


def _run_comment(action: ActionConfig, inputs: dict[str, Any]) -> ActionResult:
    return ActionResult(
        name=action.name,
        ok=True,
        output={"comment": inputs.get("comment") or action.raw.get("comment")},
    )
