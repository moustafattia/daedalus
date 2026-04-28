"""Phase A schema validation tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator

REPO_ROOT = Path(__file__).resolve().parent.parent / "daedalus"
SCHEMA_PATH = REPO_ROOT / "workflows/code_review/schema.yaml"


def _schema():
    return yaml.safe_load(SCHEMA_PATH.read_text())


def _base_config():
    return {
        "workflow": "code-review",
        "schema-version": 1,
        "instance": {"name": "test", "engine-owner": "hermes"},
        "repository": {
            "local-path": "/tmp/x",
            "github-slug": "x/y",
            "active-lane-label": "active",
        },
        "runtimes": {
            "codex-acpx": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": 900,
                "session-idle-grace-seconds": 1800,
                "session-nudge-cooldown-seconds": 600,
            },
        },
        "agents": {
            "coder": {
                "default": {"name": "c", "model": "m", "runtime": "codex-acpx"},
            },
            "internal-reviewer": {
                "name": "ir", "model": "m", "runtime": "codex-acpx",
            },
            "external-reviewer": {"enabled": False, "name": "er"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
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


def test_schema_accepts_command_override_on_coder_tier():
    cfg = _base_config()
    cfg["agents"]["coder"]["default"]["command"] = ["acpx", "{model}", "{prompt_path}"]
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_prompt_override_on_internal_reviewer():
    cfg = _base_config()
    cfg["agents"]["internal-reviewer"]["prompt"] = "prompts/internal-reviewer.md"
    Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_empty_command_array():
    from jsonschema import ValidationError

    cfg = _base_config()
    cfg["runtimes"]["codex-acpx"]["command"] = []
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)


def test_existing_yoyopod_workflow_yaml_still_validates():
    yoyopod = Path(os.path.expanduser("~/.hermes/workflows/yoyopod/config/workflow.yaml"))
    if not yoyopod.exists():
        pytest.skip("yoyopod workspace not present on this host")
    cfg = yaml.safe_load(yoyopod.read_text())
    Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_typo_in_runtime_command_field():
    from jsonschema import ValidationError
    cfg = _base_config()
    cfg["runtimes"]["codex-acpx"]["commands"] = ["acpx"]  # typo: should be 'command'
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)
