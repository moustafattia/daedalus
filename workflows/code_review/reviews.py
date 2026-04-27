from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Callable


"""YoYoPod Core review-policy helpers.

This slice extracts repair-handoff gating rules from the legacy wrapper so the
adapter workflow can own the remaining review-driven nextAction branches.
"""

SEVERITY_BADGE_RE = re.compile(r"!\[P(\d+) Badge", re.IGNORECASE)


def _iso_to_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        return None


def _json_object_or_none(text: str) -> dict[str, Any] | None:
    candidate = (text or "").strip()
    if not candidate:
        return None
    try:
        value = json.loads(candidate)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = (text or "").strip()
    if not candidate:
        raise ValueError("no json object in review agent output")
    try:
        value = json.loads(candidate)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no json object in review agent output")
    value = json.loads(candidate[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("review agent output json is not an object")
    return value


def inter_review_agent_target_head(review: dict[str, Any] | None) -> str | None:
    review = review or {}
    return review.get("targetHeadSha") or review.get("requestedHeadSha")


def inter_review_agent_started_epoch(
    review: dict[str, Any] | None,
    *,
    iso_to_epoch_fn: Callable[[str | None], int | None] = _iso_to_epoch,
) -> int | None:
    review = review or {}
    return iso_to_epoch_fn(review.get("startedAt") or review.get("requestedAt"))


def inter_review_agent_is_running_on_head(
    review: dict[str, Any] | None,
    head_sha: str | None,
    *,
    target_head_fn: Callable[[dict[str, Any] | None], str | None] = inter_review_agent_target_head,
) -> bool:
    if not head_sha:
        return False
    review = review or {}
    return (
        review.get("reviewScope") == "local-prepublish"
        and review.get("status") == "running"
        and target_head_fn(review) == head_sha
    )


class InterReviewAgentError(RuntimeError):
    """Raised when the internal review agent CLI cannot produce a parseable review.

    ``failure_class`` is a stable machine-readable code derived from the CLI's
    stderr/stdout so downstream policy can reason about transient vs terminal
    failures without string-matching inside the wrapper.
    """

    def __init__(self, message: str, *, failure_class: str = "review_subprocess_failed"):
        super().__init__(message)
        self.failure_class = failure_class


def classify_inter_review_agent_failure_text(text: str) -> str:
    lowered = (text or "").lower()
    if "error_max_turns" in lowered or "maximum number of turns" in lowered:
        return "max_turns_exhausted"
    if "invalid api key" in lowered or "authentication" in lowered or "oauth" in lowered or "unauthorized" in lowered:
        return "auth_failed"
    if "permission" in lowered and ("denied" in lowered or "ask" in lowered):
        return "permission_failed"
    if "connection error" in lowered or "econnrefused" in lowered or "failedtoopensocket" in lowered or "timed out" in lowered:
        return "transport_failed"
    if "structured review payload" in lowered or "no json object" in lowered or "json" in lowered:
        return "invalid_structured_output"
    return "review_subprocess_failed"


def extract_inter_review_agent_payload(
    raw_output: str,
    *,
    json_object_or_none_fn: Callable[[str], dict[str, Any] | None] = _json_object_or_none,
    extract_json_object_fn: Callable[[str], dict[str, Any]] = _extract_json_object,
) -> dict[str, Any]:
    result_payload = json_object_or_none_fn(raw_output)
    if result_payload is not None:
        if isinstance(result_payload.get("structured_output"), dict):
            return result_payload["structured_output"]
        if isinstance(result_payload.get("result"), str):
            return extract_json_object_fn(result_payload["result"])
        if result_payload.get("type") == "result":
            raise ValueError("review agent result missing structured review payload")
    return extract_json_object_fn(raw_output)


def inter_review_agent_failure_message(
    exc: subprocess.CalledProcessError,
    *,
    json_object_or_none_fn: Callable[[str], dict[str, Any] | None] = _json_object_or_none,
) -> str:
    result_payload = json_object_or_none_fn(exc.stdout or "")
    parts: list[str] = []
    if result_payload:
        subtype = result_payload.get("subtype")
        if subtype:
            parts.append(f"Internal review agent CLI failed ({subtype})")
        else:
            parts.append(f"Internal review agent CLI failed with exit status {exc.returncode}")
        num_turns = result_payload.get("num_turns")
        if num_turns is not None:
            parts.append(f"turns={num_turns}")
        errors = [str(item).strip() for item in list(result_payload.get("errors") or []) if str(item).strip()]
        if errors:
            parts.extend(errors)
    else:
        parts.append(f"Internal review agent CLI failed with exit status {exc.returncode}")
    stderr_text = (exc.stderr or "").strip()
    stdout_text = (exc.stdout or "").strip()
    if stderr_text:
        parts.append(stderr_text)
    elif stdout_text and not result_payload:
        parts.append(stdout_text)
    return ": ".join(part for part in parts if part)


def inter_review_agent_failure_class(
    exc: subprocess.CalledProcessError,
    *,
    classify_failure_text_fn: Callable[[str], str] = classify_inter_review_agent_failure_text,
) -> str:
    parts = [exc.stdout or "", exc.stderr or ""]
    return classify_failure_text_fn("\n".join(part for part in parts if part))


def classify_lane_failure(
    *,
    implementation: dict[str, Any] | None,
    reviews: dict[str, Any] | None,
    preflight: dict[str, Any] | None,
) -> dict[str, Any]:
    implementation = implementation or {}
    reviews = reviews or {}
    preflight = preflight or {}
    codex_review = reviews.get("codexCloud") or {}
    if (
        codex_review.get("reviewScope") == "postpublish-pr"
        and codex_review.get("status") == "completed"
        and codex_review.get("verdict") == "PASS_CLEAN"
        and int(codex_review.get("openFindingCount") or 0) == 0
    ):
        return {"failureClass": None, "detail": None}
    session_action = implementation.get("sessionActionRecommendation") or {}
    reason = session_action.get("reason")
    session_failure_map = {
        "missing-session-meta": "session_missing_meta",
        "missing-session": "session_missing",
        "stale-open-session": "session_stale_open",
        "stale-session": "session_stale",
        "closed-session": "session_closed",
        "wrong-worktree": "session_wrong_worktree",
        "missing-last-used": "session_missing_last_used",
    }
    if reason in session_failure_map:
        return {"failureClass": session_failure_map[reason], "detail": reason}
    claude_review = reviews.get("claudeCode") or {}
    if claude_review.get("status") in {"failed", "timed_out"}:
        detail = claude_review.get("failureClass") or claude_review.get("status")
        return {"failureClass": f"claude_review_{claude_review.get('status')}", "detail": detail}
    if claude_review.get("required") and claude_review.get("verdict") in {"PASS_WITH_FINDINGS", "REWORK"}:
        return {"failureClass": "claude_findings_open", "detail": claude_review.get("verdict")}
    if codex_review.get("required") and int(codex_review.get("openFindingCount") or 0) > 0:
        return {"failureClass": "codex_cloud_findings_open", "detail": codex_review.get("verdict") or "open-findings"}
    claude_preflight = preflight.get("claudeReview") or {}
    reasons = list(claude_preflight.get("reasons") or [])
    if reasons:
        return {"failureClass": "claude_preflight_blocked", "detail": reasons[0]}
    return {"failureClass": None, "detail": None}


def extract_severity(body: str) -> str:
    match = SEVERITY_BADGE_RE.search(body or "")
    if not match:
        return "minor"
    level = int(match.group(1))
    if level <= 0:
        return "critical"
    if level <= 2:
        return "major"
    return "minor"


def extract_summary(body: str) -> str:
    if not body:
        return ""
    line = body.splitlines()[0].strip()
    line = re.sub(r"\*\*<sub><sub>.*?</sub></sub>\s*", "", line)
    line = line.replace("**", "").strip()
    return line


def checks_acceptable(pr: dict[str, Any] | None) -> bool:
    checks = ((pr or {}).get("checks") or {})
    status = str(checks.get("status") or "").strip().lower()
    return status in {"acceptable", "success", "passed", "pass", "green", "clean"}


def _review_bucket(review: dict[str, Any]) -> str:
    verdict = review.get("verdict")
    if verdict == "REWORK":
        return "blocking"
    if verdict == "PASS_WITH_FINDINGS":
        return "findings"
    if verdict == "PASS_CLEAN":
        return "clean"
    return "pending"


def review_bucket(review: dict[str, Any]) -> str:
    return _review_bucket(review)


def pr_ready_for_review(open_pr: dict[str, Any] | None) -> bool:
    return bool(open_pr) and not bool((open_pr or {}).get("isDraft"))


def has_local_candidate(local_head_sha: str | None, commits_ahead: int | None) -> bool:
    return bool(local_head_sha) and int(commits_ahead or 0) > 0


def current_inter_review_agent_matches_local_head(review: dict[str, Any] | None, local_head_sha: str | None) -> bool:
    if not local_head_sha:
        return False
    review = review or {}
    return review.get("reviewScope") == "local-prepublish" and review.get("reviewedHeadSha") == local_head_sha


def local_inter_review_agent_review_count(review: dict[str, Any] | None, lane_state: dict[str, Any] | None = None) -> int:
    state = lane_state or {}
    state_review = state.get("review") or {}
    count = int(state_review.get("localClaudeReviewCount") or 0)
    last_head = state_review.get("lastClaudeReviewedHeadSha")
    review = review or {}
    if review.get("reviewScope") == "local-prepublish" and review.get("status") == "completed":
        reviewed_head = review.get("reviewedHeadSha")
        if reviewed_head and reviewed_head != last_head:
            count += 1
    return count


def single_pass_local_claude_gate_satisfied(
    review: dict[str, Any] | None,
    local_head_sha: str | None,
    lane_state: dict[str, Any] | None = None,
    *,
    pass_with_findings_reviews: int,
) -> bool:
    if not local_head_sha:
        return False
    review = review or {}
    state = lane_state or {}
    state_review = state.get("review") or {}
    review_count = local_inter_review_agent_review_count(review, state)
    latest_reviewed_head = state_review.get("lastClaudeReviewedHeadSha")
    latest_verdict = state_review.get("lastClaudeVerdict")
    if review.get("reviewScope") == "local-prepublish" and review.get("status") == "completed":
        latest_reviewed_head = review.get("reviewedHeadSha") or latest_reviewed_head
        latest_verdict = review.get("verdict") or latest_verdict
    if not latest_reviewed_head or not latest_verdict:
        return False
    if latest_verdict == "PASS_CLEAN":
        return latest_reviewed_head == local_head_sha
    if latest_verdict == "REWORK":
        return False
    if latest_verdict == "PASS_WITH_FINDINGS":
        return latest_reviewed_head != local_head_sha and review_count >= pass_with_findings_reviews
    return False


def determine_review_loop_state(reviews: dict[str, dict[str, Any]], *, has_pr: bool) -> tuple[str, list[str], bool]:
    blockers = []
    pending_required = []
    has_blocking = False
    has_findings = False
    for name, review in reviews.items():
        if not review.get("required"):
            continue
        bucket = _review_bucket(review)
        if bucket == "blocking":
            has_blocking = True
            blockers.append(f"{name}-rework")
        elif bucket == "findings":
            has_findings = True
            blockers.append(f"{name}-open-findings")
        elif bucket == "pending":
            pending_required.append(name)
            if has_pr:
                blockers.append(f"{name}-pending")
    if has_blocking:
        return "rework_required", blockers, True
    if has_findings:
        return "findings_open", blockers, True
    if pending_required:
        return "awaiting_reviews", blockers, has_pr
    return "clean", [], False


def inter_review_agent_preflight(
    *,
    active_lane: dict[str, Any] | None,
    open_pr: dict[str, Any] | None,
    workflow_state: str | None,
    pr_ledger: dict[str, Any] | None,
    inter_review_agent_review: dict[str, Any],
    inter_review_agent_job: dict[str, Any] | None,
    local_head_sha: str | None,
    implementation_commits_ahead: int | None,
    single_pass_gate_satisfied: bool = False,
    pr_ready_for_review_fn: Callable[[dict[str, Any] | None], bool],
    has_local_candidate_fn: Callable[[str | None, int | None], bool],
    checks_acceptable_fn: Callable[[dict[str, Any] | None], bool],
    target_head_fn: Callable[[dict[str, Any]], str | None],
    started_epoch_fn: Callable[[dict[str, Any]], int | None],
    now_ms_fn: Callable[[], int],
    now_epoch_fn: Callable[[], int],
    timeout_seconds: int,
    request_cooldown_seconds: int,
) -> dict[str, Any]:
    now_ms = now_ms_fn()
    now_epoch = now_epoch_fn()
    reasons: list[str] = []
    publish_ready = pr_ready_for_review_fn(open_pr)
    checks_acceptable = checks_acceptable_fn(pr_ledger) if publish_ready else True
    target_head_sha = local_head_sha if not publish_ready else None
    review_scope = "local-prepublish" if not publish_ready else "postpublish-pr"
    if not active_lane:
        reasons.append("no-active-lane")
    if publish_ready:
        reasons.append("postpublish-claude-disabled")
    if not has_local_candidate_fn(local_head_sha, implementation_commits_ahead):
        reasons.append("no-local-head-candidate")
    if workflow_state not in {"implementing_local", "awaiting_claude_prepublish", "claude_prepublish_findings", "ready_to_publish", "implementing"}:
        reasons.append("workflow-not-awaiting-local-claude")
    if not checks_acceptable:
        reasons.append("checks-not-acceptable")
    if single_pass_gate_satisfied:
        reasons.append("single-pass-claude-already-satisfied")

    review_status = inter_review_agent_review.get("status")
    reviewed_head = inter_review_agent_review.get("reviewedHeadSha")
    requested_head = target_head_fn(inter_review_agent_review)
    requested_at = _iso_to_epoch(inter_review_agent_review.get("requestedAt"))
    if target_head_sha and review_status == "completed" and reviewed_head == target_head_sha and inter_review_agent_review.get("reviewScope") == review_scope:
        reasons.append("claude-review-already-current")
    if target_head_sha and review_status == "running" and requested_head == target_head_sha:
        started_epoch = started_epoch_fn(inter_review_agent_review)
        if started_epoch is not None and (now_epoch - started_epoch) >= timeout_seconds:
            reasons.append("claude-review-running-timed-out")
        else:
            reasons.append("claude-review-running-current-head")
            if requested_at is not None and (now_epoch - requested_at) < request_cooldown_seconds:
                reasons.append("claude-review-request-recent")

    should_run = not reasons
    next_run_at = (inter_review_agent_job or {}).get("nextRunAtMs")
    wake_suggested = bool(should_run and (next_run_at is None or int(next_run_at) - now_ms > 5 * 60 * 1000))
    return {
        "shouldRun": should_run,
        "wakeSuggested": wake_suggested,
        "reasons": reasons,
        "currentHeadSha": target_head_sha,
        "checksAcceptable": checks_acceptable,
        "workflowState": workflow_state,
        "runId": inter_review_agent_review.get("runId"),
        "reviewStatus": review_status,
        "terminalState": inter_review_agent_review.get("terminalState"),
        "failureClass": inter_review_agent_review.get("failureClass"),
        "reviewedHeadSha": reviewed_head,
        "targetHeadSha": requested_head,
        "requestedHeadSha": requested_head,
        "jobNextRunAtMs": next_run_at,
        "reviewScope": review_scope,
    }


def normalize_review(
    review: dict[str, Any] | None,
    *,
    required: bool = True,
    pending_summary: str,
    agent_name: str | None = None,
    agent_role: str | None = None,
) -> dict[str, Any]:
    review = dict(review or {})
    return {
        "required": required,
        "status": review.get("status", "pending"),
        "terminalState": review.get("terminalState"),
        "runId": review.get("runId"),
        "verdict": review.get("verdict"),
        "targetHeadSha": review.get("targetHeadSha") or review.get("requestedHeadSha"),
        "reviewedHeadSha": review.get("reviewedHeadSha"),
        "startedAt": review.get("startedAt") or review.get("requestedAt"),
        "heartbeatAt": review.get("heartbeatAt"),
        "completedAt": review.get("completedAt") or review.get("updatedAt"),
        "updatedAt": review.get("updatedAt"),
        "summary": review.get("summary", pending_summary),
        "blockingFindings": list(review.get("blockingFindings", [])),
        "majorConcerns": list(review.get("majorConcerns", [])),
        "minorSuggestions": list(review.get("minorSuggestions", [])),
        "openFindingCount": int(review.get("openFindingCount", 0) or 0),
        "allFindingsClosed": bool(review.get("allFindingsClosed", False)),
        "threads": list(review.get("threads", [])),
        "supersededOpenFindingCount": int(review.get("supersededOpenFindingCount", 0) or 0),
        "prBodySignal": review.get("prBodySignal"),
        "requiredNextAction": review.get("requiredNextAction"),
        "requestedAt": review.get("requestedAt"),
        "requestedHeadSha": review.get("requestedHeadSha"),
        "reviewScope": review.get("reviewScope"),
        "failureClass": review.get("failureClass"),
        "failureSummary": review.get("failureSummary"),
        "supersededByHeadSha": review.get("supersededByHeadSha"),
        "model": review.get("model"),
        "agentName": review.get("agentName") or agent_name,
        "agentRole": review.get("agentRole") or agent_role,
    }


def codex_cloud_placeholder(
    *,
    required: bool,
    status: str,
    summary: str,
    normalize_review_fn: Callable[..., dict[str, Any]] = normalize_review,
    agent_name: str,
    agent_role: str,
) -> dict[str, Any]:
    return normalize_review_fn(
        {
            "status": status,
            "verdict": None,
            "summary": summary,
            "openFindingCount": 0,
            "allFindingsClosed": False,
            "threads": [],
            "reviewScope": "postpublish-pr",
        },
        required=required,
        pending_summary=summary,
        agent_name=agent_name,
        agent_role=agent_role,
    )


def build_codex_cloud_thread(
    *,
    node: dict[str, Any],
    comment: dict[str, Any],
    severity: str,
    summary: str,
    pr_signal: dict[str, Any] | None,
    signal_epoch: int | None,
    comment_epoch: int | None,
) -> dict[str, Any]:
    status = "resolved" if node.get("isResolved") else "open"
    superseded_by_pr_signal = bool(
        status == "open"
        and (pr_signal or {}).get("state") == "clean"
        and signal_epoch is not None
        and comment_epoch is not None
        and signal_epoch > comment_epoch
    )
    return {
        "id": node.get("id"),
        "path": node.get("path"),
        "line": node.get("line"),
        "severity": severity,
        "status": status,
        "source": "codexCloud",
        "summary": summary,
        "url": comment.get("url"),
        "createdAt": comment.get("createdAt"),
        "isOutdated": bool(node.get("isOutdated")),
        "supersededByPrSignal": superseded_by_pr_signal,
    }


def summarize_codex_cloud_review(
    *,
    head_sha: str | None,
    latest_ts: str | None,
    threads: list[dict[str, Any]],
    pr_signal: dict[str, Any] | None,
    agent_name: str,
) -> dict[str, Any]:
    open_threads = [
        t for t in list(threads or [])
        if t.get("status") == "open" and not t.get("isOutdated") and not t.get("supersededByPrSignal")
    ]
    superseded_threads = [
        t for t in list(threads or [])
        if t.get("status") == "open" and not t.get("isOutdated") and t.get("supersededByPrSignal")
    ]
    blocking = [t for t in open_threads if t.get("severity") == "critical"]
    major = [t for t in open_threads if t.get("severity") == "major"]
    minor = [t for t in open_threads if t.get("severity") == "minor"]
    if open_threads:
        verdict = "REWORK" if blocking else "PASS_WITH_FINDINGS"
        summary = f"{len(open_threads)} unresolved {agent_name} review thread(s) still block the current PR head"
        all_closed = False
    elif (pr_signal or {}).get("state") == "pending":
        verdict = None
        summary = (
            f"{agent_name} is still reviewing the current PR head; "
            f"PR-body {(pr_signal or {}).get('content')} signal from {(pr_signal or {}).get('user')} is newer than the last thread state."
        )
        all_closed = False
    else:
        verdict = "PASS_CLEAN"
        if superseded_threads and pr_signal:
            summary = (
                f"No active {agent_name} findings block the current PR head; "
                f"{len(superseded_threads)} lingering open thread(s) were superseded by "
                f"the newer PR-body {pr_signal.get('content')} signal from {pr_signal.get('user')}."
            )
        else:
            summary = f"No unresolved {agent_name} review threads on current PR head."
        all_closed = True
    return {
        "status": "completed",
        "verdict": verdict,
        "reviewedHeadSha": head_sha,
        "updatedAt": latest_ts,
        "summary": summary,
        "blockingFindings": [t.get("summary") for t in blocking],
        "majorConcerns": [t.get("summary") for t in major],
        "minorSuggestions": [t.get("summary") for t in minor],
        "openFindingCount": len(open_threads),
        "allFindingsClosed": all_closed,
        "threads": list(threads or []),
        "supersededOpenFindingCount": len(superseded_threads),
        "prBodySignal": pr_signal,
    }


def synthesize_repair_brief(
    reviews: dict[str, dict[str, Any]],
    *,
    head_sha: str | None,
    now_iso: str,
) -> dict[str, Any] | None:
    must_fix = []
    should_fix = []
    for source, review in (reviews or {}).items():
        if not review.get("required"):
            continue
        if source == "codexCloud":
            for thread in review.get("threads", []):
                if thread.get("status") != "open" or thread.get("isOutdated"):
                    continue
                item = {
                    "id": f"codexCloud:{thread['id']}",
                    "source": "codexCloud",
                    "severity": thread["severity"],
                    "summary": thread["summary"],
                    "path": thread.get("path"),
                    "line": thread.get("line"),
                    "status": "open",
                    "url": thread.get("url"),
                }
                (must_fix if thread["severity"] in {"critical", "major"} else should_fix).append(item)
        else:
            for idx, finding in enumerate(review.get("blockingFindings", []), start=1):
                must_fix.append(
                    {
                        "id": f"{source}:blocking:{idx}",
                        "source": source,
                        "severity": "critical",
                        "summary": finding,
                        "status": "open",
                    }
                )
            for idx, finding in enumerate(review.get("majorConcerns", []), start=1):
                must_fix.append(
                    {
                        "id": f"{source}:major:{idx}",
                        "source": source,
                        "severity": "major",
                        "summary": finding,
                        "status": "open",
                    }
                )
            for idx, finding in enumerate(review.get("minorSuggestions", []), start=1):
                should_fix.append(
                    {
                        "id": f"{source}:minor:{idx}",
                        "source": source,
                        "severity": "minor",
                        "summary": finding,
                        "status": "open",
                    }
                )
    if not must_fix and not should_fix:
        return None
    rerun = [name for name, review in (reviews or {}).items() if review.get("required")]
    return {
        "status": "open",
        "forHeadSha": head_sha,
        "openedAt": now_iso,
        "updatedAt": now_iso,
        "mustFix": must_fix,
        "shouldFix": should_fix,
        "deferred": [],
        "resolved": [],
        "rerunRequiredReviewers": rerun,
        "closeCondition": "All mustFix items resolved or explicitly deferred by policy.",
    }


def mark_pr_ready_for_review(
    pr_number: int | None,
    *,
    run_fn: Callable[..., Any],
    cwd: Any,
    repo_slug: str,
) -> bool:
    if pr_number is None:
        return False
    try:
        run_fn(["gh", "pr", "ready", str(pr_number), "--repo", repo_slug], cwd=cwd)
        return True
    except Exception:
        return False


def resolve_review_thread(
    thread_id: str,
    *,
    run_json_fn: Callable[..., Any],
    cwd: Any,
) -> bool:
    try:
        result = run_json_fn(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                "query=mutation($threadId:ID!){ resolveReviewThread(input:{threadId:$threadId}) { thread { id isResolved } } }",
                "-f",
                f"threadId={thread_id}",
            ],
            cwd=cwd,
        )
    except Exception:
        return False
    return bool((((result or {}).get("data") or {}).get("resolveReviewThread") or {}).get("thread", {}).get("isResolved"))


def resolve_codex_superseded_threads(
    review: dict[str, Any],
    *,
    current_head_sha: str | None,
    resolve_review_thread_fn: Callable[[str], bool],
) -> list[str]:
    if review.get("verdict") != "PASS_CLEAN":
        return []
    if ((review.get("prBodySignal") or {}).get("state")) != "clean":
        return []
    if not current_head_sha:
        return []
    if review.get("reviewedHeadSha") != current_head_sha:
        return []
    resolved: list[str] = []
    for thread in review.get("threads", []):
        if thread.get("status") != "open":
            continue
        if thread.get("isOutdated"):
            continue
        if not thread.get("supersededByPrSignal"):
            continue
        thread_id = thread.get("id")
        if thread_id and resolve_review_thread_fn(str(thread_id)):
            resolved.append(str(thread_id))
    return resolved


def fetch_codex_pr_body_signal(
    pr_number: int | None,
    *,
    run_json_fn: Callable[..., Any],
    cwd: Any,
    codex_bot_logins: set[str],
    clean_reactions: set[str],
    pending_reactions: set[str],
    repo_slug: str,
) -> dict[str, Any] | None:
    if pr_number is None:
        return None
    try:
        reactions = run_json_fn(
            [
                "gh",
                "api",
                f"repos/{repo_slug}/issues/{pr_number}/reactions",
                "-H",
                "Accept: application/vnd.github+json",
            ],
            cwd=cwd,
        )
    except Exception:
        return None
    if not isinstance(reactions, list):
        return None
    matches = [
        reaction
        for reaction in reactions
        if isinstance(reaction, dict)
        and (reaction.get("user") or {}).get("login") in codex_bot_logins
        and reaction.get("content") in (clean_reactions | pending_reactions)
    ]
    if not matches:
        return None
    latest = max(matches, key=lambda reaction: reaction.get("created_at") or "")
    content = latest.get("content")
    state = "clean" if content in clean_reactions else "pending"
    return {
        "content": content,
        "state": state,
        "createdAt": latest.get("created_at"),
        "user": (latest.get("user") or {}).get("login"),
        "source": "pr-body-reaction",
    }


def fetch_codex_cloud_review(
    pr_number: int | None,
    *,
    current_head_sha: str | None,
    cached_review: dict[str, Any] | None,
    fetch_pr_body_signal_fn: Callable[[int | None], dict[str, Any] | None],
    run_json_fn: Callable[..., Any],
    cwd: Any,
    repo_slug: str,
    codex_bot_logins: set[str],
    cache_seconds: int,
    iso_to_epoch_fn: Callable[[str | None], int | None] = _iso_to_epoch,
    now_epoch_fn: Callable[[], float] = time.time,
    extract_severity_fn: Callable[[str], str] = lambda _body: "minor",
    extract_summary_fn: Callable[[str], str] = lambda body: body,
    build_thread_fn: Callable[..., dict[str, Any]] = build_codex_cloud_thread,
    summarize_review_fn: Callable[..., dict[str, Any]] = summarize_codex_cloud_review,
    agent_name: str = "External_Reviewer_Agent",
) -> dict[str, Any]:
    base = {
        "required": True,
        "status": "pending",
        "verdict": None,
        "reviewedHeadSha": None,
        "updatedAt": None,
        "summary": "Pending published PR head.",
        "blockingFindings": [],
        "majorConcerns": [],
        "minorSuggestions": [],
        "openFindingCount": 0,
        "allFindingsClosed": False,
        "threads": [],
    }
    if pr_number is None:
        return base
    cached_updated_at = iso_to_epoch_fn((cached_review or {}).get("updatedAt"))
    if (
        cached_review
        and cached_review.get("reviewedHeadSha") == current_head_sha
        and cached_updated_at is not None
        and (float(now_epoch_fn()) - cached_updated_at) <= cache_seconds
    ):
        return {**base, **cached_review, "required": True}
    pr_signal = fetch_pr_body_signal_fn(pr_number)
    signal_epoch = iso_to_epoch_fn((pr_signal or {}).get("createdAt"))
    owner, name = repo_slug.split("/", 1)
    data = run_json_fn(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            "query=query { repository(owner:\"%s\", name:\"%s\") { pullRequest(number: %d) { state headRefOid reviewThreads(first: 100) { nodes { id isResolved isOutdated path line comments(first: 20) { nodes { author { login } body url createdAt } } } } } } }"
            % (owner, name, pr_number),
        ],
        cwd=cwd,
    )
    pr = data["data"]["repository"]["pullRequest"]
    head_sha = pr.get("headRefOid")
    threads = []
    latest_ts = None
    for node in pr.get("reviewThreads", {}).get("nodes", []):
        comments = node.get("comments", {}).get("nodes", [])
        codex_comments = [c for c in comments if (c.get("author") or {}).get("login") in codex_bot_logins]
        if not codex_comments:
            continue
        comment = codex_comments[-1]
        thread = build_thread_fn(
            node=node,
            comment=comment,
            severity=extract_severity_fn(comment.get("body", "")),
            summary=extract_summary_fn(comment.get("body", "")),
            pr_signal=pr_signal,
            signal_epoch=signal_epoch,
            comment_epoch=iso_to_epoch_fn(comment.get("createdAt")),
        )
        threads.append(thread)
        ts = comment.get("createdAt")
        if ts and (latest_ts is None or ts > latest_ts):
            latest_ts = ts
    base.update(
        summarize_review_fn(
            head_sha=head_sha,
            latest_ts=latest_ts,
            threads=threads,
            pr_signal=pr_signal,
            agent_name=agent_name,
        )
    )
    return base


