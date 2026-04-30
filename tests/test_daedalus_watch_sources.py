"""Read-only aggregation of state from existing event sources."""
import importlib.util
import json
import sqlite3
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
    return load_module("daedalus_watch_sources_test", "watch_sources.py")


def _make_workflow_root(tmp_path):
    """Build a workflow_root tree that runtime_paths recognizes (has runtime/, config/)."""
    root = tmp_path / "workflow_example"
    (root / "runtime" / "memory").mkdir(parents=True)
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()
    return root


def test_read_recent_daedalus_events_returns_last_n_lines_newest_first(tmp_path):
    sources = _module()
    root = _make_workflow_root(tmp_path)
    log_path = root / "runtime" / "memory" / "daedalus-events.jsonl"
    log_path.write_text("\n".join([
        json.dumps({"at": "2026-04-26T22:00:01Z", "event": "a"}),
        json.dumps({"at": "2026-04-26T22:00:02Z", "event": "b"}),
        json.dumps({"at": "2026-04-26T22:00:03Z", "event": "c"}),
    ]) + "\n")
    events = sources.recent_daedalus_events(root, limit=2)
    assert [e["event"] for e in events] == ["c", "b"]


def test_read_recent_workflow_audit_handles_missing_file(tmp_path):
    sources = _module()
    root = _make_workflow_root(tmp_path)
    out = sources.recent_workflow_audit(root, limit=10)
    assert out == []


def test_read_active_lanes_from_db(tmp_path):
    """Schema must match the real ``lanes`` table in runtime.py:
       lane_id (PK), issue_number, workflow_state, lane_status.
    Earlier drafts of active_lanes() queried `state` / `github_issue_number`
    which silently raised sqlite3.OperationalError and was caught — making
    /daedalus watch always show no active lanes against a real db."""
    sources = _module()
    root = _make_workflow_root(tmp_path)
    db_path = root / "runtime" / "state" / "daedalus" / "daedalus.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE lanes ("
        "  lane_id TEXT PRIMARY KEY, issue_number INTEGER, "
        "  workflow_state TEXT, lane_status TEXT)"
    )
    conn.execute("INSERT INTO lanes VALUES ('lane-329', 329, 'under_review', 'active')")
    conn.execute("INSERT INTO lanes VALUES ('lane-330', 330, 'merged', 'merged')")
    conn.execute("INSERT INTO lanes VALUES ('lane-331', 331, 'closed', 'closed')")
    conn.commit()
    conn.close()
    lanes = sources.active_lanes(root)
    assert len(lanes) == 1
    assert lanes[0]["lane_id"] == "lane-329"
    assert lanes[0]["state"] == "under_review"             # consumer-facing alias
    assert lanes[0]["workflow_state"] == "under_review"    # canonical column name
    assert lanes[0]["issue_number"] == 329
    assert lanes[0]["github_issue_number"] == 329          # consumer-facing alias
    assert lanes[0]["lane_status"] == "active"
    assert lanes[0]["work_item"]["id"] == "lane-329"
    assert lanes[0]["work_item"]["identifier"] == "#329"
    assert lanes[0]["work_item"]["source"] == "change-delivery"


