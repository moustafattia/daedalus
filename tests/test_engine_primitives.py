import json
import sqlite3


def test_engine_storage_writes_json_and_jsonl(tmp_path):
    from engine.storage import append_jsonl, load_optional_json, write_json_atomic, write_text_atomic

    payload_path = tmp_path / "state" / "payload.json"
    write_json_atomic(payload_path, {"b": 2, "a": 1})

    assert payload_path.read_text(encoding="utf-8") == '{\n  "a": 1,\n  "b": 2\n}\n'
    assert load_optional_json(payload_path) == {"a": 1, "b": 2}

    list_path = tmp_path / "state" / "list.json"
    list_path.write_text("[]", encoding="utf-8")
    assert load_optional_json(list_path) is None

    text_path = tmp_path / "state" / "note.txt"
    write_text_atomic(text_path, "hello")
    assert text_path.read_text(encoding="utf-8") == "hello"

    log_path = tmp_path / "audit" / "events.jsonl"
    append_jsonl(log_path, {"event": "b", "at": "now"})
    assert json.loads(log_path.read_text(encoding="utf-8")) == {"at": "now", "event": "b"}


def test_engine_scheduler_restores_legacy_shapes_and_snapshots():
    from engine.scheduler import build_scheduler_payload, restore_scheduler_state, retry_due_at

    restored = restore_scheduler_state(
        {
            "retryQueue": [
                {
                    "issueId": "42",
                    "identifier": "#42",
                    "attempt": 2,
                    "dueAtEpoch": 125.0,
                    "currentAttempt": 1,
                }
            ],
            "running": [
                {
                    "issueId": "43",
                    "workerId": "worker:43",
                    "startedAtEpoch": 100.0,
                    "heartbeatAtEpoch": 110.0,
                    "cancelRequested": True,
                }
            ],
            "codexTotals": {"total_tokens": 5},
            "codex_threads": {"42": {"thread_id": "thread-1", "turn_id": "turn-1"}},
        },
        now_epoch=200.0,
    )

    assert restored.retry_entries["42"]["due_at_epoch"] == 125.0
    assert restored.recovered_running[0]["issue_id"] == "43"
    assert restored.recovered_running[0]["cancel_requested"] is True
    assert restored.codex_totals == {"total_tokens": 5}
    assert restored.codex_threads["42"]["thread_id"] == "thread-1"
    assert retry_due_at(restored.retry_entries["42"], default=999.0) == 125.0

    payload = build_scheduler_payload(
        workflow="issue-runner",
        retry_entries=restored.retry_entries,
        running_entries={"43": restored.recovered_running[0]},
        codex_totals=restored.codex_totals,
        codex_threads=restored.codex_threads,
        now_iso="2026-04-30T00:00:00Z",
        now_epoch=200.0,
    )

    assert payload["workflow"] == "issue-runner"
    assert payload["retry_queue"][0]["due_in_ms"] == 0
    assert payload["running"][0]["running_for_ms"] == 100000
    assert payload["codex_threads"]["42"]["thread_id"] == "thread-1"


