"""Frame rendering: aggregator output → rich-renderable frame string.

We render to a string (capture mode) and snapshot-test the output structure.
"""
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return load_module("daedalus_watch_test", "watch.py")


def test_render_frame_with_no_active_lanes():
    watch = _module()
    out = watch.render_frame_to_string({
        "active_lanes": [],
        "alert_state": {},
        "recent_events": [],
    })
    assert "Daedalus active lanes" in out
    assert "(no active lanes)" in out


def test_render_frame_with_one_lane():
    watch = _module()
    out = watch.render_frame_to_string({
        "active_lanes": [
            {"lane_id": "329", "state": "under_review", "github_issue_number": 329}
        ],
        "alert_state": {},
        "recent_events": [
            {"at": "2026-04-26T22:30:34Z", "source": "workflow", "event": "dispatch_implementation_turn", "detail": "committed"},
        ],
    })
    assert "329" in out
    assert "under_review" in out
    assert "dispatch_implementation_turn" in out


def test_render_frame_includes_alert_banner_when_alert_active():
    watch = _module()
    out = watch.render_frame_to_string({
        "active_lanes": [],
        "alert_state": {"active": True, "fingerprint": "abc", "message": "stale heartbeat"},
        "recent_events": [],
    })
    assert "Active alerts" in out or "alert" in out.lower()


def test_render_frame_handles_stale_source():
    """Source-level [stale] markers when an aggregator returned an error sentinel."""
    watch = _module()
    out = watch.render_frame_to_string({
        "active_lanes": [{"_stale": True}],
        "alert_state": {"_stale": True},
        "recent_events": [],
    })
    # No crash; "[stale]" appears somewhere
    assert "stale" in out.lower()


def test_render_frame_includes_issue_runner_workflow_status():
    watch = _module()
    out = watch.render_frame_to_string({
        "active_lanes": [
            {"lane_id": "123", "state": "open", "issue_identifier": "#123"}
        ],
        "workflow_status": {
            "workflow": "issue-runner",
            "health": "healthy",
            "running_count": 1,
            "retry_count": 1,
            "total_tokens": 18,
            "rate_limits": {"requests_remaining": 88},
            "selected_issue": "#123",
            "latest_runs": [{"mode": "tick", "status": "completed", "selected_count": 1, "completed_count": 1}],
            "updated_at": "2026-04-30T12:00:15Z",
        },
        "alert_state": {},
        "recent_events": [],
    })
    assert "Workflow status" in out
    assert "issue-runner" in out
    assert "tokens=18" in out
    assert "selected=#123" in out
    assert "run=tick:completed selected=1 completed=1" in out


def test_render_frame_includes_canceling_codex_turns():
    watch = _module()
    out = watch.render_frame_to_string({
        "active_lanes": [],
        "workflow_status": {
            "workflow": "change-delivery",
            "running_count": 0,
            "retry_count": 0,
            "canceling_count": 1,
            "total_tokens": 18,
            "codex_turns": [
                {
                    "issue_id": "lane:42",
                    "issue_identifier": "#42",
                    "thread_id": "thread-42",
                    "turn_id": "turn-42",
                    "status": "canceling",
                    "cancel_reason": "operator-interrupt",
                }
            ],
        },
        "alert_state": {},
        "recent_events": [],
    })
    assert "canceling=1" in out
    assert "codex_canceling=#42" in out
    assert "thread=thread-42" in out
    assert "reason=operator-interrupt" in out


import json
import sqlite3


def _make_workflow_root(tmp_path):
    root = tmp_path / "workflow_example"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()
    return root


def test_build_snapshot_combines_all_sources(tmp_path):
    watch = _module()
    root = _make_workflow_root(tmp_path)

    # Seed daedalus-events
    (root / "runtime" / "memory" / "daedalus-events.jsonl").write_text(
        json.dumps({"at": "2026-04-26T22:00:01Z", "event": "lane_action_dispatched"}) + "\n"
    )
    # Seed workflow-audit
    (root / "runtime" / "memory" / "workflow-audit.jsonl").write_text(
        json.dumps({"at": "2026-04-26T22:00:02Z", "action": "merge-and-promote"}) + "\n"
    )
    # Seed lanes table
    db = root / "runtime" / "state" / "daedalus" / "daedalus.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE lanes ("
        "  lane_id TEXT PRIMARY KEY, issue_number INTEGER, "
        "  workflow_state TEXT, lane_status TEXT)"
    )
    conn.execute("INSERT INTO lanes VALUES ('lane-329', 329, 'under_review', 'active')")
    conn.commit()
    conn.close()
    # Seed alert state
    (root / "runtime" / "memory" / "daedalus-alert-state.json").write_text(
        json.dumps({"active": True, "message": "stale dispatch"})
    )

    snap = watch.build_snapshot(root)
    assert len(snap["active_lanes"]) == 1
    assert snap["active_lanes"][0]["lane_id"] == "lane-329"
    assert snap["active_lanes"][0]["issue_number"] == 329
    # interleaved + sorted recent events
    assert any(e.get("source") == "daedalus" for e in snap["recent_events"])
    assert any(e.get("source") == "workflow" for e in snap["recent_events"])
    assert snap["alert_state"]["active"] is True


