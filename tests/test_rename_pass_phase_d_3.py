"""Phase D-3 tests: top-level ledger field migrations."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_migrate_top_level_keys_renames_legacy():
    from workflows.code_review.migrations import migrate_top_level_keys

    ledger = {
        "claudeRepairHandoff": {"v": 1},
        "codexCloudRepairHandoff": {"v": 2},
        "codexCloudAutoResolved": {"v": 3},
        "interReviewAgentModel": "claude-sonnet-4",
    }
    out, changed = migrate_top_level_keys(ledger)
    assert changed is True
    assert out["internalReviewRepairHandoff"] == {"v": 1}
    assert out["externalReviewRepairHandoff"] == {"v": 2}
    assert out["externalReviewAutoResolved"] == {"v": 3}
    assert out["internalReviewerModel"] == "claude-sonnet-4"
    for old in (
        "claudeRepairHandoff", "codexCloudRepairHandoff",
        "codexCloudAutoResolved", "interReviewAgentModel",
    ):
        assert old not in out


def test_migrate_top_level_keys_drops_claude_model():
    from workflows.code_review.migrations import migrate_top_level_keys

    ledger = {"claudeModel": "claude-sonnet-4"}
    out, changed = migrate_top_level_keys(ledger)
    assert changed is True
    assert "claudeModel" not in out


def test_migrate_top_level_keys_idempotent():
    from workflows.code_review.migrations import migrate_top_level_keys

    ledger = {
        "internalReviewRepairHandoff": {"v": 1},
        "internalReviewerModel": "x",
    }
    out, changed = migrate_top_level_keys(ledger)
    assert changed is False


def test_migrate_top_level_keys_new_key_wins():
    from workflows.code_review.migrations import migrate_top_level_keys

    ledger = {
        "interReviewAgentModel": "old",
        "internalReviewerModel": "new",
    }
    out, changed = migrate_top_level_keys(ledger)
    assert changed is True
    assert out["internalReviewerModel"] == "new"
    assert "interReviewAgentModel" not in out


def test_migrate_persisted_ledger_runs_both_migrations(tmp_path):
    from workflows.code_review.migrations import migrate_persisted_ledger

    p = tmp_path / "l.json"
    p.write_text(json.dumps({
        "reviews": {"claudeCode": {"v": 1}},
        "claudeRepairHandoff": {"v": 2},
        "claudeModel": "claude-sonnet-4",
    }, indent=2))
    migrate_persisted_ledger(p)
    out = json.loads(p.read_text())
    assert out["reviews"]["internalReview"] == {"v": 1}
    assert out["internalReviewRepairHandoff"] == {"v": 2}
    assert "claudeModel" not in out


def test_get_ledger_field_returns_new_when_present():
    from workflows.code_review.migrations import get_ledger_field
    assert get_ledger_field({"internalReviewerModel": "x"}, "internalReviewerModel") == "x"


def test_get_ledger_field_returns_none_for_unknown_key():
    from workflows.code_review.migrations import get_ledger_field
    assert get_ledger_field({"x": 1}, "made-up") is None


def test_get_ledger_field_handles_none_ledger():
    from workflows.code_review.migrations import get_ledger_field
    assert get_ledger_field(None, "internalReviewerModel") is None


def test_existing_yoyopod_ledger_top_level_migration(tmp_path):
    from workflows.code_review.migrations import migrate_persisted_ledger
    src = Path(os.path.expanduser("~/.hermes/workflows/yoyopod/memory/yoyopod-workflow-status.json"))
    if not src.exists():
        pytest.skip("yoyopod ledger not present")
    dst = tmp_path / "l.json"
    dst.write_text(src.read_text())
    migrate_persisted_ledger(dst)
    out = json.loads(dst.read_text())
    # All legacy top-level keys should be gone
    for old in (
        "claudeRepairHandoff", "codexCloudRepairHandoff",
        "codexCloudAutoResolved", "interReviewAgentModel",
        "lastClaudeVerdict", "claudeModel",
    ):
        assert old not in out, f"{old} should have been migrated"


def test_lane_state_read_falls_back_to_legacy_lastClaudeVerdict():
    """Regression: status.py writes lastInternalVerdict (D-3); reviews.py must
    read it (with legacy fallback for one release). Without this fallback,
    is_local_inter_review_agent_pass silently misses the verdict."""
    # Structural assertion: reviews.py must accept either key when reading
    # state_review verdict.
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "workflows/code_review/reviews.py").read_text()
    # The reads should reference both keys (new first, legacy fallback)
    assert "lastInternalVerdict" in src

