"""Pluggable external-reviewer abstraction.

Mirrors the runtime layer: Protocol + @register decorator + factory.
Each kind wraps a way of fetching post-publish review threads (today:
GitHub PR comments from configured bots; future: webhook payloads,
HTTP polling, etc.) and normalizes them into the provider-neutral
output shape that `reviews.normalize_review` already enforces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class ReviewerContext:
    """Workspace-scoped primitives a reviewer needs at fetch time."""

    run_json: Callable[..., Any]
    repo_path: Path
    repo_slug: str
    code_host_client: Any
    iso_to_epoch: Callable[[Any], int | None]
    now_epoch: Callable[[], float]
    extract_severity: Callable[[str], str]
    extract_summary: Callable[[str], str]
    agent_name: str
    agent_role: str = "external_reviewer_agent"


@runtime_checkable
class Reviewer(Protocol):
    """Protocol every external reviewer kind implements."""

    def fetch_review(
        self,
        *,
        pr_number: int | None,
        current_head_sha: str | None,
        cached_review: dict | None,
    ) -> dict[str, Any]: ...

    def fetch_pr_body_signal(self, pr_number: int | None) -> dict | None: ...

    def placeholder(
        self,
        *,
        required: bool,
        status: str,
        summary: str,
    ) -> dict[str, Any]: ...


_REVIEWER_KINDS: dict[str, type] = {}


def register(kind: str):
    """Decorator: registers a class as the implementation for a reviewer kind."""

    def _register(cls):
        _REVIEWER_KINDS[kind] = cls
        return cls

    return _register


def build_reviewer(reviewer_cfg: dict, *, ws_context: ReviewerContext) -> Reviewer:
    """Instantiate the configured reviewer.

    Selection rules:
      - If reviewer_cfg.get('enabled') is False -> 'disabled'.
      - Else use reviewer_cfg.get('kind') (default 'github-comments').
    """
    # Trigger registration side-effects via lazy import.
    from workflows.change_delivery.reviewers import github_comments  # noqa: F401
    from workflows.change_delivery.reviewers import disabled as _disabled  # noqa: F401

    if reviewer_cfg.get("enabled") is False:
        kind = "disabled"
    else:
        kind = reviewer_cfg.get("kind") or "github-comments"

    if kind not in _REVIEWER_KINDS:
        raise ValueError(
            f"unknown external reviewer kind={kind!r}; "
            f"registered kinds: {sorted(_REVIEWER_KINDS)}"
        )
    cls = _REVIEWER_KINDS[kind]
    return cls(reviewer_cfg, ws_context=ws_context)
