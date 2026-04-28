"""S-5 tests: stall detection — Symphony §8.5."""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest


def test_runtime_protocol_has_last_activity_ts():
    """The Runtime Protocol declares last_activity_ts (optional method)."""
    from workflows.code_review.runtimes import Runtime

    assert "last_activity_ts" in Runtime.__dict__ or hasattr(Runtime, "last_activity_ts"), \
        "Runtime Protocol must declare last_activity_ts"


def test_claude_cli_runtime_updates_last_activity_on_stdout_line(monkeypatch):
    import time
    from workflows.code_review.runtimes.claude_cli import ClaudeCliRuntime

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
    from workflows.code_review.runtimes.acpx_codex import AcpxCodexRuntime

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
