"""Persisted-state migrations for the change-delivery workflow.

Phase D-1 rationale:
  reviews.claudeCode -> reviews.internalReview
  reviews.codexCloud -> reviews.externalReview

The old names tied the ledger to specific providers (Claude / Codex
Cloud). Phases A-C made runtimes/reviewers/webhooks pluggable; this
migration removes the last operator-visible coupling to provider names.

`migrate_persisted_ledger(path)` runs idempotently on workspace setup.
`get_review(reviews_dict, new_key)` reads canonical keys only; old keys are
removed by migration instead of remaining valid runtime input.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


REVIEW_KEY_RENAMES: dict[str, str] = {
    "claudeCode": "internalReview",
    "codexCloud": "externalReview",
}

_LEGACY_KEY_FOR: dict[str, str] = {v: k for k, v in REVIEW_KEY_RENAMES.items()}


def migrate_review_keys(ledger: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Rewrite legacy `reviews.<old>` keys to their new names.

    If both old and new keys are present, the new value wins and the
    old key is dropped. Returns ``(ledger, was_changed)``. The ``ledger``
    object is mutated in place AND returned for convenience.
    """
    reviews = ledger.get("reviews")
    if not isinstance(reviews, dict):
        return ledger, False

    changed = False
    for old_key, new_key in REVIEW_KEY_RENAMES.items():
        if old_key in reviews:
            if new_key not in reviews:
                reviews[new_key] = reviews[old_key]
            del reviews[old_key]
            changed = True
    return ledger, changed


def get_review(reviews: dict | None, new_key: str) -> dict:
    """Read a review by its new key. Returns empty dict if absent."""
    return ((reviews or {}).get(new_key)) or {}


def migrate_persisted_ledger(path: Path | str) -> bool:
    """Migrate the on-disk ledger at ``path``, atomically.

    Returns True if the file was rewritten, False otherwise. Missing
    files are silently no-op'd. Indent-2 JSON format is preserved.
    """
    p = Path(path)
    if not p.exists():
        return False
    try:
        ledger = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    _, c1 = migrate_review_keys(ledger)
    _, c2 = migrate_top_level_keys(ledger)
    _, c3 = migrate_lane_state_review_keys(ledger)
    if not (c1 or c2 or c3):
        return False

    # Atomic temp-file + rename in the same directory.
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=p.name, suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return True


LEDGER_KEY_RENAMES: dict[str, str] = {
    "claudeRepairHandoff": "internalReviewRepairHandoff",
    "codexCloudRepairHandoff": "externalReviewRepairHandoff",
    "codexCloudAutoResolved": "externalReviewAutoResolved",
    "interReviewAgentModel": "internalReviewerModel",
    # Note: lastClaudeVerdict lives nested under ledger["review"] (lane-state),
    # not top-level — its rename is handled by reviews.py read sites and
    # status.py write sites directly. A nested-aware migration belongs in a
    # future phase if/when lane-state field renames are tackled.
}

# Top-level keys that are dropped entirely (no rename target).
# claudeModel is canonicalized as internalReviewerModel via the
# interReviewAgentModel migration; the explicit claudeModel mirror
# from the previous migration round is no longer needed.
LEDGER_KEYS_TO_DROP: set[str] = {"claudeModel"}


def migrate_top_level_keys(ledger: dict) -> tuple[dict, bool]:
    """Rewrite legacy top-level ledger keys per LEDGER_KEY_RENAMES.

    If both old and new keys are present, the new value wins and the
    old key is dropped. Keys in LEDGER_KEYS_TO_DROP are removed entirely.
    Returns (ledger, was_changed). Mutates in place.
    """
    changed = False
    for old, new in LEDGER_KEY_RENAMES.items():
        if old in ledger:
            if new not in ledger:
                ledger[new] = ledger[old]
            del ledger[old]
            changed = True
    for k in LEDGER_KEYS_TO_DROP:
        if k in ledger:
            del ledger[k]
            changed = True
    return ledger, changed


def get_ledger_field(ledger: dict | None, new_key: str):
    """Read a top-level ledger field. Returns None if absent."""
    return (ledger or {}).get(new_key)


LANE_STATE_REVIEW_KEY_RENAMES: dict[str, str] = {
    "lastClaudeReviewedHeadSha": "lastInternalReviewedHeadSha",
    "localClaudeReviewCount": "localInternalReviewCount",
}

_LEGACY_LANE_STATE_REVIEW_KEY_FOR: dict[str, str] = {
    v: k for k, v in LANE_STATE_REVIEW_KEY_RENAMES.items()
}


def migrate_lane_state_review_keys(ledger: dict) -> tuple[dict, bool]:
    """Rewrite ledger.implementation.laneState.review.<key> per
    LANE_STATE_REVIEW_KEY_RENAMES. Returns (ledger, was_changed)."""
    impl = ledger.get("implementation")
    if not isinstance(impl, dict):
        return ledger, False
    lane_state = impl.get("laneState")
    if not isinstance(lane_state, dict):
        return ledger, False
    review = lane_state.get("review")
    if not isinstance(review, dict):
        return ledger, False

    changed = False
    for old, new in LANE_STATE_REVIEW_KEY_RENAMES.items():
        if old in review:
            if new not in review:
                review[new] = review[old]
            del review[old]
            changed = True
    return ledger, changed


def get_lane_state_review_field(state_review: dict | None, new_key: str):
    """Read lane-state review field by new key with legacy fallback (one release)."""
    state_review = state_review or {}
    if new_key in state_review:
        return state_review[new_key]
    legacy = _LEGACY_LANE_STATE_REVIEW_KEY_FOR.get(new_key)
    if legacy and legacy in state_review:
        return state_review[legacy]
    return None
