"""Phase B schema validation."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator, ValidationError

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
            "maintainer-approval": {"type": "pr-comment-approval", "enabled": True},
            "ci-green": {"type": "code-host-checks", "required-for-merge": True},
        },
        "triggers": {"lane-selector": {"type": "label", "label": "active"}},
        "storage": {"ledger": "x", "health": "x", "audit-log": "x"},
    }


def test_schema_accepts_pr_comment_approval_gate():
    cfg = _base_config()
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_disabled_pr_comment_approval_gate():
    cfg = _base_config()
    cfg["gates"]["maintainer-approval"]["enabled"] = False
    Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_unknown_gate_type():
    cfg = _base_config()
    cfg["gates"]["maintainer-approval"]["type"] = "made-up"
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_repo_slug_override():
    cfg = _base_config()
    cfg["gates"]["maintainer-approval"]["repo-slug"] = "acme/widget"
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_users_and_reactions_inside_approval_gate():
    cfg = _base_config()
    cfg["gates"]["maintainer-approval"]["users"] = ["bot[bot]"]
    cfg["gates"]["maintainer-approval"]["approvals"] = ["+1"]
    cfg["gates"]["maintainer-approval"]["pending-reactions"] = ["eyes"]
    Draft7Validator(_schema()).validate(cfg)
