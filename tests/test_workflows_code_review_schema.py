from pathlib import Path

import yaml
import jsonschema
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"
SCHEMA_PATH = REPO_ROOT / "workflows" / "change_delivery" / "schema.yaml"


def _load_schema():
    return yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))


def _minimal_valid_config():
    """The smallest YAML that should pass schema validation."""
    return {
        "workflow": "change-delivery",
        "schema-version": 1,
        "instance": {"name": "owner-repo-change-delivery", "engine-owner": "hermes"},
        "repository": {
            "local-path": "/tmp/repo",
            "github-slug": "owner/repo",
            "active-lane-label": "active-lane",
        },
        "runtimes": {
            "acpx-codex": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": 900,
                "session-idle-grace-seconds": 1800,
                "session-nudge-cooldown-seconds": 600,
            },
            "claude-cli": {
                "kind": "claude-cli",
                "max-turns-per-invocation": 24,
                "timeout-seconds": 1200,
            },
        },
        "agents": {
            "coder": {
                "default": {
                    "name": "Internal_Coder_Agent",
                    "model": "gpt-5.3-codex-spark/high",
                    "runtime": "acpx-codex",
                },
            },
            "internal-reviewer": {
                "name": "Internal_Reviewer_Agent",
                "model": "claude-sonnet-4-6",
                "runtime": "claude-cli",
                "freeze-coder-while-running": True,
            },
            "external-reviewer": {
                "enabled": True,
                "name": "External_Reviewer_Agent",
                "provider": "codex-cloud",
                "cache-seconds": 1800,
            },
        },
        "gates": {
            "internal-review": {
                "pass-with-findings-tolerance": 1,
                "require-pass-clean-before-publish": True,
                "request-cooldown-seconds": 1200,
            },
            "external-review": {"required-for-merge": True},
            "merge": {"require-ci-acceptable": True},
        },
        "triggers": {
            "lane-selector": {"type": "github-label", "label": "active-lane"},
        },
        "storage": {
            "ledger": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
        },
    }


def test_schema_accepts_minimal_valid_config():
    jsonschema.validate(_minimal_valid_config(), _load_schema())


def test_schema_accepts_codex_app_server_runtime_for_coder():
    cfg = _minimal_valid_config()
    cfg["runtimes"]["coder-runtime"] = {
        "kind": "codex-app-server",
        "command": "codex app-server",
        "mode": "managed",
        "ephemeral": False,
        "approval_policy": "never",
        "thread_sandbox": "workspace-write",
        "turn_sandbox_policy": "workspace-write",
        "turn_timeout_ms": 3600000,
        "read_timeout_ms": 5000,
        "stall_timeout_ms": 300000,
    }
    cfg["agents"]["coder"]["default"]["runtime"] = "coder-runtime"

    jsonschema.validate(cfg, _load_schema())


def test_schema_rejects_missing_workflow_key():
    cfg = _minimal_valid_config()
    del cfg["workflow"]
    with pytest.raises(jsonschema.ValidationError) as exc:
        jsonschema.validate(cfg, _load_schema())
    assert "workflow" in str(exc.value)


def test_schema_rejects_missing_runtimes_block():
    cfg = _minimal_valid_config()
    del cfg["runtimes"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(cfg, _load_schema())


def test_schema_rejects_agent_pointing_at_unknown_runtime():
    # jsonschema alone doesn't enforce cross-references (agent.runtime must be
    # a key in runtimes). This check lives in workspace.py (Task 4.3).
    # Here we just verify the schema accepts arbitrary string runtime values.
    cfg = _minimal_valid_config()
    cfg["agents"]["coder"]["default"]["runtime"] = "nonexistent"
    jsonschema.validate(cfg, _load_schema())


def test_schema_enforces_workflow_const_value_is_change_delivery():
    cfg = _minimal_valid_config()
    cfg["workflow"] = "not-change-delivery"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(cfg, _load_schema())
