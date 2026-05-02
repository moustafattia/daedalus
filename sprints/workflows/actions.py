"""Deterministic actions available to Sprints workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from subprocess import run
from typing import Any, Callable

from trackers import build_code_host_client
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
        "code-host.create-pull-request": _run_create_pull_request,
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
    if not isinstance(command, list) or not all(
        isinstance(part, str) for part in command
    ):
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


def _run_create_pull_request(
    action: ActionConfig, inputs: dict[str, Any]
) -> ActionResult:
    config = _mapping(inputs.get("config"))
    code_host_cfg = _mapping(config.get("code-host"))
    if not code_host_cfg:
        return _action_error(
            action, "code-host.create-pull-request requires code-host config"
        )
    repository_cfg = _mapping(config.get("repository"))
    repo_path = _repo_path(repository_cfg)
    head = _first_text(
        inputs,
        action.raw,
        keys=("head", "branch", "branch_name", "branch-name"),
        nested=("implementation", "issue"),
    )
    if not head:
        return _action_error(
            action,
            "code-host.create-pull-request requires head/branch input or implementation.branch_name",
        )
    title = _first_text(
        inputs,
        action.raw,
        keys=("title", "pr_title", "pr-title"),
        nested=("implementation", "issue"),
    )
    if not title:
        title = f"Change delivery: {head}"
    body = _first_text(
        inputs,
        action.raw,
        keys=("body", "pr_body", "pr-body", "summary"),
        nested=("implementation",),
    )
    if not body:
        body = _default_pull_request_body(inputs)
    try:
        client = build_code_host_client(
            workflow_root=Path(str(inputs.get("workflow_root") or ".")),
            code_host_cfg=code_host_cfg,
            repo_path=repo_path,
        )
        url = client.create_pull_request(head=head, title=title, body=body)
    except Exception as exc:
        return _action_error(action, f"pull request creation failed: {exc}")
    return ActionResult(
        name=action.name,
        ok=True,
        output={"url": url, "head": head, "title": title, "body": body},
    )


def _action_error(action: ActionConfig, message: str) -> ActionResult:
    return ActionResult(name=action.name, ok=False, output={"error": message})


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _repo_path(repository_cfg: dict[str, Any]) -> Path | None:
    raw = str(
        repository_cfg.get("local-path") or repository_cfg.get("local_path") or ""
    ).strip()
    return Path(raw).expanduser() if raw else None


def _first_text(
    *sources: dict[str, Any],
    keys: tuple[str, ...],
    nested: tuple[str, ...] = (),
) -> str:
    for source in sources:
        value = _first_text_from_mapping(source, keys)
        if value:
            return value
        for nested_key in nested:
            value = _first_text_from_mapping(_mapping(source.get(nested_key)), keys)
            if value:
                return value
    return ""


def _first_text_from_mapping(source: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _default_pull_request_body(inputs: dict[str, Any]) -> str:
    implementation = _mapping(inputs.get("implementation"))
    issue = _mapping(inputs.get("issue"))
    lines = []
    if issue:
        identifier = str(issue.get("identifier") or issue.get("id") or "").strip()
        title = str(issue.get("title") or "").strip()
        if identifier or title:
            lines.append(f"Issue: {identifier} {title}".strip())
    summary = str(implementation.get("summary") or "").strip()
    if summary:
        lines.extend(["", summary])
    verification = implementation.get("verification")
    if isinstance(verification, list) and verification:
        lines.append("")
        lines.append("Verification:")
        lines.extend(f"- {item}" for item in verification)
    return "\n".join(lines).strip() or "Created by Sprints change-delivery."
