from __future__ import annotations

import time
from pathlib import Path
from typing import Any


"""YoYoPod Core workflow orchestration (read-model + reconcile).

This module hosts the canonical ``build_status_raw`` and ``reconcile`` bodies
that used to live in the legacy ``scripts/yoyopod_workflow.py`` wrapper. Each
function takes a ``workspace`` accessor — a module-or-namespace-like object
exposing the workspace-scoped primitives (``load_jobs``, ``load_ledger``,
``audit``, ``_run``, path constants, review/session helpers, etc.). Passing
the legacy wrapper module directly is the intended usage, which keeps the
retired wrapper script as a thin workspace-config binding.
"""


def emit_operator_attention_transition(
    *,
    previous_state,
    new_state,
    reasons,
    audit_fn,
):
    """Emit a semantic audit event when a lane crosses the operator-attention
    boundary. No-op when the state did not change.

    The comment publisher (Task 1.7) listens for ``operator-attention-transition``
    and ``operator-attention-recovered`` to render the sticky ⚠️ header (and to
    clear it on recovery).
    """
    OAS = "operator_attention_required"
    if previous_state == new_state:
        return
    if new_state == OAS:
        reason = "; ".join(reasons) if reasons else "operator-attention-required"
        audit_fn(
            "operator-attention-transition",
            "Lane entered operator-attention state",
            reason=reason,
            previousState=previous_state,
        )
    elif previous_state == OAS:
        audit_fn(
            "operator-attention-recovered",
            "Lane recovered from operator-attention state",
            newState=new_state,
        )


