import argparse
import importlib.util
import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def runtime_module():
    return load_module("daedalus_runtime_test", "runtime.py")


@pytest.fixture()
def tools_module():
    return load_module("daedalus_tools_test", "daedalus_cli.py")


@pytest.fixture()
def alerts_module():
    return load_module("daedalus_alerts_test", "alerts.py")


def test_iso_to_epoch_uses_utc_timegm(runtime_module, monkeypatch):
    monkeypatch.setattr(runtime_module.time, "mktime", lambda *_args, **_kwargs: 123456789)

    assert runtime_module._iso_to_epoch("1970-01-01T00:00:00Z") == 0
    assert runtime_module._iso_to_epoch("1970-01-01T00:00:01.000000Z") == 1


def test_init_daedalus_db_migrates_execution_control_to_clean_schema(runtime_module, tmp_path):
    workflow_root = tmp_path / "workflow"
    # Seed the legacy relay-era DB at its old path; the filesystem migrator
    # wired into init_daedalus_db will rename it to the daedalus path before
    # the SQL schema migration runs.
    legacy_db_path = workflow_root / "state" / "relay" / "relay.db"
    legacy_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(legacy_db_path)
    try:
        conn.execute(
            """
            CREATE TABLE ownership_controls (
              control_id TEXT PRIMARY KEY,
              desired_owner TEXT NOT NULL,
              active_execution_enabled INTEGER NOT NULL DEFAULT 0,
              require_watchdog_paused INTEGER NOT NULL DEFAULT 1,
              updated_at TEXT NOT NULL,
              metadata_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO ownership_controls (control_id, desired_owner, active_execution_enabled, require_watchdog_paused, updated_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
            ("primary", "relay", 1, 1, "2026-04-22T00:00:00Z", json.dumps({"source": "legacy"}, sort_keys=True)),
        )
        conn.commit()
    finally:
        conn.close()

    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")

    db_path = runtime_module._runtime_paths(workflow_root)["db_path"]
    conn = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        columns = [row[1] for row in conn.execute("PRAGMA table_info(execution_controls)").fetchall()]
        lane_action_columns = [row[1] for row in conn.execute("PRAGMA table_info(lane_actions)").fetchall()]
        runtime_row = conn.execute(
            "SELECT schema_version FROM daedalus_runtime WHERE runtime_id=?",
            ("daedalus",),
        ).fetchone()
        row = conn.execute(
            "SELECT control_id, active_execution_enabled, updated_at, metadata_json FROM execution_controls WHERE control_id=?",
            ("primary",),
        ).fetchone()
    finally:
        conn.close()

    assert "execution_controls" in tables
    assert "ownership_controls" not in tables
    assert columns == ["control_id", "active_execution_enabled", "updated_at", "metadata_json"]
    assert runtime_row[0] == 3
    assert "recovery_attempt_count" in lane_action_columns
    assert row[0] == "primary"
    assert row[1] == 1
    assert json.loads(row[3]) == {"source": "legacy"}


def test_init_daedalus_db_seeds_change_delivery_state_files(runtime_module, tmp_path):
    workflow_root = tmp_path / "workflow"

    result = runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")

    ledger_path = workflow_root / "memory" / "workflow-status.json"
    health_path = workflow_root / "memory" / "workflow-health.json"
    audit_path = workflow_root / "memory" / "workflow-audit.jsonl"
    scheduler_path = workflow_root / "memory" / "workflow-scheduler.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    scheduler = json.loads(scheduler_path.read_text(encoding="utf-8"))

    assert result["state_files"]["created"]["ledger"] is True
    assert ledger["workflowState"] == "idle"
    assert ledger["workflowIdle"] is True
    assert json.loads(health_path.read_text(encoding="utf-8"))["workflow"] == "change-delivery"
    assert audit_path.read_text(encoding="utf-8") == ""
    assert scheduler["workflow"] == "change-delivery"


def test_ingest_legacy_status_preserves_active_action_operator_attention(runtime_module, tmp_path):
    workflow_root = tmp_path / "workflow"
    paths = runtime_module._runtime_paths(workflow_root)
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")

    legacy_status = {
        "activeLane": {"number": 221, "url": "https://example.com/issues/221", "title": "Issue 221", "labels": []},
        "repo": "/tmp/repo",
        "implementation": {
            "worktree": "/tmp/issue-221",
            "branch": "codex/issue-221-test",
            "localHeadSha": "abc123",
            "laneState": {
                "implementation": {
                    "lastMeaningfulProgressAt": "2026-04-22T00:00:00Z",
                    "lastMeaningfulProgressKind": "implementing_local",
                },
                "pr": {"lastPublishedHeadSha": None},
            },
            "activeSessionHealth": {"healthy": True, "lastUsedAt": "2026-04-22T00:00:00Z"},
            "sessionActionRecommendation": {"action": "continue-session"},
        },
        "reviews": {},
        "ledger": {"workflowState": "implementing_local", "reviewState": "implementing_local", "repairBrief": None},
        "derivedReviewLoopState": "awaiting_reviews",
        "derivedMergeBlocked": False,
        "derivedMergeBlockers": [],
        "openPr": None,
        "activeLaneError": None,
        "staleLaneReasons": [],
        "nextAction": {"type": "dispatch_codex_turn", "reason": "implementation-in-progress"},
    }

    runtime_module.ingest_legacy_status(workflow_root=workflow_root, legacy_status=legacy_status, now_iso="2026-04-22T00:01:00Z")

    conn = runtime_module._connect(paths["db_path"])
    try:
        conn.execute(
            "UPDATE lanes SET operator_attention_required=1, operator_attention_reason=?, updated_at=? WHERE lane_id=?",
            ("active-action-failed:dispatch_implementation_turn", "2026-04-22T00:02:00Z", "lane:221"),
        )
        conn.commit()
    finally:
        conn.close()

    runtime_module.ingest_legacy_status(workflow_root=workflow_root, legacy_status=legacy_status, now_iso="2026-04-22T00:03:00Z")

    conn = sqlite3.connect(paths["db_path"])
    try:
        required, reason = conn.execute(
            "SELECT operator_attention_required, operator_attention_reason FROM lanes WHERE lane_id=?",
            ("lane:221",),
        ).fetchone()
    finally:
        conn.close()

    assert required == 1
    assert reason == "active-action-failed:dispatch_implementation_turn"


def test_ingest_legacy_status_uses_canonical_internal_review_for_active_request(runtime_module, tmp_path):
    workflow_root = tmp_path / "workflow"
    paths = runtime_module._runtime_paths(workflow_root)
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")
    legacy_status = {
        "activeLane": {"number": 221, "url": "https://example.com/issues/221", "title": "Issue 221", "labels": []},
        "repo": "/tmp/repo",
        "implementation": {
            "worktree": "/tmp/issue-221",
            "branch": "codex/issue-221-test",
            "localHeadSha": "abc123",
            "laneState": {"implementation": {}, "pr": {"lastPublishedHeadSha": None}},
            "activeSessionHealth": {"healthy": False, "lastUsedAt": None},
            "sessionActionRecommendation": {"action": "restart-session"},
        },
        "reviews": {
            "internalReview": {
                "required": True,
                "status": "pending",
                "reviewScope": "local-prepublish",
                "requestedHeadSha": "abc123",
                "model": "claude-sonnet-4-6",
            },
            "externalReview": {"required": False, "status": "not_started"},
        },
        "ledger": {"workflowState": "awaiting_claude_prepublish", "reviewState": "awaiting_claude_prepublish", "repairBrief": None},
        "derivedReviewLoopState": "awaiting_reviews",
        "derivedMergeBlocked": False,
        "derivedMergeBlockers": [],
        "openPr": None,
        "activeLaneError": None,
        "staleLaneReasons": [],
        "nextAction": {"type": "run_internal_review", "reason": "prepublish-claude-required"},
    }

    runtime_module.ingest_legacy_status(
        workflow_root=workflow_root,
        legacy_status=legacy_status,
        now_iso="2026-04-22T00:01:00Z",
    )
    actions = runtime_module.request_active_actions_for_lane(
        workflow_root=workflow_root,
        lane_id="lane:221",
        now_iso="2026-04-22T00:02:00Z",
    )

    conn = sqlite3.connect(paths["db_path"])
    try:
        lane_required = conn.execute(
            "SELECT required_internal_review FROM lanes WHERE lane_id=?",
            ("lane:221",),
        ).fetchone()[0]
        review_row = conn.execute(
            "SELECT status, backend_type, requested_head_sha FROM lane_reviews WHERE review_id=?",
            ("review:lane:221:internal",),
        ).fetchone()
    finally:
        conn.close()

    assert lane_required == 1
    assert review_row == ("pending", "internalReview", "abc123")
    assert actions[0]["action_type"] == "request_internal_review"


def test_ingest_legacy_status_ignores_old_review_status_keys(runtime_module, tmp_path):
    workflow_root = tmp_path / "workflow"
    paths = runtime_module._runtime_paths(workflow_root)
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")
    legacy_status = {
        "activeLane": {"number": 221, "url": "https://example.com/issues/221", "title": "Issue 221", "labels": []},
        "repo": "/tmp/repo",
        "implementation": {
            "worktree": "/tmp/issue-221",
            "branch": "codex/issue-221-test",
            "localHeadSha": "abc123",
            "laneState": {"implementation": {}, "pr": {"lastPublishedHeadSha": None}},
            "activeSessionHealth": {"healthy": False, "lastUsedAt": None},
            "sessionActionRecommendation": {"action": "restart-session"},
        },
        "reviews": {
            "claudeCode": {"required": True, "status": "pending", "requestedHeadSha": "abc123"},
            "codexCloud": {"required": True, "status": "pending", "requestedHeadSha": "abc123"},
        },
        "ledger": {"workflowState": "awaiting_claude_prepublish", "reviewState": "awaiting_claude_prepublish", "repairBrief": None},
        "derivedReviewLoopState": "awaiting_reviews",
        "derivedMergeBlocked": False,
        "derivedMergeBlockers": [],
        "openPr": None,
        "activeLaneError": None,
        "staleLaneReasons": [],
        "nextAction": {"type": "run_internal_review", "reason": "prepublish-claude-required"},
    }

    runtime_module.ingest_legacy_status(
        workflow_root=workflow_root,
        legacy_status=legacy_status,
        now_iso="2026-04-22T00:01:00Z",
    )

    conn = sqlite3.connect(paths["db_path"])
    try:
        required_flags = conn.execute(
            "SELECT required_internal_review, required_external_review FROM lanes WHERE lane_id=?",
            ("lane:221",),
        ).fetchone()
        review_count = conn.execute(
            "SELECT COUNT(*) FROM lane_reviews WHERE lane_id=?",
            ("lane:221",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert required_flags == (0, 0)
    assert review_count == 0


def test_derive_shadow_actions_requests_internal_review_without_review_row(runtime_module):
    actions = runtime_module.derive_shadow_actions_for_lane(
        lane_row={
            "lane_id": "lane:221",
            "issue_number": 221,
            "workflow_state": "awaiting_claude_prepublish",
            "required_internal_review": 1,
            "active_pr_number": None,
            "current_head_sha": "abc123",
        },
        reviews=[],
        actor_row={},
    )

    assert actions == [
        {
            "action_type": "request_internal_review",
            "lane_id": "lane:221",
            "issue_number": 221,
            "target_head_sha": "abc123",
            "reason": "internal-review-pending",
        }
    ]


def test_execute_requested_action_records_ambiguous_failure_without_name_error(runtime_module, tmp_path):
    workflow_root = tmp_path / "workflow"
    paths = runtime_module._runtime_paths(workflow_root)
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")
    now_iso = "2026-04-22T00:00:00Z"
    conn = runtime_module._connect(paths["db_path"])
    try:
        conn.execute(
            """
            INSERT INTO lanes (
              lane_id, issue_number, issue_url, issue_title, repo_path, actor_backend,
              lane_status, workflow_state, review_state, merge_state, active_actor_id,
              current_head_sha, required_internal_review, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lane:221",
                221,
                "https://example.com/issues/221",
                "Issue 221",
                "/tmp/repo",
                "acpx-codex",
                "active",
                "awaiting_claude_prepublish",
                "awaiting_reviews",
                "not_ready",
                "actor:1",
                "abc123",
                1,
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            """
            INSERT INTO lane_actions (
              action_id, lane_id, action_type, action_reason, action_mode, requested_by,
              target_head_sha, idempotency_key, status, requested_at, request_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "act:review:1",
                "lane:221",
                "request_internal_review",
                "internal-review-pending",
                "active",
                "Workflow_Orchestrator",
                "abc123",
                "active:request_internal_review:lane:221:abc123",
                "requested",
                now_iso,
                json.dumps({"action_type": "request_internal_review"}, sort_keys=True),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    def fail_review():
        raise RuntimeError("review failed")

    result = runtime_module.execute_requested_action(
        workflow_root=workflow_root,
        action_id="act:review:1",
        now_iso="2026-04-22T00:01:00Z",
        action_runners={"request_internal_review": fail_review},
    )

    assert result["executed"] is False
    assert result["reason"] == "execution-failed"
    assert result["error"] == "review failed"

    conn = sqlite3.connect(paths["db_path"])
    try:
        failure_row = conn.execute(
            "SELECT failure_class, analyst_recommended_action FROM failures WHERE failure_id=?",
            ("failure:act:review:1",),
        ).fetchone()
    finally:
        conn.close()
    events = [json.loads(line) for line in paths["event_log_path"].read_text(encoding="utf-8").splitlines()]
    analysis_requested = [
        event for event in events if event.get("event_type") == "daedalus.error_analysis_requested"
    ]

    assert failure_row == ("request_internal_review_blocked", "mark_operator_attention")
    assert analysis_requested[-1]["project_key"] == "workflow-example"


def test_request_active_actions_event_payload_uses_retry_count(runtime_module, tmp_path, monkeypatch):
    workflow_root = tmp_path / "workflow"
    paths = runtime_module._runtime_paths(workflow_root)
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")

    now_iso = "2026-04-22T00:00:00Z"
    conn = runtime_module._connect(paths["db_path"])
    try:
        conn.execute(
            """
            INSERT INTO lanes (
              lane_id, issue_number, issue_url, issue_title, repo_path, actor_backend,
              lane_status, workflow_state, review_state, merge_state, active_actor_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lane:221",
                221,
                "https://example.com/issues/221",
                "Issue 221",
                "/tmp/repo",
                "acpx-codex",
                "active",
                "ready_to_publish",
                "clean",
                "ready",
                None,
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            """
            INSERT INTO lane_actions (
              action_id, lane_id, action_type, action_reason, action_mode, requested_by,
              target_head_sha, idempotency_key, status, requested_at, failed_at,
              request_payload_json, retry_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "act:failed:1",
                "lane:221",
                "publish_pr",
                "older failure",
                "active",
                "Workflow_Orchestrator",
                "abc123",
                "active:publish_pr:lane:221:abc123",
                "failed",
                "2026-04-21T23:00:00Z",
                "2026-04-21T23:05:00Z",
                json.dumps({"action_type": "publish_pr"}, sort_keys=True),
                0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        runtime_module,
        "derive_shadow_actions_for_lane",
        lambda **_kwargs: [{"action_type": "publish_pr", "reason": "ready", "target_head_sha": "abc123"}],
    )

    actions = runtime_module.request_active_actions_for_lane(
        workflow_root=workflow_root,
        lane_id="lane:221",
        now_iso="2026-04-22T00:10:00Z",
    )

    assert actions[0]["retry_count"] == 1
    assert actions[0]["recovery_attempt_count"] == 1

    event_lines = paths["event_log_path"].read_text(encoding="utf-8").strip().splitlines()
    active_action_requested = [json.loads(line) for line in event_lines if json.loads(line).get("event_type") == "daedalus.active_action_requested"]
    assert active_action_requested[-1]["payload"]["retry_count"] == 1
    assert active_action_requested[-1]["payload"]["recovery_attempt_count"] == 1


def test_reap_stuck_dispatched_actions_marks_dispatcher_lost_and_queues_recovery(runtime_module, tmp_path):
    workflow_root = tmp_path / "workflow"
    paths = runtime_module._runtime_paths(workflow_root)
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")

    now_iso = "2026-04-22T01:00:00Z"
    conn = runtime_module._connect(paths["db_path"])
    try:
        conn.execute(
            """
            INSERT INTO lanes (
              lane_id, issue_number, issue_url, issue_title, repo_path, actor_backend,
              lane_status, workflow_state, review_state, merge_state, active_actor_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lane:221",
                221,
                "https://example.com/issues/221",
                "Issue 221",
                "/tmp/repo",
                "acpx-codex",
                "active",
                "ready_to_publish",
                "clean",
                "ready",
                "actor:1",
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            """
            INSERT INTO lane_actions (
              action_id, lane_id, action_type, action_reason, action_mode, requested_by,
              target_actor_role, target_actor_id, target_head_sha, idempotency_key, status,
              requested_at, dispatched_at, request_payload_json, retry_count, recovery_attempt_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "act:dispatched:1",
                "lane:221",
                "dispatch_repair_handoff",
                "stuck-dispatch",
                "active",
                "Workflow_Orchestrator",
                "Internal_Coder_Agent",
                "actor:1",
                "abc123",
                "active:dispatch_repair_handoff:lane:221:abc123",
                "dispatched",
                "2026-04-22T00:00:00Z",
                "2026-04-22T00:00:00Z",
                json.dumps({"action_type": "dispatch_repair_handoff"}, sort_keys=True),
                0,
                0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = runtime_module.reap_stuck_dispatched_actions(
        workflow_root=workflow_root,
        lane_id="lane:221",
        now_iso=now_iso,
        timeout_seconds=1800,
    )

    assert result["checked"] == 1
    assert result["reaped"] == 1
    assert result["failures"][0]["failure_class"] == "dispatcher_lost"
    assert result["recovery_actions"]
    assert result["recovery_actions"][0]["recovery_attempt_count"] == 1

    conn = sqlite3.connect(paths["db_path"])
    try:
        original = conn.execute(
            "SELECT status, failed_at, result_code, result_summary, superseded_by_action_id FROM lane_actions WHERE action_id=?",
            ("act:dispatched:1",),
        ).fetchone()
        # superseded_by_action_id forward-links from the failed action to its
        # recovery (matches the canonical direction used by the failures
        # JOIN at runtime.py:1683 and _queue_recovery_action's UPDATE).
        recovery = conn.execute(
            "SELECT action_id, status, retry_count, recovery_attempt_count FROM lane_actions WHERE action_id=?",
            (original[4],),
        ).fetchone()
        failure_row = conn.execute(
            "SELECT failure_class, analyst_recommended_action, analyst_status FROM failures WHERE failure_id=?",
            ("failure:act:dispatched:1",),
        ).fetchone()
    finally:
        conn.close()

    assert original[0] == "failed"
    assert original[2] == "timeout"
    assert failure_row[0] == "dispatcher_lost"
    assert recovery[1] == "requested"
    assert recovery[2] == 1
    assert recovery[3] == 1


def _active_dispatch_legacy_status() -> dict:
    return {
        "activeLane": {"number": 221, "url": "https://example.com/issues/221", "title": "Issue 221", "labels": []},
        "repo": "/tmp/repo",
        "implementation": {
            "worktree": "/tmp/issue-221",
            "branch": "codex/issue-221-test",
            "localHeadSha": "abc123",
            "laneState": {
                "implementation": {
                    "lastMeaningfulProgressAt": "2026-04-22T00:00:00Z",
                    "lastMeaningfulProgressKind": "implementing_local",
                },
                "pr": {"lastPublishedHeadSha": None},
            },
            "activeSessionHealth": {"healthy": False, "lastUsedAt": None},
            "sessionActionRecommendation": {"action": "restart-session"},
        },
        "reviews": {},
        "ledger": {"workflowState": "implementing_local", "reviewState": "implementing_local", "repairBrief": None},
        "derivedReviewLoopState": "awaiting_reviews",
        "derivedMergeBlocked": False,
        "derivedMergeBlockers": [],
        "openPr": None,
        "activeLaneError": None,
        "staleLaneReasons": [],
        "nextAction": {"type": "dispatch_codex_turn", "reason": "implementation-in-progress"},
    }


def test_run_active_loop_reconciles_fast_supervised_iteration_before_bounded_exit(runtime_module, tmp_path, monkeypatch):
    workflow_root = tmp_path / "workflow"
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")
    legacy_status = _active_dispatch_legacy_status()

    monkeypatch.setattr(
        runtime_module,
        "derive_shadow_actions_for_lane",
        lambda **_kwargs: [{"action_type": "dispatch_implementation_turn", "reason": "implementation-in-progress", "target_head_sha": "abc123"}],
    )

    calls = []

    def run_action():
        calls.append("dispatch")
        return {"dispatched": True, "after": legacy_status}

    result = runtime_module.run_active_loop(
        workflow_root=workflow_root,
        project_key="workflow-example",
        instance_id="active-test",
        interval_seconds=1,
        max_iterations=1,
        legacy_status_provider=lambda: legacy_status,
        sleep_fn=lambda _seconds: None,
        action_runners={"dispatch_implementation_turn": run_action},
    )

    assert result["loop_status"] == "completed"
    assert result["running_iteration"] is None
    assert result["last_result"]["supervised"] is True
    assert result["last_result"]["iteration_status"] == "executed"
    assert calls == ["dispatch"]

    conn = sqlite3.connect(runtime_module._runtime_paths(workflow_root)["db_path"])
    try:
        action_status = conn.execute(
            """
            SELECT status
            FROM lane_actions
            WHERE action_type=? AND action_mode='active'
            ORDER BY requested_at DESC
            """,
            ("dispatch_implementation_turn",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert action_status == "completed"


def test_runtime_event_retention_uses_repo_owned_workflow_contract(runtime_module, tmp_path):
    from engine.store import EngineStore
    from workflows.contract import render_workflow_markdown

    workflow_root = tmp_path / "workflow"
    workflow_root.mkdir()
    (workflow_root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config={
                "workflow": "change-delivery",
                "retention": {"events": {"max-rows": 1}},
            },
            prompt_template="Deliver the active change.",
        ),
        encoding="utf-8",
    )
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")
    store = EngineStore(
        db_path=runtime_module._runtime_paths(workflow_root)["db_path"],
        workflow="change-delivery",
    )
    store.append_event(event_type="old", payload={"lane_id": "lane:1"})
    store.append_event(event_type="new", payload={"lane_id": "lane:2"})

    result = runtime_module._apply_workflow_event_retention(
        workflow_root=workflow_root,
        workflow="change-delivery",
    )

    assert result["applied"] is True
    assert result["deleted"] == 1
    assert len(store.events()) == 1


def test_run_active_loop_heartbeats_while_supervised_iteration_is_running(runtime_module, tmp_path, monkeypatch):
    workflow_root = tmp_path / "workflow"
    paths = runtime_module._runtime_paths(workflow_root)
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")
    legacy_status = _active_dispatch_legacy_status()

    monkeypatch.setattr(
        runtime_module,
        "derive_shadow_actions_for_lane",
        lambda **_kwargs: [{"action_type": "dispatch_implementation_turn", "reason": "implementation-in-progress", "target_head_sha": "abc123"}],
    )

    started = threading.Event()
    release = threading.Event()
    calls = []
    sleeps = {"count": 0}

    def run_action():
        calls.append("dispatch")
        started.set()
        assert release.wait(timeout=2)
        return {"dispatched": True, "after": legacy_status}

    def sleep_fn(_seconds):
        sleeps["count"] += 1
        assert started.wait(timeout=2)
        if sleeps["count"] >= 2:
            release.set()
        time.sleep(0.01)

    result = runtime_module.run_active_loop(
        workflow_root=workflow_root,
        project_key="workflow-example",
        instance_id="active-test",
        interval_seconds=1,
        max_iterations=3,
        legacy_status_provider=lambda: legacy_status,
        sleep_fn=sleep_fn,
        action_runners={"dispatch_implementation_turn": run_action},
    )

    assert result["loop_status"] == "completed"
    assert result["running_iteration"] is None
    assert result["last_result"]["iteration_status"] == "executed"
    assert calls == ["dispatch"]

    events = [json.loads(line) for line in paths["event_log_path"].read_text(encoding="utf-8").splitlines()]
    heartbeats = [event for event in events if event.get("event_type") == "daedalus.runtime_heartbeat"]
    assert len(heartbeats) >= 2


def test_run_active_loop_cancels_supervised_iteration_when_active_lane_disappears(runtime_module, tmp_path, monkeypatch):
    workflow_root = tmp_path / "workflow"
    paths = runtime_module._runtime_paths(workflow_root)
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")
    active_status = _active_dispatch_legacy_status()
    no_lane_status = {**active_status, "activeLane": None, "nextAction": {"type": "noop", "reason": "no-active-lane"}}

    monkeypatch.setattr(
        runtime_module,
        "derive_shadow_actions_for_lane",
        lambda **_kwargs: [{"action_type": "dispatch_implementation_turn", "reason": "implementation-in-progress", "target_head_sha": "abc123"}],
    )

    started = threading.Event()
    provider_calls = {"count": 0}

    def legacy_status_provider():
        provider_calls["count"] += 1
        return active_status if provider_calls["count"] == 1 else no_lane_status

    def run_action(*, cancel_event=None):
        assert cancel_event is not None
        started.set()
        assert cancel_event.wait(timeout=2)
        raise RuntimeError("stopped after cancel")

    def sleep_fn(_seconds):
        assert started.wait(timeout=2)
        time.sleep(0.01)

    result = runtime_module.run_active_loop(
        workflow_root=workflow_root,
        project_key="workflow-example",
        instance_id="active-test",
        interval_seconds=1,
        max_iterations=2,
        legacy_status_provider=legacy_status_provider,
        sleep_fn=sleep_fn,
        action_runners={"dispatch_implementation_turn": run_action},
    )

    assert result["loop_status"] == "completed"
    assert result["running_iteration"] is None
    assert result["last_result"]["cancel_requested"] is True
    assert result["last_result"]["cancel_reason"] == "no-active-lane"
    assert result["last_result"]["executed_action"]["canceled"] is True

    conn = sqlite3.connect(runtime_module._runtime_paths(workflow_root)["db_path"])
    try:
        action_status = conn.execute(
            """
            SELECT status
            FROM lane_actions
            WHERE action_type=? AND action_mode='active'
            ORDER BY requested_at DESC
            """,
            ("dispatch_implementation_turn",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert action_status == "canceled"

    events = [json.loads(line) for line in paths["event_log_path"].read_text(encoding="utf-8").splitlines()]
    assert any(event.get("event_type") == "daedalus.active_action_canceled" for event in events)


def test_default_active_action_runners_reuse_workspace_and_close(runtime_module, tmp_path, monkeypatch):
    calls = {"load": 0, "close": 0, "actions": []}

    class FakeWorkspace:
        def set_active_cancel_event(self, event):
            calls.setdefault("cancel_events", []).append(event)

        def dispatch_implementation_turn(self):
            calls["actions"].append("dispatch")
            return {"dispatched": True}

        def close(self):
            calls["close"] += 1

    def load_workspace(_workflow_root):
        calls["load"] += 1
        return FakeWorkspace()

    monkeypatch.setattr(runtime_module, "_load_legacy_workflow_module", load_workspace)
    runners = runtime_module._default_active_action_runners(workflow_root=tmp_path)
    cancel_event = threading.Event()

    assert runners["dispatch_implementation_turn"](cancel_event=cancel_event) == {"dispatched": True}
    assert runners["dispatch_implementation_turn"]() == {"dispatched": True}
    runners["__close__"]()

    assert calls["load"] == 1
    assert calls["close"] == 1
    assert calls["actions"] == ["dispatch", "dispatch"]
    assert calls["cancel_events"] == [cancel_event, None, None, None]


def test_active_loop_closes_owned_workspace_after_bounded_exit_worker_finishes(runtime_module, tmp_path, monkeypatch):
    workflow_root = tmp_path / "workflow"
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="workflow-example")
    legacy_status = _active_dispatch_legacy_status()

    monkeypatch.setattr(
        runtime_module,
        "derive_shadow_actions_for_lane",
        lambda **_kwargs: [{"action_type": "dispatch_implementation_turn", "reason": "implementation-in-progress", "target_head_sha": "abc123"}],
    )

    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class FakeWorkspace:
        def build_status(self):
            return legacy_status

        def set_active_cancel_event(self, _event):
            return None

        def dispatch_implementation_turn(self):
            started.set()
            assert release.wait(timeout=2)
            return {"dispatched": True, "after": legacy_status}

        def close(self):
            closed.set()

    monkeypatch.setattr(runtime_module, "_load_legacy_workflow_module", lambda _workflow_root: FakeWorkspace())

    result = runtime_module.run_active_loop(
        workflow_root=workflow_root,
        project_key="workflow-example",
        instance_id="active-test",
        interval_seconds=1,
        max_iterations=1,
        sleep_fn=lambda _seconds: None,
    )

    assert result["loop_status"] == "completed"
    assert result["running_iteration"] is not None
    assert started.wait(timeout=2)
    assert not closed.is_set()
    release.set()
    assert closed.wait(timeout=2)



def test_doctor_reports_stuck_dispatched_actions(tools_module, monkeypatch):
    relay_stub = SimpleNamespace(
        DISPATCHED_ACTION_TIMEOUT_SECONDS=1800,
        query_stuck_dispatched_actions=lambda **_kwargs: [
            {
                "action_id": "act:dispatched:1",
                "action_type": "dispatch_repair_handoff",
                "dispatched_at": "2026-04-22T00:00:00Z",
                "dispatched_age_seconds": 3600,
                "retry_count": 0,
                "recovery_attempt_count": 0,
            }
        ],
    )
    monkeypatch.setattr(
        tools_module,
        "build_shadow_report",
        lambda **_kwargs: {
            "report_generated_at": "2026-04-22T01:00:00Z",
            "runtime": {"latest_heartbeat_at": "2026-04-22T01:00:00Z", "runtime_status": "running", "active_orchestrator_instance_id": "relay"},
            "heartbeat": {"stale_reasons": [], "owner_instance_id": "relay"},
            "active_lane": {"lane_id": "lane:221", "issue_number": 221},
            "relay": {"compatible": True, "derived_action_type": "dispatch_repair_handoff", "reason": "ok"},
            "recent_failures": [],
            "active_failure_summary": {},
            "service": {"service_name": "daedalus-active@workflow-example.service", "installed": True, "enabled": True, "active": True},
            "service_health": {"expected_service_mode": None, "healthy": True, "reasons": []},
            "owner_summary": {"primary_owner": "relay", "gate_allowed": True},
            "recent_shadow_actions": [],
        },
    )
    monkeypatch.setattr(
        tools_module,
        "_load_daedalus_module",
        lambda _workflow_root: SimpleNamespace(
            _load_legacy_workflow_module=lambda _workflow_root: SimpleNamespace(
                build_status=lambda: {
                    "activeLane": {"number": 221},
                    "activeLaneError": None,
                }
            ),
            query_stuck_dispatched_actions=relay_stub.query_stuck_dispatched_actions,
            DISPATCHED_ACTION_TIMEOUT_SECONDS=relay_stub.DISPATCHED_ACTION_TIMEOUT_SECONDS,
        ),
    )
    # _build_project_status calls the workflow status builder directly,
    # bypassing the _load_daedalus_module mock above. Stub it so the test stays
    # a unit test of doctor-report logic rather than requiring a workflow
    # contract fixture.
    monkeypatch.setattr(
        tools_module,
        "_build_project_status",
        lambda _wr: {"activeLane": {"number": 221}, "activeLaneError": None},
    )

    report = tools_module.build_doctor_report(workflow_root=Path("/tmp/workflow"))
    checks = {check["code"]: check for check in report["checks"]}

    assert checks["stuck_dispatched_actions"]["status"] == "fail"
    assert checks["stuck_dispatched_actions"]["details"]["count"] == 1



def test_alerts_load_optional_json_rejects_non_dict(alerts_module, tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("[1, 2, 3]", encoding="utf-8")

    assert alerts_module._load_optional_json(state_path) is None


def test_alerts_only_page_on_failed_critical_checks(alerts_module):
    snapshot = {
        "doctor": {
            "checks": [
                {
                    "code": "split_brain_risk",
                    "summary": "split brain risk",
                    "severity": "critical",
                    "status": "warn",
                    "details": {"reasons": ["something"]},
                },
                {
                    "code": "runtime_down",
                    "summary": "runtime down",
                    "severity": "critical",
                    "status": "fail",
                    "details": {"reasons": ["runtime-not-running"]},
                },
            ]
        },
        "active_gate": {"allowed": True},
    }

    issues = alerts_module._critical_issues(snapshot)

    assert len(issues) == 1
    assert issues[0]["code"] == "runtime_down"


def test_collect_snapshot_avoids_direct_wrapper_subprocess(alerts_module, monkeypatch, tmp_path):
    responses = {
        f"doctor --workflow-root {tmp_path} --json": json.dumps({"report_generated_at": "2026-04-22T00:00:00Z", "checks": []}),
        f"active-gate-status --workflow-root {tmp_path} --json": json.dumps({"allowed": True, "reasons": []}),
    }
    monkeypatch.setattr(alerts_module, "_execute_plugin_command", lambda command: responses[command])

    snapshot = alerts_module.collect_snapshot(workflow_root=tmp_path)

    assert snapshot == {
        "report_generated_at": "2026-04-22T00:00:00Z",
        "doctor": {"report_generated_at": "2026-04-22T00:00:00Z", "checks": []},
        "active_gate": {"allowed": True, "reasons": []},
    }


def test_set_active_execution_updates_gate_without_wrapper_side_effects(tools_module, monkeypatch, tmp_path):
    call_order = []

    relay_stub = SimpleNamespace(
        RELAY_OWNER="relay",
        _runtime_paths=lambda workflow_root: {"db_path": workflow_root / "state" / "daedalus" / "daedalus.db", "event_log_path": workflow_root / "memory" / "daedalus-events.jsonl"},
        set_execution_control=lambda **kwargs: call_order.append(("set", kwargs["active_execution_enabled"])),
        evaluate_active_execution_gate=lambda **kwargs: {"allowed": True, "reasons": [], "execution": {"active_execution_enabled": True}},
    )

    monkeypatch.setattr(tools_module, "_record_operator_command_event", lambda **_kwargs: None)
    monkeypatch.setattr(tools_module, "_load_daedalus_module", lambda workflow_root: relay_stub)
    monkeypatch.setattr(tools_module, "_run_wrapper_json_command", lambda **_kwargs: {"health": "healthy"})

    result = tools_module.execute_namespace(
        argparse.Namespace(
            daedalus_command="set-active-execution",
            workflow_root=str(tmp_path),
            enabled="true",
        )
    )

    assert call_order == [("set", True)]
    assert result["requested_enabled"] is True


def test_execute_raw_args_catches_unexpected_exception(tools_module, monkeypatch):
    monkeypatch.setattr(tools_module, "execute_namespace", lambda _args: (_ for _ in ()).throw(ValueError("boom")))

    result = tools_module.execute_raw_args("status")

    assert result == "daedalus error: unexpected ValueError: boom"


def test_install_supervised_service_requires_plugin_runtime(tools_module, tmp_path):
    original = tools_module._expected_plugin_runtime_path
    tools_module._expected_plugin_runtime_path = lambda _workflow_root: tmp_path / "missing-runtime.py"
    try:
        with pytest.raises(tools_module.DaedalusCommandError, match="Daedalus plugin runtime not found"):
            tools_module.install_supervised_service(
                workflow_root=tmp_path,
                project_key="workflow-example",
                instance_id="relay-test",
                interval_seconds=30,
                service_mode="shadow",
            )
    finally:
        tools_module._expected_plugin_runtime_path = original
