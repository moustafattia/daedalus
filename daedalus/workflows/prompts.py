"""Small prompt-template helpers shared by workflows."""
from __future__ import annotations

import json
import re
from typing import Any

from workflows.config import AgenticConfig
from workflows.contract import ActorPolicy, WorkflowPolicy
from workflows.state import WorkflowState


def render_prompt_template(
    *,
    prompt_template: str,
    variables: dict[str, Any],
    default_template: str = "",
) -> str:
    """Render simple ``{{ dotted.variable }}`` placeholders.

    This is intentionally not a full template engine. Control blocks and
    filters are rejected so workflow policy stays predictable.
    """

    template = str(prompt_template or "").strip()
    if not template:
        template = default_template
    if "{%" in template or "%}" in template:
        raise RuntimeError("template_parse_error: control blocks are not supported")

    def replace(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        if "|" in expr:
            raise RuntimeError(f"template_render_error: unsupported filter in {expr!r}")
        value = _resolve_variable(expr, variables)
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        return str(value)

    rendered = re.sub(r"{{\s*([^{}]+?)\s*}}", replace, template)
    if "{{" in rendered or "}}" in rendered:
        raise RuntimeError("template_parse_error: unbalanced template delimiters")
    return rendered.strip() + "\n"


def _resolve_variable(expr: str, variables: dict[str, Any]) -> Any:
    parts = expr.split(".")
    value: Any = variables
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            raise RuntimeError(f"template_render_error: unknown variable {expr!r}")
        value = value[part]
    return value


def build_orchestrator_prompt(
    *,
    config: AgenticConfig,
    policy: WorkflowPolicy,
    state: WorkflowState,
    facts: dict[str, Any],
) -> str:
    payload = {
        "config": config.raw,
        "state": state.to_dict(),
        "facts": facts,
        "available_decisions": [
            "advance",
            "retry",
            "run_actor",
            "run_action",
            "operator_attention",
            "complete",
        ],
    }
    return (
        "# Orchestrator Policy\n\n"
        f"{policy.orchestrator}\n\n"
        "# Current Context\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
    )


def build_actor_prompt(
    *,
    actor_policy: ActorPolicy,
    variables: dict[str, Any],
) -> str:
    return render_prompt_template(prompt_template=actor_policy.body, variables=variables)
