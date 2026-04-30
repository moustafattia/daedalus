from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_PROMPT_BUNDLE = Path(__file__).parent / "prompts"


def _load_template(name: str) -> str:
    return (_PROMPT_BUNDLE / f"{name}.md").read_text(encoding="utf-8")


def apply_workflow_policy(prompt_text: str, workflow_policy: str | None) -> str:
    """Prefix shared workflow policy ahead of role-specific instructions."""
    policy = str(workflow_policy or "").strip()
    if not policy:
        return prompt_text
    body = prompt_text.lstrip()
    return "\n".join(
        [
            "# Shared Workflow Policy",
            "",
            policy,
            "",
            "# Role-Specific Instructions",
            "",
            body,
        ]
    )


"""Workflow prompt rendering helpers.

This slice extracts deterministic prompt construction from the legacy wrapper so
workflow execution can compose adapter-owned prompt logic without keeping all
rendering rules in the shim.
"""


def summarize_validation(ledger: dict[str, Any]) -> list[str]:
    checks = ((ledger.get("pr") or {}).get("checks") or {})
    items = []
    if checks.get("summary"):
        items.append(f"checks: {checks['summary']}")
    impl = ledger.get("implementation") or {}
    if impl.get("status"):
        items.append(f"implementation: {impl['status']}")
    return items[:4]


def render_lane_memo(
    *,
    issue: dict[str, Any],
    worktree: Path,
    branch: str | None,
    open_pr: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    latest_progress: dict[str, Any] | None,
    validation_summary: list[str] | None,
    acp_strategy: dict[str, Any] | None = None,
) -> str:
    must_fix = [item.get("summary", "") for item in (repair_brief or {}).get("mustFix", []) if item.get("summary")][:5]
    should_fix = [item.get("summary", "") for item in (repair_brief or {}).get("shouldFix", []) if item.get("summary")][:5]
    lines = [
        f"# Lane Memo: Issue #{issue.get('number')}",
        "",
        f"Issue: #{issue.get('number')} - {issue.get('title')}",
        f"Issue URL: {issue.get('url')}",
        f"Worktree: {worktree}",
        f"Branch: {branch or 'unknown'}",
        f"PR: #{open_pr.get('number')} {open_pr.get('url')}" if open_pr else "PR: none",
        f"Current head: {open_pr.get('headRefOid')}" if open_pr and open_pr.get('headRefOid') else "Current head: none",
        "",
        "## Current objective",
        "- Land the next repair head that clears the current active findings without widening scope.",
    ]
    if acp_strategy:
        lines.extend([
            "",
            "## ACP session strategy",
            "- Preferred ACP mode: persistent session",
            f"- Nudge via: {acp_strategy.get('nudgeTool')} -> {acp_strategy.get('targetSessionKey')}" if acp_strategy.get('nudgeTool') and acp_strategy.get('targetSessionKey') else "- Nudge via: not configured",
            f"- Resume session id: {acp_strategy.get('resumeSessionId')}" if acp_strategy.get('resumeSessionId') else "- Resume session id: not recorded",
        ])
    lines.extend([
        "",
        "## Current must-fix items",
    ])
    lines.extend([f"- {item}" for item in must_fix] or ["- none recorded"])
    lines.extend(["", "## Current should-fix items"])
    lines.extend([f"- {item}" for item in should_fix] or ["- none recorded"])
    lines.extend(["", "## Validation snapshot"])
    lines.extend([f"- {item}" for item in (validation_summary or [])] or ["- no validation summary recorded"])
    lines.extend(["", "## Last meaningful progress"])
    if latest_progress:
        lines.append(f"- {latest_progress.get('kind', 'unknown')} at {latest_progress.get('at', 'unknown')}")
    else:
        lines.append("- none recorded")
    lines.extend(["", "## Guardrails", "- Do not touch data/test_messages/messages.json", "- Do not publish .codex artifacts", "- Keep scope narrow to the current repair brief"])
    return "\n".join(lines[:118]) + "\n"


