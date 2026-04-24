import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_derive_next_action_prefers_merge_for_clean_published_pr():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": {"number": 301, "headRefOid": "abc123"},
            "health": "healthy",
            "implementation": {"localHeadSha": "abc123", "sessionActionRecommendation": {"action": "continue-session"}, "laneState": {}},
            "reviews": {},
            "repairBrief": None,
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "under_review"},
            "derivedReviewLoopState": "clean",
            "derivedMergeBlocked": False,
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result["type"] == "merge_and_promote"
    assert result["reason"] == "published-pr-approved"


def test_derive_next_action_uses_fresh_session_noop_for_healthy_implementation_lane():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": None,
            "health": "healthy",
            "implementation": {
                "localHeadSha": "abc123",
                "activeSessionHealth": {"healthy": True},
                "sessionActionRecommendation": {"action": "continue-session", "sessionName": "lane-224"},
                "laneState": {},
            },
            "reviews": {},
            "repairBrief": None,
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "implementing_local"},
            "derivedReviewLoopState": "awaiting_reviews",
            "derivedMergeBlocked": False,
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result["type"] == "noop"
    assert result["reason"] == "fresh-session-still-working"


def test_derive_next_action_promotes_ready_local_branch_even_when_health_is_stale_lane():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": None,
            "health": "stale-lane",
            "implementation": {
                "localHeadSha": "abc123",
                "sessionActionRecommendation": {"action": "restart-session", "sessionName": None},
                "laneState": {},
            },
            "reviews": {},
            "repairBrief": None,
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "ready_to_publish"},
            "derivedReviewLoopState": "awaiting_reviews",
            "derivedMergeBlocked": False,
            "staleLaneReasons": ["active lane has no PR and implementation state is stale"],
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result["type"] == "publish_ready_pr"
    assert result["reason"] == "ready-local-branch-needs-pr"


def test_derive_next_action_pushes_pr_update_when_local_head_is_ahead_of_pr():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": {"number": 301, "headRefOid": "prsha"},
            "health": "healthy",
            "implementation": {
                "localHeadSha": "localsha",
                "commitsAhead": 1,
                "sessionActionRecommendation": {"action": "restart-session", "sessionName": "lane-224"},
                "laneState": {},
            },
            "reviews": {},
            "repairBrief": None,
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "findings_open"},
            "derivedReviewLoopState": "findings_open",
            "derivedMergeBlocked": True,
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result["type"] == "push_pr_update"
    assert result["reason"] == "local-repair-head-ahead-of-published-pr"
    assert result["prNumber"] == 301


def test_derive_next_action_dispatches_turn_when_no_progress_budget_is_reached():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": None,
            "health": "healthy",
            "implementation": {
                "localHeadSha": "localsha",
                "sessionActionRecommendation": {"action": "restart-session", "sessionName": "lane-224"},
                "laneState": {"budget": {"noProgressTicks": 3}},
            },
            "reviews": {},
            "repairBrief": None,
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "implementing_local"},
            "derivedReviewLoopState": "awaiting_reviews",
            "derivedMergeBlocked": False,
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result["type"] == "dispatch_codex_turn"
    assert result["mode"] == "implementation"
    assert result["reason"] == "no-progress-budget-reached"


def test_derive_next_action_dispatches_retry_turn_when_failure_budget_is_reached():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": {"number": 301, "headRefOid": "prsha"},
            "health": "healthy",
            "implementation": {
                "localHeadSha": "localsha",
                "sessionActionRecommendation": {"action": "restart-session", "sessionName": "lane-224"},
                "laneState": {"failure": {"retryCount": 3}},
            },
            "reviews": {},
            "repairBrief": None,
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "findings_open"},
            "derivedReviewLoopState": "findings_open",
            "derivedMergeBlocked": True,
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result["type"] == "dispatch_codex_turn"
    assert result["mode"] == "postpublish_repair"
    assert result["reason"] == "failure-retry-budget-reached"


