"""Tests for the optional HTTP status surface (Symphony §13.7, S-6).

Covers:
- ``views.state_view`` / ``views.issue_view`` shape and fallback behaviour.
- ``refresh.RefreshController`` debounce / coalescing.
- ``html.render_dashboard`` smoke test.
- ``routes.start_server`` wiring (port=0 ephemeral binding, JSON endpoints,
  HTML index, refresh endpoint, clean shutdown).
- The ``serve`` CLI subcommand.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path
from unittest import mock

import pytest


def _make_lanes_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE lanes (
              lane_id TEXT PRIMARY KEY,
              issue_number INTEGER NOT NULL,
              issue_url TEXT,
              issue_title TEXT,
              repo_path TEXT,
              worktree_path TEXT,
              branch_name TEXT,
              priority_hint TEXT,
              effort_label TEXT,
              actor_backend TEXT,
              lane_status TEXT NOT NULL,
              workflow_state TEXT NOT NULL,
              review_state TEXT,
              merge_state TEXT,
              current_head_sha TEXT,
              last_published_head_sha TEXT,
              active_pr_number INTEGER,
              active_pr_url TEXT,
              active_pr_head_sha TEXT,
              required_internal_review INTEGER NOT NULL DEFAULT 0,
              required_external_review INTEGER NOT NULL DEFAULT 0,
              merge_blocked INTEGER NOT NULL DEFAULT 0,
              merge_blockers_json TEXT,
              repair_brief_json TEXT,
              active_actor_id TEXT,
              current_action_id TEXT,
              last_completed_action_id TEXT,
              last_meaningful_progress_at TEXT,
              last_meaningful_progress_kind TEXT,
              operator_attention_required INTEGER NOT NULL DEFAULT 0,
              operator_attention_reason TEXT,
              archived_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO lanes
              (lane_id, issue_number, issue_url, issue_title, repo_path,
               actor_backend, lane_status, workflow_state, review_state,
               merge_state, active_actor_id,
               last_meaningful_progress_kind, last_meaningful_progress_at,
               created_at, updated_at)
            VALUES
              ('lane-42', 42, 'https://x/42', 'demo lane', '/x',
               'acpx-codex', 'active', 'under_review', 'pending',
               'pending', 'thr-1-turn-3',
               'turn_completed', '2026-04-28T12:00:00Z',
               '2026-04-28T11:00:00Z', '2026-04-28T12:00:00Z')
            """
        )
        # A terminal lane, must NOT show in running.
        conn.execute(
            """
            INSERT INTO lanes
              (lane_id, issue_number, actor_backend, lane_status, workflow_state,
               review_state, merge_state,
               created_at, updated_at)
            VALUES
              ('lane-41', 41, 'acpx-codex', 'merged', 'merged',
               'pass', 'merged',
               '2026-04-27T09:00:00Z', '2026-04-28T10:00:00Z')
            """
        )
        conn.commit()
    finally:
        conn.close()


def _make_events_log(events_path: Path, entries: list[dict]) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------- views


def test_state_view_empty_when_no_db(tmp_path: Path) -> None:
    from workflows.code_review.server.views import state_view
    view = state_view(tmp_path / "missing.db", tmp_path / "missing.jsonl")
    assert view["counts"] == {"running": 0, "retrying": 0}
    assert view["running"] == []
    assert view["retrying"] == []
    assert view["totals"]["total_tokens"] == 0
    assert view["rate_limits"] is None
    assert "generated_at" in view