def build_inter_review_agent_running_review(
    previous_review: dict[str, Any] | None,
    *,
    run_id: str,
    head_sha: str | None,
    now_iso: str,
    model: str,
    pending_summary: str,
    agent_name: str,
    agent_role: str,
    normalize_review_fn: Callable[..., dict[str, Any]] = normalize_review,
) -> dict[str, Any]:
    previous = dict(previous_review or {})
    return normalize_review_fn(
        {
            **previous,
            "runId": run_id,
            "status": "running",
            "terminalState": None,
            "targetHeadSha": head_sha,
            "startedAt": now_iso,
            "heartbeatAt": now_iso,
            "requestedAt": now_iso,
            "requestedHeadSha": head_sha,
            "reviewScope": "local-prepublish",
            "failureClass": None,
            "failureSummary": None,
            "supersededByHeadSha": None,
            "summary": f"Running local unpublished branch review for head {head_sha}.",
            "model": model,
        },
        required=True,
        pending_summary=pending_summary,
        agent_name=agent_name,
        agent_role=agent_role,
    )


def build_inter_review_agent_failed_review(
    previous_review: dict[str, Any] | None,
    *,
    run_id: str,
    head_sha: str | None,
    requested_at: str,
    failed_at: str,
    failure_class: str,
    failure_summary: str,
    model: str,
    pending_summary: str,
    agent_name: str,
    agent_role: str,
    normalize_review_fn: Callable[..., dict[str, Any]] = normalize_review,
) -> dict[str, Any]:
    previous = dict(previous_review or {})
    return normalize_review_fn(
        {
            **previous,
            "runId": previous.get("runId") or run_id,
            "status": "failed",
            "terminalState": "failed",
            "verdict": None,
            "targetHeadSha": head_sha,
            "reviewedHeadSha": None,
            "updatedAt": failed_at,
            "completedAt": failed_at,
            "heartbeatAt": failed_at,
            "requestedAt": previous.get("requestedAt") or requested_at,
            "requestedHeadSha": previous.get("requestedHeadSha") or head_sha,
            "reviewScope": "local-prepublish",
            "failureClass": failure_class,
            "failureSummary": failure_summary,
            "summary": failure_summary,
            "model": model,
        },
        required=True,
        pending_summary=pending_summary,
        agent_name=agent_name,
        agent_role=agent_role,
    )