def test_engine_work_items_and_lifecycle_helpers():
    from engine.lifecycle import clear_work_entries, mark_running_work, recover_running_as_retry, schedule_retry_entry
    from engine.work_items import work_item_from_change_delivery_lane, work_item_from_issue

    issue_work = work_item_from_issue(
        {
            "id": "ISSUE-1",
            "identifier": "ISSUE-1",
            "state": "todo",
            "title": "Implement it",
            "url": "https://tracker.example/ISSUE-1",
        },
        source="local-json",
    )
    assert issue_work.to_dict()["source"] == "local-json"

    running = mark_running_work({}, work_items=[(issue_work, 2)], now_epoch=100.0)
    assert running["ISSUE-1"]["worker_id"] == "worker:ISSUE-1:100000"
    assert running["ISSUE-1"]["attempt"] == 2
    assert clear_work_entries(running, ["ISSUE-1"]) == {}

    retry, summary = schedule_retry_entry(
        work_item=issue_work,
        existing_entry=None,
        error="temporary failure",
        current_attempt=2,
        delay_type="failure",
        max_backoff_ms=300000,
        now_epoch=100.0,
    )
    assert retry["due_at_epoch"] == 110.0
    assert summary["retry_attempt"] == 1
    assert summary["delay_ms"] == 10000

    recovered = recover_running_as_retry({}, [running["ISSUE-1"]], now_epoch=200.0)
    assert recovered["ISSUE-1"]["error"] == "scheduler restarted while issue was running"
    assert recovered["ISSUE-1"]["due_at_epoch"] == 200.0

    lane_work = work_item_from_change_delivery_lane(
        {
            "lane_id": "lane-42",
            "issue_number": 42,
            "workflow_state": "under_review",
            "lane_status": "active",
        }
    )
    assert lane_work.id == "lane-42"
    assert lane_work.identifier == "#42"
    assert lane_work.source == "change-delivery"
    assert lane_work.metadata["lane_status"] == "active"


