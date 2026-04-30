"""Schema validation for the lane-selection block in workflow.yaml."""
from pathlib import Path

import jsonschema
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"
SCHEMA_PATH = REPO_ROOT / "workflows" / "change_delivery" / "schema.yaml"


def _load_schema() -> dict:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _minimal_valid_config() -> dict:
    return {
        "workflow": "change-delivery",
        "schema-version": 1,
        "instance": {"name": "test", "engine-owner": "hermes"},
        "repository": {
            "local-path": "/tmp/x",
            "github-slug": "owner/repo",
            "active-lane-label": "active-lane",
        },
        "runtimes": {
            "acpx-codex": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": 1,
                "session-idle-grace-seconds": 1,
                "session-nudge-cooldown-seconds": 1,
            }
        },
        "agents": {
            "coder": {"default": {"name": "x", "model": "y", "runtime": "acpx-codex"}},
            "internal-reviewer": {"name": "x", "model": "y", "runtime": "acpx-codex"},
            "external-reviewer": {"enabled": True, "name": "x"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-label", "label": "active-lane"}},
        "storage": {"ledger": "l", "health": "h", "audit-log": "a"},
    }


def test_schema_accepts_config_without_lane_selection_block():
    """Back-compat: workspaces without lane-selection still validate."""
    schema = _load_schema()
    cfg = _minimal_valid_config()
    jsonschema.validate(cfg, schema)


def test_schema_accepts_full_lane_selection_block():
    schema = _load_schema()
    cfg = _minimal_valid_config()
    cfg["lane-selection"] = {
        "require-labels": ["needs-review"],
        "allow-any-of": ["urgent", "wip-codex"],
        "exclude-labels": ["blocked"],
        "priority": ["severity:critical", "severity:high"],
        "tiebreak": "oldest",
    }
    jsonschema.validate(cfg, schema)


def test_schema_accepts_partial_lane_selection_block():
    schema = _load_schema()
    cfg = _minimal_valid_config()
    cfg["lane-selection"] = {"exclude-labels": ["blocked"]}
    jsonschema.validate(cfg, schema)


def test_schema_rejects_invalid_tiebreak_value():
    schema = _load_schema()
    cfg = _minimal_valid_config()
    cfg["lane-selection"] = {"tiebreak": "lottery"}
    try:
        jsonschema.validate(cfg, schema)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected ValidationError for tiebreak='lottery'")


def test_schema_rejects_unknown_lane_selection_field():
    schema = _load_schema()
    cfg = _minimal_valid_config()
    cfg["lane-selection"] = {"unknown-axis": ["x"]}
    try:
        jsonschema.validate(cfg, schema)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected ValidationError for unknown lane-selection field")


def test_schema_rejects_non_array_require_labels():
    schema = _load_schema()
    cfg = _minimal_valid_config()
    cfg["lane-selection"] = {"require-labels": "needs-review"}  # string, not array
    try:
        jsonschema.validate(cfg, schema)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected ValidationError for non-array require-labels")