def build_inter_review_agent_completed_review(
    result: dict[str, Any],
    *,
    run_id: str,
    head_sha: str | None,
    started_at: str,
    completed_at: str,
    model: str,
    pending_summary: str,
    agent_name: str,
    agent_role: str,
    normalize_review_fn: Callable[..., dict[str, Any]] = normalize_review,
) -> dict[str, Any]:
    blocking = result.get("blockingFindings") or []
    major = result.get("majorConcerns") or []
    minor = result.get("minorSuggestions") or []
    return normalize_review_fn(
        {
            "runId": run_id,
            "status": "completed",
            "terminalState": "completed",
            "verdict": result.get("verdict"),
            "targetHeadSha": head_sha,
            "reviewedHeadSha": head_sha,
            "startedAt": started_at,
            "heartbeatAt": completed_at,
            "updatedAt": completed_at,
            "completedAt": completed_at,
            "summary": result.get("summary"),
            "blockingFindings": blocking,
            "majorConcerns": major,
            "minorSuggestions": minor,
            "openFindingCount": len(blocking) + len(major) + len(minor),
            "allFindingsClosed": not any([blocking, major, minor]),
            "requiredNextAction": result.get("requiredNextAction"),
            "requestedAt": started_at,
            "requestedHeadSha": head_sha,
            "reviewScope": "local-prepublish",
            "failureClass": None,
            "failureSummary": None,
            "supersededByHeadSha": None,
            "model": model,
        },
        required=True,
        pending_summary=pending_summary,
        agent_name=agent_name,
        agent_role=agent_role,
    )


