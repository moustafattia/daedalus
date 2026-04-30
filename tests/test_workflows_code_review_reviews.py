import importlib.util
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_should_dispatch_claude_repair_handoff_when_local_review_is_actionable_and_routable():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    result = reviews_module.should_dispatch_claude_repair_handoff(
        lane_state={},
        session_action={"action": "continue-session", "sessionName": "lane-224"},
        internal_review={
            "reviewScope": "local-prepublish",
            "status": "completed",
            "verdict": "REWORK",
            "reviewedHeadSha": "head123",
            "updatedAt": "2026-04-22T01:00:00Z",
        },
        repair_brief={"forHeadSha": "head123", "mustFix": [{"summary": "Fix this"}]},
        workflow_state="claude_prepublish_findings",
        current_head_sha="head123",
        has_open_pr=False,
    )

    assert result == {"shouldDispatch": True, "reason": None}


def test_should_dispatch_claude_repair_handoff_rejects_duplicate_handoff_for_same_review():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    result = reviews_module.should_dispatch_claude_repair_handoff(
        lane_state={"sessionControl": {"lastClaudeRepairHandoff": {"sessionName": "lane-224", "headSha": "head123", "reviewedAt": "2026-04-22T01:00:00Z"}}},
        session_action={"action": "continue-session", "sessionName": "lane-224"},
        internal_review={
            "reviewScope": "local-prepublish",
            "status": "completed",
            "verdict": "REWORK",
            "reviewedHeadSha": "head123",
            "updatedAt": "2026-04-22T01:00:00Z",
        },
        repair_brief={"forHeadSha": "head123", "mustFix": [{"summary": "Fix this"}]},
        workflow_state="claude_prepublish_findings",
        current_head_sha="head123",
        has_open_pr=False,
    )

    assert result == {"shouldDispatch": False, "reason": "repair-handoff-already-sent-for-review"}


def test_should_dispatch_codex_cloud_repair_handoff_when_postpublish_review_is_actionable_and_routable():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    result = reviews_module.should_dispatch_external_review_repair_handoff(
        lane_state={},
        session_action={"action": "poke-session", "sessionName": "lane-224"},
        external_review={
            "reviewScope": "postpublish-pr",
            "status": "completed",
            "verdict": "PASS_WITH_FINDINGS",
            "reviewedHeadSha": "prsha",
            "updatedAt": "2026-04-22T01:00:00Z",
        },
        repair_brief={"forHeadSha": "prsha", "shouldFix": [{"summary": "Tighten this"}]},
        workflow_state="findings_open",
        current_head_sha="prsha",
        has_open_pr=True,
    )

    assert result == {"shouldDispatch": True, "reason": None}


def test_pr_ready_for_review_and_has_local_candidate_are_simple_truth_checks():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    assert reviews_module.pr_ready_for_review({"number": 1, "isDraft": False}) is True
    assert reviews_module.pr_ready_for_review({"number": 1, "isDraft": True}) is False
    assert reviews_module.pr_ready_for_review(None) is False

    assert reviews_module.has_local_candidate("abc123", 2) is True
    assert reviews_module.has_local_candidate("abc123", 0) is False
    assert reviews_module.has_local_candidate(None, 2) is False


def test_current_inter_review_agent_matches_local_head_requires_local_prepublish_and_same_head():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    assert reviews_module.current_inter_review_agent_matches_local_head(
        {"reviewScope": "local-prepublish", "reviewedHeadSha": "abc123"},
        "abc123",
    ) is True
    assert reviews_module.current_inter_review_agent_matches_local_head(
        {"reviewScope": "postpublish-pr", "reviewedHeadSha": "abc123"},
        "abc123",
    ) is False
    assert reviews_module.current_inter_review_agent_matches_local_head(
        {"reviewScope": "local-prepublish", "reviewedHeadSha": "zzz999"},
        "abc123",
    ) is False


def test_local_inter_review_agent_review_count_increments_only_for_new_completed_local_review_head():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    incremented = reviews_module.local_inter_review_agent_review_count(
        {"reviewScope": "local-prepublish", "status": "completed", "reviewedHeadSha": "new-head"},
        {"review": {"localClaudeReviewCount": 1, "lastClaudeReviewedHeadSha": "old-head"}},
    )
    unchanged = reviews_module.local_inter_review_agent_review_count(
        {"reviewScope": "local-prepublish", "status": "completed", "reviewedHeadSha": "same-head"},
        {"review": {"localClaudeReviewCount": 1, "lastClaudeReviewedHeadSha": "same-head"}},
    )

    assert incremented == 2
    assert unchanged == 1


def test_single_pass_local_claude_gate_satisfied_handles_pass_clean_and_pass_with_findings_threshold():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    pass_clean = reviews_module.single_pass_local_claude_gate_satisfied(
        {"reviewScope": "local-prepublish", "status": "completed", "reviewedHeadSha": "abc123", "verdict": "PASS_CLEAN"},
        "abc123",
        {"review": {}},
        pass_with_findings_reviews=1,
    )
    pass_with_findings = reviews_module.single_pass_local_claude_gate_satisfied(
        {"reviewScope": "local-prepublish", "status": "completed", "reviewedHeadSha": "old-head", "verdict": "PASS_WITH_FINDINGS"},
        "new-head",
        {"review": {"localClaudeReviewCount": 0, "lastClaudeReviewedHeadSha": "older-head", "lastClaudeVerdict": "PASS_WITH_FINDINGS"}},
        pass_with_findings_reviews=1,
    )
    rework = reviews_module.single_pass_local_claude_gate_satisfied(
        {"reviewScope": "local-prepublish", "status": "completed", "reviewedHeadSha": "abc123", "verdict": "REWORK"},
        "abc123",
        {"review": {}},
        pass_with_findings_reviews=1,
    )

    assert pass_clean is True
    assert pass_with_findings is True
    assert rework is False


def test_single_pass_local_claude_gate_satisfied_handles_new_key_shape():
    """Phase D-5: post-migration lane-state with the new key shape works."""
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    pass_clean = reviews_module.single_pass_local_claude_gate_satisfied(
        {"reviewScope": "local-prepublish", "status": "completed", "reviewedHeadSha": "abc", "verdict": "PASS_CLEAN"},
        "abc",
        {"review": {"lastInternalReviewedHeadSha": "abc", "lastInternalVerdict": "PASS_CLEAN"}},
        pass_with_findings_reviews=1,
    )
    pass_with_findings = reviews_module.single_pass_local_claude_gate_satisfied(
        {"reviewScope": "local-prepublish", "status": "completed", "reviewedHeadSha": "old-head", "verdict": "PASS_WITH_FINDINGS"},
        "new-head",
        {"review": {"localInternalReviewCount": 0, "lastInternalReviewedHeadSha": "older-head", "lastInternalVerdict": "PASS_WITH_FINDINGS"}},
        pass_with_findings_reviews=1,
    )

    assert pass_clean is True
    assert pass_with_findings is True


