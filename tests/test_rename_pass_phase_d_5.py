"""Phase D-5 tests: lane-state nested field migration."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_migrate_lane_state_review_keys_renames_legacy():
    from workflows.change_delivery.migrations import migrate_lane_state_review_keys

    ledger = {
        "implementation": {
            "laneState": {
                "review": {
                    "lastClaudeReviewedHeadSha": "abc123",
                    "localClaudeReviewCount": 5,
                    "otherField": "preserved",
                },
            },
        },
    }
    out, changed = migrate_lane_state_review_keys(ledger)
    assert changed is True
    review = out["implementation"]["laneState"]["review"]
    assert review["lastInternalReviewedHeadSha"] == "abc123"
    assert review["localInternalReviewCount"] == 5
    assert review["otherField"] == "preserved"
    assert "lastClaudeReviewedHeadSha" not in review
    assert "localClaudeReviewCount" not in review


def test_migrate_lane_state_review_keys_handles_missing_path():
    from workflows.change_delivery.migrations import migrate_lane_state_review_keys

    # No implementation block
    out, changed = migrate_lane_state_review_keys({"reviews": {}})
    assert changed is False

    # No laneState
    out, changed = migrate_lane_state_review_keys({"implementation": {}})
    assert changed is False

    # No review
    out, changed = migrate_lane_state_review_keys({"implementation": {"laneState": {}}})
    assert changed is False


def test_migrate_lane_state_review_keys_idempotent():
    from workflows.change_delivery.migrations import migrate_lane_state_review_keys

    ledger = {
        "implementation": {
            "laneState": {
                "review": {
                    "lastInternalReviewedHeadSha": "abc",
                    "localInternalReviewCount": 1,
                },
            },
        },
    }
    out, changed = migrate_lane_state_review_keys(ledger)
    assert changed is False


def test_migrate_lane_state_review_keys_new_key_wins():
    from workflows.change_delivery.migrations import migrate_lane_state_review_keys

    ledger = {
        "implementation": {
            "laneState": {
                "review": {
                    "lastClaudeReviewedHeadSha": "old",
                    "lastInternalReviewedHeadSha": "new",
                },
            },
        },
    }
    out, changed = migrate_lane_state_review_keys(ledger)
    assert changed is True  # old key was dropped
    assert out["implementation"]["laneState"]["review"]["lastInternalReviewedHeadSha"] == "new"
    assert "lastClaudeReviewedHeadSha" not in out["implementation"]["laneState"]["review"]


def test_migrate_persisted_ledger_runs_all_three_migrations(tmp_path):
    from workflows.change_delivery.migrations import migrate_persisted_ledger

    p = tmp_path / "l.json"
    p.write_text(json.dumps({
        "reviews": {"claudeCode": {"v": 1}},
        "claudeRepairHandoff": {"v": 2},
        "implementation": {
            "laneState": {"review": {"lastClaudeReviewedHeadSha": "abc"}},
        },
    }, indent=2))
    migrate_persisted_ledger(p)
    out = json.loads(p.read_text())
    assert out["reviews"]["internalReview"] == {"v": 1}
    assert out["internalReviewRepairHandoff"] == {"v": 2}
    assert out["implementation"]["laneState"]["review"]["lastInternalReviewedHeadSha"] == "abc"


def test_get_lane_state_review_field_returns_new_when_present():
    from workflows.change_delivery.migrations import get_lane_state_review_field
    assert get_lane_state_review_field({"lastInternalReviewedHeadSha": "x"}, "lastInternalReviewedHeadSha") == "x"


def test_get_lane_state_review_field_falls_back_to_legacy():
    from workflows.change_delivery.migrations import get_lane_state_review_field
    assert get_lane_state_review_field({"lastClaudeReviewedHeadSha": "x"}, "lastInternalReviewedHeadSha") == "x"
    assert get_lane_state_review_field({"localClaudeReviewCount": 5}, "localInternalReviewCount") == 5


def test_get_lane_state_review_field_returns_none_for_unknown_key():
    from workflows.change_delivery.migrations import get_lane_state_review_field
    assert get_lane_state_review_field({"x": 1}, "made-up") is None


def test_existing_installed_workflow_ledger_lane_state_migration(tmp_path):
    from workflows.change_delivery.migrations import migrate_persisted_ledger
    plugin_dir = Path.home() / ".hermes" / "plugins" / "daedalus"
    if not plugin_dir.exists():
        pytest.skip("installed workflow plugin not present")
    src = plugin_dir.resolve().parents[2] / "memory" / "workflow-status.json"
    if not src.exists():
        pytest.skip("installed workflow ledger not present")
    dst = tmp_path / "l.json"
    dst.write_text(src.read_text())
    migrate_persisted_ledger(dst)
    out = json.loads(dst.read_text())
    review = (out.get("implementation") or {}).get("laneState", {}).get("review") or {}
    assert "lastClaudeReviewedHeadSha" not in review
    assert "localClaudeReviewCount" not in review
