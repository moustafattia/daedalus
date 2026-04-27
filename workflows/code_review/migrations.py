"""Persisted-state migrations for the code-review workflow.

Phase D-1 rationale:
  reviews.claudeCode -> reviews.internalReview
  reviews.codexCloud -> reviews.externalReview

The old names tied the ledger to specific providers (Claude / Codex
Cloud). Phases A-C made runtimes/reviewers/webhooks pluggable; this
migration removes the last operator-visible coupling to provider names.

`migrate_persisted_ledger(path)` runs idempotently on workspace setup.
`get_review(reviews_dict, new_key)` reads new key with legacy fallback
so an unmigrated ledger still works for one release.
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


def get_review(reviews: dict[str, Any] | None, new_key: str) -> dict[str, Any]:
    """Read a review by its new key; fall back to the legacy key."""
    reviews = reviews or {}
    value = reviews.get(new_key)
    if value:
        return value
    legacy_key = _LEGACY_KEY_FOR.get(new_key)
    if legacy_key:
        legacy_value = reviews.get(legacy_key)
        if legacy_value:
            return legacy_value
    return {}


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

    _, changed = migrate_review_keys(ledger)
    if not changed:
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