def test_determine_review_loop_state_handles_pending_findings_and_rework():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    awaiting = reviews_module.determine_review_loop_state(
        {
            "claudeCode": {"required": True, "verdict": None},
            "codexCloud": {"required": False, "verdict": None},
        },
        has_pr=True,
    )
    findings = reviews_module.determine_review_loop_state(
        {
            "claudeCode": {"required": True, "verdict": "PASS_WITH_FINDINGS"},
        },
        has_pr=False,
    )
    rework = reviews_module.determine_review_loop_state(
        {
            "claudeCode": {"required": True, "verdict": "REWORK"},
        },
        has_pr=True,
    )

    assert awaiting == ("awaiting_reviews", ["claudeCode-pending"], True)
    assert findings == ("findings_open", ["claudeCode-open-findings"], True)
    assert rework == ("rework_required", ["claudeCode-rework"], True)


def test_inter_review_agent_preflight_allows_clean_local_prepublish_run_and_suggests_wake_when_job_far_out():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    result = reviews_module.inter_review_agent_preflight(
        active_lane={"number": 224},
        open_pr=None,
        workflow_state="awaiting_claude_prepublish",
        pr_ledger={},
        inter_review_agent_review={"status": "pending", "runId": "run-1"},
        inter_review_agent_job={"nextRunAtMs": 400_000},
        local_head_sha="abc123",
        implementation_commits_ahead=2,
        single_pass_gate_satisfied=False,
        pr_ready_for_review_fn=lambda pr: bool(pr) and not bool((pr or {}).get("isDraft")),
        has_local_candidate_fn=lambda head, ahead: bool(head) and int(ahead or 0) > 0,
        checks_acceptable_fn=lambda _pr: True,
        target_head_fn=lambda review: review.get("requestedHeadSha"),
        started_epoch_fn=lambda _review: None,
        now_ms_fn=lambda: 0,
        now_epoch_fn=lambda: 0,
        timeout_seconds=1200,
        request_cooldown_seconds=1200,
    )

    assert result["shouldRun"] is True
    assert result["wakeSuggested"] is True
    assert result["reasons"] == []
    assert result["currentHeadSha"] == "abc123"
    assert result["reviewScope"] == "local-prepublish"


def test_inter_review_agent_preflight_blocks_running_current_head_and_nonready_states():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    result = reviews_module.inter_review_agent_preflight(
        active_lane=None,
        open_pr=None,
        workflow_state="idle",
        pr_ledger={},
        inter_review_agent_review={
            "status": "running",
            "runId": "run-1",
            "requestedHeadSha": "abc123",
            "requestedAt": "2026-04-23T00:09:30Z",
        },
        inter_review_agent_job={"nextRunAtMs": 1000},
        local_head_sha="abc123",
        implementation_commits_ahead=0,
        single_pass_gate_satisfied=True,
        pr_ready_for_review_fn=lambda pr: bool(pr) and not bool((pr or {}).get("isDraft")),
        has_local_candidate_fn=lambda head, ahead: bool(head) and int(ahead or 0) > 0,
        checks_acceptable_fn=lambda _pr: True,
        target_head_fn=lambda review: review.get("requestedHeadSha"),
        started_epoch_fn=lambda _review: 100,
        now_ms_fn=lambda: 0,
        now_epoch_fn=lambda: 200,
        timeout_seconds=1200,
        request_cooldown_seconds=1200,
    )

    assert result["shouldRun"] is False
    assert result["wakeSuggested"] is False
    assert "no-active-lane" in result["reasons"]
    assert "no-local-head-candidate" in result["reasons"]
    assert "workflow-not-awaiting-local-claude" in result["reasons"]
    assert "single-pass-claude-already-satisfied" in result["reasons"]
    assert "claude-review-running-current-head" in result["reasons"]
    assert "claude-review-request-recent" in result["reasons"]


def test_normalize_review_fills_defaults_and_preserves_known_fields():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    result = reviews_module.normalize_review(
        {"status": "completed", "requestedHeadSha": "abc123", "blockingFindings": ["fix it"], "openFindingCount": "2"},
        pending_summary="pending summary",
        required=False,
        agent_name="Internal_Reviewer_Agent",
        agent_role="internal_reviewer_agent",
    )

    assert result["required"] is False
    assert result["status"] == "completed"
    assert result["targetHeadSha"] == "abc123"
    assert result["blockingFindings"] == ["fix it"]
    assert result["openFindingCount"] == 2
    assert result["summary"] == "pending summary"
    assert result["agentName"] == "Internal_Reviewer_Agent"
    assert result["agentRole"] == "internal_reviewer_agent"


def test_inter_review_agent_seed_helpers_cover_pending_superseded_and_timed_out_states():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    pending = reviews_module.inter_review_agent_pending_seed(model="claude-sonnet-4-6")
    superseded = reviews_module.inter_review_agent_superseded(
        {"requestedHeadSha": "old-head"},
        superseded_by_head_sha="new-head",
        now_iso="2026-04-23T00:10:00Z",
        target_head_fn=lambda review: review.get("requestedHeadSha"),
    )
    timed_out = reviews_module.inter_review_agent_timed_out(
        {"requestedHeadSha": "old-head"},
        now_iso="2026-04-23T00:10:00Z",
        target_head_fn=lambda review: review.get("requestedHeadSha"),
        started_epoch_fn=lambda _review: 100,
        now_epoch_fn=lambda: 200,
    )

    assert pending == {"model": "claude-sonnet-4-6"}
    assert superseded["status"] == "superseded"
    assert superseded["supersededByHeadSha"] == "new-head"
    assert timed_out["status"] == "timed_out"
    assert timed_out["failureClass"] == "review_timeout"
    assert "after 100s" in timed_out["summary"]


