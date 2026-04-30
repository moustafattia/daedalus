from __future__ import annotations

import calendar
import importlib.util
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from workflows.change_delivery.health import compute_health, compute_stale_lane_reasons
from workflows.change_delivery.migrations import get_lane_state_review_field, get_ledger_field, get_review
from workflows.change_delivery.paths import (
    lane_memo_path,
    lane_state_path,
    tick_dispatch_history_dir,
    tick_dispatch_state_path,
)
from workflows.change_delivery.prompts import render_lane_memo
from workflows.change_delivery.reviews import (
    classify_lane_failure,
    current_inter_review_agent_matches_local_head,
    has_local_candidate,
    inter_review_agent_target_head,
    local_inter_review_agent_review_count,
    single_pass_local_claude_gate_satisfied,
)
from workflows.change_delivery.sessions import (
    decide_session_action,
    expected_lane_branch,
    expected_lane_worktree,
    implementation_lane_matches,
    lane_acpx_session_name,
)
from workflows.change_delivery.workflow import derive_next_action


"""Change-delivery workflow read-model and status helpers.

Current slice behavior is intentionally conservative: the adapter establishes the
boundary and delegates status construction to the legacy wrapper module. Later
slices will move the actual read-model logic here module-by-module.
"""


LANE_COUNTER_INCREMENT_MIN_SECONDS = 240


def _iso_to_epoch(value: str | None) -> int | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return int(calendar.timegm(time.strptime(value, fmt)))
        except Exception:
            continue
    return None