def test_engine_audit_writer_fans_out_best_effort(tmp_path):
    from engine.audit import make_audit_fn

    calls = []

    def publisher(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("subscriber failed")

    audit = make_audit_fn(
        audit_log_path=tmp_path / "audit.jsonl",
        now_iso=lambda: "2026-04-30T00:00:00Z",
        publisher=publisher,
    )

    audit("tick", "ran one tick", issue_id="42")

    row = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert row == {
        "action": "tick",
        "at": "2026-04-30T00:00:00Z",
        "issue_id": "42",
        "summary": "ran one tick",
    }
    assert calls == [{"action": "tick", "summary": "ran one tick", "extra": {"issue_id": "42"}}]


def test_engine_sqlite_connection_sets_runtime_pragmas(tmp_path):
    from engine.sqlite import connect_daedalus_db

    db_path = tmp_path / "runtime" / "state" / "daedalus.db"
    conn = connect_daedalus_db(db_path)
    try:
        assert db_path.exists()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()

    reopened = sqlite3.connect(db_path)
    try:
        assert reopened.execute("SELECT 1").fetchone()[0] == 1
    finally:
        reopened.close()


def test_engine_state_persists_scheduler_snapshot_in_sqlite(tmp_path):
    from engine.state import load_engine_scheduler_state, read_engine_scheduler_state, save_engine_scheduler_state

    db_path = tmp_path / "runtime" / "state" / "daedalus.db"
    save_engine_scheduler_state(
        db_path,
        workflow="issue-runner",
        running_entries={
            "ISSUE-1": {
                "issue_id": "ISSUE-1",
                "identifier": "DAE-1",
                "state": "open",
                "worker_id": "worker-1",
                "attempt": 2,
                "started_at_epoch": 100.0,
                "heartbeat_at_epoch": 110.0,
            }
        },
        retry_entries={
            "ISSUE-2": {
                "issue_id": "ISSUE-2",
                "identifier": "DAE-2",
                "attempt": 1,
                "due_at_epoch": 130.0,
                "error": "temporary failure",
            }
        },
        codex_threads={
            "ISSUE-1": {
                "issue_id": "ISSUE-1",
                "identifier": "DAE-1",
                "session_name": "issue-1",
                "runtime_kind": "codex-app-server",
                "thread_id": "thread-1",
                "turn_id": "turn-1",
                "updated_at": "2026-04-30T00:00:00Z",
            }
        },
        codex_totals={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7, "turn_count": 1},
        now_iso="2026-04-30T00:00:00Z",
        now_epoch=120.0,
    )

    loaded = load_engine_scheduler_state(
        db_path,
        workflow="issue-runner",
        now_iso="2026-04-30T00:00:10Z",
        now_epoch=125.0,
    )
    readonly = read_engine_scheduler_state(
        db_path,
        workflow="issue-runner",
        now_iso="2026-04-30T00:00:10Z",
        now_epoch=125.0,
    )

    assert loaded["running"][0]["issue_id"] == "ISSUE-1"
    assert loaded["running"][0]["running_for_ms"] == 25000
    assert loaded["retry_queue"][0]["issue_id"] == "ISSUE-2"
    assert loaded["retry_queue"][0]["due_in_ms"] == 5000
    assert loaded["codex_threads"]["ISSUE-1"]["thread_id"] == "thread-1"
    assert loaded["codex_totals"]["total_tokens"] == 7
    assert readonly == loaded


def test_engine_store_wraps_scheduler_state_and_doctor(tmp_path):
    from engine.store import EngineStore

    db_path = tmp_path / "runtime" / "state" / "daedalus.db"
    store = EngineStore(
        db_path=db_path,
        workflow="issue-runner",
        now_iso=lambda: "2026-04-30T00:00:20Z",
        now_epoch=lambda: 120.0,
    )
    store.save_scheduler(
        running_entries={
            "ISSUE-1": {
                "issue_id": "ISSUE-1",
                "identifier": "DAE-1",
                "state": "open",
                "worker_id": "worker-1",
                "attempt": 1,
                "started_at_epoch": 100.0,
                "heartbeat_at_epoch": 110.0,
            }
        },
        retry_entries={},
        codex_threads={
            "ISSUE-1": {
                "issue_id": "ISSUE-1",
                "identifier": "DAE-1",
                "thread_id": "thread-1",
            }
        },
        codex_totals={"total_tokens": 3},
    )

    snapshot = store.load_scheduler()
    checks = {check["name"]: check for check in store.doctor(stale_running_seconds=60)}

    assert snapshot["running"][0]["issue_id"] == "ISSUE-1"
    assert snapshot["codex_threads"]["ISSUE-1"]["thread_id"] == "thread-1"
    assert checks["engine-schema"]["status"] == "pass"
    assert checks["engine-running-work"]["status"] == "pass"
    assert checks["engine-retry-queue"]["detail"] == "0 queued retry item(s)"
    assert checks["engine-runtime-sessions"]["status"] == "pass"


def test_engine_store_lease_lifecycle_and_stale_status(tmp_path):
    from engine.store import EngineStore

    db_path = tmp_path / "runtime" / "state" / "daedalus.db"
    store = EngineStore(
        db_path=db_path,
        workflow="change-delivery",
        now_iso=lambda: "2026-04-30T00:00:00Z",
        now_epoch=lambda: 1777507200.0,
    )

    acquired = store.acquire_lease(
        lease_scope="runtime",
        lease_key="primary",
        owner_instance_id="owner-1",
        owner_role="Workflow_Orchestrator",
        ttl_seconds=60,
    )
    blocked = store.acquire_lease(
        lease_scope="runtime",
        lease_key="primary",
        owner_instance_id="owner-2",
        owner_role="Workflow_Orchestrator",
        ttl_seconds=60,
    )
    status = store.lease_status(
        lease_scope="runtime",
        lease_key="primary",
        heartbeat_at="2026-04-30T00:00:00Z",
        active_owner_instance_id="owner-1",
    )
    released = store.release_lease(
        lease_scope="runtime",
        lease_key="primary",
        owner_instance_id="owner-1",
        release_reason="shutdown",
    )
    released_status = store.lease_status(
        lease_scope="runtime",
        lease_key="primary",
        heartbeat_at="2026-04-29T23:55:00Z",
        active_owner_instance_id="owner-1",
    )

    assert acquired["acquired"] is True
    assert acquired["expires_at"] == "2026-04-30T00:01:00Z"
    assert blocked == {
        "acquired": False,
        "lease_id": "lease:runtime:primary",
        "owner_instance_id": "owner-1",
    }
    assert status["stale"] is False
    assert released["released"] is True
    assert released_status["stale"] is True
    assert "lease-released" in released_status["stale_reasons"]
    assert "heartbeat-old" in released_status["stale_reasons"]
