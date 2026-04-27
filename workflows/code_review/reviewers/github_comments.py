"""GitHub PR-comments external reviewer.

Generalizes the Codex Cloud fetcher: configurable bot logins,
clean/pending reactions, repo slug, cache TTL. Delegates to
``reviews.fetch_external_review`` /
``reviews.fetch_external_review_pr_body_signal`` for the actual work.
"""
from __future__ import annotations

import time
from typing import Any

from workflows.code_review.reviewers import (
    Reviewer,
    ReviewerContext,
    register,
)


_DEFAULT_LOGINS = ("chatgpt-codex-connector", "chatgpt-codex-connector[bot]")
_DEFAULT_CLEAN_REACTIONS = ("+1",)
_DEFAULT_PENDING_REACTIONS = ("eyes",)
_DEFAULT_CACHE_SECONDS = 1800


@register("github-comments")
class GithubCommentsReviewer:
    """Reads PR review threads from GitHub via ``gh api graphql``.

    Config shape (YAML, inside ``agents.external-reviewer:``):
        kind: github-comments
        logins: ["chatgpt-codex-connector[bot]"]
        clean-reactions: ["+1", "rocket"]
        pending-reactions: ["eyes"]
        cache-seconds: 300
        repo-slug: "owner/repo"
    """

    def __init__(self, cfg: dict, *, ws_context: ReviewerContext):
        self._cfg = cfg
        self._ctx = ws_context
        self._logins = set(cfg.get("logins") or _DEFAULT_LOGINS)
        self._clean_reactions = set(cfg.get("clean-reactions") or _DEFAULT_CLEAN_REACTIONS)
        self._pending_reactions = set(cfg.get("pending-reactions") or _DEFAULT_PENDING_REACTIONS)
        _cs = cfg.get("cache-seconds")
        self._cache_seconds = int(_cs) if _cs is not None else _DEFAULT_CACHE_SECONDS
        self._repo_slug = cfg.get("repo-slug") or ws_context.repo_slug

    def fetch_review(
        self,
        *,
        pr_number: int | None,
        current_head_sha: str | None,
        cached_review: dict | None,
    ) -> dict[str, Any]:
        from workflows.code_review.reviews import fetch_external_review

        return fetch_external_review(
            pr_number,
            current_head_sha=current_head_sha,
            cached_review=cached_review,
            fetch_pr_body_signal_fn=self.fetch_pr_body_signal,
            run_json_fn=self._ctx.run_json,
            cwd=self._ctx.repo_path,
            repo_slug=self._repo_slug,
            codex_bot_logins=self._logins,
            cache_seconds=self._cache_seconds,
            iso_to_epoch_fn=self._ctx.iso_to_epoch,
            now_epoch_fn=self._ctx.now_epoch,
            extract_severity_fn=self._ctx.extract_severity,
            extract_summary_fn=self._ctx.extract_summary,
            agent_name=self._ctx.agent_name,
        )

    def fetch_pr_body_signal(self, pr_number: int | None) -> dict | None:
        from workflows.code_review.reviews import fetch_external_review_pr_body_signal

        return fetch_external_review_pr_body_signal(
            pr_number,
            run_json_fn=self._ctx.run_json,
            cwd=self._ctx.repo_path,
            codex_bot_logins=self._logins,
            clean_reactions=self._clean_reactions,
            pending_reactions=self._pending_reactions,
            repo_slug=self._repo_slug,
        )

    def placeholder(
        self,
        *,
        required: bool,
        status: str,
        summary: str,
    ) -> dict[str, Any]:
        from workflows.code_review.reviews import external_review_placeholder

        return external_review_placeholder(
            required=required,
            status=status,
            summary=summary,
            agent_name=self._ctx.agent_name,
            agent_role=self._ctx.agent_role,
        )