def test_active_lanes_returns_empty_when_query_fails():
    """Defensive test: if the lanes table somehow lacks the expected columns
    (e.g. on a freshly initialized but unfilled db), return [] gracefully
    rather than raise."""
    import tempfile
    sources = _module()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "workflow_example"
        (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
        (root / "config").mkdir()
        (root / "workspace").mkdir()
        db_path = root / "runtime" / "state" / "daedalus" / "daedalus.db"
        conn = sqlite3.connect(db_path)
        # Wrong-shape lanes table — the prior bug.
        conn.execute("CREATE TABLE lanes (foo TEXT, bar TEXT)")
        conn.commit()
        conn.close()
        assert sources.active_lanes(root) == []


def test_read_alert_state_returns_empty_dict_when_absent(tmp_path):
    sources = _module()
    root = _make_workflow_root(tmp_path)
    state = sources.alert_state(root)
    assert state == {}


def test_read_alert_state_when_present(tmp_path):
    sources = _module()
    root = _make_workflow_root(tmp_path)
    alert_path = root / "runtime" / "memory" / "daedalus-alert-state.json"
    alert_path.write_text(json.dumps({"fingerprint": "abc", "active": True}))
    state = sources.alert_state(root)
    assert state["active"] is True


def test_issue_runner_watch_sources_use_repo_storage_paths(tmp_path):
    from workflows.contract import render_workflow_markdown

    sources = _module()
    root = _make_workflow_root(tmp_path)
    (root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config={
                "workflow": "issue-runner",
                "schema-version": 1,
                "instance": {"name": "attmous-daedalus-issue-runner", "engine-owner": "hermes"},
                "repository": {"local-path": "/tmp/repo", "github-slug": "attmous/daedalus"},
                "tracker": {"kind": "github"},
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
    (root / "memory" / "workflow-scheduler.json").write_text(
        json.dumps(
            {
                "running": [{"issue_id": "123", "identifier": "#123", "state": "open"}],
                "retry_queue": [{"issue_id": "124", "identifier": "#124", "error": "x"}],
                "codex_totals": {"total_tokens": 18, "rate_limits": {"requests_remaining": 88}},
            }
        )
    )
    (root / "memory" / "workflow-audit.jsonl").write_text(
        json.dumps({"at": "2026-04-30T12:00:20Z", "event": "issue_runner.tick.completed", "issue_id": "123"}) + "\n"
    )

    lanes = sources.active_lanes(root)
    audit = sources.recent_workflow_audit(root, limit=5)
    workflow_status = sources.workflow_status(root)

    assert [lane["issue_identifier"] for lane in lanes] == ["#123", "#124"]
    assert [lane["work_item"]["id"] for lane in lanes] == ["123", "124"]
    assert all(lane["work_item"]["source"] == "issue-runner" for lane in lanes)
    assert audit[0]["event"] == "issue_runner.tick.completed"
    assert workflow_status["workflow"] == "issue-runner"
    assert workflow_status["running_count"] == 1
    assert workflow_status["retry_count"] == 1
    assert workflow_status["total_tokens"] == 18


def test_change_delivery_watch_sources_surface_canceling_codex_turns(tmp_path):
    from workflows.contract import render_workflow_markdown

    sources = _module()
    root = _make_workflow_root(tmp_path)
    (root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config={
                "workflow": "change-delivery",
                "schema-version": 1,
                "instance": {"name": "attmous-daedalus-change-delivery", "engine-owner": "hermes"},
                "repository": {"local-path": "/tmp/repo", "github-slug": "attmous/daedalus"},
                "runtimes": {"coder-runtime": {"kind": "codex-app-server", "command": "codex app-server"}},
                "agents": {"coder": {"default": {"name": "coder", "model": "gpt-5.5", "runtime": "coder-runtime"}}},
                "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
                "triggers": {"lane-selector": {"type": "github-label", "label": "active-lane"}},
                "storage": {"scheduler": "memory/workflow-scheduler.json"},
            },
            prompt_template="Deliver the active change.",
        ),
        encoding="utf-8",
    )
    (root / "memory").mkdir(exist_ok=True)
    (root / "memory" / "workflow-scheduler.json").write_text(
        json.dumps(
            {
                "workflow": "change-delivery",
                "updatedAt": "2026-04-30T12:00:20Z",
                "codex_threads": {
                    "lane:42": {
                        "issue_id": "lane:42",
                        "issue_number": 42,
                        "identifier": "#42",
                        "thread_id": "thread-42",
                        "turn_id": "turn-42",
                        "status": "canceling",
                        "cancel_requested": True,
                        "cancel_reason": "operator-interrupt",
                    }
                },
                "codex_totals": {"total_tokens": 18},
            }
        )
    )

    workflow_status = sources.workflow_status(root)

    assert workflow_status["workflow"] == "change-delivery"
    assert workflow_status["running_count"] == 0
    assert workflow_status["canceling_count"] == 1
    assert workflow_status["total_tokens"] == 18
    assert workflow_status["codex_turns"][0]["thread_id"] == "thread-42"
    assert workflow_status["codex_turns"][0]["turn_id"] == "turn-42"
    assert workflow_status["codex_turns"][0]["cancel_reason"] == "operator-interrupt"