def build_codex_cloud_repair_handoff_payload(
    *,
    session_action: dict[str, Any],
    issue: dict[str, Any] | None,
    codex_review: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    lane_memo_path: str | None,
    lane_state_path: str | None,
    now_iso: str,
) -> dict[str, Any]:
    review = codex_review or {}
    return {
        "action": "codex-cloud-repair-handoff",
        "sessionName": session_action.get("sessionName"),
        "issueNumber": (issue or {}).get("number"),
        "issueTitle": (issue or {}).get("title"),
        "headSha": review.get("reviewedHeadSha"),
        "reviewedAt": review.get("updatedAt"),
        "reviewScope": review.get("reviewScope"),
        "verdict": review.get("verdict"),
        "laneMemoPath": lane_memo_path,
        "laneStatePath": lane_state_path,
        "mustFixCount": len((repair_brief or {}).get("mustFix") or []),
        "shouldFixCount": len((repair_brief or {}).get("shouldFix") or []),
        "at": now_iso,
    }


def record_codex_cloud_repair_handoff(
    *,
    worktree: Any,
    payload: dict[str, Any],
    lane_state_path_fn: Callable[[Any], Any],
    load_optional_json_fn: Callable[[Any], dict[str, Any] | None],
    write_json_fn: Callable[[Any, dict[str, Any]], Any],
) -> dict[str, Any] | None:
    path = lane_state_path_fn(worktree)
    if path is None:
        return None
    state = load_optional_json_fn(path) or {"schemaVersion": 1}
    state.setdefault("sessionControl", {})["lastCodexCloudRepairHandoff"] = payload
    write_json_fn(path, state)
    return state


