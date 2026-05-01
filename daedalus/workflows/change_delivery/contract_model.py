from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


IMPLEMENT_STAGE = "implement"
PRE_PUBLISH_REVIEW_GATE = "pre-publish-review"
MAINTAINER_APPROVAL_GATE = "maintainer-approval"
CI_GREEN_GATE = "ci-green"


def compile_change_delivery_contract(config: Mapping[str, Any]) -> dict[str, Any]:
    """Compile the public actor/stage/gate contract to the engine view.

    The public `change-delivery` contract is intentionally small:
    `actors` bind runtime profiles, `stages` reference actors or engine actions,
    and `gates` define review/approval policy. The existing engine internals
    still consume historical coder/reviewer keys, so this compiler creates that
    private view without requiring operators to edit it.
    """
    compiled = deepcopy(dict(config or {}))
    if str(compiled.get("workflow") or "") != "change-delivery":
        return compiled
    _require_mapping(compiled, "actors")
    _require_mapping(compiled, "stages")
    _require_mapping(compiled, "gates")

    compiled["agents"] = _compile_agents(compiled)
    compiled["gates"] = _compile_engine_gates(compiled)
    _compile_escalation_defaults(compiled)
    return compiled


def change_delivery_actor_names(config: Mapping[str, Any]) -> list[str]:
    actors = config.get("actors") if isinstance(config, Mapping) else None
    if not isinstance(actors, dict):
        return []

    referenced: list[str] = []
    for stage in _mapping_values(config.get("stages")):
        _append_actor_name(referenced, stage.get("actor"))
        escalation = stage.get("escalation")
        if isinstance(escalation, dict):
            _append_actor_name(referenced, escalation.get("actor"))
    for gate in _mapping_values(config.get("gates")):
        _append_actor_name(referenced, gate.get("actor"))

    for name in actors:
        _append_actor_name(referenced, name)
    return referenced


def actor_config(config: Mapping[str, Any], actor_name: str) -> dict[str, Any] | None:
    actors = config.get("actors") if isinstance(config, Mapping) else None
    if not isinstance(actors, dict):
        return None
    actor = actors.get(actor_name)
    return actor if isinstance(actor, dict) else None


def bind_actor_runtime(config: dict[str, Any], *, role: str, runtime_name: str) -> list[str]:
    actors = config.setdefault("actors", {})
    if not isinstance(actors, dict):
        raise ValueError("change-delivery actors must be a mapping")

    names = change_delivery_actor_names(config)
    if role == "all":
        targets = names
    else:
        targets = [role] if role in actors else []
    if not targets:
        expected = ", ".join(names) if names else "all"
        raise ValueError(f"change-delivery supports --role {expected}, or all")

    for actor_name in targets:
        actor = actors.setdefault(actor_name, {})
        if not isinstance(actor, dict):
            raise ValueError(f"actors.{actor_name} must be a mapping")
        actor["runtime"] = runtime_name
    return targets


def _compile_agents(config: Mapping[str, Any]) -> dict[str, Any]:
    actors = _require_mapping(config, "actors")
    stages = _require_mapping(config, "stages")
    gates = _require_mapping(config, "gates")
    implement_stage = _require_mapping(stages, IMPLEMENT_STAGE, prefix="stages")
    implementer_name = _require_text(implement_stage, "actor", path="stages.implement")
    implementer = _actor_or_raise(actors, implementer_name)
    if isinstance(implement_stage, dict) and implement_stage.get("prompt") and not implementer.get("prompt"):
        implementer["prompt"] = implement_stage["prompt"]

    escalation = _require_mapping(implement_stage, "escalation", prefix="stages.implement")
    high_effort_name = _require_text(escalation, "actor", path="stages.implement.escalation")
    high_effort = _actor_or_raise(actors, high_effort_name)

    review_gate = _typed_gate(gates, PRE_PUBLISH_REVIEW_GATE, "agent-review")
    reviewer_name = _require_text(review_gate, "actor", path=f"gates.{PRE_PUBLISH_REVIEW_GATE}")
    reviewer = _actor_or_raise(actors, reviewer_name)
    if "freeze-actor-while-running" in review_gate:
        reviewer["freeze-coder-while-running"] = bool(review_gate["freeze-actor-while-running"])
    else:
        reviewer.setdefault("freeze-coder-while-running", True)

    approval_gate = _typed_gate(gates, MAINTAINER_APPROVAL_GATE, "pr-comment-approval")
    external_reviewer = _external_reviewer_from_gate(approval_gate)

    return {
        "coder": {
            "default": implementer,
            "high-effort": high_effort,
            "escalated": high_effort,
        },
        "internal-reviewer": reviewer,
        "external-reviewer": external_reviewer,
    }