def test_derive_next_action_dispatches_claude_repair_handoff_when_review_is_actionable_and_session_is_routable():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": None,
            "health": "healthy",
            "implementation": {
                "localHeadSha": "head123",
                "sessionActionRecommendation": {"action": "continue-session", "sessionName": "lane-224"},
                "laneState": {},
            },
            "reviews": {
                "claudeCode": {
                    "reviewScope": "local-prepublish",
                    "status": "completed",
                    "verdict": "REWORK",
                    "reviewedHeadSha": "head123",
                    "updatedAt": "2026-04-22T01:00:00Z",
                }
            },
            "repairBrief": {"forHeadSha": "head123", "mustFix": [{"summary": "Fix it"}]},
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "claude_prepublish_findings"},
            "derivedReviewLoopState": "findings_open",
            "derivedMergeBlocked": True,
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result["type"] == "dispatch_codex_turn"
    assert result["mode"] == "claude_repair_handoff"
    assert result["reason"] == "claude-findings-need-repair"


def test_derive_next_action_dispatches_codex_cloud_repair_handoff_when_review_is_actionable_and_session_is_routable():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": {"number": 301, "headRefOid": "prsha"},
            "health": "healthy",
            "implementation": {
                "localHeadSha": "localsha",
                "sessionActionRecommendation": {"action": "continue-session", "sessionName": "lane-224"},
                "laneState": {},
            },
            "reviews": {
                "codexCloud": {
                    "reviewScope": "postpublish-pr",
                    "status": "completed",
                    "verdict": "REWORK",
                    "reviewedHeadSha": "prsha",
                    "updatedAt": "2026-04-22T01:00:00Z",
                }
            },
            "repairBrief": {"forHeadSha": "prsha", "mustFix": [{"summary": "Fix it"}]},
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "findings_open"},
            "derivedReviewLoopState": "findings_open",
            "derivedMergeBlocked": True,
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result["type"] == "dispatch_codex_turn"
    assert result["mode"] == "codex_cloud_repair_handoff"
    assert result["reason"] == "codex-cloud-findings-need-repair"


def test_derive_next_action_dispatches_postpublish_repair_when_codex_findings_require_restart():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": {"number": 301, "headRefOid": "prsha"},
            "health": "healthy",
            "implementation": {
                "localHeadSha": "localsha",
                "sessionActionRecommendation": {"action": "restart-session", "sessionName": "lane-224"},
                "laneState": {},
            },
            "reviews": {
                "codexCloud": {
                    "reviewScope": "postpublish-pr",
                    "status": "completed",
                    "verdict": "REWORK",
                    "reviewedHeadSha": "prsha",
                }
            },
            "repairBrief": {"forHeadSha": "prsha", "mustFix": [{"summary": "Fix it"}]},
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "findings_open"},
            "derivedReviewLoopState": "findings_open",
            "derivedMergeBlocked": True,
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result["type"] == "dispatch_codex_turn"
    assert result["mode"] == "postpublish_repair"
    assert result["reason"] == "codex-cloud-findings-need-repair"


def test_derive_next_action_falls_back_to_wrapper_value_for_unhandled_cases():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": {"number": 301, "headRefOid": "prsha"},
            "health": "healthy",
            "implementation": {
                "localHeadSha": "localsha",
                "commitsAhead": 0,
                "sessionActionRecommendation": {"action": "no-action", "sessionName": "lane-224"},
                "laneState": {},
            },
            "reviews": {},
            "repairBrief": None,
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "findings_open"},
            "derivedReviewLoopState": "findings_open",
            "derivedMergeBlocked": True,
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result == {"type": "noop", "reason": "old-wrapper-value"}