def test_normalize_local_inter_review_agent_seed_handles_running_current_timed_out_and_superseded_cases():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    current = reviews_module.normalize_local_inter_review_agent_seed(
        {"reviewScope": "local-prepublish", "status": "completed", "reviewedHeadSha": "abc123"},
        local_head_sha="abc123",
        now_iso="2026-04-23T00:10:00Z",
        model="claude-sonnet-4-6",
        timeout_seconds=1200,
        target_head_fn=lambda review: review.get("requestedHeadSha") or review.get("reviewedHeadSha"),
        started_epoch_fn=lambda _review: None,
        now_epoch_fn=lambda: 200,
        current_head_match_fn=reviews_module.current_inter_review_agent_matches_local_head,
    )
    timed_out = reviews_module.normalize_local_inter_review_agent_seed(
        {"reviewScope": "local-prepublish", "status": "running", "requestedHeadSha": "abc123"},
        local_head_sha="abc123",
        now_iso="2026-04-23T00:10:00Z",
        model="claude-sonnet-4-6",
        timeout_seconds=50,
        target_head_fn=lambda review: review.get("requestedHeadSha") or review.get("reviewedHeadSha"),
        started_epoch_fn=lambda _review: 100,
        now_epoch_fn=lambda: 200,
        current_head_match_fn=reviews_module.current_inter_review_agent_matches_local_head,
    )
    superseded = reviews_module.normalize_local_inter_review_agent_seed(
        {"reviewScope": "local-prepublish", "status": "running", "requestedHeadSha": "old-head"},
        local_head_sha="new-head",
        now_iso="2026-04-23T00:10:00Z",
        model="claude-sonnet-4-6",
        timeout_seconds=500,
        target_head_fn=lambda review: review.get("requestedHeadSha") or review.get("reviewedHeadSha"),
        started_epoch_fn=lambda _review: 150,
        now_epoch_fn=lambda: 200,
        current_head_match_fn=reviews_module.current_inter_review_agent_matches_local_head,
    )
    pending = reviews_module.normalize_local_inter_review_agent_seed(
        None,
        local_head_sha="abc123",
        now_iso="2026-04-23T00:10:00Z",
        model="claude-sonnet-4-6",
        timeout_seconds=500,
        target_head_fn=lambda review: review.get("requestedHeadSha") or review.get("reviewedHeadSha"),
        started_epoch_fn=lambda _review: None,
        now_epoch_fn=lambda: 200,
        current_head_match_fn=reviews_module.current_inter_review_agent_matches_local_head,
    )

    assert current["model"] == "claude-sonnet-4-6"
    assert timed_out["status"] == "timed_out"
    assert superseded["status"] == "superseded"
    assert pending == {"model": "claude-sonnet-4-6"}


def test_review_bucket_and_codex_cloud_placeholder_cover_pending_findings_clean_and_blocking_cases():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    assert reviews_module.review_bucket({"verdict": "REWORK"}) == "blocking"
    assert reviews_module.review_bucket({"verdict": "PASS_WITH_FINDINGS"}) == "findings"
    assert reviews_module.review_bucket({"verdict": "PASS_CLEAN"}) == "clean"
    assert reviews_module.review_bucket({"verdict": None}) == "pending"

    placeholder = reviews_module.external_review_placeholder(
        required=False,
        status="not_started",
        summary="Codex Cloud review starts only after publish.",
        normalize_review_fn=lambda review, **kwargs: {**review, **kwargs},
        agent_name="External_Reviewer_Agent",
        agent_role="external_reviewer_agent",
    )

    assert placeholder["status"] == "not_started"
    assert placeholder["summary"] == "Codex Cloud review starts only after publish."
    assert placeholder["reviewScope"] == "postpublish-pr"
    assert placeholder["required"] is False
    assert placeholder["agent_name"] == "External_Reviewer_Agent"
    assert placeholder["agent_role"] == "external_reviewer_agent"


def test_inter_review_agent_review_builders_shape_running_failed_and_completed_reviews():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    running = reviews_module.build_inter_review_agent_running_review(
        {"requestedAt": "older", "foo": "bar"},
        run_id="run-1",
        head_sha="abc123",
        now_iso="2026-04-23T00:10:00Z",
        model="claude-sonnet-4-6",
        pending_summary="Pending local unpublished branch review before publication.",
        agent_name="Internal_Reviewer_Agent",
        agent_role="internal_reviewer_agent",
    )
    failed = reviews_module.build_inter_review_agent_failed_review(
        {"runId": "run-1", "requestedAt": "2026-04-23T00:10:00Z", "requestedHeadSha": "abc123"},
        run_id="run-1",
        head_sha="abc123",
        requested_at="2026-04-23T00:10:00Z",
        failed_at="2026-04-23T00:20:00Z",
        failure_class="review_wrapper_failed",
        failure_summary="boom",
        model="claude-sonnet-4-6",
        pending_summary="Pending local unpublished branch review before publication.",
        agent_name="Internal_Reviewer_Agent",
        agent_role="internal_reviewer_agent",
    )
    completed = reviews_module.build_inter_review_agent_completed_review(
        {"verdict": "PASS_WITH_FINDINGS", "summary": "done", "blockingFindings": ["a"], "majorConcerns": ["b"], "minorSuggestions": ["c"], "requiredNextAction": "fix"},
        run_id="run-1",
        head_sha="abc123",
        started_at="2026-04-23T00:10:00Z",
        completed_at="2026-04-23T00:30:00Z",
        model="claude-sonnet-4-6",
        pending_summary="Pending local unpublished branch review before publication.",
        agent_name="Internal_Reviewer_Agent",
        agent_role="internal_reviewer_agent",
    )

    assert running["status"] == "running"
    assert running["requestedHeadSha"] == "abc123"
    assert running["summary"].startswith("Running local unpublished branch review")
    assert failed["status"] == "failed"
    assert failed["failureClass"] == "review_wrapper_failed"
    assert failed["summary"] == "boom"
    assert completed["status"] == "completed"
    assert completed["openFindingCount"] == 3
    assert completed["allFindingsClosed"] is False