def _load_optional_json_file(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pid_is_running(pid: Any) -> bool:
    try:
        numeric_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if numeric_pid <= 0:
        return False
    try:
        os.kill(numeric_pid, 0)
    except OSError:
        return False
    return True


def _load_tick_dispatch_state(workflow_root: Path) -> dict[str, Any] | None:
    state_path = tick_dispatch_state_path(workflow_root)
    if not state_path.exists():
        return None
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    active = _pid_is_running(payload.get("pid"))
    if not active:
        history_dir = tick_dispatch_history_dir(workflow_root)
        history_dir.mkdir(parents=True, exist_ok=True)
        started_at = payload.get("startedAt") or "unknown"
        command = payload.get("command") or "unknown"
        pid = payload.get("pid") or "unknown"
        archive_path = history_dir / f"{started_at}-{command}-pid{pid}.json"
        archived_payload = {
            **payload,
            "active": False,
            "statePath": str(state_path),
            "archivedAt": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
        }
        archive_path.write_text(json.dumps(archived_payload, indent=2), encoding="utf-8")
        state_path.unlink(missing_ok=True)
        return None
    return {
        **payload,
        "active": True,
        "statePath": str(state_path),
    }



def normalize_implementation_for_active_lane(
    implementation: dict[str, Any] | None,
    *,
    active_lane: dict[str, Any] | None,
    open_pr: dict[str, Any] | None,
    selected_codex_model: str | None,
    session_runtime: str = "acpx-codex",
    session_name: str | None = None,
    resume_session_id: str | None = None,
) -> dict[str, Any]:
    impl = dict(implementation or {})
    if not active_lane:
        return impl
    lane_number = active_lane.get("number")
    expected_worktree = expected_lane_worktree(lane_number)
    expected_branch = (open_pr or {}).get("headRefName") or expected_lane_branch(active_lane)
    expected_session_name = session_name or lane_acpx_session_name(lane_number)
    if implementation_lane_matches(impl, lane_number):
        if expected_worktree is not None:
            impl["worktree"] = str(expected_worktree)
        if expected_branch:
            impl["branch"] = expected_branch
        impl["sessionRuntime"] = session_runtime or impl.get("sessionRuntime") or "acpx-codex"
        impl["sessionName"] = expected_session_name
        impl["codexModel"] = selected_codex_model
        if resume_session_id:
            impl["resumeSessionId"] = resume_session_id
        return impl
    return {
        "session": None,
        "previousSession": impl.get("session") or impl.get("previousSession"),
        "worktree": str(expected_worktree) if expected_worktree is not None else None,
        "updatedAt": None,
        "branch": expected_branch,
        "status": "implementing" if not open_pr else impl.get("status"),
        "sessionRuntime": session_runtime or "acpx-codex",
        "sessionName": expected_session_name,
        "codexModel": selected_codex_model,
        "resumeSessionId": resume_session_id,
    }



def git_branch(path: Path | None, *, run: Callable[..., Any]) -> str | None:
    if path is None or not path.exists():
        return None
    try:
        value = str(getattr(run(["git", "branch", "--show-current"], cwd=path), "stdout", "") or "").strip()
        return value or None
    except Exception:
        return None



def git_commits_ahead(path: Path | None, *, run: Callable[..., Any]) -> int | None:
    if path is None or not path.exists():
        return None
    try:
        value = str(getattr(run(["git", "rev-list", "--count", "origin/main..HEAD"], cwd=path), "stdout", "") or "").strip()
        return int(value)
    except Exception:
        return None



def git_head_sha(path: Path | None, *, run: Callable[..., Any]) -> str | None:
    if path is None or not path.exists():
        return None
    try:
        value = str(getattr(run(["git", "rev-parse", "HEAD"], cwd=path), "stdout", "") or "").strip()
        return value or None
    except Exception:
        return None



def collect_worktree_repo_facts(
    worktree: Path | None,
    *,
    run: Callable[..., Any],
) -> dict[str, Any]:
    if worktree is None or not worktree.exists():
        return {"branch": None, "commitsAhead": None, "localHeadSha": None}

    def _stdout(command: list[str]) -> str | None:
        try:
            completed = run(command, cwd=worktree)
            value = str(getattr(completed, "stdout", "") or "").strip()
            return value or None
        except Exception:
            return None

    branch = git_branch(worktree, run=run)
    commits_ahead = git_commits_ahead(worktree, run=run)
    local_head_sha = git_head_sha(worktree, run=run)
    return {
        "branch": branch,
        "commitsAhead": commits_ahead,
        "localHeadSha": local_head_sha,
    }



def load_implementation_session_meta(
    implementation: dict[str, Any] | None,
    worktree: Path | None,
    *,
    show_acpx_session_fn: Callable[..., dict[str, Any] | None],
    load_latest_session_meta_fn: Callable[[str | None], dict[str, Any] | None],
) -> dict[str, Any] | None:
    impl = implementation or {}
    if impl.get("sessionRuntime") == "acpx-codex":
        return show_acpx_session_fn(worktree=worktree, session_name=impl.get("sessionName"))
    if impl.get("sessionRuntime") == "codex-app-server":
        thread_id = impl.get("threadId") or impl.get("resumeSessionId") or impl.get("session")
        return {
            "name": impl.get("sessionName"),
            "closed": False,
            "cwd": str(worktree) if worktree is not None else impl.get("worktree"),
            "last_used_at": impl.get("updatedAt"),
            "session_id": thread_id,
            "record_id": thread_id,
        }
    return load_latest_session_meta_fn(impl.get("session"))



def normalize_status(status: dict[str, Any], workflow_root: Path | None = None) -> dict[str, Any]:
    normalized = dict(status)
    implementation = dict(normalized.get("implementation") or {})
    lane_state = implementation.get("laneState") or {}
    open_pr = normalized.get("openPr")
    ledger = normalized.get("ledger") or {}
    reviews = normalized.get("reviews") or {}
    codex_cloud = get_review(reviews, "externalReview")

    implementation["sessionActionRecommendation"] = decide_session_action(
        active_session_health=implementation.get("activeSessionHealth"),
        implementation_status=(implementation.get("status") or ledger.get("workflowState")),
        has_open_pr=bool(open_pr),
    )
    normalized["implementation"] = implementation

    normalized["staleLaneReasons"] = compute_stale_lane_reasons(
        active_lane=normalized.get("activeLane"),
        open_pr=open_pr,
        implementation=implementation,
        lane_state=lane_state,
        publish_ready=implementation.get("publishStatus") == "ready_for_review",
        review_loop_state=normalized.get("derivedReviewLoopState") or ledger.get("reviewLoopState"),
        ledger_state=ledger.get("workflowState"),
        ledger_pr_head_sha=((ledger.get("pr") or {}).get("headSha")),
        codex_reviewed_head_sha=codex_cloud.get("reviewedHeadSha"),
        now_epoch=int(time.time()),
    )

    normalized["health"] = compute_health(
        engine_owner=normalized.get("engineOwner"),
        active_lane_error=normalized.get("activeLaneError"),
        missing_core_jobs=list(normalized.get("missingCoreJobs") or []),
        disabled_core_jobs=list(normalized.get("disabledCoreJobs") or []),
        stale_core_jobs=list(normalized.get("staleCoreJobs") or []),
        drift=list(normalized.get("drift") or []),
        stale_lane_reasons=list(normalized.get("staleLaneReasons") or []),
        broken_watchers=list(normalized.get("brokenIssueWatchers") or []),
    )
    if workflow_root is not None:
        normalized["tickDispatch"] = _load_tick_dispatch_state(workflow_root)
    normalized["nextAction"] = derive_next_action(normalized)
    return normalized


def build_status(workflow_root: Path) -> dict[str, Any]:
    """Top-level status builder keyed by ``workflow_root``.

    Loads the plugin's workspace accessor from the workflow's config file and
    returns the normalized status payload. Intentionally self-contained so
    runtime + shadow-runtime code paths can call it without knowing about the
    workspace construction details.
    """
    from workflows.change_delivery.workspace import load_workspace_from_config

    ws = load_workspace_from_config(workspace_root=workflow_root)
    return normalize_status(ws.build_status_raw(), workflow_root)


def compute_ledger_drift(
    *,
    active_lane: dict[str, Any] | None,
    lane_issue_number: int | None,
    ledger_active: Any,
    ledger_active_number: int | None,
    ledger_idle: bool,
    ledger_state: str | None,
    open_pr: dict[str, Any] | None,
    pr_ledger: dict[str, Any] | None,
    review_loop_state: str | None,
    ledger_review_loop_state: str | None,
) -> list[str]:
    """Collect the human-readable drift reasons between GitHub truth and the ledger.

    Pure data function ported from ``build_status_raw`` so the read-model
    consumer doesn't need to duplicate the drift heuristics.
    """
    drift: list[str] = []
    pr_ledger = pr_ledger or {}
    if active_lane and ledger_active_number != lane_issue_number:
        drift.append(f"ledger activeLane={ledger_active!r} but GitHub active-lane is issue #{lane_issue_number}")
    if active_lane and ledger_idle:
        drift.append("ledger says workflowIdle=true while an active-lane issue exists")
    if active_lane and ledger_state in {"merged", "idle"}:
        drift.append(f"ledger workflowState={ledger_state!r} looks stale for an active lane")
    if not active_lane and ledger_active is not None:
        drift.append("ledger tracks an active lane but GitHub has no active-lane issue")
    if open_pr and not pr_ledger.get("url"):
        drift.append("open PR exists for active lane but ledger has no PR URL")
    if open_pr and pr_ledger.get("headSha") in {None, ""}:
        drift.append("open PR exists for active lane but ledger has no PR head SHA")
    if open_pr and ledger_review_loop_state not in {None, review_loop_state}:
        drift.append(f"ledger reviewLoopState={ledger_review_loop_state!r} differs from derived state {review_loop_state!r}")
    return drift


def resolve_publish_ready_workflow_state(
    review_loop_state: str | None,
    *,
    merge_blocked: bool,
) -> tuple[str, str]:
    """Map the review-loop state of a published PR into (workflowState, reviewState).

    Mirrors the wrapper's publish-ready classification so both ``build_status_raw``
    and ``reconcile`` can share the same logic.
    """
    if review_loop_state == "clean" and not merge_blocked:
        return "approved", "approved"
    if review_loop_state == "findings_open":
        return "findings_open", "findings_open"
    if review_loop_state == "rework_required":
        return "rework_required", "rework_required"
    return "under_review", "under_review"


def derive_publish_status(open_pr: dict[str, Any] | None, *, publish_ready: bool) -> str:
    """Classify the publish status exposed in the read-model and ledger.

    ``ready_for_review`` when the PR is out of draft and marked publish-ready,
    ``draft_pr`` while still a draft, ``not_published`` when no PR exists yet.
    """
    if publish_ready:
        return "ready_for_review"
    if open_pr:
        return "draft_pr"
    return "not_published"


def assemble_status_payload(
    *,
    now_iso: str,
    engine_owner: str,
    repo_path: str,
    ledger_path: str,
    health_path: str,
    audit_log_path: str,
    active_lane: dict[str, Any] | None,
    active_lane_error: dict[str, Any] | None,
    open_pr: dict[str, Any] | None,
    ledger: dict[str, Any],
    ledger_active_number: int | None,
    effective_workflow_state: str | None,
    effective_review_state: str | None,
    ledger_idle: bool | None,
    effective_repair_brief: dict[str, Any] | None,
    implementation: dict[str, Any],
    local_head_sha: str | None,
    worktree_branch: str | None,
    worktree_commits_ahead: int | None,
    lane_state_path_str: str | None,
    lane_memo_path_str: str | None,
    active_session_health: dict[str, Any] | None,
    session_action_recommendation: dict[str, Any],
    nudge_preflight: dict[str, Any],
    acp_session_strategy: dict[str, Any],
    publish_status: str,
    preferred_codex_model: str | None,
    coder_agent_name: str,
    actor_labels: dict[str, Any],
    reviews: dict[str, Any],
    review_loop_state: str | None,
    merge_blocked: bool,
    merge_blockers: list[str],
    claude_preflight: dict[str, Any],
    detailed_jobs: dict[str, Any],
    hermes_job_names: list[str],
    missing_core_jobs: list[str],
    disabled_core_jobs: list[str],
    stale_core_jobs: list[str],
    broken_watchers: list[dict[str, Any]],
    drift: list[str],
    stale_lane_reasons: list[str],
    health: str,
    legacy_watchdog_present: bool,
    legacy_watchdog_mode: str,
    inter_review_agent_model: str,
    next_action: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the full ``build_status_raw`` response dict.

    The wrapper still owns the legacy GitHub/jobs/git/ledger reads, but this
    function composes the final payload shape so the read-model envelope lives
    adapter-side.
    """
    return {
        "updatedAt": now_iso,
        "engineOwner": engine_owner,
        "repo": repo_path,
        "ledgerPath": ledger_path,
        "healthPath": health_path,
        "auditLogPath": audit_log_path,
        "activeLane": active_lane,
        "activeLaneError": active_lane_error,
        "openPr": open_pr,
        "ledger": {
            "schemaVersion": ledger.get("schemaVersion"),
            "activeLane": ledger_active_number,
            "workflowState": effective_workflow_state,
            "reviewLoopState": ledger.get("reviewLoopState"),
            "workflowIdle": ledger_idle,
            "reviewState": effective_review_state,
            "readyToCloseCount": len(ledger.get("readyToClose", [])),
            "externalReviewAutoResolved": get_ledger_field(ledger, "externalReviewAutoResolved"),
            "sessionNudge": ledger.get("sessionNudge"),
            "repairBrief": effective_repair_brief,
            "codexModel": implementation.get("codexModel") or preferred_codex_model or (ledger.get("implementation") or {}).get("codexModel"),
            "internalReviewerModel": (
                get_ledger_field(ledger, "internalReviewerModel")
                or (get_review(reviews, "internalReview").get("model"))
                or inter_review_agent_model
            ),
            "workflowActors": ledger.get("workflowActors") or actor_labels,
        },
        "implementation": {
            "session": implementation.get("session"),
            "sessionRuntime": implementation.get("sessionRuntime"),
            "sessionName": implementation.get("sessionName"),
            "codexModel": implementation.get("codexModel") or preferred_codex_model,
            "agentName": coder_agent_name,
            "agentRole": "coder_agent",
            "resumeSessionId": implementation.get("resumeSessionId"),
            "threadId": implementation.get("threadId"),
            "turnId": implementation.get("turnId"),
            "lastRuntimeEvent": implementation.get("lastRuntimeEvent"),
            "lastRuntimeMessage": implementation.get("lastRuntimeMessage"),
            "runtimeMetrics": implementation.get("runtimeMetrics"),
            "worktree": implementation.get("worktree"),
            "localHeadSha": local_head_sha,
            "publishStatus": publish_status,
            "updatedAt": implementation.get("updatedAt"),
            "branch": worktree_branch or implementation.get("branch") or ledger.get("branch"),
            "commitsAhead": worktree_commits_ahead,
            "laneStatePath": lane_state_path_str,
            "laneMemoPath": lane_memo_path_str,
            "activeSessionHealth": active_session_health,
            "sessionActionRecommendation": session_action_recommendation,
            "sessionNudgePreflight": nudge_preflight,
            "acpSessionStrategy": acp_session_strategy,
            "laneState": implementation.get("laneState"),
        },
        "reviews": {
            **reviews,
            "interReviewAgent": get_review(reviews, "internalReview") or None,
        },
        "derivedReviewLoopState": review_loop_state,
        "derivedMergeBlocked": merge_blocked,
        "derivedMergeBlockers": merge_blockers,
        "preflight": {
            "claudeReview": claude_preflight,
            "interReviewAgent": claude_preflight,
        },
        "nextAction": next_action,
        "coreJobs": detailed_jobs,
        "hermesJobNames": hermes_job_names,
        "missingCoreJobs": missing_core_jobs,
        "disabledCoreJobs": disabled_core_jobs,
        "staleCoreJobs": stale_core_jobs,
        "brokenIssueWatchers": broken_watchers,
        "drift": drift,
        "staleLaneReasons": stale_lane_reasons,
        "health": health,
        "legacyWatchdogPresent": legacy_watchdog_present,
        "legacyWatchdogMode": legacy_watchdog_mode,
    }


def derive_latest_progress(
    *,
    implementation: dict[str, Any] | None,
    ledger: dict[str, Any] | None,
    open_pr: dict[str, Any] | None,
    reviews: dict[str, Any] | None,
    review_loop_state: str | None,
    merge_blocked: bool,
    now_iso: str,
) -> dict[str, Any]:
    """Return the meaningful-progress marker the lane state payload should record.

    Mirrors the wrapper's ``_derive_latest_progress``: promotes the Codex Cloud
    completion timestamp to ``kind="approved"`` when the PR is cleanly reviewed,
    otherwise falls back to the implementation status / ledger workflow state.
    """
    impl = implementation or {}
    ledger = ledger or {}
    reviews = reviews or {}
    default = {
        "kind": (impl.get("status") or ledger.get("workflowState") or "unknown"),
        "at": impl.get("updatedAt") or now_iso,
    }
    external_review = get_review(reviews, "externalReview")
    codex_updated_at = external_review.get("updatedAt")
    if (
        open_pr
        and review_loop_state == "clean"
        and not merge_blocked
        and external_review.get("status") == "completed"
        and external_review.get("verdict") == "PASS_CLEAN"
        and codex_updated_at
    ):
        return {"kind": "approved", "at": codex_updated_at}
    return default


def apply_ledger_reviews_and_header(
    ledger: dict[str, Any],
    *,
    review_loop_state: str | None,
    codex_model: str | None,
    inter_review_agent_model: str,
    actor_labels: dict[str, Any],
    reviews: dict[str, Any],
) -> None:
    """Write the reconcile "header" fields + merged reviews into the ledger.

    Pure mutation helper ported from the wrapper's ``reconcile``; the caller
    still owns the downstream audit/transition calls.
    """
    ledger["schemaVersion"] = 6
    ledger["reviewLoopState"] = review_loop_state
    ledger["internalReviewerModel"] = inter_review_agent_model
    ledger["codexModel"] = codex_model
    ledger["workflowActors"] = actor_labels
    ledger.setdefault("approval", {})
    ledger.setdefault("reviews", {})
    ledger["reviews"]["rockClaw"] = reviews["rockClaw"]
    ledger["reviews"]["internalReview"] = reviews["internalReview"]
    ledger["reviews"]["externalReview"] = reviews["externalReview"]


def apply_ledger_implementation_merge(
    ledger: dict[str, Any],
    *,
    active_lane: dict[str, Any] | None,
    open_pr: dict[str, Any] | None,
    implementation: dict[str, Any],
    codex_model_fallback: str | None,
    coder_agent_name: str,
) -> None:
    """Merge the canonical implementation sub-object into the ledger.

    Preserves any existing ledger.implementation keys the wrapper set elsewhere
    (dispatch attempts, previous sessions, custom annotations) and stamps the
    latest normalized values on top.
    """
    impl = implementation or {}
    existing_impl = ledger.get("implementation") or {}
    merged_status = impl.get("status") or (
        "implementing" if active_lane and not open_pr else ledger.get("workflowState")
    )
    ledger["implementation"] = {
        **existing_impl,
        "session": impl.get("session"),
        "previousSession": impl.get("previousSession"),
        "sessionRuntime": impl.get("sessionRuntime"),
        "sessionName": impl.get("sessionName"),
        "codexModel": impl.get("codexModel") or codex_model_fallback,
        "agentName": coder_agent_name,
        "agentRole": "coder_agent",
        "resumeSessionId": impl.get("resumeSessionId"),
        "threadId": impl.get("threadId"),
        "turnId": impl.get("turnId"),
        "lastRuntimeEvent": impl.get("lastRuntimeEvent"),
        "lastRuntimeMessage": impl.get("lastRuntimeMessage"),
        "runtimeMetrics": impl.get("runtimeMetrics"),
        "worktree": impl.get("worktree"),
        "localHeadSha": impl.get("localHeadSha"),
        "publishStatus": impl.get("publishStatus"),
        "updatedAt": impl.get("updatedAt"),
        "branch": impl.get("branch"),
        "status": merged_status,
        "lastDispatchAttemptId": impl.get("lastDispatchAttemptId"),
        "lastDispatchAt": impl.get("lastDispatchAt"),
        "lastRestartAttemptId": impl.get("lastRestartAttemptId"),
        "lastRestartAt": impl.get("lastRestartAt"),
    }


def apply_active_lane_ledger_transition(
    ledger: dict[str, Any],
    *,
    active_lane: dict[str, Any],
    open_pr: dict[str, Any] | None,
    implementation: dict[str, Any],
    reviews: dict[str, Any],
    previous_internal_review: dict[str, Any] | None,
    publish_ready: bool,
    review_loop_state: str | None,
    merge_blocked: bool,
    merge_blockers: list[str],
    now_iso: str,
    repair_brief: dict[str, Any] | None,
    operator_attention_needed: bool,
    pass_with_findings_reviews: int = 1,
) -> None:
    """Apply the "active lane present" reconcile transition to ``ledger``.

    Pure ledger mutation helper extracted from the wrapper's ``reconcile``.
    Relies on adapter-owned helpers for pre-publish gating and approval
    classification so the wrapper only needs to inject the state inputs.
    """
    open_pr = open_pr or None
    pr_ledger = ledger.get("pr") or {}
    lane_number = active_lane["number"]
    ledger["activeLane"] = lane_number
    ledger["workflowIdle"] = False
    ledger["blockedReason"] = None
    ledger["branch"] = (open_pr or {}).get("headRefName") or implementation.get("branch")
    ledger["openActiveLanePr"] = (open_pr or {}).get("url")
    ledger["pr"] = {
        **pr_ledger,
        "number": (open_pr or {}).get("number"),
        "url": (open_pr or {}).get("url"),
        "headSha": (open_pr or {}).get("headRefOid"),
        "checks": pr_ledger.get("checks"),
        "mergeBlocked": merge_blocked,
        "mergeBlockers": merge_blockers,
        "merged": False,
        "isDraft": (open_pr or {}).get("isDraft"),
    }
    internal_review = get_review(reviews, "internalReview")
    local_head_sha = implementation.get("localHeadSha")
    prepublish_gate_ready = (
        internal_review.get("verdict") == "PASS_CLEAN"
        and current_inter_review_agent_matches_local_head(internal_review, local_head_sha)
    )
    ledger["prePublishGate"] = {
        "status": "ready" if prepublish_gate_ready else "pending",
        "headSha": local_head_sha,
        "updatedAt": now_iso,
    }
    approval = ledger.setdefault("approval", {})
    if publish_ready:
        resolved_state, resolved_review = resolve_publish_ready_workflow_state(
            review_loop_state,
            merge_blocked=merge_blocked,
        )
        ledger["workflowState"] = resolved_state
        ledger["reviewState"] = resolved_review
        approval["status"] = "approved" if ledger["workflowState"] == "approved" else "not-approved"
        approval["approvedAt"] = now_iso if ledger["workflowState"] == "approved" else None
        approval["approvedHeadSha"] = (open_pr or {}).get("headRefOid") if ledger["workflowState"] == "approved" else None
        approval["pendingReason"] = None if ledger["workflowState"] == "approved" else (
            "open-review-findings" if review_loop_state in {"findings_open", "rework_required"} else "awaiting-codex-cloud"
        )
    else:
        local_candidate = has_local_candidate(local_head_sha, implementation.get("commitsAhead"))
        claude_current = current_inter_review_agent_matches_local_head(internal_review, local_head_sha)
        single_pass_gate = single_pass_local_claude_gate_satisfied(
            previous_internal_review or internal_review,
            local_head_sha,
            implementation.get("laneState"),
            pass_with_findings_reviews=pass_with_findings_reviews,
        )
        ledger["workflowState"] = resolve_prepublish_workflow_state(
            local_candidate=local_candidate,
            single_pass_gate_satisfied=single_pass_gate,
            claude_current=claude_current,
            claude_verdict=internal_review.get("verdict"),
        )
        ledger["reviewState"] = ledger["workflowState"]
        approval["status"] = "not-approved"
        approval["approvedAt"] = None
        approval["approvedHeadSha"] = None
        approval["pendingReason"] = (
            "awaiting-local-claude" if ledger["workflowState"] != "ready_to_publish" else "awaiting-publish"
        )
    ledger["repairBrief"] = repair_brief
    if repair_brief is not None and not publish_ready:
        ledger["workflowState"] = "claude_prepublish_findings"
        ledger["reviewState"] = "claude_prepublish_findings"
        approval["pendingReason"] = "open-review-findings"
    if operator_attention_needed:
        ledger["workflowState"] = "operator_attention_required"
        ledger["reviewState"] = "operator_attention_required"
        ledger["blockedReason"] = "operator-attention-required"
        approval["status"] = "not-approved"
        approval["approvedAt"] = None
        approval["approvedHeadSha"] = None
        approval["pendingReason"] = "operator-attention-required"
    ledger["updatedAt"] = now_iso


def apply_idle_ledger_transition(ledger: dict[str, Any], *, now_iso: str) -> None:
    """Mutate ``ledger`` in place into the "no active lane" terminal shape.

    Mirrors the wrapper's ``reconcile`` else-branch when GitHub has no active
    lane and no error. Resets the whole lane+approval+workflow/review state
    envelope to idle.
    """
    ledger["activeLane"] = None
    ledger["workflowIdle"] = True
    ledger["workflowState"] = "idle"
    ledger["reviewState"] = "idle"
    ledger["reviewLoopState"] = "idle"
    ledger["branch"] = None
    ledger["openActiveLanePr"] = None
    ledger["blockedReason"] = None
    approval = ledger.setdefault("approval", {})
    approval["status"] = "not-approved"
    approval["approvedAt"] = None
    approval["approvedHeadSha"] = None
    approval["pendingReason"] = None
    ledger["repairBrief"] = None
    ledger["updatedAt"] = now_iso


def apply_active_lane_error_ledger_transition(
    ledger: dict[str, Any],
    *,
    active_lane_error: Any,
    now_iso: str,
) -> None:
    """Record the ``multiple-active-lanes`` terminal shape in ``ledger``.

    Pure mutation helper pulled out of ``reconcile``: marks the workflow and
    review states as ``blocked``, attaches the error payload, and leaves
    approval pending with reason ``multiple-active-lanes``.
    """
    ledger["workflowState"] = "blocked"
    ledger["reviewState"] = "blocked"
    ledger["reviewLoopState"] = "blocked"
    ledger["workflowIdle"] = False
    ledger["blockedReason"] = active_lane_error
    approval = ledger.setdefault("approval", {})
    approval["status"] = "not-approved"
    approval["pendingReason"] = "multiple-active-lanes"
    ledger["updatedAt"] = now_iso


def resolve_prepublish_workflow_state(
    *,
    local_candidate: bool,
    single_pass_gate_satisfied: bool,
    claude_current: bool,
    claude_verdict: str | None,
) -> str:
    """Classify the pre-publish workflow state for an active lane.

    Ordered rules (first match wins):

    1. No local candidate yet -> ``implementing_local``.
    2. Pre-publish Claude gate already satisfied on the current head ->
       ``ready_to_publish``.
    3. Claude review completed against the current head with actionable
       findings -> ``claude_prepublish_findings``.
    4. Fallback -> ``awaiting_claude_prepublish``.
    """
    if not local_candidate:
        return "implementing_local"
    if single_pass_gate_satisfied:
        return "ready_to_publish"
    if claude_current and claude_verdict in {"PASS_WITH_FINDINGS", "REWORK"}:
        return "claude_prepublish_findings"
    return "awaiting_claude_prepublish"


def increment_no_progress_ticks(
    *,
    existing: dict[str, Any],
    latest_progress: dict[str, Any] | None,
    now_iso: str | None = None,
    cooldown_seconds: int = LANE_COUNTER_INCREMENT_MIN_SECONDS,
) -> int:
    latest_progress = latest_progress or {}
    if latest_progress.get("kind") in {"approved", "merged"}:
        return 0
    prev_impl = existing.get("implementation") or {}
    prev_budget = existing.get("budget") or {}
    same_at = (prev_impl.get("lastMeaningfulProgressAt") or None) == (latest_progress.get("at") or None)
    same_kind = (prev_impl.get("lastMeaningfulProgressKind") or None) == (latest_progress.get("kind") or None)
    if same_at and same_kind and latest_progress.get("at"):
        previous_evaluated_epoch = _iso_to_epoch(prev_budget.get("lastEvaluatedAt"))
        now_epoch = _iso_to_epoch(now_iso) if now_iso else None
        if (
            previous_evaluated_epoch is not None
            and now_epoch is not None
            and (now_epoch - previous_evaluated_epoch) < cooldown_seconds
        ):
            return int(prev_budget.get("noProgressTicks") or 0)
        return int(prev_budget.get("noProgressTicks") or 0) + 1
    return 0


def write_lane_memo(
    *,
    worktree: Path | None,
    issue: dict[str, Any] | None,
    branch: str | None,
    open_pr: dict[str, Any] | None,
    repair_brief: dict[str, Any] | None,
    latest_progress: dict[str, Any] | None,
    validation_summary: list[str] | None,
    acp_strategy: dict[str, Any] | None = None,
) -> str | None:
    path = lane_memo_path(worktree)
    if path is None or issue is None or worktree is None:
        return None
    body = render_lane_memo(
        issue=issue,
        worktree=worktree,
        branch=branch,
        open_pr=open_pr,
        repair_brief=repair_brief,
        latest_progress=latest_progress,
        validation_summary=validation_summary,
        acp_strategy=acp_strategy,
    )
    _write_text_file(path, body)
    return body


def write_lane_state(
    *,
    worktree: Path | None,
    issue: dict[str, Any] | None,
    open_pr: dict[str, Any] | None,
    implementation: dict[str, Any],
    reviews: dict[str, Any],
    repair_brief: dict[str, Any] | None,
    now_iso: str,
    latest_progress: dict[str, Any] | None,
    preflight: dict[str, Any] | None = None,
    cooldown_seconds: int = LANE_COUNTER_INCREMENT_MIN_SECONDS,
) -> dict[str, Any] | None:
    path = lane_state_path(worktree)
    if path is None or issue is None:
        return None
    existing = _load_optional_json_file(path) or {}
    existing_impl = existing.get("implementation") or {}
    previous_restart = ((existing.get("restart") or {}).get("count") or 0)
    existing_restart = existing.get("restart") or {}
    recommendation = implementation.get("sessionActionRecommendation") or {}
    current_dispatch_attempt_id = implementation.get("lastDispatchAttemptId") or implementation.get("lastDispatchAt")
    previous_dispatch_attempt_id = existing_impl.get("lastDispatchAttemptId") or existing_impl.get("lastDispatchAt")
    attempted_this_cycle = bool(current_dispatch_attempt_id and current_dispatch_attempt_id != previous_dispatch_attempt_id)
    current_restart_attempt_id = implementation.get("lastRestartAttemptId") or implementation.get("lastRestartAt")
    previous_restart_attempt_id = existing_impl.get("lastRestartAttemptId") or existing_impl.get("lastRestartAt")
    restart_count = previous_restart
    if current_restart_attempt_id and current_restart_attempt_id != previous_restart_attempt_id:
        restart_count += 1
    failure = classify_lane_failure(implementation=implementation, reviews=reviews, preflight=preflight)
    existing_failure = existing.get("failure") or {}
    failure_class = failure.get("failureClass")
    retry_count = 0
    if failure_class:
        if existing_failure.get("lastClass") == failure_class:
            if attempted_this_cycle:
                previous_failure_epoch = _iso_to_epoch(existing_failure.get("lastAt"))
                now_epoch = _iso_to_epoch(now_iso)
                if (
                    previous_failure_epoch is not None
                    and now_epoch is not None
                    and (now_epoch - previous_failure_epoch) < cooldown_seconds
                ):
                    retry_count = int(existing_failure.get("retryCount") or 0)
                else:
                    retry_count = int(existing_failure.get("retryCount") or 0) + 1
            else:
                retry_count = int(existing_failure.get("retryCount") or 0)
        elif attempted_this_cycle:
            retry_count = 1
    no_progress_ticks = increment_no_progress_ticks(
        existing=existing,
        latest_progress=latest_progress,
        now_iso=now_iso,
        cooldown_seconds=cooldown_seconds,
    )
    payload = {
        "schemaVersion": 1,
        "issue": {
            "number": issue.get("number"),
            "title": issue.get("title"),
            "url": issue.get("url"),
        },
        "worktree": str(worktree),
        "branch": (open_pr or {}).get("headRefName") or implementation.get("branch"),
        "pr": {
            "number": (open_pr or {}).get("number"),
            "url": (open_pr or {}).get("url"),
            "currentHeadSha": (open_pr or {}).get("headRefOid"),
            "lastPublishedHeadSha": (open_pr or {}).get("headRefOid") or ((existing.get("pr") or {}).get("lastPublishedHeadSha")),
        },
        "implementation": {
            "activeSessionName": implementation.get("session"),
            "activeSessionLastUsedAt": ((implementation.get("activeSessionHealth") or {}).get("lastUsedAt")),
            "activeSessionHealthy": bool((implementation.get("activeSessionHealth") or {}).get("healthy")),
            "lastMeaningfulProgressAt": (latest_progress or {}).get("at") or implementation.get("updatedAt"),
            "lastMeaningfulProgressKind": (latest_progress or {}).get("kind") or implementation.get("status"),
            "localHeadSha": implementation.get("localHeadSha"),
            "publishStatus": implementation.get("publishStatus"),
            "lastDispatchAttemptId": implementation.get("lastDispatchAttemptId"),
            "lastDispatchAt": implementation.get("lastDispatchAt"),
            "lastRestartAttemptId": implementation.get("lastRestartAttemptId"),
            "lastRestartAt": implementation.get("lastRestartAt"),
        },
        "sessionControl": {
            **(((existing.get("sessionControl") or {}).copy()) if isinstance(existing.get("sessionControl"), dict) else {}),
            "strategy": implementation.get("acpSessionStrategy") or {},
        },
        "review": {
            "repairBriefHeadSha": (repair_brief or {}).get("forHeadSha"),
            "lastInternalReviewedHeadSha": ((get_review(reviews, "internalReview")).get("reviewedHeadSha")) or get_lane_state_review_field(existing.get("review"), "lastInternalReviewedHeadSha"),
            "lastInternalVerdict": ((get_review(reviews, "internalReview")).get("verdict")) or ((existing.get("review") or {}).get("lastInternalVerdict")),
            "localInternalReviewCount": local_inter_review_agent_review_count((get_review(reviews, "internalReview") or None), existing),
            "currentClaudeRunId": ((get_review(reviews, "internalReview")).get("runId")),
            "currentClaudeTargetHeadSha": inter_review_agent_target_head((get_review(reviews, "internalReview") or None)),
            "currentClaudeStatus": ((get_review(reviews, "internalReview")).get("status")),
            "currentClaudeTerminalState": ((get_review(reviews, "internalReview")).get("terminalState")),
            "lastClaudeFailureClass": ((get_review(reviews, "internalReview")).get("failureClass")),
            "lastInterReviewAgentReviewedHeadSha": ((get_review(reviews, "internalReview")).get("reviewedHeadSha")) or ((existing.get("review") or {}).get("lastInterReviewAgentReviewedHeadSha")),
            "lastInterReviewAgentVerdict": ((get_review(reviews, "internalReview")).get("verdict")) or ((existing.get("review") or {}).get("lastInterReviewAgentVerdict")),
            "localInterReviewAgentReviewCount": local_inter_review_agent_review_count((get_review(reviews, "internalReview") or None), existing),
            "currentInterReviewAgentRunId": ((get_review(reviews, "internalReview")).get("runId")),
            "currentInterReviewAgentTargetHeadSha": inter_review_agent_target_head((get_review(reviews, "internalReview") or None)),
            "currentInterReviewAgentStatus": ((get_review(reviews, "internalReview")).get("status")),
            "currentInterReviewAgentTerminalState": ((get_review(reviews, "internalReview")).get("terminalState")),
            "lastInterReviewAgentFailureClass": ((get_review(reviews, "internalReview")).get("failureClass")),
            "lastCodexCloudReviewedHeadSha": (get_review(reviews, "externalReview").get("reviewedHeadSha")),
        },
        "failure": {
            "lastClass": failure_class,
            "detail": failure.get("detail"),
            "retryCount": retry_count,
            "lastAt": now_iso if failure_class else None,
        },
        "budget": {
            "noProgressTicks": no_progress_ticks,
            "lastEvaluatedAt": now_iso,
        },
        "restart": {
            "count": restart_count,
            "lastReason": recommendation.get("reason") if recommendation.get("action") == "restart-session" else existing_restart.get("lastReason"),
            "lastAt": now_iso if recommendation.get("action") == "restart-session" else existing_restart.get("lastAt"),
        },
        "memo": {
            "lastUpdatedAt": now_iso,
            "source": "change-delivery",
        },
    }
    _write_json_file(path, payload)
    return payload
