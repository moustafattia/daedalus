"""Phase B tests: external reviewer pluggability."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_reviewer_module_exposes_protocol_and_registry():
    from workflows.code_review.reviewers import Reviewer, ReviewerContext, register, build_reviewer, _REVIEWER_KINDS
    assert callable(register)
    assert callable(build_reviewer)
    assert isinstance(_REVIEWER_KINDS, dict)


def test_build_reviewer_unknown_kind_raises():
    from workflows.code_review.reviewers import build_reviewer

    with pytest.raises(ValueError, match="unknown"):
        build_reviewer({"kind": "made-up"}, ws_context=MagicMock())


def _ws_context():
    from workflows.code_review.reviewers import ReviewerContext

    return ReviewerContext(
        run_json=MagicMock(return_value={"data": {"repository": {"pullRequest": {
            "state": "OPEN", "headRefOid": "abc123",
            "reviewThreads": {"nodes": []},
        }}}}),
        repo_path=Path("/tmp"),
        repo_slug="acme/widget",
        iso_to_epoch=lambda x: None,
        now_epoch=lambda: 1000.0,
        extract_severity=lambda body: "minor",
        extract_summary=lambda body: body,
        agent_name="External_Reviewer_Agent",
    )


def test_github_comments_reviewer_registered():
    from workflows.code_review.reviewers import _REVIEWER_KINDS, github_comments  # noqa: F401

    assert "github-comments" in _REVIEWER_KINDS


def test_github_comments_reviewer_uses_configured_repo_slug():
    """Regression: repo slug comes from reviewer config, not from workspace.py hardcode."""
    from workflows.code_review.reviewers import build_reviewer

    ctx = _ws_context()
    cfg = {
        "enabled": True,
        "name": "X",
        "kind": "github-comments",
        "logins": ["bot[bot]"],
        "repo-slug": "different/repo",
    }
    rv = build_reviewer(cfg, ws_context=ctx)
    rv.fetch_review(pr_number=42, current_head_sha="abc123", cached_review=None)
    # The GraphQL query string passed to gh api graphql contains the configured slug
    args, _ = ctx.run_json.call_args
    cmd_argv = args[0]
    flat = " ".join(cmd_argv)
    # Repo slug appears split as owner/name in the GraphQL query
    assert 'owner:"different"' in flat
    assert 'name:"repo"' in flat
    assert 'owner:"acme"' not in flat


def test_github_comments_reviewer_uses_configured_logins():
    """Bot logins come from reviewer config."""
    from workflows.code_review.reviewers import build_reviewer

    ctx = _ws_context()
    # Inject one matching review-thread comment from a custom bot login.
    ctx.run_json.return_value = {"data": {"repository": {"pullRequest": {
        "state": "OPEN", "headRefOid": "abc123",
        "reviewThreads": {"nodes": [{
            "id": "T1", "isResolved": False, "isOutdated": False,
            "path": "a.py", "line": 10,
            "comments": {"nodes": [{
                "author": {"login": "my-bot[bot]"},
                "body": "issue", "url": "https://x", "createdAt": "2026-01-01T00:00:00Z",
            }]},
        }]},
    }}}}
    cfg = {
        "enabled": True,
        "name": "X",
        "kind": "github-comments",
        "logins": ["my-bot[bot]"],
        "repo-slug": "acme/widget",
    }
    rv = build_reviewer(cfg, ws_context=ctx)
    out = rv.fetch_review(pr_number=42, current_head_sha="abc123", cached_review=None)
    assert any(t.get("source") == "externalReview" for t in out.get("threads", []))


def test_github_comments_reviewer_ignores_non_matching_logins():
    """Comments from non-configured logins are filtered out."""
    from workflows.code_review.reviewers import build_reviewer

    ctx = _ws_context()
    ctx.run_json.return_value = {"data": {"repository": {"pullRequest": {
        "state": "OPEN", "headRefOid": "abc123",
        "reviewThreads": {"nodes": [{
            "id": "T1", "isResolved": False, "isOutdated": False,
            "path": "a.py", "line": 10,
            "comments": {"nodes": [{
                "author": {"login": "human-user"},
                "body": "issue", "url": "https://x", "createdAt": "2026-01-01T00:00:00Z",
            }]},
        }]},
    }}}}
    cfg = {
        "enabled": True, "name": "X", "kind": "github-comments",
        "logins": ["my-bot[bot]"], "repo-slug": "acme/widget",
    }
    rv = build_reviewer(cfg, ws_context=ctx)
    out = rv.fetch_review(pr_number=42, current_head_sha="abc123", cached_review=None)
    assert out.get("threads") == []


def test_github_comments_reviewer_placeholder():
    """Placeholder shape matches reviews.codex_cloud_placeholder for back-compat."""
    from workflows.code_review.reviewers import build_reviewer

    cfg = {"enabled": True, "name": "X", "kind": "github-comments", "repo-slug": "x/y"}
    rv = build_reviewer(cfg, ws_context=_ws_context())
    p = rv.placeholder(required=True, status="pending", summary="waiting")
    assert p["status"] == "pending"
    assert p["summary"] == "waiting"
    assert p["agentRole"] == "external_reviewer_agent"


def test_disabled_reviewer_registered():
    from workflows.code_review.reviewers import _REVIEWER_KINDS, disabled  # noqa: F401

    assert "disabled" in _REVIEWER_KINDS


def test_disabled_reviewer_returns_skipped_placeholder():
    from workflows.code_review.reviewers import build_reviewer

    cfg = {"enabled": False, "name": "X"}
    rv = build_reviewer(cfg, ws_context=_ws_context())
    out = rv.fetch_review(pr_number=42, current_head_sha="abc", cached_review=None)
    assert out["status"] == "skipped"
    assert out["required"] is False


def test_disabled_reviewer_does_not_call_run_json():
    from workflows.code_review.reviewers import build_reviewer

    ctx = _ws_context()
    cfg = {"enabled": False, "name": "X"}
    rv = build_reviewer(cfg, ws_context=ctx)
    rv.fetch_review(pr_number=42, current_head_sha="abc", cached_review=None)
    rv.fetch_pr_body_signal(42)
    ctx.run_json.assert_not_called()


def test_build_reviewer_defaults_to_disabled_when_enabled_false():
    """enabled: false wins over an explicit kind."""
    from workflows.code_review.reviewers import build_reviewer
    from workflows.code_review.reviewers.disabled import DisabledReviewer

    rv = build_reviewer({"enabled": False, "kind": "github-comments"}, ws_context=_ws_context())
    assert isinstance(rv, DisabledReviewer)


def test_build_reviewer_defaults_to_github_comments_when_enabled():
    """No explicit kind + enabled: true -> github-comments."""
    from workflows.code_review.reviewers import build_reviewer
    from workflows.code_review.reviewers.github_comments import GithubCommentsReviewer

    rv = build_reviewer({"enabled": True, "name": "X"}, ws_context=_ws_context())
    assert isinstance(rv, GithubCommentsReviewer)


def test_default_logins_includes_both_codex_connector_forms():
    """Regression: legacy CODEX_BOT_LOGINS had both bare and bracketed forms."""
    from workflows.code_review.reviewers.github_comments import _DEFAULT_LOGINS
    assert "chatgpt-codex-connector" in _DEFAULT_LOGINS
    assert "chatgpt-codex-connector[bot]" in _DEFAULT_LOGINS


def test_default_clean_reactions_only_includes_thumbs_up():
    """Regression: legacy default was just '+1'; expanded defaults can prematurely pass merge gate."""
    from workflows.code_review.reviewers.github_comments import _DEFAULT_CLEAN_REACTIONS
    assert tuple(_DEFAULT_CLEAN_REACTIONS) == ("+1",)


def test_default_cache_seconds_matches_legacy_1800():
    """Regression: legacy CODEX_CLOUD_CACHE_SECONDS was 1800."""
    from workflows.code_review.reviewers.github_comments import _DEFAULT_CACHE_SECONDS
    assert _DEFAULT_CACHE_SECONDS == 1800


def test_cache_seconds_zero_is_preserved():
    """cache-seconds: 0 must not be treated as falsy and replaced with the default."""
    from workflows.code_review.reviewers.github_comments import GithubCommentsReviewer

    rv = GithubCommentsReviewer(
        {"enabled": True, "name": "X", "cache-seconds": 0},
        ws_context=_ws_context(),
    )
    assert rv._cache_seconds == 0