def test_repair_handoff_payload_builders_shape_claude_and_codex_payloads():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    claude_payload = reviews_module.build_claude_repair_handoff_payload(
        session_action={"sessionName": "lane-224"},
        issue={"number": 224, "title": "Issue 224"},
        internal_review={"reviewedHeadSha": "abc123", "updatedAt": "2026-04-23T00:10:00Z", "reviewScope": "local-prepublish", "verdict": "REWORK"},
        repair_brief={"mustFix": [{"summary": "a"}], "shouldFix": [{"summary": "b"}, {"summary": "c"}]},
        lane_memo_path="/tmp/memo.md",
        lane_state_path="/tmp/state.json",
        now_iso="2026-04-23T00:20:00Z",
    )
    codex_payload = reviews_module.build_external_review_repair_handoff_payload(
        session_action={"sessionName": "lane-224"},
        issue={"number": 224, "title": "Issue 224"},
        external_review={"reviewedHeadSha": "def456", "updatedAt": "2026-04-23T00:11:00Z", "reviewScope": "postpublish-pr", "verdict": "PASS_WITH_FINDINGS"},
        repair_brief={"mustFix": [], "shouldFix": [{"summary": "x"}]},
        lane_memo_path="/tmp/memo.md",
        lane_state_path="/tmp/state.json",
        now_iso="2026-04-23T00:21:00Z",
    )

    assert claude_payload["action"] == "claude-repair-handoff"
    assert claude_payload["mustFixCount"] == 1
    assert claude_payload["shouldFixCount"] == 2
    assert claude_payload["headSha"] == "abc123"
    assert codex_payload["action"] == "codex-cloud-repair-handoff"
    assert codex_payload["mustFixCount"] == 0
    assert codex_payload["shouldFixCount"] == 1
    assert codex_payload["headSha"] == "def456"


def test_record_repair_handoff_helpers_store_payload_under_session_control(tmp_path):
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    seen = {}
    lane_state_path = tmp_path / ".lane-state.json"

    def fake_load(path):
        seen.setdefault("loaded", []).append(path)
        return {"schemaVersion": 1}

    def fake_write(path, payload):
        seen.setdefault("written", []).append((path, payload))

    claude_state = reviews_module.record_claude_repair_handoff(
        worktree=tmp_path,
        payload={"sessionName": "lane-224", "headSha": "abc123"},
        lane_state_path_fn=lambda worktree: worktree / ".lane-state.json",
        load_optional_json_fn=fake_load,
        write_json_fn=fake_write,
    )
    codex_state = reviews_module.record_external_review_repair_handoff(
        worktree=tmp_path,
        payload={"sessionName": "lane-224", "headSha": "def456"},
        lane_state_path_fn=lambda worktree: worktree / ".lane-state.json",
        load_optional_json_fn=fake_load,
        write_json_fn=fake_write,
    )

    assert claude_state["sessionControl"]["lastClaudeRepairHandoff"]["headSha"] == "abc123"
    assert codex_state["sessionControl"]["lastCodexCloudRepairHandoff"]["headSha"] == "def456"
    assert seen["loaded"] == [lane_state_path, lane_state_path]
    assert seen["written"][0][0] == lane_state_path
    assert seen["written"][1][0] == lane_state_path


def test_inter_review_agent_outcome_helpers_cover_target_head_payload_and_failure_classification():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    review = {"requestedHeadSha": "req-1", "targetHeadSha": "target-1", "reviewScope": "local-prepublish", "status": "running"}
    payload = reviews_module.extract_inter_review_agent_payload(
        '{"structured_output":{"verdict":"REWORK","summary":"Fix it","blockingFindings":[],"majorConcerns":[],"minorSuggestions":[],"requiredNextAction":null}}'
    )
    exc = subprocess.CalledProcessError(
        1,
        ["claude", "--print"],
        output='{"subtype":"error_max_turns","num_turns":5,"errors":["hit max turns"]}',
        stderr="",
    )

    assert reviews_module.inter_review_agent_target_head(review) == "target-1"
    assert reviews_module.inter_review_agent_started_epoch({"requestedAt": "1970-01-01T00:01:40Z"}, iso_to_epoch_fn=lambda value: 100 if value else None) == 100
    assert reviews_module.inter_review_agent_is_running_on_head(review, "target-1") is True
    assert payload["verdict"] == "REWORK"
    assert payload["summary"] == "Fix it"
    assert reviews_module.classify_inter_review_agent_failure_text("maximum number of turns reached") == "max_turns_exhausted"
    assert reviews_module.inter_review_agent_failure_class(exc) == "max_turns_exhausted"
    assert reviews_module.inter_review_agent_failure_message(exc) == "Internal review agent CLI failed (error_max_turns): turns=5: hit max turns"


def test_classify_lane_failure_covers_clean_session_review_and_preflight_paths():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    clean = reviews_module.classify_lane_failure(
        implementation={},
        reviews={"externalReview": {"reviewScope": "postpublish-pr", "status": "completed", "verdict": "PASS_CLEAN", "openFindingCount": 0}},
        preflight={},
    )
    session_failure = reviews_module.classify_lane_failure(
        implementation={"sessionActionRecommendation": {"reason": "stale-session"}},
        reviews={},
        preflight={},
    )
    claude_failed = reviews_module.classify_lane_failure(
        implementation={},
        reviews={"internalReview": {"status": "failed", "failureClass": "transport_failed"}},
        preflight={},
    )
    findings_open = reviews_module.classify_lane_failure(
        implementation={},
        reviews={"externalReview": {"required": True, "openFindingCount": 2, "verdict": "PASS_WITH_FINDINGS"}},
        preflight={},
    )
    preflight_blocked = reviews_module.classify_lane_failure(
        implementation={},
        reviews={},
        preflight={"claudeReview": {"reasons": ["checks-not-acceptable"]}},
    )

    assert clean == {"failureClass": None, "detail": None}
    assert session_failure == {"failureClass": "session_stale", "detail": "stale-session"}
    assert claude_failed == {"failureClass": "claude_review_failed", "detail": "transport_failed"}
    assert findings_open == {"failureClass": "codex_cloud_findings_open", "detail": "PASS_WITH_FINDINGS"}
    assert preflight_blocked == {"failureClass": "claude_preflight_blocked", "detail": "checks-not-acceptable"}


