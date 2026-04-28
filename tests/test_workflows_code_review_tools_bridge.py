import importlib.util
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_shadow_report_uses_adapter_status_bridge(monkeypatch, tmp_path):
    tools_module = load_module("daedalus_tools_bridge_test", "tools.py")
    runtime_module = load_module("daedalus_runtime_for_tools_bridge_test", "runtime.py")
    workflow_root = tmp_path / "workflow"
    runtime_paths = runtime_module._runtime_paths(workflow_root)
    runtime_module.init_daedalus_db(workflow_root=workflow_root, project_key="yoyopod")

    monkeypatch.setattr(
        tools_module,
        "service_status",
        lambda **_kwargs: {"installed": True, "enabled": "enabled", "active": "active", "service_name": "relay.service"},
    )
    monkeypatch.setattr(
        tools_module,
        "_evaluate_service_supervision",
        lambda **_kwargs: {"healthy": True, "reasons": [], "expected_service_mode": None, "summary": "ok"},
    )
    monkeypatch.setattr(
        tools_module,
        "_build_project_status",
        lambda _workflow_root: {
            "activeLane": {"number": 224, "title": "Issue 224", "url": "https://example.com/issues/224", "labels": []},
            "activeLaneError": None,
            "nextAction": {"type": "noop", "reason": "workflow-not-healthy:stale-lane"},
            "derivedReviewLoopState": "clean",
            "derivedMergeBlocked": False,
            "derivedMergeBlockers": [],
            "implementation": {},
            "reviews": {},
            "ledger": {"workflowState": "implementing_local", "reviewState": "implementing_local", "repairBrief": None},
            "repo": "/tmp/repo",
            "openPr": None,
            "staleLaneReasons": [],
        },
        raising=False,
    )

    relay_stub = SimpleNamespace(
        RELAY_OWNER="relay",
        RUNTIME_LEASE_SCOPE="runtime",
        RUNTIME_LEASE_KEY="primary-orchestrator",
        get_runtime_status=lambda **_kwargs: {
            "runtime_status": "running",
            "latest_heartbeat_at": "2026-04-22T01:00:00Z",
            "active_orchestrator_instance_id": "relay",
            "current_mode": "active",
        },
        _now_iso=lambda: "2026-04-22T01:00:00Z",
        _iso_to_epoch=lambda _value: 0,
        ingest_legacy_status=lambda **_kwargs: {"lane_id": None},
        evaluate_active_execution_gate=lambda **_kwargs: {
            "primary_owner": "relay",
            "execution": {"active_execution_enabled": True},
            "allowed": True,
            "reasons": [],
        },
        _runtime_paths=lambda _workflow_root: runtime_paths,
        query_recent_failures=lambda **_kwargs: [],
        query_stuck_dispatched_actions=lambda **_kwargs: [],
        DISPATCHED_ACTION_TIMEOUT_SECONDS=1800,
    )
    monkeypatch.setattr(tools_module, "_load_daedalus_module", lambda _workflow_root: relay_stub)

    report = tools_module.build_shadow_report(workflow_root=workflow_root)

    assert report["legacy"]["next_action_type"] == "noop"
    assert report["active_lane"]["issue_number"] == 224
