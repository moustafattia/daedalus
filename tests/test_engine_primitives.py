import json
import sqlite3
import threading


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
            "codexThreads": {"42": {"thread_id": "thread-1", "turn_id": "turn-1"}},
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

    restored_zero = restore_scheduler_state(
        {
            "retryQueue": [{"issueId": "zero-retry", "dueAtEpoch": 0.0}],
            "running": [{"issueId": "zero-running", "startedAtEpoch": 0.0, "heartbeatAtEpoch": 0.0}],
        },
        now_epoch=200.0,
    )
    assert restored_zero.retry_entries["zero-retry"]["due_at_epoch"] == 0.0
    assert restored_zero.recovered_running[0]["started_at_epoch"] == 0.0
    assert restored_zero.recovered_running[0]["heartbeat_at_epoch"] == 0.0


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
    events = []

    def publisher(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("subscriber failed")

    def event_sink(event):
        events.append(event)
        raise RuntimeError("index failed")

    audit = make_audit_fn(
        audit_log_path=tmp_path / "audit.jsonl",
        now_iso=lambda: "2026-04-30T00:00:00Z",
        publisher=publisher,
        event_sink=event_sink,
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
    assert events == [row]


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
                "run_id": "run-1",
            }
        },
        retry_entries={
            "ISSUE-2": {
                "issue_id": "ISSUE-2",
                "identifier": "DAE-2",
                "attempt": 1,
                "due_at_epoch": 130.0,
                "error": "temporary failure",
                "run_id": "run-1",
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
                "run_id": "run-1",
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
    assert loaded["running"][0]["run_id"] == "run-1"
    assert loaded["running"][0]["running_for_ms"] == 25000
    assert loaded["retry_queue"][0]["issue_id"] == "ISSUE-2"
    assert loaded["retry_queue"][0]["run_id"] == "run-1"
    assert loaded["retry_queue"][0]["due_in_ms"] == 5000
    assert loaded["codex_threads"]["ISSUE-1"]["thread_id"] == "thread-1"
    assert loaded["codex_threads"]["ISSUE-1"]["run_id"] == "run-1"
    assert loaded["codex_totals"]["total_tokens"] == 7
    assert readonly == loaded


def test_engine_state_preserves_zero_epoch_scheduler_values(tmp_path):
    from engine.state import load_engine_scheduler_state, save_engine_scheduler_state

    db_path = tmp_path / "runtime" / "state" / "daedalus.db"
    save_engine_scheduler_state(
        db_path,
        workflow="issue-runner",
        running_entries={
            "ISSUE-0": {
                "issue_id": "ISSUE-0",
                "identifier": "DAE-0",
                "started_at_epoch": 0.0,
                "heartbeat_at_epoch": 0.0,
            }
        },
        retry_entries={
            "ISSUE-RETRY-0": {
                "issue_id": "ISSUE-RETRY-0",
                "identifier": "DAE-R0",
                "due_at_epoch": 0.0,
                "error": "immediate retry",
            }
        },
        codex_threads={},
        codex_totals={},
        now_iso="2026-04-30T00:00:00Z",
        now_epoch=120.0,
    )

    loaded = load_engine_scheduler_state(
        db_path,
        workflow="issue-runner",
        now_iso="2026-04-30T00:00:10Z",
        now_epoch=125.0,
    )

    assert loaded["running"][0]["started_at_epoch"] == 0.0
    assert loaded["running"][0]["heartbeat_at_epoch"] == 0.0
    assert loaded["running"][0]["running_for_ms"] == 125000
    assert loaded["retry_queue"][0]["due_at_epoch"] == 0.0
    assert loaded["retry_queue"][0]["due_in_ms"] == 0


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
    assert checks["engine-runs"]["status"] == "pass"
    assert checks["engine-events"]["status"] == "pass"
    assert checks["engine-runtime-sessions"]["status"] == "pass"


def test_engine_store_tracks_run_ledger_and_stale_runs(tmp_path):
    from engine.store import EngineStore

    clock = {"iso": "2026-04-30T00:00:00Z", "epoch": 100.0}
    store = EngineStore(
        db_path=tmp_path / "runtime" / "state" / "daedalus.db",
        workflow="issue-runner",
        now_iso=lambda: clock["iso"],
        now_epoch=lambda: clock["epoch"],
    )

    first = store.start_run(mode="tick", metadata={"source": "test"})
    clock.update({"iso": "2026-04-30T00:00:05Z", "epoch": 105.0})
    completed = store.complete_run(
        first["run_id"],
        selected_count=2,
        completed_count=2,
        metadata={"result": "ok"},
    )
    stale = store.start_run(mode="supervised")
    clock.update({"iso": "2026-04-30T00:20:00Z", "epoch": 1300.0})

    latest = store.latest_runs(limit=5)
    checks = {check["name"]: check for check in store.doctor(stale_running_seconds=60)}

    assert completed["status"] == "completed"
    assert completed["metadata"] == {"source": "test", "result": "ok"}
    assert latest[0]["run_id"] == stale["run_id"]
    assert latest[0]["status"] == "running"
    assert latest[1]["completed_count"] == 2
    assert checks["engine-runs"]["status"] == "warn"
    assert stale["run_id"] in checks["engine-runs"]["items"]


def test_engine_store_tracks_event_ledger_and_doctor_orphans(tmp_path):
    from engine.store import EngineStore

    clock = {"iso": "2026-04-30T00:00:00Z", "epoch": 100.0}
    store = EngineStore(
        db_path=tmp_path / "runtime" / "state" / "daedalus.db",
        workflow="issue-runner",
        now_iso=lambda: clock["iso"],
        now_epoch=lambda: clock["epoch"],
    )

    run = store.start_run(mode="tick")
    event = store.append_event(
        payload={
            "event": "issue_runner.tick.completed",
            "run_id": run["run_id"],
            "issue_id": "ISSUE-1",
        },
    )
    events = store.events_for_run(run["run_id"])
    checks = {check["name"]: check for check in store.doctor()}

    assert event["event_type"] == "issue_runner.tick.completed"
    assert event["inserted"] is True
    assert event["work_id"] == "ISSUE-1"
    assert events[0]["event_id"] == event["event_id"]
    assert events[0]["payload"]["issue_id"] == "ISSUE-1"
    assert checks["engine-events"]["status"] == "pass"

    duplicate = store.append_event(
        event_id=event["event_id"],
        event_type="changed",
        payload={"run_id": run["run_id"], "issue_id": "ISSUE-2"},
    )
    assert duplicate["inserted"] is False
    assert duplicate["event_type"] == "issue_runner.tick.completed"
    assert duplicate["work_id"] == "ISSUE-1"
    assert len(store.events_for_run(run["run_id"])) == 1

    clock.update({"iso": "2026-04-30T00:00:01Z", "epoch": 101.0})
    orphaned = store.append_event(event_type="runtime.error", payload={"run_id": "missing-run"})
    checks = {check["name"]: check for check in store.doctor()}

    assert checks["engine-events"]["status"] == "warn"
    assert orphaned["event_id"] in checks["engine-events"]["items"]


def test_engine_run_and_event_ids_are_workflow_scoped_after_schema_migration(tmp_path):
    from engine.store import EngineStore

    db_path = tmp_path / "runtime" / "state" / "daedalus.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE engine_runs (
              workflow TEXT NOT NULL,
              run_id TEXT PRIMARY KEY,
              mode TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              started_at_epoch REAL NOT NULL,
              completed_at TEXT,
              completed_at_epoch REAL,
              selected_count INTEGER NOT NULL DEFAULT 0,
              completed_count INTEGER NOT NULL DEFAULT 0,
              error TEXT,
              metadata_json TEXT
            );

            CREATE TABLE engine_events (
              workflow TEXT NOT NULL,
              event_id TEXT PRIMARY KEY,
              run_id TEXT,
              work_id TEXT,
              event_type TEXT NOT NULL,
              severity TEXT NOT NULL DEFAULT 'info',
              created_at TEXT NOT NULL,
              created_at_epoch REAL NOT NULL,
              payload_json TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    clock = {"iso": "2026-04-30T00:00:00Z", "epoch": 100.0}
    issue_runner = EngineStore(
        db_path=db_path,
        workflow="issue-runner",
        now_iso=lambda: clock["iso"],
        now_epoch=lambda: clock["epoch"],
    )
    change_delivery = EngineStore(
        db_path=db_path,
        workflow="change-delivery",
        now_iso=lambda: clock["iso"],
        now_epoch=lambda: clock["epoch"],
    )

    issue_run = issue_runner.start_run(mode="tick", run_id="shared-run")
    change_run = change_delivery.start_run(mode="tick", run_id="shared-run")
    issue_event = issue_runner.append_event(
        event_id="shared-event",
        event_type="issue.event",
        payload={"run_id": issue_run["run_id"]},
    )
    change_event = change_delivery.append_event(
        event_id="shared-event",
        event_type="change.event",
        payload={"run_id": change_run["run_id"]},
    )

    assert issue_run["workflow"] == "issue-runner"
    assert change_run["workflow"] == "change-delivery"
    assert issue_event["inserted"] is True
    assert change_event["inserted"] is True
    assert issue_runner.get_run("shared-run")["workflow"] == "issue-runner"
    assert change_delivery.get_run("shared-run")["workflow"] == "change-delivery"
    assert issue_runner.events_for_run("shared-run")[0]["event_type"] == "issue.event"
    assert change_delivery.events_for_run("shared-run")[0]["event_type"] == "change.event"

    conn = sqlite3.connect(db_path)
    try:
        run_pk = [row[1] for row in conn.execute("PRAGMA table_info(engine_runs)") if row[5]]
        event_pk = [row[1] for row in conn.execute("PRAGMA table_info(engine_events)") if row[5]]
    finally:
        conn.close()
    assert run_pk == ["workflow", "run_id"]
    assert event_pk == ["workflow", "event_id"]


def test_engine_store_filters_and_prunes_events(tmp_path):
    from engine.store import EngineStore

    clock = {"iso": "2026-04-30T00:00:00Z", "epoch": 100.0}
    store = EngineStore(
        db_path=tmp_path / "runtime" / "state" / "daedalus.db",
        workflow="issue-runner",
        now_iso=lambda: clock["iso"],
        now_epoch=lambda: clock["epoch"],
    )
    run = store.start_run(mode="tick")
    store.append_event(event_type="a", payload={"run_id": run["run_id"], "issue_id": "ISSUE-1"})
    clock.update({"iso": "2026-04-30T00:00:01Z", "epoch": 101.0})
    store.append_event(event_type="b", payload={"run_id": run["run_id"], "issue_id": "ISSUE-2"}, severity="warn")
    clock.update({"iso": "2026-04-30T00:00:02Z", "epoch": 102.0})
    store.append_event(event_type="b", payload={"run_id": run["run_id"], "issue_id": "ISSUE-1"})

    assert [event["event_type"] for event in store.events(event_type="b")] == ["b", "b"]
    assert [event["work_id"] for event in store.events(work_id="ISSUE-1", order="asc")] == ["ISSUE-1", "ISSUE-1"]
    assert store.events(severity="warn")[0]["work_id"] == "ISSUE-2"

    stats = store.event_stats({"events": {"max-age-seconds": 1, "max-rows": 2}})
    checks = {
        check["name"]: check
        for check in store.doctor(event_retention={"events": {"max-age-seconds": 1, "max-rows": 2}})
    }
    not_configured = store.apply_event_retention({})
    pruned = store.apply_event_retention({"events": {"max-rows": 1}})
    remaining = store.events()

    assert stats["total_events"] == 3
    assert stats["oldest_age_seconds"] == 2.0
    assert stats["by_type"] == {"b": 2, "a": 1}
    assert stats["by_severity"] == {"info": 2, "warn": 1}
    assert stats["retention"]["excess_rows"] == 1
    assert stats["retention"]["age_overdue"] is True
    assert checks["engine-event-retention"]["status"] == "warn"
    assert "excess_rows=1" in checks["engine-event-retention"]["detail"]
    assert not_configured["applied"] is False
    assert not_configured["reason"] == "not-configured"
    assert pruned["applied"] is True
    assert pruned["deleted"] == 2
    assert pruned["remaining"] == 1
    assert remaining[0]["event_type"] == "b"
    assert remaining[0]["work_id"] == "ISSUE-1"


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


def test_engine_lease_acquire_allows_only_one_contender_for_expired_lease(tmp_path):
    from engine.leases import acquire_engine_lease, init_engine_leases

    db_path = tmp_path / "runtime" / "state" / "daedalus.db"
    db_path.parent.mkdir(parents=True)
    seed = sqlite3.connect(db_path)
    try:
        init_engine_leases(seed)
        seed.execute(
            """
            INSERT INTO leases (
              lease_id, lease_scope, lease_key, owner_instance_id, owner_role,
              acquired_at, expires_at, released_at, release_reason, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            (
                "lease:runtime:primary",
                "runtime",
                "primary",
                "old-owner",
                "Workflow_Orchestrator",
                "2026-04-30T00:00:00Z",
                "2026-04-30T00:00:01Z",
            ),
        )
        seed.commit()
    finally:
        seed.close()

    barrier = threading.Barrier(2)

    class ContendedAcquireConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            normalized = sql.lstrip()
            waits_before_first_write = normalized.startswith("INSERT OR IGNORE INTO leases")
            waits_before_legacy_read = normalized.startswith(
                "SELECT owner_instance_id, expires_at, released_at FROM leases"
            )
            if not getattr(self, "_lease_barrier_waited", False) and (
                waits_before_first_write or waits_before_legacy_read
            ):
                self._lease_barrier_waited = True
                barrier.wait(timeout=5)
            return super().execute(sql, parameters)

    results: list[dict] = []
    errors: list[str] = []

    def acquire(owner: str) -> None:
        conn = sqlite3.connect(db_path, timeout=5, factory=ContendedAcquireConnection)
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            results.append(
                acquire_engine_lease(
                    conn,
                    lease_scope="runtime",
                    lease_key="primary",
                    owner_instance_id=owner,
                    owner_role="Workflow_Orchestrator",
                    now_iso="2026-04-30T00:02:00Z",
                    ttl_seconds=60,
                )
            )
            conn.commit()
        except Exception as exc:
            errors.append(f"{owner}: {type(exc).__name__}: {exc}")
        finally:
            conn.close()

    threads = [threading.Thread(target=acquire, args=(owner,)) for owner in ("owner-a", "owner-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 2
    acquired = [result for result in results if result["acquired"]]
    blocked = [result for result in results if not result["acquired"]]
    assert len(acquired) == 1
    assert len(blocked) == 1
    assert blocked[0]["owner_instance_id"] == acquired[0]["owner_instance_id"]

    final = sqlite3.connect(db_path)
    try:
        row = final.execute(
            "SELECT owner_instance_id, released_at FROM leases WHERE lease_scope=? AND lease_key=?",
            ("runtime", "primary"),
        ).fetchone()
    finally:
        final.close()
    assert row == (acquired[0]["owner_instance_id"], None)


def test_engine_lease_release_cannot_release_new_owner_after_reclaim(tmp_path):
    from engine.leases import acquire_engine_lease, init_engine_leases, release_engine_lease

    db_path = tmp_path / "runtime" / "state" / "daedalus.db"
    db_path.parent.mkdir(parents=True)
    seed = sqlite3.connect(db_path)
    try:
        init_engine_leases(seed)
        seed.execute(
            """
            INSERT INTO leases (
              lease_id, lease_scope, lease_key, owner_instance_id, owner_role,
              acquired_at, expires_at, released_at, release_reason, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            (
                "lease:runtime:primary",
                "runtime",
                "primary",
                "owner-a",
                "Workflow_Orchestrator",
                "2026-04-30T00:00:00Z",
                "2026-04-30T00:00:01Z",
            ),
        )
        seed.commit()
    finally:
        seed.close()

    release_ready = threading.Event()
    release_continue = threading.Event()

    class DelayedReleaseConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if sql.lstrip().startswith("UPDATE leases") and "release_reason" in sql:
                release_ready.set()
                assert release_continue.wait(timeout=5)
            return super().execute(sql, parameters)

    release_result: dict[str, dict] = {}
    errors: list[str] = []

    def release_stale_owner() -> None:
        conn = sqlite3.connect(db_path, timeout=5, factory=DelayedReleaseConnection)
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            release_result["value"] = release_engine_lease(
                conn,
                lease_scope="runtime",
                lease_key="primary",
                owner_instance_id="owner-a",
                now_iso="2026-04-30T00:02:00Z",
                release_reason="shutdown",
            )
            conn.commit()
        except Exception as exc:
            errors.append(f"release: {type(exc).__name__}: {exc}")
        finally:
            conn.close()

    thread = threading.Thread(target=release_stale_owner)
    thread.start()
    assert release_ready.wait(timeout=5)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            acquired = acquire_engine_lease(
                conn,
                lease_scope="runtime",
                lease_key="primary",
                owner_instance_id="owner-b",
                owner_role="Workflow_Orchestrator",
                now_iso="2026-04-30T00:02:00Z",
                ttl_seconds=60,
            )
            conn.commit()
        finally:
            conn.close()
    finally:
        release_continue.set()
    thread.join(timeout=10)

    assert errors == []
    assert acquired["acquired"] is True
    assert release_result["value"]["released"] is False
    assert release_result["value"]["owner_instance_id"] == "owner-b"

    final = sqlite3.connect(db_path)
    try:
        row = final.execute(
            "SELECT owner_instance_id, released_at FROM leases WHERE lease_scope=? AND lease_key=?",
            ("runtime", "primary"),
        ).fetchone()
    finally:
        final.close()
    assert row == ("owner-b", None)