def test_build_snapshot_prefers_engine_event_ledger(tmp_path):
    from engine.store import EngineStore
    from workflows.contract import render_workflow_markdown
    from workflows.shared.paths import runtime_paths

    watch = _module()
    root = _make_workflow_root(tmp_path)
    (root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config={
                "workflow": "issue-runner",
                "schema-version": 1,
                "instance": {"name": "attmous-daedalus-issue-runner", "engine-owner": "hermes"},
                "repository": {"local-path": "/tmp/repo"},
                "tracker": {"kind": "local-json", "path": "config/issues.json"},
                "workspace": {"root": "workspace/issues"},
                "agent": {"name": "runner", "model": "gpt-5.4"},
                "storage": {
                    "status": "memory/workflow-status.json",
                    "health": "memory/workflow-health.json",
                    "audit-log": "memory/workflow-audit.jsonl",
                },
            },
            prompt_template="Issue: {{ issue.identifier }}",
        ),
        encoding="utf-8",
    )
    (root / "runtime" / "memory" / "daedalus-events.jsonl").write_text(
        json.dumps({"at": "2026-04-26T22:00:01Z", "event": "jsonl_event"}) + "\n"
    )
    store = EngineStore(
        db_path=runtime_paths(root)["db_path"],
        workflow="issue-runner",
        now_iso=lambda: "2026-04-30T12:00:21Z",
        now_epoch=lambda: 1714478421.0,
    )
    store.append_event(event_type="sql_event", payload={"summary": "from sqlite"})

    snap = watch.build_snapshot(root)

    assert snap["recent_events"][0]["source"] == "engine-events"
    assert snap["recent_events"][0]["event_type"] == "sql_event"
    assert all(event.get("event") != "jsonl_event" for event in snap["recent_events"])


def test_build_snapshot_includes_issue_runner_workflow_status(tmp_path):
    from engine.state import save_engine_scheduler_state
    from workflows.contract import render_workflow_markdown
    from workflows.shared.paths import runtime_paths

    watch = _module()
    root = _make_workflow_root(tmp_path)
    (root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config={
                "workflow": "issue-runner",
                "schema-version": 1,
                "instance": {"name": "attmous-daedalus-issue-runner", "engine-owner": "hermes"},
                "repository": {"local-path": "/tmp/repo", "slug": "attmous/daedalus"},
                "tracker": {"kind": "github", "github_slug": "attmous/daedalus"},
                "workspace": {"root": "workspace/issues"},
                "agent": {"name": "runner", "model": "gpt-5.4", "runtime": "default"},
                "storage": {
                    "status": "memory/workflow-status.json",
                    "health": "memory/workflow-health.json",
                    "audit-log": "memory/workflow-audit.jsonl",
                    "scheduler": "memory/workflow-scheduler.json",
                },
            },
            prompt_template="Issue: {{ issue.identifier }}",
        ),
        encoding="utf-8",
    )
    (root / "memory").mkdir(exist_ok=True)
    (root / "memory" / "workflow-status.json").write_text(
        json.dumps({"workflow": "issue-runner", "health": "healthy", "lastRun": {"updatedAt": "2026-04-30T12:00:15Z"}})
    )
    save_engine_scheduler_state(
        runtime_paths(root)["db_path"],
        workflow="issue-runner",
        running_entries={"123": {"issue_id": "123", "identifier": "#123", "state": "open"}},
        retry_entries={},
        codex_totals={"total_tokens": 18},
        codex_threads={},
        now_iso="2026-04-30T12:00:20Z",
        now_epoch=1714478420.0,
    )

    snap = watch.build_snapshot(root)
    assert snap["workflow_status"]["workflow"] == "issue-runner"
    assert snap["workflow_status"]["total_tokens"] == 18