def render_implementation_dispatch_prompt(
    *,
    issue: dict[str, Any],
    issue_details: dict[str, Any] | None,
    worktree: Path,
    lane_memo_path: Path | None,
    lane_state_path: Path | None,
    open_pr: dict[str, Any] | None,
    action: str,
    workflow_state: str | None,
    workflow_policy: str | None = None,
) -> str:
    issue_body = (issue_details or {}).get("body") or "No issue body provided. Use the title plus existing repo context honestly."
    compact_turn = action in {"continue-session", "poke-session"}

    if open_pr:
        open_pr_block = "\n".join([
            f"Open PR: #{open_pr.get('number')} {open_pr.get('url')}",
            f"Current PR head: {open_pr.get('headRefOid')}",
        ])
    else:
        open_pr_block = "There is no open PR yet for this lane."

    if action == "restart-session":
        action_line = "You are resuming ownership in a persistent Codex session after the previous owner was missing or stale."
    elif action == "poke-session":
        action_line = "The existing persistent Codex session went quiet; continue from the lane memo/state without re-scoping the task."
    else:
        action_line = "Continue the existing persistent Codex implementation session for this lane without re-reading the full issue brief unless the lane memo/state requires it."

    if workflow_state == "ready_to_publish":
        action_and_workflow_block = "\n".join([
            action_line,
            "The local branch has already passed the Claude pre-publish gate.",
            "Publish now: push the branch, open or update the PR, and make sure it is ready for review immediately (not left as draft).",
        ])
    elif workflow_state in {"awaiting_claude_prepublish", "claude_prepublish_findings", "implementing_local", "implementing"} and not open_pr:
        action_and_workflow_block = "\n".join([
            action_line,
            "Do not publish yet.",
            "Your target in this phase is a committed local candidate head that is ready for Claude pre-publish review.",
        ])
    else:
        action_and_workflow_block = action_line

    if compact_turn:
        compact_or_issue_block = "\n".join([
            "Current turn context is intentionally compact to save tokens.",
            "Use the lane memo/state plus current worktree diff as the source of truth for any remaining detail.",
        ])
    else:
        compact_or_issue_block = "\n".join([
            "Issue summary:",
            issue_body.strip() or "No issue body provided.",
        ])

    prompt_text = _load_template("coder").format(
        issue_number=issue.get("number"),
        issue_title=issue.get("title"),
        issue_url=issue.get("url"),
        worktree=worktree,
        lane_memo_line=f"Lane memo: {lane_memo_path}" if lane_memo_path else "Lane memo: none",
        lane_state_line=f"Lane state: {lane_state_path}" if lane_state_path else "Lane state: none",
        open_pr_block=open_pr_block,
        action_and_workflow_block=action_and_workflow_block,
        compact_or_issue_block=compact_or_issue_block,
    )
    return apply_workflow_policy(prompt_text, workflow_policy)


def render_external_reviewer_repair_handoff_prompt(
    *,
    issue: dict[str, Any] | None,
    external_review: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    lane_memo_path: Path | None,
    lane_state_path: Path | None,
    pr_url: str | None,
    external_reviewer_agent_name: str,
    workflow_policy: str | None = None,
) -> str:
    review = external_review or {}
    must_fix = [item.get("summary", "") for item in (repair_brief or {}).get("mustFix", []) if item.get("summary")][:8]
    should_fix = [item.get("summary", "") for item in (repair_brief or {}).get("shouldFix", []) if item.get("summary")][:8]
    must_fix_lines = "\n".join([f"- {item}" for item in must_fix] or ["- none recorded"])
    should_fix_lines = "\n".join([f"- {item}" for item in should_fix] or ["- none recorded"])
    prompt_text = _load_template("external-reviewer-repair-handoff").format(
        external_reviewer_agent_name=external_reviewer_agent_name,
        issue_number=(issue or {}).get("number"),
        issue_title=(issue or {}).get("title"),
        reviewed_head_sha=review.get("reviewedHeadSha") or "unknown",
        lane_memo_line=f"Lane memo: {lane_memo_path}" if lane_memo_path else "Lane memo: none",
        lane_state_line=f"Lane state: {lane_state_path}" if lane_state_path else "Lane state: none",
        pr_url=pr_url or "unknown",
        review_summary=review.get("summary") or f"No {external_reviewer_agent_name} summary recorded.",
        must_fix_lines=must_fix_lines,
        should_fix_lines=should_fix_lines,
    )
    return apply_workflow_policy(prompt_text, workflow_policy)


def render_claude_repair_handoff_prompt(
    *,
    issue: dict[str, Any] | None,
    internal_review: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    lane_memo_path: Path | None,
    lane_state_path: Path | None,
    internal_reviewer_agent_name: str,
    workflow_policy: str | None = None,
) -> str:
    review = internal_review or {}
    must_fix = [item.get("summary", "") for item in (repair_brief or {}).get("mustFix", []) if item.get("summary")][:8]
    should_fix = [item.get("summary", "") for item in (repair_brief or {}).get("shouldFix", []) if item.get("summary")][:8]
    must_fix_lines = "\n".join([f"- {item}" for item in must_fix] or ["- none recorded"])
    should_fix_lines = "\n".join([f"- {item}" for item in should_fix] or ["- none recorded"])
    prompt_text = _load_template("repair-handoff").format(
        internal_reviewer_agent_name=internal_reviewer_agent_name,
        issue_number=(issue or {}).get("number"),
        issue_title=(issue or {}).get("title"),
        reviewed_head_sha=review.get("reviewedHeadSha") or "unknown",
        lane_memo_line=f"Lane memo: {lane_memo_path}" if lane_memo_path else "Lane memo: none",
        lane_state_line=f"Lane state: {lane_state_path}" if lane_state_path else "Lane state: none",
        review_summary=review.get("summary") or "No Claude summary recorded.",
        must_fix_lines=must_fix_lines,
        should_fix_lines=should_fix_lines,
    )
    return apply_workflow_policy(prompt_text, workflow_policy)


def render_inter_review_agent_prompt(
    *,
    issue: dict[str, Any],
    worktree: Path,
    lane_memo_path: Path | None,
    lane_state_path: Path | None,
    head_sha: str,
    workflow_policy: str | None = None,
) -> str:
    prompt_text = _load_template("internal-reviewer").format(
        worktree=worktree,
        head_sha=head_sha,
        issue_number=issue.get("number"),
        issue_title=issue.get("title"),
        issue_url=issue.get("url"),
        lane_memo_line=f"Lane memo: {lane_memo_path}" if lane_memo_path else "Lane memo: none",
        lane_state_line=f"Lane state: {lane_state_path}" if lane_state_path else "Lane state: none",
    )
    return apply_workflow_policy(prompt_text, workflow_policy)
