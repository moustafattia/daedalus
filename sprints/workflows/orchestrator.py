"""Orchestrator prompt rendering and decision parsing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import json
import re

from workflows.config import WorkflowConfig
from workflows.contracts import ActorPolicy, WorkflowPolicy


class OrchestratorDecisionError(RuntimeError):
    """Raised when an orchestrator response is not a valid decision."""


@dataclass(frozen=True)
class OrchestratorDecision:
    decision: str
    stage: str
    target: str | None = None
    reason: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    operator_message: str | None = None

    @classmethod
    def from_output(cls, output: str) -> "OrchestratorDecision":
        try:
            raw = json.loads(output)
        except json.JSONDecodeError as exc:
            raise OrchestratorDecisionError(
                f"orchestrator returned invalid JSON: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise OrchestratorDecisionError(
                "orchestrator decision must be a JSON object"
            )
        decision = str(raw.get("decision", ""))
        if decision not in {
            "advance",
            "retry",
            "run_actor",
            "run_action",
            "operator_attention",
            "complete",
        }:
            raise OrchestratorDecisionError(
                f"unsupported orchestrator decision: {decision}"
            )
        stage = str(raw.get("stage", ""))
        if not stage:
            raise OrchestratorDecisionError("orchestrator decision is missing stage")
        inputs = raw.get("inputs") or {}
        if not isinstance(inputs, dict):
            raise OrchestratorDecisionError(
                "orchestrator decision inputs must be an object"
            )
        target = raw.get("target")
        return cls(
            decision=decision,
            stage=stage,
            target=str(target) if target is not None else None,
            reason=str(raw.get("reason") or ""),
            inputs=inputs,
            operator_message=raw.get("operator_message"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def render_prompt_template(
    *,
    prompt_template: str,
    variables: dict[str, Any],
    default_template: str = "",
) -> str:
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


def build_orchestrator_prompt(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: Any,
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


def build_actor_prompt(*, actor_policy: ActorPolicy, variables: dict[str, Any]) -> str:
    return render_prompt_template(
        prompt_template=actor_policy.body, variables=variables
    )


def _resolve_variable(expr: str, variables: dict[str, Any]) -> Any:
    parts = expr.split(".")
    value: Any = variables
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            raise RuntimeError(f"template_render_error: unknown variable {expr!r}")
        value = value[part]
    return value