def test_codex_cloud_review_shaping_helpers_cover_thread_mapping_findings_pending_and_clean():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    superseded_thread = reviews_module.build_external_review_thread(
        node={"id": "thread-1", "path": "app.py", "line": 12, "isResolved": False, "isOutdated": False},
        comment={"url": "https://example.com/thread-1", "createdAt": "2026-04-23T00:00:00Z"},
        severity="major",
        summary="stale finding",
        pr_signal={"state": "clean", "createdAt": "2026-04-23T00:10:00Z", "content": "+1", "user": "codex-cloud"},
        signal_epoch=200,
        comment_epoch=100,
    )
    blocking_thread = reviews_module.build_external_review_thread(
        node={"id": "thread-2", "path": "core.py", "line": 44, "isResolved": False, "isOutdated": False},
        comment={"url": "https://example.com/thread-2", "createdAt": "2026-04-23T00:05:00Z"},
        severity="critical",
        summary="real blocker",
        pr_signal={"state": "clean", "createdAt": "2026-04-23T00:10:00Z", "content": "+1", "user": "codex-cloud"},
        signal_epoch=200,
        comment_epoch=300,
    )
    findings = reviews_module.summarize_external_review(
        head_sha="head-1",
        latest_ts="2026-04-23T00:05:00Z",
        threads=[superseded_thread, blocking_thread],
        pr_signal={"state": "clean", "createdAt": "2026-04-23T00:10:00Z", "content": "+1", "user": "codex-cloud"},
        agent_name="External_Reviewer_Agent",
    )
    pending = reviews_module.summarize_external_review(
        head_sha="head-2",
        latest_ts=None,
        threads=[],
        pr_signal={"state": "pending", "createdAt": "2026-04-23T00:11:00Z", "content": "eyes", "user": "codex-cloud"},
        agent_name="External_Reviewer_Agent",
    )
    clean = reviews_module.summarize_external_review(
        head_sha="head-3",
        latest_ts="2026-04-23T00:06:00Z",
        threads=[superseded_thread],
        pr_signal={"state": "clean", "createdAt": "2026-04-23T00:10:00Z", "content": "+1", "user": "codex-cloud"},
        agent_name="External_Reviewer_Agent",
    )

    assert superseded_thread["supersededByPrSignal"] is True
    assert findings["verdict"] == "REWORK"
    assert findings["openFindingCount"] == 1
    assert findings["supersededOpenFindingCount"] == 1
    assert findings["blockingFindings"] == ["real blocker"]
    assert pending["status"] == "completed"
    assert pending["verdict"] is None
    assert "still reviewing the current PR head" in pending["summary"]
    assert clean["verdict"] == "PASS_CLEAN"
    assert clean["allFindingsClosed"] is True
    assert "lingering open thread(s) were superseded" in clean["summary"]


def test_synthesize_repair_brief_collects_required_codex_threads_and_local_findings():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    result = reviews_module.synthesize_repair_brief(
        {
            "externalReview": {
                "required": True,
                "threads": [
                    {"id": "t1", "status": "open", "isOutdated": False, "severity": "major", "summary": "Fix API edge", "path": "api.py", "line": 88, "url": "https://example.com/t1"},
                    {"id": "t2", "status": "resolved", "isOutdated": False, "severity": "critical", "summary": "Already closed"},
                ],
            },
            "claudeCode": {
                "required": True,
                "blockingFindings": ["Stop mutating shared state"],
                "majorConcerns": ["Tighten retry guard"],
                "minorSuggestions": ["Rename helper"],
            },
            "rockClaw": {"required": False, "blockingFindings": ["ignore advisory"]},
        },
        head_sha="head-123",
        now_iso="2026-04-23T00:20:00Z",
    )

    assert result["forHeadSha"] == "head-123"
    assert result["openedAt"] == "2026-04-23T00:20:00Z"
    assert result["rerunRequiredReviewers"] == ["externalReview", "claudeCode"]
    assert [item["id"] for item in result["mustFix"]] == ["externalReview:t1", "claudeCode:blocking:1", "claudeCode:major:1"]
    assert [item["summary"] for item in result["shouldFix"]] == ["Rename helper"]


def test_codex_review_mutation_helpers_cover_pr_ready_thread_resolution_and_superseded_cleanup():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    calls = []

    def fake_run(command, cwd=None):
        calls.append(("run", command, cwd))
        return {"ok": True}

    def fake_run_json(command, cwd=None):
        calls.append(("run_json", command, cwd))
        return {"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}}

    ready = reviews_module.mark_pr_ready_for_review(
        297,
        run_fn=fake_run,
        cwd="/tmp/repo",
        repo_slug="owner/repo",
    )
    unresolved_none = reviews_module.mark_pr_ready_for_review(
        None,
        run_fn=fake_run,
        cwd="/tmp/repo",
        repo_slug="owner/repo",
    )
    resolved = reviews_module.resolve_review_thread(
        "thread-123",
        run_json_fn=fake_run_json,
        cwd="/tmp/repo",
    )
    superseded = reviews_module.resolve_codex_superseded_threads(
        {
            "verdict": "PASS_CLEAN",
            "prBodySignal": {"state": "clean"},
            "reviewedHeadSha": "head-1",
            "threads": [
                {"id": "thread-123", "status": "open", "isOutdated": False, "supersededByPrSignal": True},
                {"id": "thread-456", "status": "open", "isOutdated": False, "supersededByPrSignal": False},
            ],
        },
        current_head_sha="head-1",
        resolve_review_thread_fn=lambda thread_id: thread_id == "thread-123",
    )

    assert ready is True
    assert unresolved_none is False
    assert resolved is True
    assert superseded == ["thread-123"]
    assert calls[0] == (
        "run",
        ["gh", "pr", "ready", "297", "--repo", "owner/repo"],
        "/tmp/repo",
    )
    assert calls[1] == (
        "run_json",
        [
            "gh",
            "api",
            "graphql",
            "-f",
            "query=mutation($threadId:ID!){ resolveReviewThread(input:{threadId:$threadId}) { thread { id isResolved } } }",
            "-f",
            "threadId=thread-123",
        ],
        "/tmp/repo",
    )


def test_fetch_codex_pr_body_signal_picks_latest_codex_reaction_and_maps_clean_vs_pending():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    def fake_run_json(command, cwd=None):
        assert command == [
            "gh",
            "api",
            "repos/owner/repo/issues/297/reactions",
            "-H",
            "Accept: application/vnd.github+json",
        ]
        assert cwd == "/tmp/repo"
        return [
            {"user": {"login": "random-user"}, "content": "+1", "created_at": "2026-04-23T00:00:00Z"},
            {"user": {"login": "codex-bot"}, "content": "eyes", "created_at": "2026-04-23T00:01:00Z"},
            {"user": {"login": "codex-bot"}, "content": "+1", "created_at": "2026-04-23T00:02:00Z"},
        ]

    result = reviews_module.fetch_external_review_pr_body_signal(
        297,
        run_json_fn=fake_run_json,
        cwd="/tmp/repo",
        codex_bot_logins={"codex-bot"},
        clean_reactions={"+1"},
        pending_reactions={"eyes"},
        repo_slug="owner/repo",
    )

    assert result == {
        "content": "+1",
        "state": "clean",
        "createdAt": "2026-04-23T00:02:00Z",
        "user": "codex-bot",
        "source": "pr-body-reaction",
    }


def test_fetch_codex_cloud_review_uses_cache_and_builds_from_graphql_threads():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    cached = reviews_module.fetch_external_review(
        297,
        current_head_sha="head-1",
        cached_review={"reviewedHeadSha": "head-1", "updatedAt": "cached-ts", "summary": "cached summary"},
        fetch_pr_body_signal_fn=lambda _pr_number: (_ for _ in ()).throw(AssertionError("cache hit should skip signal fetch")),
        run_json_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache hit should skip graphql fetch")),
        cwd="/tmp/repo",
        repo_slug="owner/repo",
        codex_bot_logins={"codex-bot"},
        cache_seconds=30,
        iso_to_epoch_fn=lambda value: 100 if value == "cached-ts" else None,
        now_epoch_fn=lambda: 120,
        extract_severity_fn=lambda _body: "minor",
        extract_summary_fn=lambda body: body,
        agent_name="External_Reviewer_Agent",
    )

    def fake_run_json(command, cwd=None):
        assert cwd == "/tmp/repo"
        assert command[:4] == ["gh", "api", "graphql", "-f"]
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "headRefOid": "head-2",
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "thread-1",
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "path": "a.py",
                                    "line": 10,
                                    "comments": {"nodes": [{"author": {"login": "codex-bot"}, "body": "sev0 blocker", "url": "https://example.com/1", "createdAt": "t1"}]},
                                },
                                {
                                    "id": "thread-2",
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "path": "b.py",
                                    "line": 20,
                                    "comments": {"nodes": [{"author": {"login": "codex-bot"}, "body": "minor note", "url": "https://example.com/2", "createdAt": "t2"}]},
                                },
                            ]
                        },
                    }
                }
            }
        }

    built = reviews_module.fetch_external_review(
        297,
        current_head_sha="head-other",
        cached_review=None,
        fetch_pr_body_signal_fn=lambda _pr_number: {"state": "clean", "createdAt": "signal", "content": "+1", "user": "codex-bot"},
        run_json_fn=fake_run_json,
        cwd="/tmp/repo",
        repo_slug="owner/repo",
        codex_bot_logins={"codex-bot"},
        cache_seconds=30,
        iso_to_epoch_fn=lambda value: {"signal": 50, "t1": 60, "t2": 40}.get(value),
        now_epoch_fn=lambda: 999,
        extract_severity_fn=lambda body: "critical" if "sev0" in body else "minor",
        extract_summary_fn=lambda body: body,
        agent_name="External_Reviewer_Agent",
    )

    assert cached["summary"] == "cached summary"
    assert cached["required"] is True
    assert built["reviewedHeadSha"] == "head-2"
    assert built["verdict"] == "REWORK"
    assert built["openFindingCount"] == 1
    assert built["supersededOpenFindingCount"] == 1
    assert built["blockingFindings"] == ["sev0 blocker"]


