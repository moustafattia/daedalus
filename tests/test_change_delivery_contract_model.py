from __future__ import annotations

import pytest

from workflows.change_delivery.contract_model import (
    bind_actor_runtime,
    change_delivery_actor_names,
    compile_change_delivery_contract,
)


def _contract() -> dict:
    return {
        "workflow": "change-delivery",
        "runtimes": {
            "coder-runtime": {"kind": "acpx-codex"},
            "reviewer-runtime": {"kind": "hermes-agent"},
        },
        "actors": {
            "implementer": {"name": "impl", "model": "gpt-5", "runtime": "coder-runtime"},
            "implementer-high-effort": {"name": "impl-hi", "model": "gpt-5.5", "runtime": "coder-runtime"},
            "reviewer": {"name": "review", "model": "gpt-5", "runtime": "reviewer-runtime"},
        },
        "stages": {
            "implement": {
                "actor": "implementer",
                "escalation": {"after-attempts": 2, "actor": "implementer-high-effort"},
            },
            "publish": {"action": "pr.publish"},
            "merge": {"action": "pr.merge"},
        },
        "gates": {
            "pre-publish-review": {
                "type": "agent-review",
                "actor": "reviewer",
                "pass-with-findings-tolerance": 0,
                "require-pass-clean-before-publish": False,
            },
            "maintainer-approval": {
                "type": "pr-comment-approval",
                "enabled": True,
                "required-for-merge": True,
                "users": ["maintainer"],
                "approvals": ["+1"],
            },
            "ci-green": {"type": "code-host-checks", "required-for-merge": False},
        },
    }


def test_compile_change_delivery_contract_builds_private_engine_view():
    compiled = compile_change_delivery_contract(_contract())

    assert compiled["agents"]["coder"]["default"]["name"] == "impl"
    assert compiled["agents"]["coder"]["high-effort"]["name"] == "impl-hi"
    assert compiled["agents"]["internal-reviewer"]["runtime"] == "reviewer-runtime"
    assert compiled["agents"]["external-reviewer"]["kind"] == "github-comments"
    assert compiled["agents"]["external-reviewer"]["logins"] == ["maintainer"]
    assert compiled["agents"]["external-reviewer"]["clean-reactions"] == ["+1"]
    assert compiled["gates"]["internal-review"]["pass-with-findings-tolerance"] == 0
    assert compiled["gates"]["internal-review"]["require-pass-clean-before-publish"] is False
    assert compiled["gates"]["external-review"]["required-for-merge"] is True
    assert compiled["gates"]["merge"]["require-ci-acceptable"] is False
    assert compiled["escalation"]["restart-count-threshold"] == 2


def test_change_delivery_actor_names_are_stage_order_then_remaining_actors():
    assert change_delivery_actor_names(_contract()) == [
        "implementer",
        "implementer-high-effort",
        "reviewer",
    ]


def test_bind_actor_runtime_updates_named_actor_and_rejects_unknown_role():
    contract = _contract()

    changed = bind_actor_runtime(contract, role="reviewer", runtime_name="codex-service")

    assert changed == ["reviewer"]
    assert contract["actors"]["reviewer"]["runtime"] == "codex-service"
    with pytest.raises(ValueError, match="change-delivery supports"):
        bind_actor_runtime(contract, role="coder.default", runtime_name="codex-service")
