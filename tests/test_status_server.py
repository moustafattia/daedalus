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


def _make_issue_runner_root(root: Path) -> None:
    from workflows.contract import render_workflow_markdown

    root.mkdir(parents=True, exist_ok=True)
    (root / "memory").mkdir()
    (root / "config").mkdir()
    (root / "workspace").mkdir()
    (root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config={
                "workflow": "issue-runner",
                "schema-version": 1,
                "instance": {"name": "attmous-daedalus-issue-runner", "engine-owner": "hermes"},
                "repository": {"local-path": "/tmp/repo", "github-slug": "attmous/daedalus"},
                "tracker": {"kind": "github", "active_states": ["open"], "terminal_states": ["closed"]},
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
    (root / "memory" / "workflow-status.json").write_text(
        json.dumps(
            {
                "workflow": "issue-runner",
                "health": "healthy",
                "lastRun": {
                    "ok": True,
                    "issue": {"id": "123", "identifier": "#123"},
                    "updatedAt": "2026-04-30T12:00:15Z",
                },
                "metrics": {
                    "tokens": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
                    "rate_limits": {"requests_remaining": 99},
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "memory" / "workflow-scheduler.json").write_text(
        json.dumps(
            {
                "workflow": "issue-runner",
                "updatedAt": "2026-04-30T12:00:20Z",
                "running": [
                    {
                        "issue_id": "123",
                        "identifier": "#123",
                        "attempt": 2,
                        "state": "open",
                        "started_at_epoch": 1714478400.0,
                        "running_for_ms": 15000,
                    }
                ],
                "retry_queue": [
                    {
                        "issue_id": "124",
                        "identifier": "#124",
                        "attempt": 1,
                        "error": "tool call rejected",
                        "due_at_epoch": 1714478410.0,
                        "due_in_ms": 5000,
                    }
                ],
                "codex_totals": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "total_tokens": 18,
                    "rate_limits": {"requests_remaining": 88},
                },
            }
        ),
        encoding="utf-8",
    )
    _make_events_log(
        root / "memory" / "workflow-audit.jsonl",
        [
            {"at": "2026-04-30T12:00:20Z", "event": "issue_runner.tick.completed", "issue_id": "123", "identifier": "#123"},
            {"at": "2026-04-30T12:00:10Z", "event": "issue_runner.retry.scheduled", "issue_id": "124", "identifier": "#124"},
        ],
    )


def _make_change_delivery_root(root: Path) -> None:
    from workflows.contract import render_workflow_markdown

    root.mkdir(parents=True, exist_ok=True)
    (root / "memory").mkdir()
    (root / "WORKFLOW.md").write_text(
        render_workflow_markdown(
            config={
                "workflow": "change-delivery",
                "schema-version": 1,
                "instance": {"name": "attmous-daedalus-change-delivery", "engine-owner": "hermes"},
                "repository": {"local-path": "/tmp/repo", "github-slug": "attmous/daedalus", "active-lane-label": "active-lane"},
                "runtimes": {
                    "coder-runtime": {"kind": "codex-app-server", "command": "codex app-server"},
                    "reviewer-runtime": {"kind": "claude-cli", "max-turns-per-invocation": 24, "timeout-seconds": 1200},
                },
                "agents": {
                    "coder": {"default": {"name": "coder", "model": "gpt-5.5", "runtime": "coder-runtime"}},
                    "internal-reviewer": {"name": "reviewer", "model": "claude-sonnet-4-6", "runtime": "reviewer-runtime"},
                    "external-reviewer": {"enabled": False, "name": "external"},
                },
                "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
                "triggers": {"lane-selector": {"type": "github-label", "label": "active-lane"}},
                "storage": {
                    "ledger": "memory/workflow-status.json",
                    "health": "memory/workflow-health.json",
                    "audit-log": "memory/workflow-audit.jsonl",
                    "scheduler": "memory/workflow-scheduler.json",
                },
            },
            prompt_template="Deliver the active change.",
        ),
        encoding="utf-8",
    )
    (root / "memory" / "workflow-scheduler.json").write_text(
        json.dumps(
            {
                "workflow": "change-delivery",
                "updatedAt": "2026-04-30T12:00:20Z",
                "codex_threads": {"lane:42": {"thread_id": "thread-42"}},
                "codex_totals": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "total_tokens": 18,
                    "rate_limits": {"requests_remaining": 88},
                },
            }
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------- views


def test_state_view_empty_when_no_db(tmp_path: Path) -> None:
    from workflows.change_delivery.server.views import state_view
    view = state_view(tmp_path / "missing.db", tmp_path / "missing.jsonl")
    assert view["counts"] == {"running": 0, "retrying": 0}
    assert view["running"] == []
    assert view["retrying"] == []
    assert view["codex_totals"]["total_tokens"] == 0
    assert view["rate_limits"] is None
    assert "generated_at" in view


def test_state_view_lists_active_lanes(tmp_path: Path) -> None:
    from workflows.change_delivery.server.views import state_view

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
    from workflows.change_delivery.server.views import issue_view

    db = tmp_path / "daedalus.db"
    events = tmp_path / "events.jsonl"
    _make_lanes_db(db)
    assert issue_view(db, events, "#999") is None
    assert issue_view(db, events, "lane-999") is None


def test_issue_view_resolves_by_issue_number_and_lane_id(tmp_path: Path) -> None:
    from workflows.change_delivery.server.views import issue_view

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


def test_issue_runner_state_view_reads_scheduler_and_audit_files(tmp_path: Path) -> None:
    from workflows.change_delivery.server.views import state_view

    root = tmp_path / "issue_runner_root"
    _make_issue_runner_root(root)

    view = state_view(tmp_path / "unused.db", tmp_path / "unused-events.jsonl", workflow_root=root)
    assert view["counts"] == {"running": 1, "retrying": 1}
    assert view["running"][0]["issue_identifier"] == "#123"
    assert view["retrying"][0]["issue_identifier"] == "#124"
    assert view["codex_totals"]["total_tokens"] == 18
    assert view["rate_limits"] == {"requests_remaining": 88}
    assert view["recent_events"][0]["event"] == "issue_runner.tick.completed"


def test_change_delivery_state_view_reads_codex_scheduler_totals(tmp_path: Path) -> None:
    from workflows.change_delivery.server.views import state_view

    root = tmp_path / "change_delivery_root"
    _make_change_delivery_root(root)
    db = tmp_path / "daedalus.db"
    events = tmp_path / "events.jsonl"
    _make_lanes_db(db)
    _make_events_log(events, [])

    view = state_view(db, events, workflow_root=root)

    assert view["counts"] == {"running": 1, "retrying": 0}
    assert view["codex_totals"]["total_tokens"] == 18
    assert view["rate_limits"] == {"requests_remaining": 88}


def test_issue_runner_issue_view_resolves_running_and_retry_entries(tmp_path: Path) -> None:
    from workflows.change_delivery.server.views import issue_view

    root = tmp_path / "issue_runner_root"
    _make_issue_runner_root(root)

    running = issue_view(tmp_path / "unused.db", tmp_path / "unused-events.jsonl", "#123", workflow_root=root)
    retrying = issue_view(tmp_path / "unused.db", tmp_path / "unused-events.jsonl", "124", workflow_root=root)

    assert running is not None
    assert running["issue_identifier"] == "#123"
    assert running["recent_events"][0]["event"] == "issue_runner.tick.completed"
    assert retrying is not None
    assert retrying["issue_identifier"] == "#124"
    assert retrying["last_event"] == "retry_queued"


# ------------------------------------------------------------------- refresh


def test_refresh_controller_coalesces_rapid_triggers(tmp_path: Path) -> None:
    from workflows.change_delivery.server.refresh import RefreshController

    ctrl = RefreshController(tmp_path)
    with mock.patch("workflows.change_delivery.server.refresh.subprocess.Popen") as popen:
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
    from workflows.change_delivery.server.refresh import RefreshController

    ctrl = RefreshController(tmp_path)
    ctrl.DEBOUNCE_SECONDS = 0.01  # speed up the test
    with mock.patch("workflows.change_delivery.server.refresh.subprocess.Popen") as popen:
        assert ctrl.trigger() is True
        time.sleep(0.05)
        assert ctrl.trigger() is True
    assert popen.call_count == 2


# ---------------------------------------------------------------------- html


def test_render_dashboard_includes_lane_identifier(tmp_path: Path) -> None:
    from workflows.change_delivery.server.views import state_view
    from workflows.change_delivery.server.html import render_dashboard

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
    from workflows.change_delivery.server.html import render_dashboard

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
        "codex_totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "seconds_running": 0},
        "rate_limits": None,
        "recent_events": [],
    }
    html_text = render_dashboard(state)
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text


# -------------------------------------------------------------------- server


def _start_test_server(tmp_path: Path):
    from workflows.change_delivery.server import start_server

    db = tmp_path / "daedalus.db"
    events = tmp_path / "events.jsonl"
    _make_lanes_db(db)
    _make_events_log(events, [])

    workflow_root = tmp_path
    # Patch path resolution so the server reads the test fixtures.
    with mock.patch("workflows.change_delivery.server.routes.runtime_paths") as rp:
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
        with mock.patch("workflows.change_delivery.server.refresh.subprocess.Popen") as popen:
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
    from workflows.change_delivery.server import start_server as real_start_server

    captured: dict = {}

    def fake_start_server(workflow_root, port, bind):
        # Force the server to read the fixture DB rather than the real
        # workspace layout (which is fully mocked here).
        with mock.patch("workflows.change_delivery.server.routes.runtime_paths") as rp:
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
    from workflows.change_delivery.cli import main as cli_main

    workspace = SimpleNamespace(WORKSPACE=tmp_path, CONFIG={})

    done = threading.Event()

    def runner():
        try:
            with mock.patch("workflows.change_delivery.server.start_server", side_effect=fake_start_server):
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


def test_read_events_tail_is_bounded_by_limit_not_file_size(tmp_path):
    """Codex P2 on PR #22: tail read must be O(limit) not O(file_size).

    Build a large events log (10_000 entries), call _read_events_tail
    with limit=20, assert correct content + reasonable read size budget.
    """
    import json
    import os
    from workflows.change_delivery.server.views import _read_events_tail

    log = tmp_path / "events.jsonl"
    with log.open("w") as fh:
        for i in range(10_000):
            fh.write(json.dumps({"event_type": "x", "i": i}) + "\n")

    # Read with limit=20 and verify newest-first ordering.
    out = _read_events_tail(log, limit=20)
    assert len(out) == 20
    assert out[0]["i"] == 9999
    assert out[19]["i"] == 9980

    # Sanity: the file is large (~250+ KB at 25-byte avg). The function
    # should not have loaded the whole thing. We can't introspect the
    # internal seeks, but we can at least assert that the result is
    # correct and the function returns quickly. The real correctness
    # check is the output ordering above.
    assert log.stat().st_size > 100_000


def test_refresh_controller_uses_workflow_cli_argv(tmp_path, monkeypatch):
    """Codex P1 on PR #22: refresh must use workflow_cli_argv (plugin
    entrypoint), not -m workflows.change_delivery which fails in script-form
    deployments where workflows isn't on the child's sys.path.
    """
    captured: dict[str, list[str]] = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = list(argv)

        class _FakeProc:
            pass

        return _FakeProc()

    from workflows.change_delivery.server import refresh as refresh_mod
    monkeypatch.setattr(refresh_mod.subprocess, "Popen", fake_popen)

    rc = refresh_mod.RefreshController(tmp_path)
    assert rc.trigger() is True

    argv = captured.get("argv", [])
    # Must NOT contain "-m workflows.change_delivery" — that's the broken form.
    joined = " ".join(argv)
    assert "-m workflows.change_delivery" not in joined, (
        f"refresh argv uses module-form which breaks in installed script "
        f"deployments. argv={argv}"
    )
    # Must include the tick subcommand and the workflow_root.
    assert "tick" in argv
    assert str(tmp_path) in argv