def test_codex_parsing_and_checks_helpers_cover_severity_summary_and_acceptability():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_test", "workflows/change_delivery/reviews.py")

    critical = reviews_module.extract_severity("**<sub><sub>![P0 Badge](x)</sub></sub>** boom")
    major = reviews_module.extract_severity("**<sub><sub>![P2 Badge](x)</sub></sub>** boom")
    minor = reviews_module.extract_severity("plain body")
    summary = reviews_module.extract_summary("**<sub><sub>badge</sub></sub>** **Tighten null checks**\nextra line")

    assert critical == "critical"
    assert major == "major"
    assert minor == "minor"
    assert summary == "Tighten null checks"
    assert reviews_module.checks_acceptable({"checks": {"status": "green"}}) is True
    assert reviews_module.checks_acceptable({"checks": {"status": "failing"}}) is False
    assert reviews_module.checks_acceptable(None) is False


def _repair_handoff_deps(captured: dict):
    def fake_run_acpx_prompt(*, worktree, session_name, prompt, codex_model):
        captured["run_acpx"] = {
            "worktree": str(worktree),
            "session_name": session_name,
            "prompt_len": len(prompt),
            "codex_model": codex_model,
        }
        return "ok"

    def fake_audit(action, summary, **extra):
        captured.setdefault("audit", []).append({"action": action, "summary": summary, **extra})

    return fake_run_acpx_prompt, fake_audit


def test_maybe_dispatch_repair_handoff_dispatches_claude_branch_when_routable(tmp_path):
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_mdrh", "workflows/change_delivery/reviews.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    captured: dict = {}
    run_acpx, audit = _repair_handoff_deps(captured)

    status = {
        "activeLane": {"number": 224, "title": "T"},
        "implementation": {
            "worktree": str(worktree),
            "localHeadSha": "head123",
            "laneState": {},
            "laneMemoPath": str(worktree / ".lane-memo.md"),
            "laneStatePath": str(worktree / ".lane-state.json"),
            "sessionActionRecommendation": {"action": "continue-session", "sessionName": "lane-224"},
        },
        "reviews": {
            "internalReview": {
                "reviewScope": "local-prepublish",
                "status": "completed",
                "verdict": "REWORK",
                "reviewedHeadSha": "head123",
                "updatedAt": "2026-04-22T00:00:00Z",
            },
        },
        "openPr": None,
        "ledger": {"workflowState": "claude_prepublish_findings"},
    }
    ledger = {
        "repairBrief": {"forHeadSha": "head123", "mustFix": [{"summary": "Fix"}], "shouldFix": []},
        "workflowState": "claude_prepublish_findings",
    }

    result, changed = reviews_module.maybe_dispatch_repair_handoff(
        status=status,
        ledger=ledger,
        now_iso="2026-04-22T00:05:00Z",
        codex_model="gpt-5.3-codex",
        run_prompt_fn=run_acpx,
        audit_fn=audit,
    )

    assert changed is True
    assert result["dispatched"] is True
    assert result["mode"] == "claude_repair_handoff"
    assert result["issueNumber"] == 224
    assert ledger["internalReviewRepairHandoff"]["sessionName"] == "lane-224"
    assert captured["run_acpx"]["session_name"] == "lane-224"
    assert captured["audit"][0]["action"] == "claude-repair-handoff-dispatched"
    # Record helper actually wrote .lane-state.json
    assert (worktree / ".lane-state.json").exists()


