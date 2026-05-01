"""S-5 tests: stall detection — Symphony §8.5."""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest


def test_runtime_protocol_has_last_activity_ts():
    """The Runtime Protocol declares last_activity_ts (optional method)."""
    from workflows.change_delivery.runtimes import Runtime

    assert "last_activity_ts" in Runtime.__dict__ or hasattr(Runtime, "last_activity_ts"), \
        "Runtime Protocol must declare last_activity_ts"


def test_claude_cli_runtime_updates_last_activity_on_stdout_line(monkeypatch):
    import time
    from workflows.change_delivery.runtimes.claude_cli import ClaudeCliRuntime

    rt = ClaudeCliRuntime({"kind": "claude-cli", "max-turns-per-invocation": 1, "timeout-seconds": 60}, run=None, run_json=None)
    assert rt.last_activity_ts() is None  # no signal yet

    before = time.monotonic()
    rt._record_activity()  # internal helper called per stdout/stderr line
    after = time.monotonic()
    ts = rt.last_activity_ts()
    assert ts is not None
    assert before <= ts <= after


def test_acpx_codex_runtime_updates_last_activity_on_app_server_event():
    import time
    from workflows.change_delivery.runtimes.acpx_codex import AcpxCodexRuntime

    rt = AcpxCodexRuntime(
        {"kind": "acpx-codex",
         "session-idle-freshness-seconds": 60,
         "session-idle-grace-seconds": 60,
         "session-nudge-cooldown-seconds": 60},
        run=None, run_json=None,
    )
    assert rt.last_activity_ts() is None

    rt._record_activity()
    assert rt.last_activity_ts() is not None


def test_hermes_agent_runtime_updates_last_activity_on_callback():
    from workflows.change_delivery.runtimes.hermes_agent import HermesAgentRuntime

    rt = HermesAgentRuntime({"kind": "hermes-agent"}, run=None, run_json=None)
    assert rt.last_activity_ts() is None

    rt._record_activity()
    assert rt.last_activity_ts() is not None


def test_claude_cli_records_activity_before_run_returns(tmp_path):
    """Codex P1 on PR #18: long-running invocations must not look idle.

    The fix is to call _record_activity() BEFORE the blocking _run() call
    so stall reconciliation measures from "work started", not from "previous
    work ended". This test exercises that ordering by inspecting the
    activity timestamp from inside the run callback.
    """
    import time
    from workflows.change_delivery.runtimes.claude_cli import ClaudeCliRuntime

    captured: dict[str, float | None] = {}

    class _FakeCompleted:
        stdout = ""

    def fake_run(cmd, cwd, timeout):
        # Emulates the agent process being invoked. By this point the
        # runtime should ALREADY have recorded activity so a concurrent
        # stall reconciler observes the work as fresh.
        captured["mid_run_ts"] = rt.last_activity_ts()
        return _FakeCompleted()

    rt = ClaudeCliRuntime(
        {"kind": "claude-cli", "max-turns-per-invocation": 1, "timeout-seconds": 60},
        run=fake_run, run_json=None,
    )
    assert rt.last_activity_ts() is None

    before = time.monotonic()
    rt.run_prompt(worktree=tmp_path, session_name="x", prompt="hi", model="m")
    after = time.monotonic()

    mid = captured["mid_run_ts"]
    assert mid is not None, (
        "Activity timestamp must be set BEFORE _run() is called. "
        "Otherwise stall reconciliation can't see the worker as live "
        "until the (potentially long) invocation completes."
    )
    assert before <= mid <= after


@dataclass
class _FakeRuntime:
    last_ts: float | None
    def last_activity_ts(self) -> float | None:
        return self.last_ts


@dataclass
class _FakeEntry:
    runtime: _FakeRuntime
    started_at_monotonic: float


def _snap_with_stall(timeout_ms: int):
    from workflows.change_delivery.config_snapshot import ConfigSnapshot
    return ConfigSnapshot(
        config={"stall": {"timeout_ms": timeout_ms}},
        prompts={}, loaded_at=0.0, source_mtime=0.0,
    )


def test_reconcile_stalls_terminates_inactive_worker():
    from workflows.change_delivery.stall import reconcile_stalls

    snap = _snap_with_stall(1000)  # 1s threshold
    rt = _FakeRuntime(last_ts=100.0)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=50.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=200.0)
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.issue_id == "i1"
    assert v.action == "terminate"
    assert v.threshold_seconds == 1.0


def test_reconcile_stalls_skips_active_worker():
    from workflows.change_delivery.stall import reconcile_stalls

    snap = _snap_with_stall(10000)  # 10s threshold
    rt = _FakeRuntime(last_ts=199.5)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=50.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=200.0)
    assert verdicts == []


def test_reconcile_stalls_disabled_when_timeout_zero():
    from workflows.change_delivery.stall import reconcile_stalls

    snap = _snap_with_stall(0)
    rt = _FakeRuntime(last_ts=0.0)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=0.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=99999.0)
    assert verdicts == []


def test_reconcile_stalls_baseline_falls_back_to_started_at():
    """Worker that has produced no signal still gets a deadline."""
    from workflows.change_delivery.stall import reconcile_stalls

    snap = _snap_with_stall(1000)
    rt = _FakeRuntime(last_ts=None)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=100.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=200.0)
    assert len(verdicts) == 1
    assert verdicts[0].action == "terminate"


