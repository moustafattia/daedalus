from __future__ import annotations

from typing import Any

from workflows.change_delivery.migrations import get_review
from workflows.change_delivery.reviews import (
    has_local_candidate,
    inter_review_agent_is_running_on_head,
    should_dispatch_internal_review_repair_handoff,
    should_dispatch_external_review_repair_handoff,
)


"""Top-level change-delivery workflow orchestration entrypoints.

This slice extracts a safe subset of next-action derivation from the legacy
wrapper. Where the adapter does not yet own a branch, it falls back to the
wrapper-provided `nextAction` so behavior does not regress while the migration
is incomplete.
"""


DEFAULT_LANE_FAILURE_RETRY_BUDGET = 3
DEFAULT_LANE_NO_PROGRESS_TICK_BUDGET = 3


def _operator_attention_reasons(status: dict[str, Any]) -> list[str]:
    return [
        str(reason)
        for reason in (status.get("staleLaneReasons") or [])
        if str(reason).startswith("operator-attention-required:")
    ]


def derive_next_action(
    status: dict[str, Any],
    *,
    failure_retry_budget: int = DEFAULT_LANE_FAILURE_RETRY_BUDGET,
    no_progress_tick_budget: int = DEFAULT_LANE_NO_PROGRESS_TICK_BUDGET,
) -> dict[str, Any]:
    active_lane = status.get("activeLane") or {}
    open_pr = status.get("openPr") or None
    health = status.get("health")
    implementation = status.get("implementation") or {}
    session_action = implementation.get("sessionActionRecommendation") or {}
    active_session_health = implementation.get("activeSessionHealth") or {}
    local_head_sha = implementation.get("localHeadSha")
    workflow_state = ((status.get("ledger") or {}).get("workflowState"))
    review_loop_state = status.get("derivedReviewLoopState")
    merge_blocked = bool(status.get("derivedMergeBlocked"))
    pre_publish_review_preflight = ((status.get("preflight") or {}).get("prePublishReview") or {})
    operator_attention_reasons = _operator_attention_reasons(status)
    pr_head_sha = (open_pr or {}).get("headRefOid")
    fallback = status.get("nextAction") or {"type": "noop", "reason": "no-forward-action-needed"}
    lane_state = implementation.get("laneState") or {}
    failure_state = lane_state.get("failure") or {}
    budget_state = lane_state.get("budget") or {}
    repair_brief = status.get("repairBrief") or ((status.get("ledger") or {}).get("repairBrief")) or {}
    reviews = status.get("reviews") or {}
    external_review = get_review(reviews, "externalReview")
    current_postpublish_head = pr_head_sha or local_head_sha

    internal_review = get_review(reviews, "internalReview")
    if inter_review_agent_is_running_on_head(internal_review, local_head_sha):
        return {
            "type": "noop",
            "reason": "internal-review-running",
            "issueNumber": active_lane.get("number") if active_lane else None,
            "headSha": local_head_sha,
        }

    if not active_lane:
        return {"type": "noop", "reason": "no-active-lane"}

    if open_pr and review_loop_state == "clean" and not merge_blocked:
        return {
            "type": "merge_and_promote",
            "reason": "published-pr-approved",
            "issueNumber": active_lane.get("number"),
            "headSha": pr_head_sha,
        }

    internal_review_repair_handoff = should_dispatch_internal_review_repair_handoff(
        lane_state=lane_state,
        session_action=session_action,
        internal_review=internal_review,
        repair_brief=repair_brief,
        workflow_state=workflow_state,
        current_head_sha=local_head_sha,
        has_open_pr=bool(open_pr),
    )

    if health not in {"healthy", "stale-ledger"}:
        if (
            health == "stale-lane"
            and not open_pr
            and not operator_attention_reasons
            and workflow_state == "ready_to_publish"
        ):
            return {
                "type": "publish_ready_pr",
                "reason": "ready-local-branch-needs-pr",
                "issueNumber": active_lane.get("number"),
                "headSha": local_head_sha,
            }
        if (
            health == "stale-lane"
            and not open_pr
            and not operator_attention_reasons
            and pre_publish_review_preflight.get("shouldRun")
            and workflow_state in {"implementing_local", "awaiting_pre_publish_review", "pre_publish_review_findings", "implementing"}
        ):
            return {
                "type": "run_internal_review",
                "reason": "prepublish-review-required",
                "headSha": pre_publish_review_preflight.get("currentHeadSha"),
                "issueNumber": active_lane.get("number"),
                "sessionName": session_action.get("sessionName"),
            }
        if health == "stale-lane" and not operator_attention_reasons and internal_review_repair_handoff.get("shouldDispatch"):
            return {
                "type": "dispatch_codex_turn",
                "mode": "internal_review_repair_handoff",
                "reason": "internal-review-findings-need-repair",
                "issueNumber": active_lane.get("number"),
                "sessionName": session_action.get("sessionName"),
                "headSha": local_head_sha,
            }
        if operator_attention_reasons:
            return {
                "type": "noop",
                "reason": "operator-attention-required",
                "issueNumber": active_lane.get("number"),
                "sessionName": session_action.get("sessionName"),
                "headSha": pr_head_sha or local_head_sha,
                "details": operator_attention_reasons,
            }
        return {"type": "noop", "reason": f"workflow-not-healthy:{health}"}

    if operator_attention_reasons:
        return {
            "type": "noop",
            "reason": "operator-attention-required",
            "issueNumber": active_lane.get("number"),
            "sessionName": session_action.get("sessionName"),
            "headSha": pr_head_sha or local_head_sha,
            "details": operator_attention_reasons,
        }

    if int(budget_state.get("noProgressTicks") or 0) >= no_progress_tick_budget and workflow_state in {"implementing_local", "implementing"} and session_action.get("action") in {"continue-session", "poke-session", "restart-session"}:
        return {
            "type": "dispatch_codex_turn",
            "mode": "implementation",
            "reason": "no-progress-budget-reached",
            "issueNumber": active_lane.get("number"),
            "sessionName": session_action.get("sessionName"),
            "headSha": local_head_sha,
        }

    if int(failure_state.get("retryCount") or 0) >= failure_retry_budget and workflow_state in {"implementing_local", "implementing", "pre_publish_review_findings", "findings_open", "rework_required"} and session_action.get("action") in {"continue-session", "poke-session", "restart-session"}:
        return {
            "type": "dispatch_codex_turn",
            "mode": "implementation" if not open_pr else "postpublish_repair",
            "reason": "failure-retry-budget-reached",
            "issueNumber": active_lane.get("number"),
            "sessionName": session_action.get("sessionName"),
            "headSha": pr_head_sha or local_head_sha,
        }

    if (
        open_pr
        and pr_head_sha
        and local_head_sha
        and local_head_sha != pr_head_sha
        and has_local_candidate(local_head_sha, implementation.get("commitsAhead"))
    ):
        return {
            "type": "push_pr_update",
            "reason": "local-repair-head-ahead-of-published-pr",
            "issueNumber": active_lane.get("number"),
            "headSha": local_head_sha,
            "prNumber": (open_pr or {}).get("number"),
        }

    if pre_publish_review_preflight.get("shouldRun"):
        return {
            "type": "run_internal_review",
            "reason": "prepublish-review-required",
            "headSha": pre_publish_review_preflight.get("currentHeadSha"),
            "issueNumber": active_lane.get("number"),
            "sessionName": session_action.get("sessionName"),
        }

    if internal_review_repair_handoff.get("shouldDispatch"):
        return {
            "type": "dispatch_codex_turn",
            "mode": "internal_review_repair_handoff",
            "reason": "internal-review-findings-need-repair",
            "issueNumber": active_lane.get("number"),
            "sessionName": session_action.get("sessionName"),
            "headSha": local_head_sha,
        }

    if should_dispatch_external_review_repair_handoff(
        lane_state=lane_state,
        session_action=session_action,
        external_review=external_review,
        repair_brief=repair_brief,
        workflow_state=workflow_state,
        current_head_sha=current_postpublish_head,
        has_open_pr=bool(open_pr),
    ).get("shouldDispatch"):
        return {
            "type": "dispatch_codex_turn",
            "mode": "external_review_repair_handoff",
            "reason": "external-review-findings-need-repair",
            "issueNumber": active_lane.get("number"),
            "sessionName": session_action.get("sessionName"),
            "headSha": current_postpublish_head,
        }

    if (
        open_pr
        and workflow_state in {"findings_open", "rework_required", "under_review"}
        and session_action.get("action") == "restart-session"
        and session_action.get("sessionName")
        and external_review.get("reviewScope") == "postpublish-pr"
        and external_review.get("status") == "completed"
        and external_review.get("verdict") in {"PASS_WITH_FINDINGS", "REWORK"}
        and current_postpublish_head
        and external_review.get("reviewedHeadSha") == current_postpublish_head
        and repair_brief.get("forHeadSha") == current_postpublish_head
        and (repair_brief.get("mustFix") or repair_brief.get("shouldFix"))
    ):
        return {
            "type": "dispatch_codex_turn",
            "mode": "postpublish_repair",
            "reason": "external-review-findings-need-repair",
            "issueNumber": active_lane.get("number"),
            "sessionName": session_action.get("sessionName"),
            "headSha": current_postpublish_head,
        }

    if workflow_state in {"implementing_local", "implementing"} and session_action.get("action") in {"continue-session", "poke-session", "restart-session"}:
        if session_action.get("action") == "continue-session" and active_session_health.get("healthy"):
            return {
                "type": "noop",
                "reason": "fresh-session-still-working",
                "issueNumber": active_lane.get("number"),
                "headSha": local_head_sha,
                "sessionName": session_action.get("sessionName"),
            }
        return {
            "type": "dispatch_codex_turn",
            "mode": "implementation",
            "reason": "implementation-in-progress",
            "issueNumber": active_lane.get("number"),
            "sessionName": session_action.get("sessionName"),
            "headSha": local_head_sha,
        }

    if workflow_state == "ready_to_publish":
        return {
            "type": "publish_ready_pr",
            "reason": "local-head-cleared-for-publish",
            "issueNumber": active_lane.get("number"),
            "headSha": local_head_sha,
        }

    return fallback