def _compile_engine_gates(config: Mapping[str, Any]) -> dict[str, Any]:
    gates = _require_mapping(config, "gates")
    review_gate = _typed_gate(gates, PRE_PUBLISH_REVIEW_GATE, "agent-review")
    approval_gate = _typed_gate(gates, MAINTAINER_APPROVAL_GATE, "pr-comment-approval")
    ci_gate = _typed_gate(gates, CI_GREEN_GATE, "code-host-checks")

    return {
        "internal-review": {
            "pass-with-findings-tolerance": int(review_gate.get("pass-with-findings-tolerance", 1)),
            "require-pass-clean-before-publish": bool(
                review_gate.get("require-pass-clean-before-publish", True)
            ),
            "request-cooldown-seconds": int(review_gate.get("request-cooldown-seconds", 1200)),
        },
        "external-review": {
            "required-for-merge": bool(approval_gate.get("required-for-merge", False)),
        },
        "merge": {
            "require-ci-acceptable": bool(ci_gate.get("required-for-merge", True)),
        },
    }


def _compile_escalation_defaults(compiled: dict[str, Any]) -> None:
    stages = _require_mapping(compiled, "stages")
    implement_stage = _require_mapping(stages, IMPLEMENT_STAGE, prefix="stages")
    escalation = _require_mapping(implement_stage, "escalation", prefix="stages.implement")
    after_attempts = escalation.get("after-attempts")
    if after_attempts is None:
        raise ValueError("stages.implement.escalation.after-attempts is required")
    policy = compiled.setdefault("escalation", {})
    if isinstance(policy, dict):
        policy.setdefault("restart-count-threshold", int(after_attempts))


def _external_reviewer_from_gate(gate: Mapping[str, Any]) -> dict[str, Any]:
    reviewer: dict[str, Any] = {
        "enabled": bool(gate.get("enabled", True)),
        "name": str(gate.get("name") or "Maintainer_Approval_Gate"),
        "kind": "github-comments",
    }
    if gate.get("repo-slug"):
        reviewer["repo-slug"] = gate.get("repo-slug")
    users = gate.get("users") or gate.get("logins")
    if isinstance(users, list):
        reviewer["logins"] = list(users)
    approvals = gate.get("approvals") or gate.get("clean-reactions")
    if isinstance(approvals, list):
        reviewer["clean-reactions"] = list(approvals)
    pending = gate.get("pending-reactions")
    if isinstance(pending, list):
        reviewer["pending-reactions"] = list(pending)
    if gate.get("cache-seconds") is not None:
        reviewer["cache-seconds"] = int(gate["cache-seconds"])
    if not reviewer.get("enabled"):
        reviewer["kind"] = "disabled"
    return reviewer


def _mapping_values(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    return [item for item in value.values() if isinstance(item, dict)]


def _typed_gate(gates: Mapping[str, Any], name: str, gate_type: str) -> dict[str, Any]:
    gate = _require_mapping(gates, name, prefix="gates")
    actual = str(gate.get("type") or "").strip()
    if actual != gate_type:
        raise ValueError(f"gates.{name}.type must be {gate_type!r}, got {actual!r}")
    return gate


def _actor_or_raise(actors: Mapping[str, Any], actor_name: str) -> dict[str, Any]:
    actor = actors.get(actor_name)
    if not isinstance(actor, dict):
        raise ValueError(f"actors.{actor_name} is required because it is referenced by the contract")
    return dict(actor)


def _require_mapping(value: Mapping[str, Any], key: str, *, prefix: str | None = None) -> dict[str, Any]:
    section = value.get(key)
    if not isinstance(section, dict):
        path = f"{prefix}.{key}" if prefix else key
        raise ValueError(f"{path} must be a mapping")
    return section


def _require_text(value: Mapping[str, Any], key: str, *, path: str) -> str:
    text = str(value.get(key) or "").strip()
    if not text:
        raise ValueError(f"{path}.{key} is required")
    return text


def _append_actor_name(names: list[str], value: Any) -> None:
    name = str(value or "").strip()
    if name and name not in names:
        names.append(name)
