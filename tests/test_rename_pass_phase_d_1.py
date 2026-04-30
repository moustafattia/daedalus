"""Phase D-1 tests: persisted-state migration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_migrate_review_keys_renames_legacy_keys():
    from workflows.change_delivery.migrations import migrate_review_keys

    ledger = {"reviews": {"claudeCode": {"v": 1}, "codexCloud": {"v": 2}}}
    out, changed = migrate_review_keys(ledger)
    assert changed is True
    assert out["reviews"]["internalReview"] == {"v": 1}
    assert out["reviews"]["externalReview"] == {"v": 2}
    assert "claudeCode" not in out["reviews"]
    assert "codexCloud" not in out["reviews"]


def test_migrate_review_keys_idempotent():
    from workflows.change_delivery.migrations import migrate_review_keys

    ledger = {"reviews": {"internalReview": {"v": 1}, "externalReview": {"v": 2}}}
    out, changed = migrate_review_keys(ledger)
    assert changed is False
    assert out["reviews"]["internalReview"] == {"v": 1}


def test_migrate_review_keys_new_key_wins_when_both_present():
    from workflows.change_delivery.migrations import migrate_review_keys

    ledger = {"reviews": {
        "claudeCode": {"v": "old"}, "internalReview": {"v": "new"},
        "codexCloud": {"v": "old2"}, "externalReview": {"v": "new2"},
    }}
    out, changed = migrate_review_keys(ledger)
    assert changed is True  # old keys were dropped
    assert out["reviews"]["internalReview"] == {"v": "new"}
    assert out["reviews"]["externalReview"] == {"v": "new2"}
    assert "claudeCode" not in out["reviews"]
    assert "codexCloud" not in out["reviews"]


def test_migrate_review_keys_passes_through_unknown_keys():
    from workflows.change_delivery.migrations import migrate_review_keys

    ledger = {"reviews": {"claudeCode": {"v": 1}, "rockClaw": {"v": 9}}}
    out, _ = migrate_review_keys(ledger)
    assert out["reviews"]["rockClaw"] == {"v": 9}


def test_migrate_review_keys_handles_missing_reviews_block():
    from workflows.change_delivery.migrations import migrate_review_keys

    ledger = {"activeLane": {"number": 42}}
    out, changed = migrate_review_keys(ledger)
    assert changed is False
    assert out == ledger


def test_get_review_returns_new_when_present():
    from workflows.change_delivery.migrations import get_review

    reviews = {"internalReview": {"v": 1}}
    assert get_review(reviews, "internalReview") == {"v": 1}


def test_get_review_no_longer_falls_back_to_legacy():
    """Phase D-2: legacy-key fallback dropped after one-release deprecation window."""
    from workflows.change_delivery.migrations import get_review

    reviews = {"claudeCode": {"v": 1}}
    assert get_review(reviews, "internalReview") == {}

    reviews = {"codexCloud": {"v": 2}}
    assert get_review(reviews, "externalReview") == {}


def test_get_review_prefers_new_when_both_present():
    from workflows.change_delivery.migrations import get_review

    reviews = {"claudeCode": {"v": "old"}, "internalReview": {"v": "new"}}
    assert get_review(reviews, "internalReview") == {"v": "new"}


def test_get_review_returns_empty_dict_for_unknown_key():
    from workflows.change_delivery.migrations import get_review

    assert get_review({"x": 1}, "made-up") == {}


def test_get_review_returns_empty_dict_when_value_is_none():
    from workflows.change_delivery.migrations import get_review

    assert get_review({"internalReview": None}, "internalReview") == {}


def test_migrate_persisted_ledger_rewrites_file_atomically(tmp_path):
    from workflows.change_delivery.migrations import migrate_persisted_ledger

    p = tmp_path / "ledger.json"
    p.write_text(json.dumps({"reviews": {"claudeCode": {"v": 1}}}, indent=2))
    migrate_persisted_ledger(p)
    out = json.loads(p.read_text())
    assert out["reviews"]["internalReview"] == {"v": 1}
    assert "claudeCode" not in out["reviews"]


def test_migrate_persisted_ledger_noop_on_already_migrated(tmp_path):
    from workflows.change_delivery.migrations import migrate_persisted_ledger

    p = tmp_path / "ledger.json"
    initial = {"reviews": {"internalReview": {"v": 1}}}
    p.write_text(json.dumps(initial, indent=2))
    mtime_before = p.stat().st_mtime_ns
    # Sleep just enough that any rewrite would change mtime
    import time
    time.sleep(0.01)
    migrate_persisted_ledger(p)
    mtime_after = p.stat().st_mtime_ns
    assert mtime_before == mtime_after  # file untouched


def test_migrate_persisted_ledger_handles_missing_file(tmp_path):
    from workflows.change_delivery.migrations import migrate_persisted_ledger

    p = tmp_path / "does-not-exist.json"
    # Should not raise
    migrate_persisted_ledger(p)
    assert not p.exists()


def test_migrate_persisted_ledger_preserves_indent(tmp_path):
    from workflows.change_delivery.migrations import migrate_persisted_ledger

    p = tmp_path / "ledger.json"
    p.write_text(json.dumps({"reviews": {"claudeCode": {"v": 1}}}, indent=2))
    migrate_persisted_ledger(p)
    text = p.read_text()
    assert "  " in text  # 2-space indent preserved


def test_existing_installed_workflow_ledger_migrates_cleanly(tmp_path):
    """Smoke test: copy the installed workflow ledger to tmp, migrate, assert it works."""
    from workflows.change_delivery.migrations import migrate_persisted_ledger, get_review
    plugin_dir = Path.home() / ".hermes" / "plugins" / "daedalus"
    if not plugin_dir.exists():
        pytest.skip("installed workflow plugin not present on this host")
    src = plugin_dir.resolve().parents[2] / "memory" / "workflow-status.json"
    if not src.exists():
        pytest.skip("installed workflow ledger not present on this host")

    dst = tmp_path / "ledger.json"
    dst.write_text(src.read_text())
    migrate_persisted_ledger(dst)

    out = json.loads(dst.read_text())
    reviews = out.get("reviews") or {}
    # Old keys gone (if they were present)
    assert "claudeCode" not in reviews
    assert "codexCloud" not in reviews
    # New keys readable via get_review (passes whether or not the source had old keys)
    _ = get_review(reviews, "internalReview")
    _ = get_review(reviews, "externalReview")


def test_action_dispatcher_accepts_run_internal_review():
    """The dispatcher matches the new literal."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "daedalus" / "workflows/change_delivery/actions.py"
    text = src.read_text()
    assert "run_internal_review" in text


def test_parity_gate_accepts_run_internal_review():
    """Phase D-1 alias regression: parity compatibility map must accept the new
    relay action type, otherwise active mode blocks the lane with shadow-parity-mismatch."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent / "daedalus"
    runtime_src = (repo_root / "runtime.py").read_text()
    tools_src = (repo_root / "daedalus_cli.py").read_text()
    for src in (runtime_src, tools_src):
        assert "run_internal_review" in src
        assert (
            '("run_internal_review", "request_internal_review")' in src
            or '("run_internal_review","request_internal_review")' in src
        )
