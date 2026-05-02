from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from workflows.change_delivery.migrations import get_review
from workflows.change_delivery.paths import lane_memo_path, lane_state_path
from workflows.change_delivery.reviews import external_review_clean_for_head
from workflows.change_delivery.sessions import (
    expected_lane_branch,
    expected_lane_worktree,
    lane_acpx_session_name,
)


"""Change-delivery workflow action execution helpers.

Each ``run_*`` function below is a pure, dependency-injected implementation of
one operator action (publish, push, merge, dispatch, tick). Callers inject
workspace-scoped primitives (``reconcile_fn``, ``_run``, ``audit``, etc.).
The live CLI (``python -m workflows``) and systemd-supervised runtime both
route through these functions, which are the single source of truth for the
action bodies.
"""


def _object_value(value: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(value, dict) and key in value:
            return value.get(key)
        if hasattr(value, key):
            return getattr(value, key)
    return None


def _prompt_output(value: Any) -> str:
    output = _object_value(value, "output")
    if output is not None:
        return str(output).strip()
    return str(value or "").strip()


def _runtime_metrics_payload(value: Any) -> dict[str, Any]:
    if value is None or isinstance(value, str):
        return {}
    tokens = _object_value(value, "tokens")
    return {
        "session_id": _object_value(value, "session_id", "sessionId"),
        "thread_id": _object_value(value, "thread_id", "threadId"),
        "turn_id": _object_value(value, "turn_id", "turnId"),
        "last_event": _object_value(value, "last_event", "lastEvent"),
        "last_message": _object_value(value, "last_message", "lastMessage"),
        "turn_count": int(_object_value(value, "turn_count", "turnCount") or 0),
        "tokens": tokens if isinstance(tokens, dict) else {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "rate_limits": _object_value(value, "rate_limits", "rateLimits"),
    }


def _prompt_result_from_exception(exc: Exception) -> Any:
    result = getattr(exc, "result", None)
    if result is not None:
        return result
    return SimpleNamespace(
        output="",
        session_id=None,
        thread_id=None,
        turn_id=None,
        last_event=None,
        last_message=str(exc),
        turn_count=0,
        tokens={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        rate_limits=None,
    )


def _reconciled_status_after_prompt_error(
    *,
    reconcile_fn: Callable[..., dict[str, Any]],
    status_before: dict[str, Any],
    issue_number: Any,
) -> dict[str, Any] | None:
    try:
        status_after = reconcile_fn(fix_watchers=True)
    except Exception:
        return None
    active_lane = status_after.get("activeLane") or {}
    if issue_number and active_lane.get("number") != issue_number:
        return None
    before_head = (status_before.get("implementation") or {}).get("localHeadSha")
    after_head = (status_after.get("implementation") or {}).get("localHeadSha")
    if not after_head:
        return None
    next_action_type = (status_after.get("nextAction") or {}).get("type")
    if next_action_type not in {"run_internal_review", "publish_ready_pr", "push_pr_update", "merge_and_promote"}:
        return None
    return status_after


def _stage_runtime_result(value: Any) -> Any:
    runtime_result = _object_value(value, "runtime_result", "runtimeResult")
    return runtime_result if runtime_result is not None else value


def _stage_session_handle(value: Any) -> Any:
    return _object_value(value, "session_handle", "sessionHandle")


def run_publish_ready_pr(
    *,
    reconcile_fn: Callable[..., dict[str, Any]],
    run_fn: Callable[..., Any],
    audit_fn: Callable[..., Any],
    code_host_client: Any,
) -> dict[str, Any]:
    """Adapter-owned implementation of ``publish_ready_pr_raw``.

    All side-effectful primitives (reconcile, subprocess, audit, PR-ready) are
    injected so the adapter owns the decision flow without needing to reimport
    the wrapper's helpers.
    """
    status = reconcile_fn(fix_watchers=True)
    issue = status.get('activeLane')
    if not issue:
        return {'published': False, 'reason': 'no-active-lane'}
    impl = status.get('implementation') or {}
    worktree = Path(impl['worktree']) if impl.get('worktree') else None
    branch = impl.get('branch')
    if worktree is None or not branch:
        return {'published': False, 'reason': 'missing-worktree-or-branch'}
    if status.get('openPr'):
        pr = status.get('openPr') or {}
        if pr.get('isDraft') and code_host_client.mark_pull_request_ready(pr.get('number')):
            after = reconcile_fn(fix_watchers=True)
            return {'published': True, 'prNumber': pr.get('number'), 'after': after}
        return {'published': False, 'reason': 'pr-already-exists', 'after': status}
    run_fn(['git', 'push', '-u', 'origin', branch], cwd=worktree)
    slug_title = re.sub(r'^\[[^\]]+\]\s*', '', issue.get('title') or '').strip()
    title = f"[codex] {slug_title}"
    body = f"Implements issue #{issue.get('number')}. Generated by wrapper-owned workflow watchdog."
    result = code_host_client.create_pull_request(
        head=branch,
        title=title,
        body=body,
    )
    after = reconcile_fn(fix_watchers=True)
    audit_fn(
        'publish-ready-pr',
        'Published ready-for-review PR for active lane',
        issueNumber=issue.get('number'),
        branch=branch,
    )
    return {'published': True, 'result': str(result or '').strip(), 'after': after}


def run_push_pr_update(
    *,
    reconcile_fn: Callable[..., dict[str, Any]],
    run_fn: Callable[..., Any],
    audit_fn: Callable[..., Any],
) -> dict[str, Any]:
    """Adapter-owned implementation of ``push_pr_update_raw``."""
    status = reconcile_fn(fix_watchers=True)
    issue = status.get('activeLane')
    pr = status.get('openPr') or {}
    impl = status.get('implementation') or {}
    worktree = Path(impl['worktree']) if impl.get('worktree') else None
    branch = impl.get('branch') or pr.get('headRefName')
    local_head_sha = impl.get('localHeadSha')
    published_head_sha = pr.get('headRefOid')
    if not issue or not pr:
        return {'pushed': False, 'reason': 'missing-active-lane-or-pr', 'after': status}
    if worktree is None or not branch:
        return {'pushed': False, 'reason': 'missing-worktree-or-branch', 'after': status}
    if not local_head_sha or local_head_sha == published_head_sha:
        return {'pushed': False, 'reason': 'pr-already-current', 'after': status}
    run_fn(['git', 'push', 'origin', f'HEAD:{branch}'], cwd=worktree)
    after = reconcile_fn(fix_watchers=True)
    audit_fn(
        'push-pr-update',
        'Pushed updated repair head to existing PR branch',
        issueNumber=issue.get('number'),
        prNumber=pr.get('number'),
        branch=branch,
        headSha=local_head_sha,
    )
    return {'pushed': True, 'prNumber': pr.get('number'), 'headSha': local_head_sha, 'after': after}


def run_merge_and_promote(
    *,
    reconcile_fn: Callable[..., dict[str, Any]],
    audit_fn: Callable[..., Any],
    issue_remove_label_fn: Callable[[Any, str], Any],
    issue_close_fn: Callable[[Any, str], Any],
    issue_add_label_fn: Callable[[Any, str], Any],
    issue_comment_fn: Callable[[Any, str], Any],
    pick_next_lane_issue_fn: Callable[[], dict[str, Any] | None],
    now_iso_fn: Callable[[], str],
    active_lane_label: str,
    code_host_client: Any,
) -> dict[str, Any]:
    """Adapter-owned implementation of ``merge_and_promote_raw``."""
    status = reconcile_fn(fix_watchers=True)
    issue = status.get('activeLane')
    pr = status.get('openPr') or {}
    if not issue or not pr:
        return {'merged': False, 'reason': 'missing-active-lane-or-pr'}
    review_loop_state = status.get("derivedReviewLoopState")
    merge_blocked = bool(status.get("derivedMergeBlocked"))
    external_review = get_review(status.get("reviews"), "externalReview")
    if (
        review_loop_state != "clean"
        or merge_blocked
        or not external_review_clean_for_head(external_review, pr.get("headRefOid"))
    ):
        return {
            "merged": False,
            "reason": "merge-gate-not-satisfied",
            "reviewLoopState": review_loop_state,
            "mergeBlocked": merge_blocked,
            "mergeBlockers": list(status.get("derivedMergeBlockers") or []),
            "externalReview": {
                "required": external_review.get("required"),
                "status": external_review.get("status"),
                "verdict": external_review.get("verdict"),
                "reviewedHeadSha": external_review.get("reviewedHeadSha"),
            },
            "headSha": pr.get("headRefOid"),
        }
    code_host_client.merge_pull_request(
        pr.get('number'),
        squash=True,
        delete_branch=True,
    )
    merged_at = now_iso_fn()
    issue_remove_label_fn(issue.get('number'), active_lane_label)
    close_comment = f"Merged in PR #{pr.get('number')} at {merged_at}. Closing the lane and moving workflow forward."
    issue_close_fn(issue.get('number'), close_comment)
    next_issue = pick_next_lane_issue_fn()
    if next_issue:
        issue_add_label_fn(next_issue.get('number'), active_lane_label)
        issue_comment_fn(
            next_issue.get('number'),
            f"Promoted to active lane after issue #{issue.get('number')} merged in PR #{pr.get('number')}.",
        )
    after = reconcile_fn(fix_watchers=True)
    audit_fn(
        'merge-and-promote',
        'Merged approved PR and promoted next lane',
        mergedPrNumber=pr.get('number'),
        closedIssueNumber=issue.get('number'),
        nextIssueNumber=(next_issue or {}).get('number'),
    )
    return {
        'merged': True,
        'mergedPrNumber': pr.get('number'),
        'closedIssueNumber': issue.get('number'),
        'nextIssueNumber': (next_issue or {}).get('number'),
        'after': after,
    }


def run_ensure_active_lane(
    *,
    build_status_fn: Callable[[], dict[str, Any]],
    reconcile_fn: Callable[..., dict[str, Any]],
    audit_fn: Callable[..., Any],
    issue_add_label_fn: Callable[[Any, str], Any],
    issue_comment_fn: Callable[[Any, str], Any],
    pick_next_lane_issue_fn: Callable[[], dict[str, Any] | None],
    active_lane_label: str,
) -> dict[str, Any]:
    """Promote the first eligible issue when a fresh workflow has no active lane."""
    try:
        status = build_status_fn()
    except Exception as exc:
        return {
            "ok": False,
            "promoted": False,
            "reason": "status-build-failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    active_lane = status.get("activeLane")
    if active_lane:
        return {
            "ok": True,
            "promoted": False,
            "reason": "active-lane-present",
            "issueNumber": active_lane.get("number"),
        }
    active_lane_error = status.get("activeLaneError")
    if active_lane_error:
        return {
            "ok": False,
            "promoted": False,
            "reason": active_lane_error.get("error") or "active-lane-error",
            "activeLaneError": active_lane_error,
        }
    try:
        next_issue = pick_next_lane_issue_fn()
    except Exception as exc:
        return {
            "ok": False,
            "promoted": False,
            "reason": "lane-selection-failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not next_issue:
        return {"ok": True, "promoted": False, "reason": "no-eligible-issue"}

    issue_number = next_issue.get("number")
    if issue_number in (None, ""):
        return {
            "ok": False,
            "promoted": False,
            "reason": "eligible-issue-missing-number",
            "issue": next_issue,
        }
    try:
        label_added = bool(issue_add_label_fn(issue_number, active_lane_label))
    except Exception as exc:
        return {
            "ok": False,
            "promoted": False,
            "reason": "active-lane-label-failed",
            "issueNumber": issue_number,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not label_added:
        return {
            "ok": False,
            "promoted": False,
            "reason": "active-lane-label-not-applied",
            "issueNumber": issue_number,
        }

    comment_posted = False
    try:
        comment_posted = bool(
            issue_comment_fn(
                issue_number,
                "Promoted to active lane during Daedalus startup.",
            )
        )
    except Exception:
        comment_posted = False

    audit_fn(
        "bootstrap-active-lane",
        "Promoted eligible issue to active lane during Daedalus startup",
        issueNumber=issue_number,
        activeLaneLabel=active_lane_label,
    )
    try:
        after = reconcile_fn(fix_watchers=True)
    except Exception as exc:
        return {
            "ok": False,
            "promoted": True,
            "reason": "reconcile-after-promotion-failed",
            "issueNumber": issue_number,
            "commentPosted": comment_posted,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": True,
        "promoted": True,
        "reason": "promoted",
        "issueNumber": issue_number,
        "issueTitle": next_issue.get("title"),
        "issueUrl": next_issue.get("url"),
        "commentPosted": comment_posted,
        "after": {
            "health": after.get("health"),
            "activeLane": (after.get("activeLane") or {}).get("number"),
            "nextAction": (after.get("nextAction") or {}).get("type"),
        },
    }


def run_dispatch_lane_turn(
    *,
    status: dict[str, Any],
    forced_action: str | None,
    audit_action: str,
    now_iso_fn: Callable[[], str],
    close_session_fn: Callable[..., Any],
    show_session_fn: Callable[..., dict[str, Any] | None],
    run_stage_fn: Callable[..., Any],
    prepare_lane_worktree_fn: Callable[..., dict[str, Any]],
    implementation_actor_name: str,
    implementation_actor_cfg: dict[str, Any],
    get_issue_details_fn: Callable[[Any], dict[str, Any] | None],
    workflow_actors_payload_fn: Callable[[dict[str, Any]], dict[str, Any]],
    load_ledger_fn: Callable[[], dict[str, Any]],
    save_ledger_fn: Callable[[dict[str, Any]], Any],
    reconcile_fn: Callable[..., dict[str, Any]],
    audit_fn: Callable[..., Any],
    render_implementation_dispatch_prompt_fn: Callable[..., str],
    runtime_name: str,
    runtime_kind: str = "codex-app-server",
    record_runtime_result_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Adapter-owned implementation of ``_dispatch_lane_turn``.

    The function is the single place that turns a normalized status into either
    an actor-session dispatch or a well-known skip reason. Every side-effectful
    primitive is injected so the adapter owns the branching logic without
    bringing ACPX/GitHub/ledger-write concerns inline.
    """
    issue = status.get('activeLane')
    if not issue:
        return {'dispatched': False, 'reason': 'no-active-lane'}
    impl = status.get('implementation') or {}
    worktree = Path(impl['worktree']) if impl.get('worktree') else expected_lane_worktree(issue.get('number'))
    if worktree is None:
        return {'dispatched': False, 'reason': 'missing-worktree'}
    worktree.mkdir(parents=True, exist_ok=True)
    session_name = impl.get('sessionName') or lane_acpx_session_name(issue.get('number'))
    if not session_name:
        return {'dispatched': False, 'reason': 'missing-session-name'}
    branch = impl.get('branch') or expected_lane_branch(issue)
    if not branch:
        return {'dispatched': False, 'reason': 'missing-branch'}
    worktree_info = prepare_lane_worktree_fn(worktree=worktree, branch=branch, open_pr=status.get('openPr'))
    action = forced_action or ((impl.get('sessionActionRecommendation') or {}).get('action')) or 'restart-session'
    actor_cfg = dict(implementation_actor_cfg or {})
    actor_key = str(implementation_actor_name or "").strip()
    actor_display_name = str(actor_cfg.get("name") or actor_key or "implementation-actor")
    actor_model = str(actor_cfg.get("model") or "")
    if action == 'no-action':
        return {'dispatched': False, 'reason': 'no-action'}
    if action == 'restart-session':
        close_session_fn(
            worktree=worktree,
            session_name=session_name,
            runtime_name=runtime_name,
            runtime_kind=runtime_kind,
        )
    issue_details = get_issue_details_fn(issue.get('number'))
    attempt_at = now_iso_fn()
    attempt_id = f"{session_name}:{attempt_at}"
    prompt = render_implementation_dispatch_prompt_fn(
        issue=issue,
        issue_details=issue_details,
        worktree=worktree,
        lane_memo_path=Path(impl['laneMemoPath']) if impl.get('laneMemoPath') else lane_memo_path(worktree),
        lane_state_path=Path(impl['laneStatePath']) if impl.get('laneStatePath') else lane_state_path(worktree),
        open_pr=status.get('openPr'),
        action=action,
        workflow_state=(status.get('ledger') or {}).get('workflowState'),
    )
    reconciled_after_prompt_error: dict[str, Any] | None = None
    prompt_error: Exception | None = None
    try:
        stage_result = run_stage_fn(
            worktree=worktree,
            session_name=session_name,
            prompt=prompt,
            actor_name=actor_key,
            actor_cfg=actor_cfg,
            runtime_name=runtime_name,
            runtime_kind=runtime_kind,
            resume_session_id=impl.get('resumeSessionId'),
        )
        prompt_result = _stage_runtime_result(stage_result)
        ensured = _stage_session_handle(stage_result)
    except Exception as exc:
        reconciled_after_prompt_error = _reconciled_status_after_prompt_error(
            reconcile_fn=reconcile_fn,
            status_before=status,
            issue_number=issue.get('number'),
        )
        if reconciled_after_prompt_error is None:
            raise
        prompt_error = exc
        prompt_result = _prompt_result_from_exception(exc)
        ensured = None
    session_meta = show_session_fn(
        worktree=worktree,
        session_name=session_name,
        runtime_name=runtime_name,
        runtime_kind=runtime_kind,
    ) or {}
    runtime_metrics = _runtime_metrics_payload(prompt_result)
    if record_runtime_result_fn is not None:
        runtime_metrics = record_runtime_result_fn(
            issue=issue,
            session_name=session_name,
            runtime_name=runtime_name,
            runtime_kind=runtime_kind,
            result=prompt_result,
            metrics=runtime_metrics,
            at=attempt_at,
        ) or runtime_metrics
    ensured_record_id = _object_value(ensured, "record_id", "recordId", "acpxRecordId")
    ensured_session_id = _object_value(ensured, "session_id", "sessionId", "acpSessionId", "acpxSessionId")
    if runtime_kind == "codex-app-server":
        session_record_id = (
            runtime_metrics.get("thread_id")
            or runtime_metrics.get("session_id")
            or session_meta.get('record_id')
            or ensured_record_id
        )
    else:
        session_record_id = (
            session_meta.get('record_id')
            or ensured_record_id
            or runtime_metrics.get("thread_id")
            or runtime_metrics.get("session_id")
        )
    resume_session_id = (
        runtime_metrics.get("thread_id")
        or runtime_metrics.get("session_id")
        or session_meta.get('session_id')
        or ensured_session_id
    )
    implementation_actor = {
        "key": actor_key,
        "name": actor_display_name,
        "model": actor_model,
        "role": "implementation_actor",
        "runtimeName": runtime_name,
        "runtimeKind": runtime_kind or "codex-app-server",
    }
    ledger = load_ledger_fn()
    ledger.setdefault('implementation', {})
    ledger['implementationActor'] = implementation_actor
    ledger['workflowActors'] = workflow_actors_payload_fn(implementation_actor)
    ledger['implementation'] = {
        **ledger.get('implementation', {}),
        'session': session_record_id,
        'previousSession': ledger.get('implementation', {}).get('previousSession'),
        'runtimeName': runtime_name,
        'runtimeKind': runtime_kind or 'codex-app-server',
        'sessionName': session_name,
        'actorKey': actor_key,
        'actorName': actor_display_name,
        'actorModel': actor_model,
        'actorRole': 'implementation_actor',
        'resumeSessionId': resume_session_id,
        'threadId': runtime_metrics.get("thread_id"),
        'turnId': runtime_metrics.get("turn_id"),
        'lastRuntimeEvent': runtime_metrics.get("last_event"),
        'lastRuntimeMessage': runtime_metrics.get("last_message"),
        'runtimeMetrics': runtime_metrics,
        'worktree': str(worktree),
        'updatedAt': attempt_at,
        'branch': branch,
        'status': 'implementing' if not status.get('openPr') else ledger.get('workflowState'),
        'lastDispatchAttemptId': attempt_id,
        'lastDispatchAt': attempt_at,
        'lastRestartAttemptId': attempt_id if action == 'restart-session' else ledger.get('implementation', {}).get('lastRestartAttemptId'),
        'lastRestartAt': attempt_at if action == 'restart-session' else ledger.get('implementation', {}).get('lastRestartAt'),
    }
    save_ledger_fn(ledger)
    reconciled = reconciled_after_prompt_error or reconcile_fn(fix_watchers=True)
    result = {
        'dispatched': True,
        'action': action,
        'issueNumber': issue.get('number'),
        'runtimeName': runtime_name,
        'runtimeKind': runtime_kind or 'codex-app-server',
        'sessionName': session_name,
        'actorKey': actor_key,
        'actorName': actor_display_name,
        'actorModel': actor_model,
        'sessionRecordId': session_record_id,
        'resumeSessionId': resume_session_id,
        'threadId': runtime_metrics.get("thread_id"),
        'turnId': runtime_metrics.get("turn_id"),
        'metrics': runtime_metrics,
        'worktree': str(worktree),
        'worktreePrepared': worktree_info,
        'promptResult': _prompt_output(prompt_result),
        'health': reconciled.get('health'),
    }
    if prompt_error is not None:
        result['reconciledAfterRuntimeError'] = True
        result['runtimeError'] = str(prompt_error)
    audit_fn(
        audit_action,
        f"Dispatched implementation actor turn via {runtime_kind or 'codex-app-server'} session {session_name}",
        issueNumber=issue.get('number'),
        sessionName=session_name,
        sessionRecordId=result.get('sessionRecordId'),
        threadId=result.get('threadId'),
        sessionAction=action,
    )
    return result


def run_dispatch_inter_review_agent_review(
    *,
    reconcile_fn: Callable[..., dict[str, Any]],
    load_ledger_fn: Callable[[], dict[str, Any]],
    save_ledger_fn: Callable[[dict[str, Any]], Any],
    audit_inter_review_agent_transition_fn: Callable[[dict[str, Any] | None, dict[str, Any] | None], Any],
    run_inter_review_agent_review_fn: Callable[..., dict[str, Any]],
    now_iso_fn: Callable[[], str],
    new_inter_review_agent_run_id_fn: Callable[[], str],
    workflow_actors_payload_fn: Callable[[dict[str, Any]], dict[str, Any]],
    inter_review_agent_model: str,
    internal_reviewer_agent_name: str,
    pending_summary: str = "Pending local unpublished branch review before publication.",
    agent_role: str = "internal_reviewer_agent",
) -> dict[str, Any]:
    """Adapter-owned implementation of ``dispatch_inter_review_agent_review_raw``.

    Drives the full review lifecycle:

      1. Reconcile + preflight guard.
      2. Mark the review as ``running`` in the ledger (via adapter helpers).
      3. Invoke the review CLI through ``run_inter_review_agent_review_fn``.
      4. Record success or failure, re-raise review errors to preserve the
         wrapper's historical contract with callers that rely on exception flow.
    """
    from workflows.change_delivery.reviews import (
        build_inter_review_agent_completed_review,
        build_inter_review_agent_failed_review,
        build_inter_review_agent_running_review,
    )

    status = reconcile_fn(fix_watchers=True)
    issue = status.get('activeLane')
    if not issue:
        return {'dispatched': False, 'reason': 'no-active-lane'}
    impl = status.get('implementation') or {}
    worktree = Path(impl['worktree']) if impl.get('worktree') else None
    if worktree is None:
        return {'dispatched': False, 'reason': 'missing-worktree'}
    preflight = ((status.get('preflight') or {}).get('prePublishReview') or {})
    if not preflight.get('shouldRun'):
        return {'dispatched': False, 'reason': 'internal-review-preflight-blocked', 'preflight': preflight}
    head_sha = preflight.get('currentHeadSha')
    now_iso = now_iso_fn()
    run_id = new_inter_review_agent_run_id_fn()
    ledger = load_ledger_fn()
    ledger.setdefault('reviews', {})
    previous = get_review(ledger['reviews'], 'internalReview').copy()
    ledger['reviews']['internalReview'] = build_inter_review_agent_running_review(
        previous,
        run_id=run_id,
        head_sha=head_sha,
        now_iso=now_iso,
        model=inter_review_agent_model,
        pending_summary=pending_summary,
        agent_name=internal_reviewer_agent_name,
        agent_role=agent_role,
    )
    ledger['internalReviewerModel'] = inter_review_agent_model
    ledger['workflowActors'] = workflow_actors_payload_fn(ledger.get("implementationActor") or {})
    save_ledger_fn(ledger)
    audit_inter_review_agent_transition_fn(previous, ledger['reviews']['internalReview'])
    memo_path = Path(impl['laneMemoPath']) if impl.get('laneMemoPath') else lane_memo_path(worktree)
    state_path = Path(impl['laneStatePath']) if impl.get('laneStatePath') else lane_state_path(worktree)
    try:
        result = run_inter_review_agent_review_fn(
            issue=issue,
            worktree=worktree,
            lane_memo_path=memo_path,
            lane_state_path=state_path,
            head_sha=head_sha,
        )
    except Exception as exc:
        failure_class = getattr(exc, 'failure_class', 'review_wrapper_failed')
        failure_summary = str(exc).strip() or 'Internal review agent failed without a detailed error message.'
        failed_at = now_iso_fn()
        ledger = load_ledger_fn()
        ledger.setdefault('reviews', {})
        previous = get_review(ledger['reviews'], 'internalReview').copy()
        ledger['reviews']['internalReview'] = build_inter_review_agent_failed_review(
            previous,
            run_id=run_id,
            head_sha=head_sha,
            requested_at=now_iso,
            failed_at=failed_at,
            failure_class=failure_class,
            failure_summary=failure_summary,
            model=inter_review_agent_model,
            pending_summary=pending_summary,
            agent_name=internal_reviewer_agent_name,
            agent_role=agent_role,
        )
        ledger['internalReviewerModel'] = inter_review_agent_model
        ledger['workflowActors'] = workflow_actors_payload_fn(ledger.get("implementationActor") or {})
        save_ledger_fn(ledger)
        audit_inter_review_agent_transition_fn(previous, ledger['reviews']['internalReview'])
        raise
    completed_at = now_iso_fn()
    final_review = build_inter_review_agent_completed_review(
        result,
        run_id=run_id,
        head_sha=head_sha,
        started_at=now_iso,
        completed_at=completed_at,
        model=inter_review_agent_model,
        pending_summary=pending_summary,
        agent_name=internal_reviewer_agent_name,
        agent_role=agent_role,
    )
    ledger = load_ledger_fn()
    ledger.setdefault('reviews', {})
    previous = get_review(ledger['reviews'], 'internalReview').copy()
    ledger['reviews']['internalReview'] = final_review
    ledger['internalReviewerModel'] = inter_review_agent_model
    ledger['workflowActors'] = workflow_actors_payload_fn(ledger.get("implementationActor") or {})
    save_ledger_fn(ledger)
    audit_inter_review_agent_transition_fn(previous, final_review)
    after = reconcile_fn(fix_watchers=True)
    return {
        'dispatched': True,
        'headSha': head_sha,
        'internalReviewerModel': inter_review_agent_model,
        'review': final_review,
        'after': after,
    }


def run_tick_raw(
    *,
    reconcile_fn: Callable[..., dict[str, Any]],
    audit_fn: Callable[..., Any],
    dispatch_inter_review_agent_review_fn: Callable[[], dict[str, Any]],
    dispatch_implementation_turn_fn: Callable[[], dict[str, Any]],
    publish_ready_pr_fn: Callable[[], dict[str, Any]],
    push_pr_update_fn: Callable[[], dict[str, Any]],
    merge_and_promote_fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Adapter-owned implementation of the wrapper's ``tick_raw``.

    Reads the pre-tick status via ``reconcile_fn`` (which writes health), then
    dispatches at most one forward action. Each dispatcher is injected so the
    adapter owns the routing logic without taking a dependency on the wrapper's
    entrypoints directly.
    """
    before = reconcile_fn(fix_watchers=True)
    action = before.get('nextAction') or {'type': 'noop', 'reason': 'no-forward-action-needed'}
    executed: dict[str, Any] | None = None
    action_type = action.get('type')
    if action_type == 'run_internal_review':
        executed = dispatch_inter_review_agent_review_fn()
    elif action_type == 'dispatch_implementation_turn':
        executed = dispatch_implementation_turn_fn()
    elif action_type == 'publish_ready_pr':
        executed = publish_ready_pr_fn()
    elif action_type == 'push_pr_update':
        executed = push_pr_update_fn()
    elif action_type == 'merge_and_promote':
        executed = merge_and_promote_fn()
    if executed is None:
        after = reconcile_fn(fix_watchers=True)
    else:
        after = executed.get('after') or reconcile_fn(fix_watchers=True)
    audit_fn(
        'workflow-tick-action',
        f"Workflow tick chose action {action_type}",
        actionType=action_type,
        reason=action.get('reason'),
        issueNumber=action.get('issueNumber'),
        headSha=action.get('headSha'),
    )
    return {'before': before, 'action': action, 'executed': executed, 'after': after}