def build_claude_repair_handoff_payload(
    *,
    session_action: dict[str, Any],
    issue: dict[str, Any] | None,
    claude_review: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    lane_memo_path: str | None,
    lane_state_path: str | None,
    now_iso: str,
) -> dict[str, Any]:
    review = claude_review or {}
    return {
        "action": "claude-repair-handoff",
        "sessionName": session_action.get("sessionName"),
        "issueNumber": (issue or {}).get("number"),
        "issueTitle": (issue or {}).get("title"),
        "headSha": review.get("reviewedHeadSha"),
        "reviewedAt": review.get("updatedAt"),
        "reviewScope": review.get("reviewScope"),
        "verdict": review.get("verdict"),
        "laneMemoPath": lane_memo_path,
        "laneStatePath": lane_state_path,
        "mustFixCount": len((repair_brief or {}).get("mustFix") or []),
        "shouldFixCount": len((repair_brief or {}).get("shouldFix") or []),
        "at": now_iso,
    }


def record_claude_repair_handoff(
    *,
    worktree: Any,
    payload: dict[str, Any],
    lane_state_path_fn: Callable[[Any], Any],
    load_optional_json_fn: Callable[[Any], dict[str, Any] | None],
    write_json_fn: Callable[[Any, dict[str, Any]], Any],
) -> dict[str, Any] | None:
    path = lane_state_path_fn(worktree)
    if path is None:
        return None
    state = load_optional_json_fn(path) or {"schemaVersion": 1}
    state.setdefault("sessionControl", {})["lastClaudeRepairHandoff"] = payload
    write_json_fn(path, state)
    return state


def inter_review_agent_pending_seed(*, model: str) -> dict[str, Any]:
    return {"model": model}


def inter_review_agent_superseded(
    review: dict[str, Any],
    *,
    superseded_by_head_sha: str | None,
    now_iso: str,
    target_head_fn: Callable[[dict[str, Any]], str | None],
) -> dict[str, Any]:
    target_head = target_head_fn(review)
    summary = (
        f"Superseded local pre-publish internal review for head {target_head}; "
        f"current local head is {superseded_by_head_sha or 'unknown'}."
    )
    return {
        **review,
        "status": "superseded",
        "terminalState": "superseded",
        "targetHeadSha": target_head,
        "updatedAt": now_iso,
        "completedAt": now_iso,
        "failureClass": None,
        "failureSummary": summary,
        "supersededByHeadSha": superseded_by_head_sha,
        "summary": summary,
    }


def inter_review_agent_timed_out(
    review: dict[str, Any],
    *,
    now_iso: str,
    target_head_fn: Callable[[dict[str, Any]], str | None],
    started_epoch_fn: Callable[[dict[str, Any]], int | None],
    now_epoch_fn: Callable[[], int],
) -> dict[str, Any]:
    target_head = target_head_fn(review)
    started_epoch = started_epoch_fn(review)
    age_seconds = None if started_epoch is None else max(0, int(now_epoch_fn()) - started_epoch)
    summary = (
        f"Local pre-publish internal review for head {target_head or 'unknown'} timed out"
        + (f" after {age_seconds}s." if age_seconds is not None else ".")
    )
    return {
        **review,
        "status": "timed_out",
        "terminalState": "timed_out",
        "targetHeadSha": target_head,
        "updatedAt": now_iso,
        "completedAt": now_iso,
        "failureClass": "review_timeout",
        "failureSummary": summary,
        "summary": summary,
    }


