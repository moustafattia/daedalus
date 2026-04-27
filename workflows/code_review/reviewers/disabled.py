"""Disabled external reviewer — used when ``enabled: false`` or
``kind: disabled``. All operations short-circuit with a skipped
placeholder; no GitHub API calls."""
from __future__ import annotations

from typing import Any

from workflows.code_review.reviewers import (
    Reviewer,
    ReviewerContext,
    register,
)


@register("disabled")
class DisabledReviewer:
    def __init__(self, cfg: dict, *, ws_context: ReviewerContext):
        self._cfg = cfg
        self._ctx = ws_context

    def fetch_review(
        self,
        *,
        pr_number: int | None,
        current_head_sha: str | None,
        cached_review: dict | None,
    ) -> dict[str, Any]:
        return self.placeholder(
            required=False,
            status="skipped",
            summary="External review disabled.",
        )

    def fetch_pr_body_signal(self, pr_number: int | None) -> dict | None:
        return None

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
