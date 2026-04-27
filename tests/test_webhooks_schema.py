"""Phase C schema validation."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator, ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
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
            "coder": {"default": {"name": "c", "model": "m", "runtime": "codex-acpx"}},
            "internal-reviewer": {"name": "ir", "model": "m", "runtime": "codex-acpx"},
            "external-reviewer": {"enabled": True, "name": "er"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "label", "label": "active"}},
        "storage": {"ledger": "x", "health": "x", "audit-log": "x"},
    }


def test_schema_accepts_no_webhooks_block():
    Draft7Validator(_schema()).validate(_base_config())


def test_schema_accepts_empty_webhooks_array():
    cfg = _base_config()
    cfg["webhooks"] = []
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_http_json_webhook():
    cfg = _base_config()
    cfg["webhooks"] = [{"name": "wh", "kind": "http-json", "url": "https://x"}]
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_slack_incoming_webhook():
    cfg = _base_config()
    cfg["webhooks"] = [{"name": "slack", "kind": "slack-incoming", "url": "https://hooks.slack.com/X"}]
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_full_subscription():
    cfg = _base_config()
    cfg["webhooks"] = [{
        "name": "wh", "kind": "http-json", "url": "https://x",
        "enabled": True,
        "events": ["merge_*", "run_*"],
        "headers": {"X-Custom": "v"},
        "timeout-seconds": 10,
        "retry-count": 3,
    }]
    Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_unknown_kind():
    cfg = _base_config()
    cfg["webhooks"] = [{"name": "wh", "kind": "made-up"}]
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_extra_property_on_subscription():
    cfg = _base_config()
    cfg["webhooks"] = [{"name": "wh", "kind": "http-json", "urls": "https://x"}]  # typo
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)


def test_existing_yoyopod_workflow_yaml_still_validates():
    yoyopod = Path(os.path.expanduser("~/.hermes/workflows/yoyopod/config/workflow.yaml"))
    if not yoyopod.exists():
        pytest.skip("yoyopod workspace not present on this host")
    cfg = yaml.safe_load(yoyopod.read_text())
    Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_excessive_timeout():
    cfg = _base_config()
    cfg["webhooks"] = [{
        "name": "wh", "kind": "http-json", "url": "https://x",
        "timeout-seconds": 60,
    }]
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_excessive_retry_count():
    cfg = _base_config()
    cfg["webhooks"] = [{
        "name": "wh", "kind": "http-json", "url": "https://x",
        "retry-count": 100,
    }]
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)