def normalize_local_inter_review_agent_seed(
    review: dict[str, Any] | None,
    *,
    local_head_sha: str | None,
    now_iso: str,
    model: str,
    timeout_seconds: int,
    target_head_fn: Callable[[dict[str, Any]], str | None],
    started_epoch_fn: Callable[[dict[str, Any]], int | None],
    now_epoch_fn: Callable[[], int],
    current_head_match_fn: Callable[[dict[str, Any] | None, str | None], bool],
) -> dict[str, Any]:
    review = dict(review or {})
    if not review:
        return inter_review_agent_pending_seed(model=model)
    if review.get("reviewScope") != "local-prepublish":
        return inter_review_agent_pending_seed(model=model)
    if review.get("status") == "running":
        started_epoch = started_epoch_fn(review)
        if started_epoch is not None and (int(now_epoch_fn()) - started_epoch) >= timeout_seconds:
            return inter_review_agent_timed_out(
                review,
                now_iso=now_iso,
                target_head_fn=target_head_fn,
                started_epoch_fn=started_epoch_fn,
                now_epoch_fn=now_epoch_fn,
            )
        target_head = target_head_fn(review)
        if local_head_sha and target_head and target_head != local_head_sha:
            return inter_review_agent_superseded(
                review,
                superseded_by_head_sha=local_head_sha,
                now_iso=now_iso,
                target_head_fn=target_head_fn,
            )
        return {**review, "model": review.get("model") or model}
    if current_head_match_fn(review, local_head_sha):
        return {**review, "model": review.get("model") or model}
    if review.get("status") in {"failed", "timed_out", "superseded"}:
        return {**review, "model": review.get("model") or model}
    return inter_review_agent_pending_seed(model=model)


def should_dispatch_claude_repair_handoff(
    *,
    lane_state: dict[str, Any] | None,
    session_action: dict[str, Any],
    claude_review: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    workflow_state: str | None,
    current_head_sha: str | None,
    has_open_pr: bool,
) -> dict[str, Any]:
    review = claude_review or {}
    if has_open_pr:
        return {"shouldDispatch": False, "reason": "published-pr-phase"}
    if workflow_state != "claude_prepublish_findings":
        return {"shouldDispatch": False, "reason": "workflow-not-awaiting-local-repair"}
    if session_action.get("action") not in {"continue-session", "poke-session"}:
        return {"shouldDispatch": False, "reason": "session-not-routable"}
    if not session_action.get("sessionName"):
        return {"shouldDispatch": False, "reason": "missing-session-name"}
    if review.get("reviewScope") != "local-prepublish":
        return {"shouldDispatch": False, "reason": "wrong-review-scope"}
    if review.get("status") != "completed":
        return {"shouldDispatch": False, "reason": "claude-review-not-complete"}
    if review.get("verdict") not in {"PASS_WITH_FINDINGS", "REWORK"}:
        return {"shouldDispatch": False, "reason": "claude-review-not-actionable"}
    if not current_head_sha or review.get("reviewedHeadSha") != current_head_sha:
        return {"shouldDispatch": False, "reason": "review-head-mismatch"}
    if (repair_brief or {}).get("forHeadSha") != current_head_sha:
        return {"shouldDispatch": False, "reason": "repair-brief-head-mismatch"}
    if not ((repair_brief or {}).get("mustFix") or (repair_brief or {}).get("shouldFix")):
        return {"shouldDispatch": False, "reason": "repair-brief-empty"}
    last_handoff = ((lane_state or {}).get("sessionControl") or {}).get("lastClaudeRepairHandoff") or {}
    if (
        last_handoff.get("sessionName") == session_action.get("sessionName")
        and last_handoff.get("headSha") == current_head_sha
        and last_handoff.get("reviewedAt") == review.get("updatedAt")
    ):
        return {"shouldDispatch": False, "reason": "repair-handoff-already-sent-for-review"}
    return {"shouldDispatch": True, "reason": None}


def should_dispatch_codex_cloud_repair_handoff(
    *,
    lane_state: dict[str, Any] | None,
    session_action: dict[str, Any],
    codex_review: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    workflow_state: str | None,
    current_head_sha: str | None,
    has_open_pr: bool,
) -> dict[str, Any]:
    review = codex_review or {}
    if not has_open_pr:
        return {"shouldDispatch": False, "reason": "no-published-pr"}
    if workflow_state not in {"findings_open", "rework_required", "under_review"}:
        return {"shouldDispatch": False, "reason": "workflow-not-awaiting-postpublish-repair"}
    if session_action.get("action") not in {"continue-session", "poke-session"}:
        return {"shouldDispatch": False, "reason": "session-not-routable"}
    if not session_action.get("sessionName"):
        return {"shouldDispatch": False, "reason": "missing-session-name"}
    if review.get("reviewScope") != "postpublish-pr":
        return {"shouldDispatch": False, "reason": "wrong-review-scope"}
    if review.get("status") != "completed":
        return {"shouldDispatch": False, "reason": "codex-review-not-complete"}
    if review.get("verdict") not in {"PASS_WITH_FINDINGS", "REWORK"}:
        return {"shouldDispatch": False, "reason": "codex-review-not-actionable"}
    if not current_head_sha or review.get("reviewedHeadSha") != current_head_sha:
        return {"shouldDispatch": False, "reason": "review-head-mismatch"}
    if (repair_brief or {}).get("forHeadSha") != current_head_sha:
        return {"shouldDispatch": False, "reason": "repair-brief-head-mismatch"}
    if not ((repair_brief or {}).get("mustFix") or (repair_brief or {}).get("shouldFix")):
        return {"shouldDispatch": False, "reason": "repair-brief-empty"}
    last_handoff = ((lane_state or {}).get("sessionControl") or {}).get("lastCodexCloudRepairHandoff") or {}
    if (
        last_handoff.get("sessionName") == session_action.get("sessionName")
        and last_handoff.get("headSha") == current_head_sha
        and last_handoff.get("reviewedAt") == review.get("updatedAt")
    ):
        return {"shouldDispatch": False, "reason": "repair-handoff-already-sent-for-review"}
    return {"shouldDispatch": True, "reason": None}


def _default_lane_state_path(worktree):
    if worktree is None:
        return None
    from pathlib import Path as _Path

    return _Path(worktree) / ".lane-state.json"


