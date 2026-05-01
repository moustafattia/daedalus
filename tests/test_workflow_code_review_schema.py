"""Schema validation for change-delivery workflow config."""
import importlib.util
from pathlib import Path

import jsonschema
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"
SCHEMA_PATH = REPO_ROOT / "workflows" / "change_delivery" / "schema.yaml"


def _load_schema() -> dict:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _minimal_valid_config() -> dict:
    """Smallest workflow.yaml dict that satisfies the existing required fields."""
    return {
        "workflow": "change-delivery",
        "schema-version": 1,
        "instance": {"name": "test", "engine-owner": "hermes"},
        "repository": {
            "local-path": "/tmp/x",
            "slug": "owner/repo",
            "active-lane-label": "active-lane",
        },
        "tracker": {
            "kind": "github",
            "github_slug": "owner/repo",
            "active_states": ["open"],
            "terminal_states": ["closed"],
        },
        "code-host": {"kind": "github", "github_slug": "owner/repo"},
        "runtimes": {
            "acpx-codex": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": 1,
                "session-idle-grace-seconds": 1,
                "session-nudge-cooldown-seconds": 1,
            }
        },
        "agents": {
            "coder": {
                "default": {"name": "x", "model": "y", "runtime": "acpx-codex"}
            },
            "internal-reviewer": {"name": "x", "model": "y", "runtime": "acpx-codex"},
            "external-reviewer": {"enabled": True, "name": "x"},
        },
        "gates": {
            "internal-review": {},
            "external-review": {},
            "merge": {},
        },
        "triggers": {"lane-selector": {"type": "github-label", "label": "active-lane"}},
        "storage": {
            "ledger": "memory/ledger.json",
            "health": "memory/health.json",
            "audit-log": "memory/audit.jsonl",
        },
    }


def test_schema_accepts_config_without_tracker_feedback_block():
    schema = _load_schema()
    config = _minimal_valid_config()
    jsonschema.validate(config, schema)  # must not raise


def test_schema_accepts_tracker_feedback_disabled():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["tracker-feedback"] = {"enabled": False}
    jsonschema.validate(config, schema)


def test_schema_accepts_tracker_feedback_full_block():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["tracker-feedback"] = {
        "enabled": True,
        "comment-mode": "append",
        "include": ["dispatch-implementation-turn", "merge-and-promote"],
        "state-updates": {"enabled": False},
    }
    jsonschema.validate(config, schema)


def test_schema_rejects_unknown_tracker_feedback_field():
    """Schema is strict (additionalProperties: false) — typos like
    suppress-transient-failures, append-mode, etc. fail loudly rather than
    being silently ignored."""
    schema = _load_schema()
    config = _minimal_valid_config()
    config["tracker-feedback"] = {"enabled": True, "suppress-transient-failures": True}
    try:
        jsonschema.validate(config, schema)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected ValidationError for unknown field")


def test_schema_rejects_invalid_tracker_feedback_mode():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["tracker-feedback"] = {"enabled": True, "comment-mode": "edit-in-place"}
    try:
        jsonschema.validate(config, schema)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected ValidationError for invalid tracker-feedback mode")


def test_schema_accepts_config_without_server_block():
    """Back-compat: server block is optional (Symphony §13.7 — disabled by default)."""
    schema = _load_schema()
    config = _minimal_valid_config()
    jsonschema.validate(config, schema)


def test_schema_accepts_server_block():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["server"] = {"port": 8080, "bind": "127.0.0.1"}
    jsonschema.validate(config, schema)


def test_schema_accepts_server_block_port_only():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["server"] = {"port": 0}  # ephemeral port, used by tests
    jsonschema.validate(config, schema)


def test_schema_rejects_server_unknown_field():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["server"] = {"port": 8080, "unexpected": "x"}
    try:
        jsonschema.validate(config, schema)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected ValidationError for unknown server field")


def test_schema_rejects_server_port_out_of_range():
    schema = _load_schema()
    config = _minimal_valid_config()
    config["server"] = {"port": 70000}
    try:
        jsonschema.validate(config, schema)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected ValidationError for out-of-range port")
