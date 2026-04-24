import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_workspace(**overrides):
    """Build a fake workspace accessor whose callables record invocations."""

    calls: list = []

    def _recorder(name, return_value=None):
        def _call(*args, **kwargs):
            calls.append((name, args, kwargs))
            if isinstance(return_value, Exception):
                raise return_value
            return return_value
        return _call

    ws = SimpleNamespace(
        calls=calls,
        HEALTH_PATH=Path("/tmp/health.json"),
        AUDIT_LOG_PATH=Path("/tmp/audit.jsonl"),
        WORKFLOW_WATCHDOG_JOB_NAME="yoyopod-workflow-watchdog",
        build_status=_recorder("build_status", {
            "updatedAt": "2026-04-24T00:00:00Z",
            "activeLane": {"number": 224, "title": "T"},
            "activeLaneError": None,
            "openPr": None,
            "health": "healthy",
            "ledger": {"workflowState": "implementing_local", "workflowIdle": False},
            "implementation": {"worktree": "/tmp/worktree", "branch": "yoyopod-issue-224", "status": "implementing_local", "laneStatePath": "/tmp/lane.json", "laneMemoPath": "/tmp/lane.md"},
            "reviews": {},
            "derivedReviewLoopState": "awaiting_reviews",
            "preflight": {"interReviewAgent": {"shouldRun": True, "currentHeadSha": "abc", "wakeSuggested": True}},
            "coreJobs": {"j1": {}},
            "missingCoreJobs": [],
            "disabledCoreJobs": [],
            "staleCoreJobs": [],
            "brokenIssueWatchers": [],
            "drift": [],
            "staleLaneReasons": [],
        }),
        reconcile=_recorder("reconcile", {"health": "healthy"}),
        doctor=_recorder("doctor", {"before": "healthy", "after": "healthy"}),
        tick=_recorder("tick", {"ticked": True}),
        publish_ready_pr=_recorder("publish_ready_pr", {"published": False}),
        push_pr_update=_recorder("push_pr_update", {"pushed": False}),
        merge_and_promote=_recorder("merge_and_promote", {"merged": False}),
        dispatch_implementation_turn=_recorder("dispatch_implementation_turn", {"dispatched": False}),
        dispatch_inter_review_agent_review=_recorder("dispatch_inter_review_agent_review", {"dispatched": False}),
        dispatch_repair_handoff=_recorder("dispatch_repair_handoff", {"dispatched": False}),
        restart_actor_session=_recorder("restart_actor_session", {"dispatched": False}),
        set_core_jobs_enabled=_recorder("set_core_jobs_enabled", {"health": "healthy"}),
        wake_core_jobs=_recorder("wake_core_jobs", {"health": "healthy"}),
        wake_named_jobs=_recorder("wake_named_jobs", {"result": "woken"}),
        write_lane_state=_recorder("write_lane_state", None),
        write_lane_memo=_recorder("write_lane_memo", None),
        load_ledger=_recorder("load_ledger", {"repairBrief": None}),
        _write_json=_recorder("_write_json", None),
        _load_optional_json=_recorder("_load_optional_json", None),
        _summarize_validation=_recorder("_summarize_validation", []),
    )
    for k, v in overrides.items():
        setattr(ws, k, v)
    return ws


def _run_main(workspace, argv):
    cli = load_module("hermes_relay_yoyopod_core_cli_test", "adapters/yoyopod_core/cli.py")
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(workspace, argv=argv)
    return code, buf.getvalue()


def test_status_command_prints_human_summary():
    ws = _make_workspace()
    code, out = _run_main(ws, ["status"])
    assert code == 0
    assert "health: healthy" in out
    assert "active lane: #224" in out
    assert "health file: /tmp/health.json" in out


def test_status_command_json_flag_emits_full_payload():
    ws = _make_workspace()
    code, out = _run_main(ws, ["status", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["health"] == "healthy"
    assert payload["activeLane"]["number"] == 224


def test_status_write_health_flag_writes_files_and_calls_recorders():
    ws = _make_workspace()
    code, _ = _run_main(ws, ["status", "--write-health"])
    assert code == 0
    names = [c[0] for c in ws.calls]
    assert "_write_json" in names
    assert "write_lane_state" in names
    assert "write_lane_memo" in names


def test_reconcile_delegates_and_returns_zero():
    ws = _make_workspace()
    code, out = _run_main(ws, ["reconcile", "--fix-watchers"])
    assert code == 0
    assert json.loads(out)["health"] == "healthy"
    assert ws.calls[0] == ("reconcile", (), {"fix_watchers": True})


def test_doctor_honors_no_fix_watchers_flag():
    ws = _make_workspace()
    _run_main(ws, ["doctor", "--no-fix-watchers"])
    assert ws.calls[0] == ("doctor", (), {"fix_watchers": False})


def test_pause_resume_wake_each_call_their_workspace_helpers():
    ws = _make_workspace()
    _run_main(ws, ["pause"])
    _run_main(ws, ["resume"])
    _run_main(ws, ["wake"])
    names = [c[0] for c in ws.calls]
    assert names == ["set_core_jobs_enabled", "set_core_jobs_enabled", "wake_core_jobs"]
    assert ws.calls[0][2] == {"wake_now": False}
    assert ws.calls[1][2] == {"wake_now": True}


def test_wake_job_forwards_name():
    ws = _make_workspace()
    _run_main(ws, ["wake-job", "yoyopod-workflow-watchdog"])
    assert ws.calls[0] == ("wake_named_jobs", (["yoyopod-workflow-watchdog"],), {})


def test_preflight_inter_review_agent_can_wake_when_suggested():
    ws = _make_workspace()
    code, out = _run_main(ws, ["preflight-inter-review-agent", "--wake-if-needed"])
    assert code == 0
    assert "woken" in json.loads(out)
    names = [c[0] for c in ws.calls]
    assert "wake_named_jobs" in names


def test_preflight_claude_review_alias_works():
    ws = _make_workspace()
    code, out = _run_main(ws, ["preflight-claude-review"])
    assert code == 0
    assert json.loads(out)["shouldRun"] is True


def test_action_commands_delegate_to_workspace():
    for command, method in [
        ("dispatch-implementation-turn", "dispatch_implementation_turn"),
        ("publish-ready-pr", "publish_ready_pr"),
        ("push-pr-update", "push_pr_update"),
        ("merge-and-promote", "merge_and_promote"),
        ("dispatch-repair-handoff", "dispatch_repair_handoff"),
        ("restart-actor-session", "restart_actor_session"),
        ("tick", "tick"),
    ]:
        ws = _make_workspace()
        code, _ = _run_main(ws, [command])
        assert code == 0, command
        assert [c[0] for c in ws.calls] == [method], command


def test_dispatch_claude_review_and_inter_review_are_aliases():
    ws_a = _make_workspace()
    ws_b = _make_workspace()
    _run_main(ws_a, ["dispatch-claude-review"])
    _run_main(ws_b, ["dispatch-inter-review-agent"])
    assert [c[0] for c in ws_a.calls] == ["dispatch_inter_review_agent_review"]
    assert [c[0] for c in ws_b.calls] == ["dispatch_inter_review_agent_review"]


def test_show_active_lane_and_show_core_jobs_print_json_projection():
    ws = _make_workspace()
    _, lane_out = _run_main(ws, ["show-active-lane"])
    _, jobs_out = _run_main(ws, ["show-core-jobs"])
    assert json.loads(lane_out)["number"] == 224
    assert json.loads(jobs_out) == {"j1": {}}