def _default_load_optional_json(path):
    if path is None or not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _default_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def maybe_dispatch_repair_handoff(
    *,
    status: dict[str, Any],
    ledger: dict[str, Any],
    now_iso: str,
    codex_model: str | None,
    run_acpx_prompt_fn: Callable[..., Any],
    audit_fn: Callable[..., Any],
    lane_state_override: dict[str, Any] | None = None,
    lane_state_path_fn: Callable[[Any], Any] | None = None,
    load_optional_json_fn: Callable[[Any], dict[str, Any] | None] | None = None,
    write_json_fn: Callable[[Any, dict[str, Any]], Any] | None = None,
    internal_reviewer_agent_name: str = "Internal_Reviewer_Agent",
    external_reviewer_agent_name: str = "External_Reviewer_Agent",
) -> tuple[dict[str, Any], bool]:
    """Adapter-owned implementation of the wrapper's ``_maybe_dispatch_repair_handoff``.

    Callers inject the side-effectful primitives (``run_acpx_prompt_fn`` to poke
    the actor session; ``audit_fn`` for audit trail) and optionally custom
    lane-state path / JSON I/O helpers. The default helpers write a
    ``.lane-state.json`` file adjacent to the worktree using stdlib primitives,
    matching the wrapper's historical behaviour.
    """
    from pathlib import Path as _Path

    from workflows.code_review.prompts import (
        render_claude_repair_handoff_prompt,
        render_codex_cloud_repair_handoff_prompt,
    )

    lane_state_path_fn = lane_state_path_fn or _default_lane_state_path
    load_optional_json_fn = load_optional_json_fn or _default_load_optional_json
    write_json_fn = write_json_fn or _default_write_json

    issue = status.get("activeLane")
    if not issue:
        return {"dispatched": False, "reason": "no-active-lane"}, False
    impl = status.get("implementation") or {}
    worktree = _Path(impl["worktree"]) if impl.get("worktree") else None
    if worktree is None:
        return {"dispatched": False, "reason": "missing-worktree"}, False
    reviews = status.get("reviews") or {}
    open_pr = status.get("openPr") or {}
    session_action = impl.get("sessionActionRecommendation") or {}
    lane_state = lane_state_override if lane_state_override is not None else (impl.get("laneState") or {})
    repair_brief = ledger.get("repairBrief")
    workflow_state = ((status.get("ledger") or {}).get("workflowState")) or ledger.get("workflowState")
    lane_memo_path_str = impl.get("laneMemoPath")
    lane_state_path_str = impl.get("laneStatePath")
    lane_memo_path_obj = _Path(lane_memo_path_str) if lane_memo_path_str else None
    lane_state_path_obj = _Path(lane_state_path_str) if lane_state_path_str else None

    claude_decision = should_dispatch_claude_repair_handoff(
        lane_state=lane_state,
        session_action=session_action,
        claude_review=reviews.get("claudeCode"),
        repair_brief=repair_brief,
        workflow_state=workflow_state,
        current_head_sha=impl.get("localHeadSha"),
        has_open_pr=bool(open_pr),
    )
    if claude_decision.get("shouldDispatch"):
        repair_payload = build_claude_repair_handoff_payload(
            session_action=session_action,
            issue=issue,
            claude_review=reviews.get("claudeCode"),
            repair_brief=repair_brief,
            lane_memo_path=lane_memo_path_str,
            lane_state_path=lane_state_path_str,
            now_iso=now_iso,
        )
        repair_prompt = render_claude_repair_handoff_prompt(
            issue=issue,
            claude_review=reviews.get("claudeCode"),
            repair_brief=repair_brief,
            lane_memo_path=lane_memo_path_obj,
            lane_state_path=lane_state_path_obj,
            internal_reviewer_agent_name=internal_reviewer_agent_name,
        )
        run_acpx_prompt_fn(
            worktree=worktree,
            session_name=repair_payload.get("sessionName"),
            prompt=repair_prompt,
            codex_model=codex_model,
        )
        record_claude_repair_handoff(
            worktree=worktree,
            payload=repair_payload,
            lane_state_path_fn=lane_state_path_fn,
            load_optional_json_fn=load_optional_json_fn,
            write_json_fn=write_json_fn,
        )
        ledger["claudeRepairHandoff"] = repair_payload
        audit_fn(
            "claude-repair-handoff-dispatched",
            "Sent Claude pre-publish repair brief back into the active Codex session",
            issueNumber=repair_payload.get("issueNumber"),
            sessionName=repair_payload.get("sessionName"),
            headSha=repair_payload.get("headSha"),
            verdict=repair_payload.get("verdict"),
            mustFixCount=repair_payload.get("mustFixCount"),
            shouldFixCount=repair_payload.get("shouldFixCount"),
        )
        return {
            "dispatched": True,
            "mode": "claude_repair_handoff",
            "issueNumber": repair_payload.get("issueNumber"),
            "sessionName": repair_payload.get("sessionName"),
            "headSha": repair_payload.get("headSha"),
            "payload": repair_payload,
        }, True

    codex_cloud_decision = should_dispatch_codex_cloud_repair_handoff(
        lane_state=lane_state,
        session_action=session_action,
        codex_review=reviews.get("codexCloud"),
        repair_brief=repair_brief,
        workflow_state=workflow_state,
        current_head_sha=open_pr.get("headRefOid") or impl.get("localHeadSha"),
        has_open_pr=bool(open_pr),
    )
    if codex_cloud_decision.get("shouldDispatch"):
        repair_payload = build_codex_cloud_repair_handoff_payload(
            session_action=session_action,
            issue=issue,
            codex_review=reviews.get("codexCloud"),
            repair_brief=repair_brief,
            lane_memo_path=lane_memo_path_str,
            lane_state_path=lane_state_path_str,
            now_iso=now_iso,
        )
        repair_prompt = render_codex_cloud_repair_handoff_prompt(
            issue=issue,
            codex_review=reviews.get("codexCloud"),
            repair_brief=repair_brief,
            lane_memo_path=lane_memo_path_obj,
            lane_state_path=lane_state_path_obj,
            pr_url=open_pr.get("url"),
            external_reviewer_agent_name=external_reviewer_agent_name,
        )
        run_acpx_prompt_fn(
            worktree=worktree,
            session_name=repair_payload.get("sessionName"),
            prompt=repair_prompt,
            codex_model=codex_model,
        )
        record_codex_cloud_repair_handoff(
            worktree=worktree,
            payload=repair_payload,
            lane_state_path_fn=lane_state_path_fn,
            load_optional_json_fn=load_optional_json_fn,
            write_json_fn=write_json_fn,
        )
        ledger["codexCloudRepairHandoff"] = repair_payload
        audit_fn(
            "codex-cloud-repair-handoff-dispatched",
            "Sent Codex Cloud repair brief back into the active Codex session",
            issueNumber=repair_payload.get("issueNumber"),
            sessionName=repair_payload.get("sessionName"),
            headSha=repair_payload.get("headSha"),
            verdict=repair_payload.get("verdict"),
            mustFixCount=repair_payload.get("mustFixCount"),
            shouldFixCount=repair_payload.get("shouldFixCount"),
        )
        return {
            "dispatched": True,
            "mode": "codex_cloud_repair_handoff",
            "issueNumber": repair_payload.get("issueNumber"),
            "sessionName": repair_payload.get("sessionName"),
            "headSha": repair_payload.get("headSha"),
            "payload": repair_payload,
        }, True

    return {
        "dispatched": False,
        "reason": "repair-handoff-not-needed",
        "claudeReason": claude_decision.get("reason"),
        "codexCloudReason": codex_cloud_decision.get("reason"),
    }, False