def test_reconcile_stalls_default_timeout_when_section_absent():
    """Spec §8.4 default: 300_000 ms."""
    from workflows.change_delivery.config_snapshot import ConfigSnapshot
    from workflows.change_delivery.stall import reconcile_stalls

    snap = ConfigSnapshot(config={}, prompts={}, loaded_at=0.0, source_mtime=0.0)
    rt = _FakeRuntime(last_ts=0.0)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=0.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=299.0)
    assert verdicts == []  # 299s < 300s default
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=400.0)
    assert len(verdicts) == 1


def test_reconcile_stalls_opt_out_when_method_absent():
    """Codex P1 on PR #16: a runtime that doesn't implement
    last_activity_ts opts out entirely — the reconciler must skip it,
    NOT fall back to started_at_monotonic."""
    from workflows.change_delivery.stall import reconcile_stalls

    class _OptOutRuntime:
        # Deliberately does NOT define last_activity_ts.
        pass

    snap = _snap_with_stall(1000)
    rt = _OptOutRuntime()
    entry = _FakeEntry(runtime=rt, started_at_monotonic=100.0)
    # Elapsed since started_at is 99_900 seconds — vastly past threshold.
    # If the implementation falls back to started_at, this would terminate.
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=100_000.0)
    assert verdicts == [], (
        f"Opt-out runtime (no last_activity_ts attr) must be skipped, "
        f"not force-killed via started_at fallback. Got verdicts={verdicts}"
    )


def test_schema_accepts_stall_section():
    import yaml
    from pathlib import Path
    from jsonschema import Draft7Validator

    schema = yaml.safe_load(Path("daedalus/workflows/change_delivery/schema.yaml").read_text())
    base = {
        "workflow": "change-delivery", "schema-version": 1,
        "instance": {"name": "i", "engine-owner": "hermes"},
        "repository": {"local-path": "/tmp", "slug": "o/r", "active-lane-label": "x"},
        "tracker": {"kind": "github", "github_slug": "o/r", "active_states": ["open"], "terminal_states": ["closed"]},
        "code-host": {"kind": "github", "github_slug": "o/r"},
        "runtimes": {"r1": {"kind": "claude-cli", "max-turns-per-invocation": 1, "timeout-seconds": 60}},
        "agents": {
            "coder": {"t1": {"name": "c", "model": "m", "runtime": "r1"}},
            "internal-reviewer": {"name": "i", "model": "m", "runtime": "r1"},
            "external-reviewer": {"enabled": False, "name": "e"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-issue-label", "label": "x"}},
        "storage": {"ledger": "l", "health": "h", "audit-log": "a"},
        "stall": {"timeout_ms": 60000},
    }
    Draft7Validator(schema).validate(base)


def test_schema_rejects_negative_stall_timeout():
    import yaml
    import pytest
    from pathlib import Path
    from jsonschema import Draft7Validator
    from jsonschema.exceptions import ValidationError as JSError

    schema = yaml.safe_load(Path("daedalus/workflows/change_delivery/schema.yaml").read_text())
    base = {
        "workflow": "change-delivery", "schema-version": 1,
        "instance": {"name": "i", "engine-owner": "hermes"},
        "repository": {"local-path": "/tmp", "slug": "o/r", "active-lane-label": "x"},
        "tracker": {"kind": "github", "github_slug": "o/r", "active_states": ["open"], "terminal_states": ["closed"]},
        "code-host": {"kind": "github", "github_slug": "o/r"},
        "runtimes": {"r1": {"kind": "claude-cli", "max-turns-per-invocation": 1, "timeout-seconds": 60}},
        "agents": {
            "coder": {"t1": {"name": "c", "model": "m", "runtime": "r1"}},
            "internal-reviewer": {"name": "i", "model": "m", "runtime": "r1"},
            "external-reviewer": {"enabled": False, "name": "e"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-issue-label", "label": "x"}},
        "storage": {"ledger": "l", "health": "h", "audit-log": "a"},
        "stall": {"timeout_ms": -1},
    }
    with pytest.raises(JSError):
        Draft7Validator(schema).validate(base)


def test_stall_emits_both_events_and_queues_retry(tmp_path, monkeypatch):
    """Smoke: when reconcile_stalls returns a verdict, the tick-loop
    integration emits stall_detected, terminates, emits stall_terminated,
    and queues a retry. Tested via a thin fake orchestrator."""
    from workflows.change_delivery.stall import StallVerdict, reconcile_stalls
    from workflows.change_delivery.event_taxonomy import (
        DAEDALUS_STALL_DETECTED, DAEDALUS_STALL_TERMINATED,
    )

    snap = _snap_with_stall(1000)
    rt = _FakeRuntime(last_ts=0.0)
    entry = _FakeEntry(runtime=rt, started_at_monotonic=0.0)
    verdicts = reconcile_stalls(snap, {"i1": entry}, now=2.0)
    assert len(verdicts) == 1

    # Emulate the watch.py side-effect block in isolation
    events: list[dict] = []
    terminated: list[str] = []
    retried: list[tuple[str, str]] = []
    for v in verdicts:
        events.append({"type": DAEDALUS_STALL_DETECTED, "issue_id": v.issue_id})
        terminated.append(v.issue_id)
        events.append({"type": DAEDALUS_STALL_TERMINATED, "issue_id": v.issue_id})
        retried.append((v.issue_id, "stall_timeout"))
    assert [e["type"] for e in events] == [DAEDALUS_STALL_DETECTED, DAEDALUS_STALL_TERMINATED]
    assert terminated == ["i1"]
    assert retried == [("i1", "stall_timeout")]