def test_maybe_dispatch_repair_handoff_dispatches_codex_cloud_branch_when_routable(tmp_path):
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_mdrh", "workflows/change_delivery/reviews.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    captured: dict = {}
    run_acpx, audit = _repair_handoff_deps(captured)

    status = {
        "activeLane": {"number": 224, "title": "T"},
        "implementation": {
            "worktree": str(worktree),
            "localHeadSha": "localsha",
            "laneState": {},
            "laneMemoPath": str(worktree / ".lane-memo.md"),
            "laneStatePath": str(worktree / ".lane-state.json"),
            "sessionActionRecommendation": {"action": "continue-session", "sessionName": "lane-224"},
        },
        "reviews": {
            "externalReview": {
                "reviewScope": "postpublish-pr",
                "status": "completed",
                "verdict": "REWORK",
                "reviewedHeadSha": "prsha",
                "updatedAt": "2026-04-22T00:00:00Z",
            },
        },
        "openPr": {"number": 301, "url": "https://example.test/pr/301", "headRefOid": "prsha"},
        "ledger": {"workflowState": "findings_open"},
    }
    ledger = {
        "repairBrief": {"forHeadSha": "prsha", "mustFix": [{"summary": "Fix"}], "shouldFix": []},
        "workflowState": "findings_open",
    }

    result, changed = reviews_module.maybe_dispatch_repair_handoff(
        status=status,
        ledger=ledger,
        now_iso="2026-04-22T00:05:00Z",
        codex_model="gpt-5.3-codex",
        run_prompt_fn=run_acpx,
        audit_fn=audit,
    )

    assert changed is True
    assert result["dispatched"] is True
    assert result["mode"] == "codex_cloud_repair_handoff"
    assert result["issueNumber"] == 224
    assert ledger["externalReviewRepairHandoff"]["sessionName"] == "lane-224"
    assert captured["audit"][0]["action"] == "codex-cloud-repair-handoff-dispatched"


def test_maybe_dispatch_repair_handoff_returns_noop_when_no_dispatch_branch_is_routable(tmp_path):
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_mdrh", "workflows/change_delivery/reviews.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    captured: dict = {}
    run_acpx, audit = _repair_handoff_deps(captured)

    status = {
        "activeLane": {"number": 224, "title": "T"},
        "implementation": {
            "worktree": str(worktree),
            "localHeadSha": "head123",
            "laneState": {},
            "laneMemoPath": None,
            "laneStatePath": None,
            "sessionActionRecommendation": {"action": "continue-session", "sessionName": "lane-224"},
        },
        "reviews": {},
        "openPr": None,
        "ledger": {"workflowState": "implementing_local"},
    }
    ledger = {"repairBrief": None, "workflowState": "implementing_local"}

    result, changed = reviews_module.maybe_dispatch_repair_handoff(
        status=status,
        ledger=ledger,
        now_iso="2026-04-22T00:05:00Z",
        codex_model="gpt-5.3-codex",
        run_prompt_fn=run_acpx,
        audit_fn=audit,
    )

    assert changed is False
    assert result["dispatched"] is False
    assert "repair-handoff-not-needed" in result["reason"]
    assert "run_acpx" not in captured


def test_maybe_dispatch_repair_handoff_short_circuits_when_no_active_lane(tmp_path):
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_mdrh", "workflows/change_delivery/reviews.py")
    captured: dict = {}
    run_acpx, audit = _repair_handoff_deps(captured)
    result, changed = reviews_module.maybe_dispatch_repair_handoff(
        status={"activeLane": None, "implementation": {}, "reviews": {}, "openPr": None, "ledger": {}},
        ledger={},
        now_iso="2026-04-22T00:00:00Z",
        codex_model=None,
        run_prompt_fn=run_acpx,
        audit_fn=audit,
    )
    assert changed is False
    assert result["reason"] == "no-active-lane"


def test_render_inter_review_agent_prompt_includes_head_and_scope(tmp_path):
    prompts_module = load_module("daedalus_workflows_change_delivery_prompts_irp", "workflows/change_delivery/prompts.py")

    prompt = prompts_module.render_inter_review_agent_prompt(
        issue={"number": 224, "title": "T", "url": "https://example.test/issue/224"},
        worktree=tmp_path,
        lane_memo_path=tmp_path / ".lane-memo.md",
        lane_state_path=tmp_path / ".lane-state.json",
        head_sha="abc123",
    )

    assert "Scope: local-prepublish only" in prompt
    assert "abc123" in prompt
    assert "#224" in prompt


class _FakeCalledProcessError(Exception):
    def __init__(self, *, returncode=1, stdout="", stderr=""):
        super().__init__(stderr or stdout or "fake CLI failed")
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_inter_review_agent_review_returns_parsed_payload_on_success():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_riar", "workflows/change_delivery/reviews.py")
    ok_payload = (
        '{"verdict":"PASS_CLEAN","summary":"fine","blockingFindings":[],'
        '"majorConcerns":[],"minorSuggestions":[],"requiredNextAction":null}'
    )

    class _Completed:
        stdout = ok_payload

    seen: dict = {}

    def fake_run(command, cwd=None):
        seen["command"] = command
        seen["cwd"] = str(cwd) if cwd else None
        return _Completed()

    result = reviews_module.run_inter_review_agent_review(
        issue={"number": 1, "title": "T", "url": "https://example.test/1"},
        worktree=Path("/tmp/fake"),
        lane_memo_path=None,
        lane_state_path=None,
        head_sha="abc123",
        run_fn=fake_run,
        inter_review_agent_model="claude-sonnet-4-6",
        inter_review_agent_max_turns=10,
        error_cls=_FakeCalledProcessError,
    )
    assert result["verdict"] == "PASS_CLEAN"
    assert seen["command"][0] == "claude"
    assert "--model" in seen["command"]


def test_run_inter_review_agent_review_extracts_payload_from_stdout_on_cli_error():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_riar", "workflows/change_delivery/reviews.py")
    crashed_payload = (
        '{"verdict":"REWORK","summary":"broken","blockingFindings":["x"],'
        '"majorConcerns":[],"minorSuggestions":[],"requiredNextAction":"fix"}'
    )

    def fake_run(command, cwd=None):
        raise _FakeCalledProcessError(stdout=crashed_payload, stderr="error_max_turns")

    result = reviews_module.run_inter_review_agent_review(
        issue={"number": 1, "title": "T", "url": "https://example.test/1"},
        worktree=Path("/tmp/fake"),
        lane_memo_path=None,
        lane_state_path=None,
        head_sha="abc123",
        run_fn=fake_run,
        inter_review_agent_model="claude-sonnet-4-6",
        inter_review_agent_max_turns=10,
        error_cls=_FakeCalledProcessError,
    )
    assert result["verdict"] == "REWORK"
    assert result["blockingFindings"] == ["x"]