def build_reviews_block(
    *,
    existing_reviews: dict[str, Any],
    codex_cloud: dict[str, Any],
    publish_ready: bool,
    local_head_sha: str | None,
    local_candidate_exists: bool,
    inter_review_agent_model: str,
    internal_reviewer_agent_name: str,
    external_reviewer_agent_name: str,
    advisory_reviewer_agent_name: str,
    now_iso: str,
    claude_seed_fn: Callable[[dict[str, Any] | None, str | None, str], dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Assemble the ``reviews`` block exposed by ``build_status_raw``.

    Routes publish-ready vs pre-publish branches to the right normalized seeds,
    using adapter-owned review normalizers. ``claude_seed_fn`` is optional; if
    not provided, a minimal pre-publish seed with the given model is used.
    """
    existing_claude_review = existing_reviews.get("claudeCode")
    if publish_ready:
        return {
            "rockClaw": normalize_review(
                existing_reviews.get("rockClaw"),
                required=False,
                pending_summary="Retired reviewer; kept only for historical context.",
                agent_name=advisory_reviewer_agent_name,
                agent_role="advisory_reviewer_agent",
            ),
            "claudeCode": normalize_review(
                {**(existing_claude_review or {}), "model": (existing_claude_review or {}).get("model") or inter_review_agent_model},
                required=False,
                pending_summary="Claude pre-publish gate already completed before publication.",
                agent_name=internal_reviewer_agent_name,
                agent_role="internal_reviewer_agent",
            ),
            "codexCloud": {
                **codex_cloud,
                "required": True,
                "reviewScope": "postpublish-pr",
                "agentName": codex_cloud.get("agentName") or external_reviewer_agent_name,
                "agentRole": codex_cloud.get("agentRole") or "external_reviewer_agent",
            },
        }
    if claude_seed_fn is not None:
        claude_seed = claude_seed_fn(existing_claude_review, local_head_sha, now_iso)
    else:
        claude_seed = inter_review_agent_pending_seed(model=inter_review_agent_model)
    return {
        "rockClaw": normalize_review(
            existing_reviews.get("rockClaw"),
            required=False,
            pending_summary="Retired reviewer; kept only for historical context.",
            agent_name=advisory_reviewer_agent_name,
            agent_role="advisory_reviewer_agent",
        ),
        "claudeCode": normalize_review(
            claude_seed,
            required=local_candidate_exists,
            pending_summary="Pending local unpublished branch review before publication.",
            agent_name=internal_reviewer_agent_name,
            agent_role="internal_reviewer_agent",
        ),
        "codexCloud": {
            **codex_cloud,
            "agentName": codex_cloud.get("agentName") or external_reviewer_agent_name,
            "agentRole": codex_cloud.get("agentRole") or "external_reviewer_agent",
        },
    }


def audit_inter_review_agent_transition(
    *,
    previous_review: dict[str, Any] | None,
    current_review: dict[str, Any] | None,
    audit_fn: Callable[..., Any],
    internal_reviewer_agent_name: str,
    target_head_fn: Callable[[dict[str, Any] | None], str | None] = inter_review_agent_target_head,
) -> None:
    """Emit audit events when a review transitions between meaningful states.

    Pure audit logic ported from the wrapper's
    ``_audit_inter_review_agent_transition``. The caller injects ``audit_fn``
    so the adapter can stay free of workflow-wrapper globals.
    """
    previous_review = previous_review or {}
    current_review = current_review or {}

    requested_head = target_head_fn(current_review)
    requested_at = current_review.get("requestedAt")
    if requested_head and requested_at and (
        requested_head != target_head_fn(previous_review)
        or requested_at != previous_review.get("requestedAt")
        or current_review.get("reviewScope") != previous_review.get("reviewScope")
    ):
        audit_fn(
            "claude-review-requested",
            f"{internal_reviewer_agent_name} review requested for head {requested_head}",
            headSha=requested_head,
            status=current_review.get("status") or "pending",
            reviewScope=current_review.get("reviewScope"),
            runId=current_review.get("runId"),
        )

    reviewed_head = current_review.get("reviewedHeadSha")
    if current_review.get("status") == "completed" and reviewed_head and (
        previous_review.get("status") != "completed"
        or reviewed_head != previous_review.get("reviewedHeadSha")
        or current_review.get("updatedAt") != previous_review.get("updatedAt")
        or current_review.get("verdict") != previous_review.get("verdict")
    ):
        audit_fn(
            "claude-review-completed",
            f"{internal_reviewer_agent_name} {current_review.get('verdict') or 'completed'} for head {reviewed_head}",
            headSha=reviewed_head,
            verdict=current_review.get("verdict"),
            openFindingCount=current_review.get("openFindingCount", 0),
            reviewScope=current_review.get("reviewScope"),
            runId=current_review.get("runId"),
        )
    terminal_status = current_review.get("status")
    if terminal_status in {"failed", "timed_out", "superseded"} and (
        previous_review.get("status") != terminal_status
        or previous_review.get("updatedAt") != current_review.get("updatedAt")
        or target_head_fn(previous_review) != target_head_fn(current_review)
    ):
        summary = current_review.get("failureSummary") or current_review.get("summary") or f"{terminal_status} local internal review"
        audit_fn(
            f"claude-review-{terminal_status}",
            summary,
            headSha=target_head_fn(current_review),
            status=terminal_status,
            reviewScope=current_review.get("reviewScope"),
            runId=current_review.get("runId"),
            failureClass=current_review.get("failureClass"),
            supersededByHeadSha=current_review.get("supersededByHeadSha"),
        )


def run_inter_review_agent_review(
    *,
    issue: dict[str, Any],
    worktree: Any,
    lane_memo_path: Any,
    lane_state_path: Any,
    head_sha: str,
    run_fn: Callable[..., Any],
    inter_review_agent_model: str,
    inter_review_agent_max_turns: int,
    error_cls: type = subprocess.CalledProcessError,
) -> dict[str, Any]:
    """Run the Claude inter-review agent CLI and return its parsed verdict.

    Callers inject the subprocess ``run_fn`` (wrapper's ``_run``), the
    CalledProcessError class raised by that runner, and the model/max-turns
    tunables. Any unparseable output is surfaced as :class:`InterReviewAgentError`
    with a stable :attr:`failure_class`.
    """
    from workflows.code_review.prompts import render_inter_review_agent_prompt

    prompt = render_inter_review_agent_prompt(
        issue=issue,
        worktree=worktree,
        lane_memo_path=lane_memo_path,
        lane_state_path=lane_state_path,
        head_sha=head_sha,
    )
    review_schema = json.dumps({
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["PASS_CLEAN", "PASS_WITH_FINDINGS", "REWORK"]},
            "summary": {"type": "string"},
            "blockingFindings": {"type": "array", "items": {"type": "string"}},
            "majorConcerns": {"type": "array", "items": {"type": "string"}},
            "minorSuggestions": {"type": "array", "items": {"type": "string"}},
            "requiredNextAction": {"type": ["string", "null"]},
        },
        "required": ["verdict", "summary", "blockingFindings", "majorConcerns", "minorSuggestions", "requiredNextAction"],
        "additionalProperties": False,
    }, separators=(",", ":"))
    command = [
        'claude',
        '--model',
        inter_review_agent_model,
        '--permission-mode',
        'bypassPermissions',
        '--output-format',
        'json',
        '--json-schema',
        review_schema,
        '--max-turns',
        str(inter_review_agent_max_turns),
        '--print',
        prompt,
    ]
    try:
        completed = run_fn(command, cwd=worktree)
        raw_output = (getattr(completed, "stdout", "") or "").strip()
    except error_cls as exc:
        raw_output = (getattr(exc, "stdout", "") or "").strip()
        if raw_output:
            try:
                payload = extract_inter_review_agent_payload(raw_output)
            except Exception:
                raise InterReviewAgentError(
                    inter_review_agent_failure_message(exc),
                    failure_class=inter_review_agent_failure_class(exc),
                ) from exc
            else:
                return {
                    'verdict': payload.get('verdict'),
                    'summary': payload.get('summary'),
                    'blockingFindings': list(payload.get('blockingFindings') or []),
                    'majorConcerns': list(payload.get('majorConcerns') or []),
                    'minorSuggestions': list(payload.get('minorSuggestions') or []),
                    'requiredNextAction': payload.get('requiredNextAction'),
                }
        raise InterReviewAgentError(
            inter_review_agent_failure_message(exc),
            failure_class=inter_review_agent_failure_class(exc),
        ) from exc
    try:
        payload = extract_inter_review_agent_payload(raw_output)
    except Exception as exc:
        raise InterReviewAgentError(
            f"Internal review agent CLI returned invalid structured output: {str(exc).strip() or 'unknown parse error'}",
            failure_class="invalid_structured_output",
        ) from exc
    return {
        'verdict': payload.get('verdict'),
        'summary': payload.get('summary'),
        'blockingFindings': list(payload.get('blockingFindings') or []),
        'majorConcerns': list(payload.get('majorConcerns') or []),
        'minorSuggestions': list(payload.get('minorSuggestions') or []),
        'requiredNextAction': payload.get('requiredNextAction'),
    }