def test_state_view_lists_active_lanes(tmp_path: Path) -> None:
    from workflows.code_review.server.views import state_view

    db = tmp_path / "daedalus.db"
    events = tmp_path / "events.jsonl"
    _make_lanes_db(db)
    _make_events_log(
        events,
        [
            {"kind": "turn_completed", "lane_id": "lane-42", "at": "2026-04-28T12:00:01Z"},
            {"kind": "tick_started", "at": "2026-04-28T12:00:02Z"},
        ],
    )

    view = state_view(db, events)
    assert view["counts"]["running"] == 1
    assert len(view["running"]) == 1
    entry = view["running"][0]
    assert entry["issue_id"] == "lane-42"
    assert entry["issue_identifier"] == "#42"
    assert entry["state"] == "under_review"
    assert entry["session_id"] == "thr-1-turn-3"
    assert entry["last_event"] == "turn_completed"
    assert entry["tokens"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_issue_view_returns_none_for_unknown(tmp_path: Path) -> None:
    from workflows.code_review.server.views import issue_view

    db = tmp_path / "daedalus.db"
    events = tmp_path / "events.jsonl"
    _make_lanes_db(db)
    assert issue_view(db, events, "#999") is None
    assert issue_view(db, events, "lane-999") is None


def test_issue_view_resolves_by_issue_number_and_lane_id(tmp_path: Path) -> None:
    from workflows.code_review.server.views import issue_view

    db = tmp_path / "daedalus.db"
    events = tmp_path / "events.jsonl"
    _make_lanes_db(db)
    _make_events_log(
        events,
        [{"kind": "turn_completed", "issue_number": 42, "at": "2026-04-28T12:00:01Z"}],
    )

    by_hash = issue_view(db, events, "#42")
    by_number = issue_view(db, events, "42")
    by_lane_id = issue_view(db, events, "lane-42")
    for view in (by_hash, by_number, by_lane_id):
        assert view is not None
        assert view["issue_id"] == "lane-42"
        assert view["issue_identifier"] == "#42"
        assert isinstance(view["recent_events"], list)
        assert view["recent_events"] and view["recent_events"][0]["kind"] == "turn_completed"


# ------------------------------------------------------------------- refresh


def test_refresh_controller_coalesces_rapid_triggers(tmp_path: Path) -> None:
    from workflows.code_review.server.refresh import RefreshController

    ctrl = RefreshController(tmp_path)
    with mock.patch("workflows.code_review.server.refresh.subprocess.Popen") as popen:
        results = [ctrl.trigger() for _ in range(10)]
    # First call fires; the rest are debounced.
    assert results[0] is True
    assert results.count(True) == 1
    assert popen.call_count == 1
    # Argv contains the workflow root and the tick subcommand.
    args, _ = popen.call_args
    argv = args[0]
    assert "tick" in argv
    assert str(tmp_path) in argv


def test_refresh_controller_allows_after_debounce(tmp_path: Path) -> None:
    from workflows.code_review.server.refresh import RefreshController

    ctrl = RefreshController(tmp_path)
    ctrl.DEBOUNCE_SECONDS = 0.01  # speed up the test
    with mock.patch("workflows.code_review.server.refresh.subprocess.Popen") as popen:
        assert ctrl.trigger() is True
        time.sleep(0.05)
        assert ctrl.trigger() is True
    assert popen.call_count == 2


# ---------------------------------------------------------------------- html


def test_render_dashboard_includes_lane_identifier(tmp_path: Path) -> None:
    from workflows.code_review.server.views import state_view
    from workflows.code_review.server.html import render_dashboard

    db = tmp_path / "daedalus.db"
    events = tmp_path / "events.jsonl"
    _make_lanes_db(db)
    _make_events_log(events, [])

    state = state_view(db, events)
    html_text = render_dashboard(state)
    assert "<html" in html_text.lower()
    assert "#42" in html_text
    assert "under_review" in html_text
    assert 'http-equiv="refresh"' in html_text


def test_render_dashboard_escapes_html(tmp_path: Path) -> None:
    from workflows.code_review.server.html import render_dashboard

    state = {
        "generated_at": "2026-04-28T20:15:30Z",
        "counts": {"running": 1, "retrying": 0},
        "running": [
            {
                "issue_id": "lane-1",
                "issue_identifier": "<script>alert(1)</script>",
                "state": "under_review",
                "session_id": "x",
                "turn_count": 0,
                "last_event": "x",
                "started_at": "x",
                "last_event_at": "x",
                "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            }
        ],
        "retrying": [],
        "totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "seconds_running": 0},
        "rate_limits": None,
        "recent_events": [],
    }
    html_text = render_dashboard(state)
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text


# -------------------------------------------------------------------- server


def _start_test_server(tmp_path: Path):
    from workflows.code_review.server import start_server

    db = tmp_path / "daedalus.db"
    events = tmp_path / "events.jsonl"
    _make_lanes_db(db)
    _make_events_log(events, [])

    workflow_root = tmp_path
    # Patch path resolution so the server reads the test fixtures.
    with mock.patch("workflows.code_review.server.routes.runtime_paths") as rp:
        rp.return_value = {"db_path": db, "event_log_path": events, "alert_state_path": tmp_path / "alert.json"}
        handle = start_server(workflow_root, port=0, bind="127.0.0.1")
    return handle


def test_server_state_endpoint_returns_json_shape(tmp_path: Path) -> None:
    handle = _start_test_server(tmp_path)
    try:
        url = f"http://127.0.0.1:{handle.port}/api/v1/state"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.status == 200
            assert "application/json" in resp.headers.get("content-type", "")
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["counts"]["running"] == 1
        assert payload["running"][0]["issue_identifier"] == "#42"
        assert payload["rate_limits"] is None
    finally:
        handle.shutdown()


def test_server_unknown_issue_returns_404(tmp_path: Path) -> None:
    import urllib.error

    handle = _start_test_server(tmp_path)
    try:
        url = f"http://127.0.0.1:{handle.port}/api/v1/%23999"
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(url, timeout=5)
        assert exc_info.value.code == 404
        body = json.loads(exc_info.value.read().decode("utf-8"))
        assert body["error"]["code"] == "issue_not_found"
    finally:
        handle.shutdown()


def test_server_known_issue_returns_view(tmp_path: Path) -> None:
    handle = _start_test_server(tmp_path)
    try:
        url = f"http://127.0.0.1:{handle.port}/api/v1/%2342"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["issue_identifier"] == "#42"
    finally:
        handle.shutdown()


def test_server_refresh_endpoint_triggers_tick(tmp_path: Path) -> None:
    handle = _start_test_server(tmp_path)
    try:
        url = f"http://127.0.0.1:{handle.port}/api/v1/refresh"
        with mock.patch("workflows.code_review.server.refresh.subprocess.Popen") as popen:
            req = urllib.request.Request(url, method="POST", data=b"")
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 202
                payload = json.loads(resp.read().decode("utf-8"))
            assert payload["triggered"] is True
            assert popen.call_count == 1
    finally:
        handle.shutdown()


def test_server_html_index_returns_html(tmp_path: Path) -> None:
    handle = _start_test_server(tmp_path)
    try:
        url = f"http://127.0.0.1:{handle.port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.status == 200
            assert "text/html" in resp.headers.get("content-type", "")
            body = resp.read().decode("utf-8")
        assert "<html" in body.lower()
        assert "#42" in body
    finally:
        handle.shutdown()


def test_server_unknown_path_returns_404_json(tmp_path: Path) -> None:
    import urllib.error

    handle = _start_test_server(tmp_path)
    try:
        url = f"http://127.0.0.1:{handle.port}/nope"
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(url, timeout=5)
        assert exc_info.value.code == 404
        body = json.loads(exc_info.value.read().decode("utf-8"))
        assert body["error"]["code"] == "not_found"
    finally:
        handle.shutdown()


def test_server_shutdown_is_clean(tmp_path: Path) -> None:
    handle = _start_test_server(tmp_path)
    handle.shutdown()
    # Thread should exit quickly.
    handle.thread.join(timeout=5)
    assert not handle.thread.is_alive()


# ------------------------------------------------------------------ cli wire


def test_serve_subcommand_binds_and_serves(tmp_path: Path) -> None:
    """End-to-end smoke: build a workspace, invoke cli_main(['serve','--port','0'])
    in a thread, assert the state endpoint responds, then shut the server down."""
    from workflows.code_review.server import start_server as real_start_server

    captured: dict = {}

    def fake_start_server(workflow_root, port, bind):
        # Force the server to read the fixture DB rather than the real
        # workspace layout (which is fully mocked here).
        with mock.patch("workflows.code_review.server.routes.runtime_paths") as rp:
            rp.return_value = {
                "db_path": tmp_path / "daedalus.db",
                "event_log_path": tmp_path / "events.jsonl",
                "alert_state_path": tmp_path / "alert.json",
            }
            handle = real_start_server(workflow_root, port=port, bind=bind)
        captured["handle"] = handle
        return handle

    _make_lanes_db(tmp_path / "daedalus.db")
    _make_events_log(tmp_path / "events.jsonl", [])

    from types import SimpleNamespace
    from workflows.code_review.cli import main as cli_main

    workspace = SimpleNamespace(WORKSPACE=tmp_path, CONFIG={})

    done = threading.Event()

    def runner():
        try:
            with mock.patch("workflows.code_review.server.start_server", side_effect=fake_start_server):
                # Make handle.thread.join() return immediately so the CLI
                # function exits cleanly after we shut down the server.
                cli_main(workspace, ["serve", "--port", "0"])
        finally:
            done.set()

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    # Wait for the server to come up.
    deadline = time.monotonic() + 5.0
    while "handle" not in captured and time.monotonic() < deadline:
        time.sleep(0.02)
    handle = captured.get("handle")
    assert handle is not None, "serve subcommand never started a server"
    try:
        url = f"http://127.0.0.1:{handle.port}/api/v1/state"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["counts"]["running"] == 1
    finally:
        handle.shutdown()
    done.wait(timeout=5)
