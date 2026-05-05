"""Orchestrator prompt rendering and decision parsing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import json
import re

from workflows.config import WorkflowConfig
from workflows.contracts import ActorPolicy, WorkflowPolicy
from workflows.prompt_context import (
    APP_SERVER_INPUT_LIMIT_CHARS,
    PromptBuild,
    build_orchestrator_payload,
    orchestrator_prompt_budget,
    prompt_size_report,
)


class OrchestratorDecisionError(RuntimeError):
    """Raised when an orchestrator response is not a valid decision."""


@dataclass(frozen=True)
class OrchestratorDecision:
    decision: str
    stage: str
    lane_id: str | None = None
    target: str | None = None
    reason: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    operator_message: str | None = None

    @classmethod
    def from_output(cls, output: str) -> "OrchestratorDecision":
        decisions = parse_orchestrator_decisions(output)
        if len(decisions) != 1:
            raise OrchestratorDecisionError(
                f"expected one orchestrator decision, got {len(decisions)}"
            )
        return decisions[0]

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "OrchestratorDecision":
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
        lane_id = raw.get("lane_id") or raw.get("lane-id")
        return cls(
            decision=decision,
            stage=stage,
            lane_id=str(lane_id) if lane_id not in (None, "") else None,
            target=str(target) if target is not None else None,
            reason=str(raw.get("reason") or ""),
            inputs=inputs,
            operator_message=raw.get("operator_message"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_orchestrator_decisions(output: str) -> list[OrchestratorDecision]:
    try:
        raw = json.loads(output)
    except json.JSONDecodeError as exc:
        raw = _parse_trailing_json_object(output)
        if raw is None:
            raise OrchestratorDecisionError(
                f"orchestrator returned invalid JSON: {exc}"
            ) from exc
    if not isinstance(raw, dict):
        raise OrchestratorDecisionError("orchestrator decision must be a JSON object")
    raw_decisions = raw.get("decisions")
    if raw_decisions is None:
        return [OrchestratorDecision.from_mapping(raw)]
    if not isinstance(raw_decisions, list):
        raise OrchestratorDecisionError("orchestrator decisions must be a list")
    decisions = [
        OrchestratorDecision.from_mapping(item)
        for item in raw_decisions
        if isinstance(item, dict)
    ]
    if len(decisions) != len(raw_decisions):
        raise OrchestratorDecisionError(
            "each orchestrator decision entry must be an object"
        )
    return decisions


def _parse_trailing_json_object(output: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if output[index + end :].strip():
            continue
        if isinstance(value, dict):
            return value
    return None


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
    if template.count("{{") != template.count("}}"):
        raise RuntimeError("template_parse_error: unbalanced template delimiters")

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
    return rendered.strip() + "\n"


AVAILABLE_ORCHESTRATOR_DECISIONS = [
    "advance",
    "retry",
    "run_actor",
    "run_action",
    "operator_attention",
    "complete",
]


def prepare_orchestrator_prompt(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: Any,
    facts: dict[str, Any],
) -> PromptBuild:
    payload, report = build_orchestrator_payload(
        config=config,
        state=state,
        facts=facts,
        available_decisions=AVAILABLE_ORCHESTRATOR_DECISIONS,
    )
    prompt = _render_orchestrator_prompt(policy=policy, payload=payload)
    budget = orchestrator_prompt_budget(config)
    size_report = prompt_size_report(prompt=prompt, report=report, budget=budget)
    if size_report["status"] == "too_large":
        payload, report = build_orchestrator_payload(
            config=config,
            state=state,
            facts=facts,
            available_decisions=AVAILABLE_ORCHESTRATOR_DECISIONS,
            aggressive=True,
        )
        prompt = _render_orchestrator_prompt(policy=policy, payload=payload)
        size_report = prompt_size_report(
            prompt=prompt,
            report={**report, "auto_compacted_due_to_size": True},
            budget=budget,
        )
    if size_report["status"] == "too_large":
        raise RuntimeError(
            "orchestrator prompt exceeds compacted input limit: "
            f"{size_report['prompt_chars']} chars, configured limit "
            f"{budget.max_chars}, app-server limit {APP_SERVER_INPUT_LIMIT_CHARS}. "
            "Reduce active lane count or prompt context limits."
        )
    return PromptBuild(prompt=prompt, report=size_report)


def build_orchestrator_prompt(
    *,
    config: WorkflowConfig,
    policy: WorkflowPolicy,
    state: Any,
    facts: dict[str, Any],
) -> str:
    return prepare_orchestrator_prompt(
        config=config,
        policy=policy,
        state=state,
        facts=facts,
    ).prompt


def _render_orchestrator_prompt(
    *, policy: WorkflowPolicy, payload: dict[str, Any]
) -> str:
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