def build_status_raw(workspace: Any) -> dict[str, Any]:
    """Build the raw read-model payload for the workflow.

    This is the adapter-owned version of the wrapper's ``build_status_raw``.
    All workspace-scoped primitives and constants are looked up on
    ``workspace`` so the wrapper does not have to re-host the logic itself.
    """
    ws = workspace
    jobs_payload = ws.load_jobs()
    ledger = ws.load_ledger()
    job_map = ws._job_lookup(jobs_payload)
    active_lane = ws._get_active_lane()

    if active_lane and active_lane.get("error") == "multiple-active-lanes":
        open_pr = None
        lane_issue_number = None
        active_lane_error = active_lane
        active_lane = None
    else:
        active_lane_error = None
        lane_issue_number = active_lane["number"] if active_lane else None
        open_pr = ws._get_open_pr_for_issue(lane_issue_number)
    publish_ready = ws._pr_ready_for_review(open_pr)

    existing_reviews = ledger.get("reviews") or {}
    if publish_ready:
        codex_cloud = ws._fetch_codex_cloud_review(
            open_pr.get("number") if open_pr else None,
            open_pr.get("headRefOid") if open_pr else None,
            existing_reviews.get("codexCloud"),
        )
    elif open_pr and open_pr.get("isDraft"):
        codex_cloud = ws._codex_cloud_placeholder(
            required=False,
            status="not_started",
            summary="Draft PR is not ready for Codex Cloud review yet.",
        )
    else:
        codex_cloud = ws._codex_cloud_placeholder(
            required=False,
            status="not_started",
            summary="Codex Cloud review starts only after the PR is published ready-for-review.",
        )

    implementation = ws._normalize_implementation_for_active_lane(
        ledger.get("implementation") or {},
        active_lane=active_lane,
        open_pr=open_pr,
    )
    worktree = Path(implementation["worktree"]) if implementation.get("worktree") else None
    worktree_facts = ws._load_adapter_status_module().collect_worktree_repo_facts(worktree, run=ws._run)
    worktree_branch = worktree_facts.get("branch")
    worktree_commits_ahead = worktree_facts.get("commitsAhead")
    local_head_sha = worktree_facts.get("localHeadSha")
    active_session_meta = ws._load_implementation_session_meta(implementation, worktree)
    active_session_health = ws._assess_codex_session_health(active_session_meta, worktree)
    session_action_recommendation = ws.decide_lane_session_action(
        active_session_health=active_session_health,
        implementation_status=implementation.get("status") or ledger.get("workflowState"),
        has_open_pr=bool(open_pr),
    )
    lane_state = ws._load_optional_json(ws._lane_state_path(worktree)) or {}
    nudge_preflight = ws.should_nudge_session(
        lane_state=lane_state,
        session_action=session_action_recommendation,
        current_head_sha=(open_pr or {}).get("headRefOid"),
    )
    acp_session_strategy = ws.build_acp_session_strategy(
        implementation_session_key=implementation.get("session"),
        session_action=session_action_recommendation,
        lane_state=lane_state,
        session_runtime=implementation.get("sessionRuntime"),
        session_name=implementation.get("sessionName"),
        resume_session_id=implementation.get("resumeSessionId"),
    )

    managed_job_names = ws._managed_job_names()
    legacy_watchdog_present = ws._legacy_watchdog_present(managed_job_names=managed_job_names, job_map=job_map)
    _core_job_status = ws._load_adapter_health_module().compute_core_job_status(
        managed_job_names,
        job_map,
        summarize_job_fn=ws._summarize_job,
    )
    missing_core_jobs = _core_job_status["missing"]
    disabled_core_jobs = _core_job_status["disabled"]
    stale_core_jobs = _core_job_status["stale"]
    detailed_jobs_from_adapter = _core_job_status["detailed"]
    broken_watchers = ws._collect_broken_watchers(jobs_payload)

    ledger_active = ledger.get("activeLane")
    ledger_active_number = ledger_active.get("number") if isinstance(ledger_active, dict) else ledger_active
    ledger_state = ledger.get("workflowState")
    ledger_idle = ledger.get("workflowIdle")

    local_candidate_exists = ws._has_local_candidate(local_head_sha, worktree_commits_ahead)
    existing_claude_review = existing_reviews.get("claudeCode")
    single_pass_claude_gate_satisfied = ws._single_pass_local_claude_gate_satisfied(existing_claude_review, local_head_sha, lane_state)
    effective_workflow_state = ledger_state
    effective_review_state = ledger.get("reviewState")
    if not publish_ready and local_candidate_exists and single_pass_claude_gate_satisfied:
        effective_workflow_state = "ready_to_publish"
        effective_review_state = "ready_to_publish"
    reviews = ws._load_adapter_reviews_module().build_reviews_block(
        existing_reviews=existing_reviews,
        codex_cloud=codex_cloud,
        publish_ready=publish_ready,
        local_head_sha=local_head_sha,
        local_candidate_exists=local_candidate_exists,
        inter_review_agent_model=ws.INTER_REVIEW_AGENT_MODEL,
        internal_reviewer_agent_name=ws.INTERNAL_REVIEWER_AGENT_NAME,
        external_reviewer_agent_name=ws.EXTERNAL_REVIEWER_AGENT_NAME,
        advisory_reviewer_agent_name=ws.ADVISORY_REVIEWER_AGENT_NAME,
        now_iso=ws._now_iso(),
        claude_seed_fn=(lambda existing, head_sha, now_iso: ws._normalize_local_inter_review_agent_seed(
            existing,
            local_head_sha=head_sha,
            now_iso=now_iso,
        )),
    )
    review_loop_state, merge_blockers, merge_blocked = ws._determine_review_loop_state(reviews, has_pr=publish_ready)
    repair_head_sha = local_head_sha if not publish_ready else (open_pr or {}).get("headRefOid")
    effective_repair_brief = ws._synthesize_repair_brief(reviews, repair_head_sha)
    if publish_ready:
        effective_workflow_state, effective_review_state = ws._load_adapter_status_module().resolve_publish_ready_workflow_state(
            review_loop_state,
            merge_blocked=merge_blocked,
        )
    claude_preflight = ws._inter_review_agent_preflight(
        active_lane=active_lane,
        open_pr=open_pr,
        workflow_state=effective_workflow_state,
        pr_ledger=ledger.get("pr") or {},
        inter_review_agent_review=reviews["claudeCode"],
        inter_review_agent_job=ws._summarize_job(job_map.get(ws.WORKFLOW_WATCHDOG_JOB_NAME)),
        local_head_sha=local_head_sha,
        implementation_commits_ahead=worktree_commits_ahead,
        single_pass_gate_satisfied=single_pass_claude_gate_satisfied,
    )
    if (
        ws.INTER_REVIEW_AGENT_FREEZE_CODER_WHILE_RUNNING
        and ws._inter_review_agent_is_running_on_head(reviews.get("claudeCode"), local_head_sha)
    ):
        session_action_recommendation = {
            "action": "no-action",
            "reason": "claude-review-running",
            "sessionName": (active_session_health or {}).get("sessionName"),
        }
        nudge_preflight = {"shouldNudge": False, "reason": "claude-review-running"}
        acp_session_strategy = ws.build_acp_session_strategy(
            implementation_session_key=implementation.get("session"),
            session_action=session_action_recommendation,
            lane_state=lane_state,
            session_runtime=implementation.get("sessionRuntime"),
            session_name=implementation.get("sessionName"),
            resume_session_id=implementation.get("resumeSessionId"),
        )

    drift = ws._load_adapter_status_module().compute_ledger_drift(
        active_lane=active_lane,
        lane_issue_number=lane_issue_number,
        ledger_active=ledger_active,
        ledger_active_number=ledger_active_number,
        ledger_idle=ledger_idle,
        ledger_state=ledger_state,
        open_pr=open_pr,
        pr_ledger=ledger.get("pr") or {},
        review_loop_state=review_loop_state,
        ledger_review_loop_state=ledger.get("reviewLoopState"),
    )

    stale_lane_reasons: list[str] = []
    if active_lane and not open_pr:
        latest_progress_epoch = ws._latest_lane_progress_epoch(implementation, lane_state)
        if latest_progress_epoch and time.time() - latest_progress_epoch > (ws.LANE_NO_PR_MINUTES * 60):
            stale_lane_reasons.append("active lane has no PR and implementation state is stale")
    if publish_ready and review_loop_state == "awaiting_reviews" and open_pr and codex_cloud.get("reviewedHeadSha") in {None, ""}:
        stale_lane_reasons.append("published PR is waiting for review artifacts")
    if publish_ready and ledger_state in {"under_review", "revalidating", "findings_open", "rework_required"} and open_pr and (ledger.get("pr") or {}).get("headSha") in {None, ""}:
        stale_lane_reasons.append("review state lacks current PR head SHA")
    stale_lane_reasons.extend(ws._lane_operator_attention_reasons(lane_state))

    detailed_jobs = detailed_jobs_from_adapter if detailed_jobs_from_adapter is not None else {
        name: ws._summarize_job(job_map.get(name)) for name in managed_job_names
    }
    health = ws._load_adapter_health_module().compute_health(
        engine_owner=ws.ENGINE_OWNER,
        active_lane_error=active_lane_error,
        missing_core_jobs=missing_core_jobs,
        disabled_core_jobs=disabled_core_jobs,
        stale_core_jobs=stale_core_jobs,
        drift=drift,
        stale_lane_reasons=stale_lane_reasons,
        broken_watchers=broken_watchers,
    )

    preferred_codex_model = ws._codex_model_for_issue(
        active_lane,
        lane_state=implementation.get("laneState"),
        workflow_state=ledger.get("workflowState"),
        reviews=reviews,
    )
    legacy_watchdog_mode = ws._legacy_watchdog_mode(managed_job_names=managed_job_names, job_map=job_map)
    adapter_status = ws._load_adapter_status_module()
    publish_status = adapter_status.derive_publish_status(open_pr, publish_ready=publish_ready)

    _lane_state_path_obj = ws._lane_state_path(worktree)
    _lane_memo_path_obj = ws._lane_memo_path(worktree)
    lane_state_path_str = str(_lane_state_path_obj) if _lane_state_path_obj else None
    lane_memo_path_str = str(_lane_memo_path_obj) if _lane_memo_path_obj else None
    next_action = ws._derive_next_action(
        active_lane=active_lane,
        open_pr=open_pr,
        health=health,
        implementation={
            **implementation,
            "localHeadSha": local_head_sha,
            "publishStatus": publish_status,
            "sessionActionRecommendation": session_action_recommendation,
            "laneState": lane_state,
            "laneMemoPath": lane_memo_path_str,
            "laneStatePath": lane_state_path_str,
        },
        reviews=reviews,
        repair_brief=effective_repair_brief,
        preflight={"claudeReview": claude_preflight},
        workflow_state=effective_workflow_state,
        review_loop_state=review_loop_state,
        merge_blocked=merge_blocked,
    )
    implementation_with_lane_state = {**implementation, "laneState": lane_state}
    return adapter_status.assemble_status_payload(
        now_iso=ws._now_iso(),
        engine_owner=ws.ENGINE_OWNER,
        repo_path=str(ws.REPO_PATH),
        ledger_path=str(ws.LEDGER_PATH),
        health_path=str(ws.HEALTH_PATH),
        audit_log_path=str(ws.AUDIT_LOG_PATH),
        active_lane=active_lane,
        active_lane_error=active_lane_error,
        open_pr=open_pr,
        ledger=ledger,
        ledger_active_number=ledger_active_number,
        effective_workflow_state=effective_workflow_state,
        effective_review_state=effective_review_state,
        ledger_idle=ledger_idle,
        effective_repair_brief=effective_repair_brief,
        implementation=implementation_with_lane_state,
        local_head_sha=local_head_sha,
        worktree_branch=worktree_branch,
        worktree_commits_ahead=worktree_commits_ahead,
        lane_state_path_str=lane_state_path_str,
        lane_memo_path_str=lane_memo_path_str,
        active_session_health=active_session_health,
        session_action_recommendation=session_action_recommendation,
        nudge_preflight=nudge_preflight,
        acp_session_strategy=acp_session_strategy,
        publish_status=publish_status,
        preferred_codex_model=preferred_codex_model,
        coder_agent_name=ws._coder_agent_name_for_model(implementation.get("codexModel") or preferred_codex_model),
        actor_labels=ws._actor_labels_payload(implementation.get("codexModel") or preferred_codex_model),
        reviews=reviews,
        review_loop_state=review_loop_state,
        merge_blocked=merge_blocked,
        merge_blockers=merge_blockers,
        claude_preflight=claude_preflight,
        detailed_jobs=detailed_jobs,
        hermes_job_names=ws.HERMES_JOB_NAMES,
        missing_core_jobs=missing_core_jobs,
        disabled_core_jobs=disabled_core_jobs,
        stale_core_jobs=stale_core_jobs,
        broken_watchers=broken_watchers,
        drift=drift,
        stale_lane_reasons=stale_lane_reasons,
        health=health,
        legacy_watchdog_present=legacy_watchdog_present,
        legacy_watchdog_mode=legacy_watchdog_mode,
        inter_review_agent_model=ws.INTER_REVIEW_AGENT_MODEL,
        next_action=next_action,
    )