def test_run_inter_review_agent_review_raises_review_error_when_stdout_unparsable():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_riar", "workflows/change_delivery/reviews.py")

    class _Completed:
        stdout = "not json at all"

    def fake_run(command, cwd=None):
        return _Completed()

    import pytest

    with pytest.raises(reviews_module.InterReviewAgentError) as exc_info:
        reviews_module.run_inter_review_agent_review(
            issue={"number": 1, "title": "T", "url": "https://example.test/1"},
            worktree=Path("/tmp/fake"),
            lane_memo_path=None,
            lane_state_path=None,
            head_sha="abc123",
            run_fn=fake_run,
            inter_review_agent_model="claude-sonnet-4-6",
            inter_review_agent_max_turns=10,
            error_cls=_FakeCalledProcessError,
        )
    assert "invalid structured output" in str(exc_info.value).lower()
    assert exc_info.value.failure_class == "invalid_structured_output"


def _capture_audit_fn():
    events: list[dict] = []

    def audit_fn(action, summary, **extra):
        events.append({"action": action, "summary": summary, **extra})

    return events, audit_fn


def test_audit_inter_review_agent_transition_emits_requested_event_when_head_changes():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_airat", "workflows/change_delivery/reviews.py")
    events, audit_fn = _capture_audit_fn()
    reviews_module.audit_inter_review_agent_transition(
        previous_review={},
        current_review={
            "requestedAt": "2026-04-23T00:00:00Z",
            "requestedHeadSha": "head123",
            "reviewScope": "local-prepublish",
            "status": "running",
            "runId": "run-1",
        },
        audit_fn=audit_fn,
        internal_reviewer_agent_name="Internal_Reviewer_Agent",
    )
    assert any(e["action"] == "claude-review-requested" for e in events)


def test_audit_inter_review_agent_transition_emits_completed_event_on_success():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_airat", "workflows/change_delivery/reviews.py")
    events, audit_fn = _capture_audit_fn()
    reviews_module.audit_inter_review_agent_transition(
        previous_review={"status": "running", "reviewedHeadSha": None, "requestedHeadSha": "head123"},
        current_review={
            "status": "completed",
            "reviewedHeadSha": "head123",
            "verdict": "PASS_CLEAN",
            "openFindingCount": 0,
            "reviewScope": "local-prepublish",
            "runId": "run-1",
            "updatedAt": "2026-04-23T00:05:00Z",
        },
        audit_fn=audit_fn,
        internal_reviewer_agent_name="Internal_Reviewer_Agent",
    )
    assert any(e["action"] == "claude-review-completed" and e["verdict"] == "PASS_CLEAN" for e in events)


def test_audit_inter_review_agent_transition_emits_failure_event_with_failure_class():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_airat", "workflows/change_delivery/reviews.py")
    events, audit_fn = _capture_audit_fn()
    reviews_module.audit_inter_review_agent_transition(
        previous_review={"status": "running"},
        current_review={
            "status": "failed",
            "failureClass": "max_turns_exhausted",
            "failureSummary": "CLI exhausted",
            "requestedHeadSha": "head123",
            "reviewScope": "local-prepublish",
            "runId": "run-1",
            "updatedAt": "2026-04-23T00:05:00Z",
        },
        audit_fn=audit_fn,
        internal_reviewer_agent_name="Internal_Reviewer_Agent",
    )
    assert any(e["action"] == "claude-review-failed" for e in events)


def test_build_reviews_block_routes_postpublish_defaults_when_pr_is_ready_for_review():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_brb", "workflows/change_delivery/reviews.py")

    reviews = reviews_module.build_reviews_block(
        existing_reviews={
            "rockClaw": None,
            "claudeCode": {"reviewedHeadSha": "prsha", "verdict": "PASS_CLEAN"},
            "codexCloud": {"status": "running", "reviewScope": "postpublish-pr"},
        },
        codex_cloud={"status": "running", "reviewScope": "postpublish-pr"},
        publish_ready=True,
        local_head_sha="prsha",
        local_candidate_exists=False,
        inter_review_agent_model="claude-sonnet-4-6",
        internal_reviewer_agent_name="Internal_Reviewer_Agent",
        external_reviewer_agent_name="External_Reviewer_Agent",
        advisory_reviewer_agent_name="Advisory_Reviewer_Agent",
        now_iso="2026-04-23T00:00:00Z",
    )
    assert reviews["internalReview"]["required"] is False
    assert reviews["externalReview"]["required"] is True
    assert reviews["externalReview"]["reviewScope"] == "postpublish-pr"
    assert reviews["externalReview"]["agentName"] == "External_Reviewer_Agent"
    assert reviews["rockClaw"]["required"] is False


def test_build_reviews_block_seeds_local_prepublish_for_draft_pr_state():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_brb", "workflows/change_delivery/reviews.py")

    def fake_seed(existing, local_head_sha, now_iso):
        return {
            "reviewScope": "local-prepublish",
            "status": "not_started",
            "requestedHeadSha": local_head_sha,
            "model": "claude-sonnet-4-6",
        }

    reviews = reviews_module.build_reviews_block(
        existing_reviews={"rockClaw": None, "claudeCode": None, "codexCloud": {}},
        codex_cloud={"required": False, "status": "not_started"},
        publish_ready=False,
        local_head_sha="localsha",
        local_candidate_exists=True,
        inter_review_agent_model="claude-sonnet-4-6",
        internal_reviewer_agent_name="Internal_Reviewer_Agent",
        external_reviewer_agent_name="External_Reviewer_Agent",
        advisory_reviewer_agent_name="Advisory_Reviewer_Agent",
        now_iso="2026-04-23T00:00:00Z",
        claude_seed_fn=fake_seed,
    )
    assert reviews["internalReview"]["required"] is True
    assert reviews["internalReview"]["reviewScope"] == "local-prepublish"
    assert reviews["externalReview"]["required"] is False
    assert reviews["externalReview"]["agentName"] == "External_Reviewer_Agent"


def test_audit_inter_review_agent_transition_is_noop_when_nothing_changed():
    reviews_module = load_module("daedalus_workflows_change_delivery_reviews_airat", "workflows/change_delivery/reviews.py")
    events, audit_fn = _capture_audit_fn()
    same = {
        "status": "completed",
        "requestedAt": "t",
        "requestedHeadSha": "h",
        "reviewedHeadSha": "h",
        "reviewScope": "local-prepublish",
        "verdict": "PASS_CLEAN",
        "updatedAt": "t",
        "runId": "r",
    }
    reviews_module.audit_inter_review_agent_transition(
        previous_review=same,
        current_review=same,
        audit_fn=audit_fn,
        internal_reviewer_agent_name="Internal_Reviewer_Agent",
    )
    assert events == []
