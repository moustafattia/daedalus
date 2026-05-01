"""Phase A schema validation tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator

REPO_ROOT = Path(__file__).resolve().parent.parent / "daedalus"
SCHEMA_PATH = REPO_ROOT / "workflows/change_delivery/schema.yaml"


def _schema():
    return yaml.safe_load(SCHEMA_PATH.read_text())


def _base_config():
    return {
        "workflow": "change-delivery",
        "schema-version": 1,
        "instance": {"name": "test", "engine-owner": "hermes"},
        "repository": {
            "local-path": "/tmp/x",
            "slug": "x/y",
            "active-lane-label": "active",
        },
        "tracker": {
            "kind": "github",
            "github_slug": "x/y",
            "active_states": ["open"],
            "terminal_states": ["closed"],
        },
        "code-host": {"kind": "github", "github_slug": "x/y"},
        "runtimes": {
            "codex-acpx": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": 900,
                "session-idle-grace-seconds": 1800,
                "session-nudge-cooldown-seconds": 600,
            },
        },
        "actors": {
            "implementer": {"name": "c", "model": "m", "runtime": "codex-acpx"},
            "implementer-high-effort": {"name": "c-hi", "model": "m-hi", "runtime": "codex-acpx"},
            "reviewer": {"name": "ir", "model": "m", "runtime": "codex-acpx"},
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
            "pre-publish-review": {"type": "agent-review", "actor": "reviewer"},
            "maintainer-approval": {"type": "pr-comment-approval", "enabled": False},
            "ci-green": {"type": "code-host-checks", "required-for-merge": True},
        },
        "triggers": {"lane-selector": {"type": "label", "label": "active"}},
        "storage": {"ledger": "x", "health": "x", "audit-log": "x"},
    }


def test_schema_accepts_hermes_agent_runtime():
    cfg = _base_config()
    cfg["runtimes"]["hm"] = {"kind": "hermes-agent"}
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_command_override_on_runtime():
    cfg = _base_config()
    cfg["runtimes"]["codex-acpx"]["command"] = ["acpx", "{model}"]
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_command_override_on_actor():
    cfg = _base_config()
    cfg["actors"]["implementer"]["command"] = ["acpx", "{model}", "{prompt_path}"]
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_prompt_override_on_actor():
    cfg = _base_config()
    cfg["actors"]["reviewer"]["prompt"] = "prompts/reviewer.md"
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_actor_required_capabilities():
    cfg = _base_config()
    cfg["actors"]["implementer"]["required-capabilities"] = ["persistent-session", "resume"]
    Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_empty_command_array():
    from jsonschema import ValidationError

    cfg = _base_config()
    cfg["runtimes"]["codex-acpx"]["command"] = []
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_typo_in_runtime_command_field():
    from jsonschema import ValidationError
    cfg = _base_config()
    cfg["runtimes"]["codex-acpx"]["commands"] = ["acpx"]  # typo: should be 'command'
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)
