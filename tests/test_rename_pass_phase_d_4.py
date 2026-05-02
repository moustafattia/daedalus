"""Phase D-4 tests: drop D-2/D-3 aliases + per-thread source rename."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("name", [
    "fetch_codex_cloud_review",
    "summarize_codex_cloud_review",
    "build_codex_cloud_thread",
    "should_dispatch_codex_cloud_repair_handoff",
    "codex_cloud_placeholder",
    "build_codex_cloud_repair_handoff_payload",
    "record_codex_cloud_repair_handoff",
    "fetch_codex_pr_body_signal",
])
def test_external_review_alias_dropped(name):
    """All 8 Phase D-2 module-level aliases should be gone."""
    from workflows.change_delivery import reviews
    assert not hasattr(reviews, name), f"{name} alias should have been removed"


def test_build_external_review_thread_uses_externalReview_source():
    """Per-thread source label is provider-neutral after D-4."""
    from workflows.change_delivery.reviews import build_external_review_thread

    out = build_external_review_thread(
        node={"id": "T1", "isResolved": False, "isOutdated": False, "path": "a.py", "line": 1},
        comment={"body": "x", "url": "https://x", "createdAt": "2026-01-01T00:00:00Z"},
        severity="minor", summary="x",
        pr_signal=None, signal_epoch=None, comment_epoch=None,
    )
    assert out["source"] == "externalReview"


def test_get_ledger_field_no_legacy_fallback():
    """D-3 fallback to legacy keys is dropped after D-4."""
    from workflows.change_delivery.migrations import get_ledger_field
    assert get_ledger_field({"interReviewAgentModel": "x"}, "internalReviewerModel") is None
    assert get_ledger_field({"claudeRepairHandoff": {"v": 1}}, "internalReviewRepairHandoff") is None


def test_reviews_no_lastClaudeVerdict_fallback():
    """reviews.py:308 should read only the new key after D-4."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "daedalus" / "workflows/change_delivery/reviews.py").read_text()
    # The fallback `or state_review.get("lastClaudeVerdict")` should be gone.
    assert 'state_review.get("lastClaudeVerdict")' not in src


def test_workspace_no_interReviewAgentModel_fallback():
    """workspace.py review_policy fallback should not include the legacy key after D-4."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "daedalus" / "workflows/change_delivery/workspace.py").read_text()
    # The fallback `or review_policy.get("interReviewAgentModel")` should be gone.
    assert 'review_policy.get("interReviewAgentModel")' not in src
