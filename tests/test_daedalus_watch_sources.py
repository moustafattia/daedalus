"""Read-only aggregation of state from existing event sources."""
import importlib.util
import json
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


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
    sources = _module()
    root = _make_workflow_root(tmp_path)
    db_path = root / "runtime" / "state" / "daedalus" / "daedalus.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE lanes (project_key TEXT, lane_id TEXT, state TEXT, github_issue_number INTEGER)")
    conn.execute("INSERT INTO lanes VALUES ('yoyopod', '329', 'under_review', 329)")
    conn.execute("INSERT INTO lanes VALUES ('yoyopod', '330', 'merged', 330)")
    conn.commit()
    conn.close()
    lanes = sources.active_lanes(root)
    assert len(lanes) == 1
    assert lanes[0]["lane_id"] == "329"
    assert lanes[0]["state"] == "under_review"


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