def test_derive_next_action_short_circuits_when_claude_review_is_running_on_local_head():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": None,
            "health": "healthy",
            "implementation": {
                "localHeadSha": "head123",
                "sessionActionRecommendation": {"action": "continue-session", "sessionName": "lane-224"},
                "laneState": {},
            },
            "reviews": {
                "claudeCode": {
                    "reviewScope": "local-prepublish",
                    "status": "running",
                    "requestedHeadSha": "head123",
                }
            },
            "repairBrief": None,
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "implementing_local"},
            "derivedReviewLoopState": "awaiting_reviews",
            "derivedMergeBlocked": False,
            "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
        }
    )

    assert result["type"] == "noop"
    assert result["reason"] == "claude-review-running"
    assert result["issueNumber"] == 224
    assert result["headSha"] == "head123"


def test_derive_next_action_honors_configurable_no_progress_tick_budget():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    status = {
        "activeLane": {"number": 224},
        "openPr": None,
        "health": "healthy",
        "implementation": {
            "localHeadSha": "localsha",
            "sessionActionRecommendation": {"action": "restart-session", "sessionName": "lane-224"},
            "laneState": {"budget": {"noProgressTicks": 4}},
        },
        "reviews": {},
        "repairBrief": None,
        "preflight": {"claudeReview": {"shouldRun": False}},
        "ledger": {"workflowState": "implementing_local"},
        "derivedReviewLoopState": "awaiting_reviews",
        "derivedMergeBlocked": False,
        "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
    }

    assert workflow_module.derive_next_action(
        status, no_progress_tick_budget=5
    )["reason"] == "implementation-in-progress"
    assert workflow_module.derive_next_action(
        status, no_progress_tick_budget=4
    )["reason"] == "no-progress-budget-reached"


def test_derive_next_action_honors_configurable_failure_retry_budget():
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    status = {
        "activeLane": {"number": 224},
        "openPr": {"number": 301, "headRefOid": "prsha"},
        "health": "healthy",
        "implementation": {
            "localHeadSha": "localsha",
            "sessionActionRecommendation": {"action": "restart-session", "sessionName": "lane-224"},
            "laneState": {"failure": {"retryCount": 4}},
        },
        "reviews": {},
        "repairBrief": None,
        "preflight": {"claudeReview": {"shouldRun": False}},
        "ledger": {"workflowState": "findings_open"},
        "derivedReviewLoopState": "findings_open",
        "derivedMergeBlocked": True,
        "nextAction": {"type": "noop", "reason": "old-wrapper-value"},
    }

    assert workflow_module.derive_next_action(
        status, failure_retry_budget=5
    )["reason"] == "old-wrapper-value"
    assert workflow_module.derive_next_action(
        status, failure_retry_budget=4
    )["reason"] == "failure-retry-budget-reached"


def test_derive_next_action_uses_has_local_candidate_for_push_pr_update_check():
    # commitsAhead unset but local head still differs: has_local_candidate() returns False
    # because the default implementation requires commitsAhead > 0. The next-action logic
    # must not emit push_pr_update in that case.
    workflow_module = load_module("hermes_relay_yoyopod_core_workflow_test", "adapters/yoyopod_core/workflow.py")

    result = workflow_module.derive_next_action(
        {
            "activeLane": {"number": 224},
            "openPr": {"number": 301, "headRefOid": "prsha"},
            "health": "healthy",
            "implementation": {
                "localHeadSha": "localsha",
                "commitsAhead": None,
                "sessionActionRecommendation": {"action": "restart-session", "sessionName": "lane-224"},
                "laneState": {},
            },
            "reviews": {},
            "repairBrief": None,
            "preflight": {"claudeReview": {"shouldRun": False}},
            "ledger": {"workflowState": "findings_open"},
            "derivedReviewLoopState": "findings_open",
            "derivedMergeBlocked": True,
            "nextAction": {"type": "noop", "reason": "fallback-signal"},
        }
    )

    assert result["type"] == "noop"
    assert result["reason"] == "fallback-signal"