def reconcile(workspace: Any, *, write_health: bool = True, fix_watchers: bool = False) -> dict[str, Any]:
    """Reconcile stale ledger state. Adapter-owned port of the wrapper's ``reconcile``."""
    ws = workspace
    status = ws.build_status()
    ledger = ws.load_ledger()
    previous_workflow_state = ledger.get("workflowState") or "unknown"
    previous_claude_review = ((ledger.get("reviews") or {}).get("claudeCode") or {}).copy()
    jobs_payload = ws.load_jobs()
    changed = {"ledger": False, "jobs": False}

    active_lane = status.get("activeLane")
    open_pr = status.get("openPr")
    impl = status.get("implementation") or {}
    publish_ready = ws._pr_ready_for_review(open_pr)
    reviews = status.get("reviews") or {}
    review_loop_state = status.get("derivedReviewLoopState")
    merge_blockers = status.get("derivedMergeBlockers") or []
    merge_blocked = bool(status.get("derivedMergeBlocked"))
    claude_preflight = ((status.get("preflight") or {}).get("interReviewAgent") or (status.get("preflight") or {}).get("claudeReview") or {})
    now_iso = status["updatedAt"]
    codex_model = impl.get("codexModel") or ws._codex_model_for_issue(
        active_lane,
        lane_state=impl.get("laneState"),
        workflow_state=(status.get("ledger") or {}).get("workflowState"),
        reviews=reviews,
    )

    if (
        open_pr
        and open_pr.get("isDraft")
        and ws._current_inter_review_agent_matches_local_head(reviews.get("claudeCode"), impl.get("localHeadSha"))
        and (reviews.get("claudeCode") or {}).get("verdict") == "PASS_CLEAN"
        and ws._mark_pr_ready_for_review(open_pr.get("number"))
    ):
        ws.audit("reconcile", "Marked draft PR ready for review after clean pre-publish Claude gate", prNumber=open_pr.get("number"), headSha=impl.get("localHeadSha"))
        status = ws.build_status()
        active_lane = status.get("activeLane")
        open_pr = status.get("openPr")
        impl = status.get("implementation") or {}
        reviews = status.get("reviews") or {}
        review_loop_state = status.get("derivedReviewLoopState")
        merge_blockers = status.get("derivedMergeBlockers") or []
        merge_blocked = bool(status.get("derivedMergeBlocked"))
        claude_preflight = ((status.get("preflight") or {}).get("interReviewAgent") or (status.get("preflight") or {}).get("claudeReview") or {})
        now_iso = status["updatedAt"]
        codex_model = impl.get("codexModel") or ws._codex_model_for_issue(
            active_lane,
            lane_state=impl.get("laneState"),
            workflow_state=(status.get("ledger") or {}).get("workflowState"),
            reviews=reviews,
        )
        changed["ledger"] = True

    latest_progress = ws._derive_latest_progress(
        implementation=impl,
        ledger=status.get("ledger") or {},
        open_pr=open_pr,
        reviews=reviews,
        review_loop_state=review_loop_state,
        merge_blocked=merge_blocked,
        now_iso=now_iso,
    )

    adapter_status = ws._load_adapter_status_module()
    adapter_status.apply_ledger_reviews_and_header(
        ledger,
        review_loop_state=review_loop_state,
        codex_model=codex_model,
        inter_review_agent_model=ws.INTER_REVIEW_AGENT_MODEL,
        actor_labels=ws._actor_labels_payload(codex_model),
        reviews=reviews,
    )
    ws._audit_inter_review_agent_transition(previous_claude_review, reviews["claudeCode"])
    adapter_status.apply_ledger_implementation_merge(
        ledger,
        active_lane=active_lane,
        open_pr=open_pr,
        implementation=impl,
        codex_model_fallback=ws._codex_model_for_issue(
            active_lane,
            lane_state=impl.get("laneState"),
            workflow_state=ledger.get("workflowState"),
            reviews=reviews,
        ),
        coder_agent_name=ws._coder_agent_name_for_model(impl.get("codexModel") or codex_model),
    )

    if active_lane:
        operator_attention_needed = ws._lane_operator_attention_needed(impl.get("laneState"))
        repair_head_sha = impl.get("localHeadSha") if not publish_ready else (open_pr or {}).get("headRefOid")
        repair_brief = ws._synthesize_repair_brief(reviews, repair_head_sha)
        adapter_status.apply_active_lane_ledger_transition(
            ledger,
            active_lane=active_lane,
            open_pr=open_pr,
            implementation=impl,
            reviews=reviews,
            previous_claude_review=previous_claude_review,
            publish_ready=publish_ready,
            review_loop_state=review_loop_state,
            merge_blocked=merge_blocked,
            merge_blockers=merge_blockers,
            now_iso=now_iso,
            repair_brief=repair_brief,
            operator_attention_needed=operator_attention_needed,
            pass_with_findings_reviews=ws.CLAUDE_PASS_WITH_FINDINGS_REVIEWS,
        )
        new_workflow_state = ledger.get("workflowState") or "unknown"
        emit_operator_attention_transition(
            previous_state=previous_workflow_state,
            new_state=new_workflow_state,
            reasons=ws._lane_operator_attention_reasons(impl.get("laneState")),
            audit_fn=ws.audit,
        )
        changed["ledger"] = True
    elif status.get("activeLaneError"):
        adapter_status.apply_active_lane_error_ledger_transition(
            ledger,
            active_lane_error=status["activeLaneError"],
            now_iso=now_iso,
        )
        changed["ledger"] = True
    else:
        adapter_status.apply_idle_ledger_transition(ledger, now_iso=now_iso)
        changed["ledger"] = True

    if fix_watchers:
        disabled_watchers = ws._load_adapter_health_module().disable_broken_watchers(
            jobs_payload,
            issue_watcher_re=ws.ISSUE_WATCHER_RE,
            now_ms_fn=ws._now_ms,
        )
        if disabled_watchers:
            changed["jobs"] = True
            ws.audit("reconcile", "Disabled broken issue watcher jobs", disabledWatchers=disabled_watchers)

    if claude_preflight.get("wakeSuggested"):
        touched = ws._wake_jobs(jobs_payload, [ws.WORKFLOW_WATCHDOG_JOB_NAME])
        if touched:
            changed["jobs"] = True
            ws.audit(
                "reconcile",
                "Woke workflow watchdog from cheap Claude preflight",
                jobNames=touched,
                headSha=claude_preflight.get("currentHeadSha"),
            )

    resolved_codex_threads = ws._resolve_codex_superseded_threads(
        reviews.get("codexCloud") or {},
        current_head_sha=(open_pr or {}).get("headRefOid"),
    )
    if resolved_codex_threads:
        resolution_event = {
            "at": now_iso,
            "headSha": (open_pr or {}).get("headRefOid"),
            "prNumber": (open_pr or {}).get("number"),
            "signal": ((reviews.get("codexCloud") or {}).get("prBodySignal") or {}).get("content"),
            "threadIds": resolved_codex_threads,
        }
        ledger["codexCloudAutoResolved"] = resolution_event
        ws.audit(
            "reconcile",
            "Resolved superseded Codex Cloud review threads after clean PR-body signal",
            threadIds=resolved_codex_threads,
            activeLane=(active_lane or {}).get("number"),
            prNumber=(open_pr or {}).get("number"),
            headSha=(open_pr or {}).get("headRefOid"),
            signal=((reviews.get("codexCloud") or {}).get("prBodySignal") or {}).get("content"),
        )
        status = ws.build_status()
        reviews = status.get("reviews") or reviews
        review_loop_state = status.get("derivedReviewLoopState") or review_loop_state
        merge_blockers = status.get("derivedMergeBlockers") or merge_blockers
        merge_blocked = bool(status.get("derivedMergeBlocked"))
        ledger["reviewLoopState"] = review_loop_state
        ledger["reviews"]["codexCloud"] = reviews["codexCloud"]
        ledger["codexCloudAutoResolved"] = resolution_event
        if ledger.get("pr"):
            ledger["pr"]["mergeBlocked"] = merge_blocked
            ledger["pr"]["mergeBlockers"] = merge_blockers
        changed["ledger"] = True

    lane_state_payload = ws.write_lane_state(
        worktree=Path(impl["worktree"]) if impl.get("worktree") else None,
        issue=active_lane,
        open_pr=open_pr,
        implementation={**impl, **(status.get("implementation") or {})},
        reviews=reviews,
        repair_brief=ledger.get("repairBrief"),
        now_iso=now_iso,
        latest_progress=latest_progress,
        preflight=status.get("preflight") or {},
    )
    if (status.get("implementation") or {}).get("sessionActionRecommendation", {}).get("action") == "poke-session":
        nudge_preflight = ((status.get("implementation") or {}).get("sessionNudgePreflight") or {})
        if nudge_preflight.get("shouldNudge"):
            nudge_payload = ws.build_session_nudge_payload(
                session_action=(status.get("implementation") or {}).get("sessionActionRecommendation") or {},
                issue=active_lane,
                open_pr=open_pr,
                lane_memo_path=(status.get("implementation") or {}).get("laneMemoPath"),
                now_iso=now_iso,
            )
            nudge_payload["nudgeMethod"] = ((status.get("implementation") or {}).get("acpSessionStrategy") or {}).get("nudgeTool")
            ws.record_session_nudge(worktree=Path(impl["worktree"]) if impl.get("worktree") else None, payload=nudge_payload)
            ledger["sessionNudge"] = nudge_payload
            changed["ledger"] = True
            ws.audit(
                "reconcile",
                "Recorded explicit same-session nudge request",
                sessionName=nudge_payload.get("sessionName"),
                headSha=nudge_payload.get("headSha"),
                issueNumber=nudge_payload.get("issueNumber"),
                reason=nudge_payload.get("reason"),
            )
    lane_memo_body = ws.write_lane_memo(
        worktree=Path(impl["worktree"]) if impl.get("worktree") else None,
        issue=active_lane,
        branch=(open_pr or {}).get("headRefName") or impl.get("branch"),
        open_pr=open_pr,
        repair_brief=ledger.get("repairBrief"),
        latest_progress=latest_progress,
        validation_summary=ws._summarize_validation(ledger),
        acp_strategy=(status.get("implementation") or {}).get("acpSessionStrategy"),
    )
    if lane_state_payload is not None or lane_memo_body is not None:
        changed["ledger"] = True

    repair_handoff_result, repair_handoff_changed = ws._maybe_dispatch_repair_handoff(
        status=status,
        ledger=ledger,
        now_iso=now_iso,
        codex_model=codex_model,
        lane_state_override=lane_state_payload or (status.get("implementation") or {}).get("laneState"),
    )
    if repair_handoff_changed:
        changed["ledger"] = True

    if changed["ledger"]:
        ws.save_ledger(ledger)
    if changed["jobs"]:
        ws.save_jobs(jobs_payload)

    reconciled = ws.build_status()
    reconciled["actionsTaken"] = changed
    if write_health:
        ws._write_json(ws.HEALTH_PATH, reconciled)
    ws.audit(
        "reconcile",
        f"Workflow reconciled to health={reconciled['health']} reviewLoopState={reconciled['derivedReviewLoopState']}",
        health=reconciled["health"],
        activeLane=(reconciled.get("activeLane") or {}).get("number"),
        reviewLoopState=reconciled.get("derivedReviewLoopState"),
    )
    return reconciled
