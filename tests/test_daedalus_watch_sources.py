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
    root = tmp_path / "yoyopod_core"
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


def test_active_lanes_returns_empty_when_query_fails():
    """Defensive test: if the lanes table somehow lacks the expected columns
    (e.g. on a freshly initialized but unfilled db), return [] gracefully
    rather than raise."""
    import tempfile
    sources = _module()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "yoyopod_core"
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
