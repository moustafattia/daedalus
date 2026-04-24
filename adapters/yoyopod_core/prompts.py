from __future__ import annotations

import re
from pathlib import Path
from typing import Any


"""YoYoPod Core prompt rendering helpers.

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
) -> str:
    issue_body = (issue_details or {}).get("body") or "No issue body provided. Use the title plus existing repo context honestly."
    compact_turn = action in {"continue-session", "poke-session"}
    shared = [
        f"YoyoPod_Core active lane owner for issue #{issue.get('number')} in {worktree}.",
        f"Issue: #{issue.get('number')} {issue.get('title')}",
        f"Issue URL: {issue.get('url')}",
        f"Lane memo: {lane_memo_path}" if lane_memo_path else "Lane memo: none",
        f"Lane state: {lane_state_path}" if lane_state_path else "Lane state: none",
        "Read .lane-memo.md and .lane-state.json first; they are authoritative.",
        "Do not touch data/test_messages/messages.json.",
        "Do not publish .codex artifacts.",
        "Keep scope narrow and honest.",
    ]
    if open_pr:
        shared.extend([
            f"Open PR: #{open_pr.get('number')} {open_pr.get('url')}",
            f"Current PR head: {open_pr.get('headRefOid')}",
        ])
    else:
        shared.append("There is no open PR yet for this lane.")
    if action == "restart-session":
        shared.append("You are resuming ownership in a persistent Codex session after the previous owner was missing or stale.")
    elif action == "poke-session":
        shared.append("The existing persistent Codex session went quiet; continue from the lane memo/state without re-scoping the task.")
    else:
        shared.append("Continue the existing persistent Codex implementation session for this lane without re-reading the full issue brief unless the lane memo/state requires it.")
    if workflow_state == "ready_to_publish":
        shared.extend([
            "The local branch has already passed the Claude pre-publish gate.",
            "Publish now: push the branch, open or update the PR, and make sure it is ready for review immediately (not left as draft).",
        ])
    elif workflow_state in {"awaiting_claude_prepublish", "claude_prepublish_findings", "implementing_local", "implementing"} and not open_pr:
        shared.extend([
            "Do not publish yet.",
            "Your target in this phase is a committed local candidate head that is ready for Claude pre-publish review.",
        ])
    shared.extend([
        "Run the internal quality gate before you report done: uv run python scripts/quality.py ci.",
        "If that command fails, fix the issues and rerun it; do not claim green validation without a passing run.",
        "If there is no PR and the workflow has not reached ready_to_publish, stop after a clean local commit plus focused validation.",
        "If the workflow state is ready_to_publish, publish the branch and create or update the PR ready for review.",
        "If a PR already exists, continue from the current branch head and only address the active lane objective.",
        "Report exactly what changed, what validation ran, commit SHA, branch, and PR URL.",
    ])
    if compact_turn:
        shared.extend([
            "Current turn context is intentionally compact to save tokens.",
            "Use the lane memo/state plus current worktree diff as the source of truth for any remaining detail.",
        ])
    else:
        shared.extend([
            "Issue summary:",
            issue_body.strip() or "No issue body provided.",
        ])
    return "\n".join(shared)


def render_codex_cloud_repair_handoff_prompt(
    *,
    issue: dict[str, Any] | None,
    codex_review: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    lane_memo_path: Path | None,
    lane_state_path: Path | None,
    pr_url: str | None,
    external_reviewer_agent_name: str,
) -> str:
    review = codex_review or {}
    must_fix = [item.get("summary", "") for item in (repair_brief or {}).get("mustFix", []) if item.get("summary")][:8]
    should_fix = [item.get("summary", "") for item in (repair_brief or {}).get("shouldFix", []) if item.get("summary")][:8]
    lines = [
        f"{external_reviewer_agent_name} review found follow-up work for issue #{(issue or {}).get('number')} on published head {review.get('reviewedHeadSha') or 'unknown'}.",
        f"Issue: #{(issue or {}).get('number')} {(issue or {}).get('title')}",
        f"PR: {pr_url or 'unknown'}",
        f"Lane memo: {lane_memo_path}" if lane_memo_path else "Lane memo: none",
        f"Lane state: {lane_state_path}" if lane_state_path else "Lane state: none",
        "Read .lane-memo.md and .lane-state.json first; they are authoritative.",
        "Stay on the same branch and fix the current Codex Cloud review findings on the published head.",
        "After fixes, run focused validation, update the branch head, and stop so the normal review loop can re-evaluate.",
        "",
        "Codex Cloud summary:",
        review.get("summary") or "No Codex Cloud summary recorded.",
        "",
        "Current must-fix items:",
    ]
    lines.extend([f"- {item}" for item in must_fix] or ["- none recorded"])
    lines.extend(["", "Current should-fix items:"])
    lines.extend([f"- {item}" for item in should_fix] or ["- none recorded"])
    lines.extend([
        "",
        "Guardrails:",
        "- Do not touch data/test_messages/messages.json.",
        "- Do not publish .codex artifacts.",
        "- Keep scope narrow to the active Codex Cloud repair brief.",
        "- Report exactly what changed, what validation ran, and the new HEAD SHA.",
    ])
    return "\n".join(lines)


def render_claude_repair_handoff_prompt(
    *,
    issue: dict[str, Any] | None,
    claude_review: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    lane_memo_path: Path | None,
    lane_state_path: Path | None,
    internal_reviewer_agent_name: str,
) -> str:
    review = claude_review or {}
    must_fix = [item.get("summary", "") for item in (repair_brief or {}).get("mustFix", []) if item.get("summary")][:8]
    should_fix = [item.get("summary", "") for item in (repair_brief or {}).get("shouldFix", []) if item.get("summary")][:8]
    lines = [
        f"{internal_reviewer_agent_name} pre-publish review found follow-up work for issue #{(issue or {}).get('number')} on local head {review.get('reviewedHeadSha') or 'unknown'}.",
        f"Issue: #{(issue or {}).get('number')} {(issue or {}).get('title')}",
        f"Lane memo: {lane_memo_path}" if lane_memo_path else "Lane memo: none",
        f"Lane state: {lane_state_path}" if lane_state_path else "Lane state: none",
        "Read .lane-memo.md and .lane-state.json first; they are authoritative.",
        "Do not publish yet.",
        "Stay in the same lane and fix the current Claude pre-publish findings on the local branch.",
        "After fixes, run focused validation, update the local branch head, and stop for Claude re-review.",
        "",
        "Claude summary:",
        review.get("summary") or "No Claude summary recorded.",
        "",
        "Current must-fix items:",
    ]
    lines.extend([f"- {item}" for item in must_fix] or ["- none recorded"])
    lines.extend(["", "Current should-fix items:"])
    lines.extend([f"- {item}" for item in should_fix] or ["- none recorded"])
    lines.extend([
        "",
        "Guardrails:",
        "- Do not touch data/test_messages/messages.json.",
        "- Do not publish .codex artifacts.",
        "- Keep scope narrow to the current repair brief.",
        "- Report exactly what changed, what validation ran, and the new local HEAD SHA.",
    ])
    return "\n".join(lines)


def render_inter_review_agent_prompt(
    *,
    issue: dict[str, Any],
    worktree: Path,
    lane_memo_path: Path | None,
    lane_state_path: Path | None,
    head_sha: str,
) -> str:
    lines = [
        'You are reviewing the unpublished local lane head for YoyoPod_Core as a strict pre-publish code review gate.',
        f'Repository: {worktree}',
        f'Target local head SHA: {head_sha}',
        'Scope: local-prepublish only. Review the actual current local HEAD in this worktree.',
        f'Issue: #{issue.get("number")} {issue.get("title")}',
        f'Issue URL: {issue.get("url")}',
        f'Lane memo: {lane_memo_path}' if lane_memo_path else 'Lane memo: none',
        f'Lane state: {lane_state_path}' if lane_state_path else 'Lane state: none',
        'Read the lane memo/state if present before reviewing.',
        'Focus on correctness, regressions, test honesty, and whether the code is actually ready to publish.',
        'Return JSON only, no markdown fences, with this exact schema:',
        '{"verdict":"PASS_CLEAN"|"PASS_WITH_FINDINGS"|"REWORK","summary":"short paragraph","blockingFindings":["..."],"majorConcerns":["..."],"minorSuggestions":["..."],"requiredNextAction":"string or null"}',
        'Rules:',
        '- Use REWORK only for blocking issues that must be fixed before publish.',
        '- Use PASS_WITH_FINDINGS for non-blocking but real concerns worth recording.',
        '- Use PASS_CLEAN only if you genuinely found nothing worth recording.',
        '- Be concise and tie findings to the actual current local diff/head.',
    ]
    return '\n'.join(lines)
