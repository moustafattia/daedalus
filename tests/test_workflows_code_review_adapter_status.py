import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed_workspace(workflow_root: Path, *, raw_status: dict) -> None:
    """Seed the workflow root with the minimal files ``build_status`` needs.

    ``status.build_status(workflow_root)`` loads the workspace via
    ``load_workspace_from_config`` and calls ``ws.build_status_raw()``. We
    monkeypatch ``build_status_raw`` on the resulting workspace to return our
    fixed ``raw_status`` payload — that keeps each test focused on the
    normalization + tick-dispatch surface under test.
    """
    config_dir = workflow_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "legacy-workflow.json"
    config_path.write_text(
        json.dumps({
            "repoPath": str(workflow_root / "repo"),
            "cronJobsPath": str(workflow_root / "cron-jobs.json"),
            "ledgerPath": str(workflow_root / "ledger.json"),
            "healthPath": str(workflow_root / "health.json"),
            "auditLogPath": str(workflow_root / "audit.jsonl"),
            "engineOwner": "hermes",
            "activeLaneLabel": "active-lane",
            "coreJobNames": [],
            "hermesJobNames": [],
            "sessionPolicy": {"codexModel": "gpt-5.3-codex-spark/high"},
            "reviewPolicy": {"claudeModel": "claude-sonnet-4-6"},
            "agentLabels": {"internalReviewerAgent": "Internal_Reviewer_Agent"},
        }),
        encoding="utf-8",
    )


def _install_workspace_stub(monkeypatch, status_module, raw_status: dict) -> None:
    """Replace ``load_workspace_from_config`` with a lightweight stub.

    The stub returns an object whose only method we exercise
    (``build_status_raw``) yields ``raw_status``. That lets these tests keep
    asserting normalization semantics without rebuilding a full workspace.
    """
    import sys as _sys
    plugin_root = str(REPO_ROOT)
    if plugin_root not in _sys.path:
        _sys.path.insert(0, plugin_root)
    workspace_mod = importlib.import_module("workflows.change_delivery.workspace")

    class _StubWorkspace:
        def build_status_raw(self):
            return dict(raw_status)

    def _fake_load(*, workspace_root):
        return _StubWorkspace()

    monkeypatch.setattr(workspace_mod, "load_workspace_from_config", _fake_load)


def test_adapter_status_build_status_normalizes_raw_payload(monkeypatch, tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")
    workflow_root = tmp_path / "workflow"
    workflow_root.mkdir()
    raw = {
        'activeLane': {'number': 224}, 'nextAction': {'type': 'noop'}, 'engineOwner': 'hermes',
        'missingCoreJobs': ['job-a'], 'disabledCoreJobs': [], 'staleCoreJobs': [], 'drift': [],
        'staleLaneReasons': [], 'brokenIssueWatchers': [], 'activeLaneError': None,
        'health': 'missing-core-jobs', 'openPr': None,
        'ledger': {'workflowState': 'implementing_local'},
        'implementation': {
            'activeSessionHealth': {'healthy': False, 'reason': 'missing-session-meta', 'sessionName': None},
            'sessionActionRecommendation': {'action': 'no-action'},
            'laneState': {'implementation': {'lastMeaningfulProgressAt': '2026-04-22T00:00:00Z'}},
            'updatedAt': '2026-04-22T00:00:00Z',
        },
        'reviews': {'codexCloud': {'reviewedHeadSha': None}},
    }
    _install_workspace_stub(monkeypatch, status_module, raw)

    result = status_module.build_status(workflow_root)

    assert result["activeLane"]["number"] == 224
    assert result["nextAction"]["type"] == "noop"
    assert result["health"] == "stale-lane"
    assert result["implementation"]["sessionActionRecommendation"]["action"] == "restart-session"
    assert "active lane has no PR and implementation state is stale" in result["staleLaneReasons"]


def test_adapter_status_surfaces_tick_dispatch_state(monkeypatch, tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")
    workflow_root = tmp_path / "workflow"
    workflow_root.mkdir()
    raw = {
        'activeLane': {'number': 224}, 'nextAction': {'type': 'noop'}, 'engineOwner': 'hermes',
        'missingCoreJobs': [], 'disabledCoreJobs': [], 'staleCoreJobs': [], 'drift': [],
        'staleLaneReasons': [], 'brokenIssueWatchers': [], 'activeLaneError': None,
        'health': 'healthy', 'openPr': None,
        'ledger': {'workflowState': 'implementing_local'},
        'implementation': {
            'activeSessionHealth': {'healthy': True, 'sessionName': 'lane-224'},
            'laneState': {},
        },
        'reviews': {'codexCloud': {'reviewedHeadSha': None}},
    }
    _install_workspace_stub(monkeypatch, status_module, raw)
    state_dir = workflow_root / "runtime" / "memory" / "tick-dispatch"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "active.json"
    state_path.write_text(
        '{"background": true, "command": "dispatch-internal-review", "pid": 4321, "logPath": "/tmp/tick.log", "startedAt": "20260423T011700Z"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(status_module, "_pid_is_running", lambda pid: pid == 4321)

    result = status_module.build_status(workflow_root)

    assert result["tickDispatch"]["active"] is True
    assert result["tickDispatch"]["command"] == "dispatch-internal-review"
    assert result["tickDispatch"]["statePath"] == str(state_path)


def test_adapter_status_archives_inactive_tick_dispatch_state(monkeypatch, tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")
    workflow_root = tmp_path / "workflow"
    workflow_root.mkdir()
    raw = {
        'activeLane': {'number': 224}, 'nextAction': {'type': 'noop'}, 'engineOwner': 'hermes',
        'missingCoreJobs': [], 'disabledCoreJobs': [], 'staleCoreJobs': [], 'drift': [],
        'staleLaneReasons': [], 'brokenIssueWatchers': [], 'activeLaneError': None,
        'health': 'healthy', 'openPr': None,
        'ledger': {'workflowState': 'implementing_local'},
        'implementation': {
            'activeSessionHealth': {'healthy': True, 'sessionName': 'lane-224'},
            'laneState': {},
        },
        'reviews': {'codexCloud': {'reviewedHeadSha': None}},
    }
    _install_workspace_stub(monkeypatch, status_module, raw)
    state_dir = workflow_root / "runtime" / "memory" / "tick-dispatch"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "active.json"
    state_path.write_text(
        '{"background": true, "command": "dispatch-implementation-turn", "pid": 999999, "logPath": "/tmp/tick.log", "startedAt": "20260423T011700Z"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(status_module, "_pid_is_running", lambda _pid: False)

    result = status_module.build_status(workflow_root)
    archive_dir = state_dir / "history"
    archived_files = sorted(archive_dir.glob('*.json'))

    assert result.get("tickDispatch") is None
    assert not state_path.exists()
    assert len(archived_files) == 1


def test_normalize_implementation_for_active_lane_preserves_matching_lane_and_sets_expected_paths():
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")

    result = status_module.normalize_implementation_for_active_lane(
        {
            "session": "keep-me",
            "worktree": "/tmp/old",
            "branch": "codex/issue-224-old",
            "laneState": {"issue": {"number": 224}},
            "sessionRuntime": "acpx-codex",
            "sessionName": "lane-224",
        },
        active_lane={"number": 224, "title": "[A07] Active lane"},
        open_pr={"headRefName": "codex/issue-224-pr-head"},
        selected_codex_model="gpt-5.4",
    )

    assert result["session"] == "keep-me"
    assert result["worktree"] == "/tmp/issue-224"
    assert result["branch"] == "codex/issue-224-pr-head"
    assert result["sessionRuntime"] == "acpx-codex"
    assert result["sessionName"] == "lane-224"
    assert result["codexModel"] == "gpt-5.4"


def test_normalize_implementation_for_active_lane_resets_mismatched_lane_to_fresh_expected_shape():
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")

    result = status_module.normalize_implementation_for_active_lane(
        {
            "session": "stale-session",
            "previousSession": "older-session",
            "worktree": "/tmp/issue-999",
            "branch": "codex/issue-999-wrong",
            "status": "findings_open",
        },
        active_lane={"number": 224, "title": "[A07] Active lane"},
        open_pr=None,
        selected_codex_model="gpt-5.3-codex",
    )

    assert result == {
        "session": None,
        "previousSession": "stale-session",
        "worktree": "/tmp/issue-224",
        "updatedAt": None,
        "branch": "codex/issue-224-active-lane",
        "status": "implementing",
        "sessionRuntime": "acpx-codex",
        "sessionName": "lane-224",
        "codexModel": "gpt-5.3-codex",
        "resumeSessionId": None,
    }


def test_normalize_implementation_for_active_lane_accepts_codex_app_server_runtime():
    status_module = load_module("daedalus_workflows_change_delivery_status_codex_runtime", "workflows/change_delivery/status.py")

    result = status_module.normalize_implementation_for_active_lane(
        {"worktree": "/tmp/issue-224", "branch": "codex/issue-224-old"},
        active_lane={"number": 224, "title": "[A07] Active lane"},
        open_pr=None,
        selected_codex_model="gpt-5.5",
        session_runtime="codex-app-server",
        resume_session_id="thread-224",
    )

    assert result["sessionRuntime"] == "codex-app-server"
    assert result["sessionName"] == "lane-224"
    assert result["resumeSessionId"] == "thread-224"
    assert result["codexModel"] == "gpt-5.5"


def test_collect_worktree_repo_facts_reads_branch_commit_count_and_head_sha(tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")

    seen = []
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    class Completed:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(command, cwd=None):
        seen.append((command, cwd))
        if command == ["git", "branch", "--show-current"]:
            return Completed("codex/issue-224-active\n")
        if command == ["git", "rev-list", "--count", "origin/main..HEAD"]:
            return Completed("3\n")
        if command == ["git", "rev-parse", "HEAD"]:
            return Completed("abc123\n")
        raise AssertionError(command)

    result = status_module.collect_worktree_repo_facts(worktree, run=fake_run)

    assert result == {
        "branch": "codex/issue-224-active",
        "commitsAhead": 3,
        "localHeadSha": "abc123",
    }
    assert seen == [
        (["git", "branch", "--show-current"], worktree),
        (["git", "rev-list", "--count", "origin/main..HEAD"], worktree),
        (["git", "rev-parse", "HEAD"], worktree),
    ]


def test_git_branch_and_commits_ahead_helpers_handle_missing_paths_and_run_output(tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    class Completed:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(command, cwd=None):
        if command == ["git", "branch", "--show-current"]:
            return Completed("codex/issue-224-active\n")
        if command == ["git", "rev-list", "--count", "origin/main..HEAD"]:
            return Completed("7\n")
        raise AssertionError(command)

    assert status_module.git_branch(worktree, run=fake_run) == "codex/issue-224-active"
    assert status_module.git_commits_ahead(worktree, run=fake_run) == 7
    assert status_module.git_branch(tmp_path / "missing", run=fake_run) is None
    assert status_module.git_commits_ahead(tmp_path / "missing", run=fake_run) is None


def test_git_head_sha_helper_reads_head_and_handles_missing_path(tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    class Completed:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(command, cwd=None):
        assert command == ["git", "rev-parse", "HEAD"]
        assert cwd == worktree
        return Completed("abc123\n")

    assert status_module.git_head_sha(worktree, run=fake_run) == "abc123"
    assert status_module.git_head_sha(tmp_path / "missing", run=fake_run) is None


def test_collect_worktree_repo_facts_gracefully_handles_missing_path_and_git_failures(tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")

    missing = status_module.collect_worktree_repo_facts(tmp_path / "missing", run=lambda *_args, **_kwargs: None)

    assert missing == {"branch": None, "commitsAhead": None, "localHeadSha": None}

    worktree = tmp_path / "worktree"
    worktree.mkdir()

    def failing_run(command, cwd=None):
        if command == ["git", "branch", "--show-current"]:
            raise RuntimeError("no branch")
        if command == ["git", "rev-list", "--count", "origin/main..HEAD"]:
            return type("Completed", (), {"stdout": "not-a-number\n"})()
        if command == ["git", "rev-parse", "HEAD"]:
            raise RuntimeError("no head")
        raise AssertionError((command, cwd))

    result = status_module.collect_worktree_repo_facts(worktree, run=failing_run)

    assert result == {"branch": None, "commitsAhead": None, "localHeadSha": None}


def test_load_implementation_session_meta_prefers_acpx_session_lookup_for_acpx_runtime(tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    seen = {}

    def fake_show(*, worktree, session_name):
        seen["worktree"] = worktree
        seen["session_name"] = session_name
        return {"name": session_name, "last_used_at": "2026-04-23T00:00:00Z"}

    result = status_module.load_implementation_session_meta(
        {"sessionRuntime": "acpx-codex", "sessionName": "lane-224", "session": "legacy-key"},
        worktree,
        show_acpx_session_fn=fake_show,
        load_latest_session_meta_fn=lambda _session: (_ for _ in ()).throw(AssertionError("legacy lookup should not run")),
    )

    assert result == {"name": "lane-224", "last_used_at": "2026-04-23T00:00:00Z"}
    assert seen == {"worktree": worktree, "session_name": "lane-224"}


def test_load_implementation_session_meta_falls_back_to_legacy_session_lookup_when_not_acpx():
    status_module = load_module("daedalus_workflows_change_delivery_status_test", "workflows/change_delivery/status.py")

    seen = {}

    def fake_load_latest(session_name):
        seen["session_name"] = session_name
        return {"name": session_name, "closed": False}

    result = status_module.load_implementation_session_meta(
        {"sessionRuntime": "legacy", "session": "session-123"},
        None,
        show_acpx_session_fn=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("acpx lookup should not run")),
        load_latest_session_meta_fn=fake_load_latest,
    )

    assert result == {"name": "session-123", "closed": False}
    assert seen == {"session_name": "session-123"}


def test_load_implementation_session_meta_synthesizes_codex_app_server_thread_meta(tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_codex_meta", "workflows/change_delivery/status.py")
    worktree = tmp_path / "worktree"

    result = status_module.load_implementation_session_meta(
        {
            "sessionRuntime": "codex-app-server",
            "sessionName": "lane-224",
            "session": "thread-224",
            "resumeSessionId": "thread-224",
            "updatedAt": "2026-04-30T00:00:00Z",
        },
        worktree,
        show_acpx_session_fn=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("acpx lookup should not run")),
        load_latest_session_meta_fn=lambda _session: (_ for _ in ()).throw(AssertionError("legacy lookup should not run")),
    )

    assert result == {
        "name": "lane-224",
        "closed": False,
        "cwd": str(worktree),
        "last_used_at": "2026-04-30T00:00:00Z",
        "session_id": "thread-224",
        "record_id": "thread-224",
    }


def test_increment_no_progress_ticks_resets_on_approval_or_merge():
    status_module = load_module("daedalus_workflows_change_delivery_status_ipt", "workflows/change_delivery/status.py")

    assert status_module.increment_no_progress_ticks(
        existing={"budget": {"noProgressTicks": 4}},
        latest_progress={"kind": "approved", "at": "2026-04-22T00:00:00Z"},
        now_iso="2026-04-22T00:05:00Z",
    ) == 0
    assert status_module.increment_no_progress_ticks(
        existing={"budget": {"noProgressTicks": 2}},
        latest_progress={"kind": "merged", "at": "2026-04-22T00:00:00Z"},
        now_iso="2026-04-22T00:05:00Z",
    ) == 0


def test_increment_no_progress_ticks_bumps_when_same_progress_and_cooldown_elapsed():
    status_module = load_module("daedalus_workflows_change_delivery_status_ipt", "workflows/change_delivery/status.py")

    # Same progress fingerprint, evaluated well past the cooldown window -> bump.
    existing = {
        "implementation": {
            "lastMeaningfulProgressAt": "2026-04-22T00:00:00Z",
            "lastMeaningfulProgressKind": "implementing_local",
        },
        "budget": {"noProgressTicks": 2, "lastEvaluatedAt": "2026-04-22T00:00:00Z"},
    }
    assert status_module.increment_no_progress_ticks(
        existing=existing,
        latest_progress={"kind": "implementing_local", "at": "2026-04-22T00:00:00Z"},
        now_iso="2026-04-22T01:00:00Z",
    ) == 3

    # Same progress but within cooldown -> no increment.
    within_cooldown_existing = {
        "implementation": {
            "lastMeaningfulProgressAt": "2026-04-22T00:00:00Z",
            "lastMeaningfulProgressKind": "implementing_local",
        },
        "budget": {"noProgressTicks": 2, "lastEvaluatedAt": "2026-04-22T00:59:58Z"},
    }
    assert status_module.increment_no_progress_ticks(
        existing=within_cooldown_existing,
        latest_progress={"kind": "implementing_local", "at": "2026-04-22T00:00:00Z"},
        now_iso="2026-04-22T01:00:00Z",
    ) == 2


def test_increment_no_progress_ticks_resets_when_progress_advances():
    status_module = load_module("daedalus_workflows_change_delivery_status_ipt", "workflows/change_delivery/status.py")

    existing = {
        "implementation": {
            "lastMeaningfulProgressAt": "2026-04-22T00:00:00Z",
            "lastMeaningfulProgressKind": "implementing_local",
        },
        "budget": {"noProgressTicks": 2, "lastEvaluatedAt": "2026-04-22T00:00:00Z"},
    }
    assert status_module.increment_no_progress_ticks(
        existing=existing,
        latest_progress={"kind": "implementing_local", "at": "2026-04-22T00:30:00Z"},
        now_iso="2026-04-22T01:00:00Z",
    ) == 0


def test_write_lane_memo_writes_rendered_markdown(tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_wlm", "workflows/change_delivery/status.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    body = status_module.write_lane_memo(
        worktree=worktree,
        issue={"number": 224, "title": "Test lane", "url": "https://example.test/issue/224"},
        branch="issue-224",
        open_pr=None,
        repair_brief=None,
        latest_progress={"kind": "implementing_local", "at": "2026-04-22T00:00:00Z"},
        validation_summary=None,
    )
    assert body is not None
    memo = (worktree / ".lane-memo.md").read_text(encoding="utf-8")
    assert "#224" in memo or "224" in memo
    assert memo == body


def test_write_lane_memo_skips_when_required_args_missing(tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_wlm", "workflows/change_delivery/status.py")
    assert status_module.write_lane_memo(
        worktree=None,
        issue={"number": 224},
        branch=None,
        open_pr=None,
        repair_brief=None,
        latest_progress=None,
        validation_summary=None,
    ) is None
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    assert status_module.write_lane_memo(
        worktree=worktree,
        issue=None,
        branch=None,
        open_pr=None,
        repair_brief=None,
        latest_progress=None,
        validation_summary=None,
    ) is None


def test_write_lane_state_emits_expected_payload_shape(tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_wls", "workflows/change_delivery/status.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    result = status_module.write_lane_state(
        worktree=worktree,
        issue={"number": 224, "title": "T", "url": "https://example.test/issue/224"},
        open_pr={"number": 301, "url": "https://example.test/pr/301", "headRefName": "issue-224", "headRefOid": "prsha"},
        implementation={
            "session": "session-224",
            "activeSessionHealth": {"healthy": True, "lastUsedAt": "2026-04-22T00:00:00Z"},
            "updatedAt": "2026-04-22T00:00:00Z",
            "status": "implementing_local",
            "publishStatus": None,
            "branch": "issue-224",
            "localHeadSha": "localsha",
            "lastDispatchAttemptId": None,
            "lastDispatchAt": None,
            "lastRestartAttemptId": None,
            "lastRestartAt": None,
            "sessionActionRecommendation": {"action": "continue-session", "reason": None},
            "acpSessionStrategy": {},
        },
        reviews={"claudeCode": {}, "codexCloud": {}},
        repair_brief=None,
        now_iso="2026-04-22T00:05:00Z",
        latest_progress={"kind": "implementing_local", "at": "2026-04-22T00:00:00Z"},
        preflight=None,
    )
    assert result is not None
    written = result
    assert written["schemaVersion"] == 1
    assert written["issue"]["number"] == 224
    assert written["pr"]["number"] == 301
    assert written["budget"]["noProgressTicks"] == 0
    assert written["failure"]["retryCount"] == 0
    # The adapter must write the file at the expected path.
    assert (worktree / ".lane-state.json").exists()


def test_compute_ledger_drift_reports_lane_and_pr_mismatches():
    status_module = load_module("daedalus_workflows_change_delivery_status_drift", "workflows/change_delivery/status.py")

    drift = status_module.compute_ledger_drift(
        active_lane={"number": 224},
        lane_issue_number=224,
        ledger_active=225,
        ledger_active_number=225,
        ledger_idle=False,
        ledger_state="under_review",
        open_pr={"number": 301},
        pr_ledger={"url": None, "headSha": ""},
        review_loop_state="clean",
        ledger_review_loop_state="rework_required",
    )
    assert any("activeLane=225" in item for item in drift)
    assert any("no PR URL" in item for item in drift)
    assert any("no PR head SHA" in item for item in drift)
    assert any("reviewLoopState" in item for item in drift)


def test_compute_ledger_drift_detects_workflowidle_mismatch_and_stale_state():
    status_module = load_module("daedalus_workflows_change_delivery_status_drift", "workflows/change_delivery/status.py")

    drift = status_module.compute_ledger_drift(
        active_lane={"number": 224},
        lane_issue_number=224,
        ledger_active=224,
        ledger_active_number=224,
        ledger_idle=True,
        ledger_state="merged",
        open_pr=None,
        pr_ledger={"url": "https://x", "headSha": "abc"},
        review_loop_state=None,
        ledger_review_loop_state=None,
    )
    assert any("workflowIdle=true" in item for item in drift)
    assert any("workflowState='merged'" in item for item in drift)


def test_compute_ledger_drift_detects_missing_active_lane_when_ledger_has_one():
    status_module = load_module("daedalus_workflows_change_delivery_status_drift", "workflows/change_delivery/status.py")

    drift = status_module.compute_ledger_drift(
        active_lane=None,
        lane_issue_number=None,
        ledger_active=224,
        ledger_active_number=224,
        ledger_idle=False,
        ledger_state="implementing_local",
        open_pr=None,
        pr_ledger={},
        review_loop_state=None,
        ledger_review_loop_state=None,
    )
    assert any("GitHub has no active-lane" in item for item in drift)


def test_resolve_publish_ready_workflow_state_maps_review_loop_states():
    status_module = load_module("daedalus_workflows_change_delivery_status_rpws", "workflows/change_delivery/status.py")

    assert status_module.resolve_publish_ready_workflow_state("clean", merge_blocked=False) == ("approved", "approved")
    assert status_module.resolve_publish_ready_workflow_state("clean", merge_blocked=True) == ("under_review", "under_review")
    assert status_module.resolve_publish_ready_workflow_state("findings_open", merge_blocked=True) == ("findings_open", "findings_open")
    assert status_module.resolve_publish_ready_workflow_state("rework_required", merge_blocked=True) == ("rework_required", "rework_required")
    assert status_module.resolve_publish_ready_workflow_state("awaiting_reviews", merge_blocked=False) == ("under_review", "under_review")


def test_derive_publish_status_returns_ready_draft_or_not_published():
    status_module = load_module("daedalus_workflows_change_delivery_status_dps", "workflows/change_delivery/status.py")

    assert status_module.derive_publish_status({"isDraft": False}, publish_ready=True) == "ready_for_review"
    assert status_module.derive_publish_status({"isDraft": True}, publish_ready=False) == "draft_pr"
    assert status_module.derive_publish_status(None, publish_ready=False) == "not_published"


def test_resolve_prepublish_workflow_state_returns_implementing_local_when_no_candidate():
    status_module = load_module("daedalus_workflows_change_delivery_status_rpre", "workflows/change_delivery/status.py")

    assert status_module.resolve_prepublish_workflow_state(
        local_candidate=False,
        single_pass_gate_satisfied=False,
        internal_review_current=False,
        internal_review_verdict=None,
    ) == "implementing_local"


def test_resolve_prepublish_workflow_state_returns_ready_to_publish_when_gate_already_satisfied():
    status_module = load_module("daedalus_workflows_change_delivery_status_rpre", "workflows/change_delivery/status.py")

    assert status_module.resolve_prepublish_workflow_state(
        local_candidate=True,
        single_pass_gate_satisfied=True,
        internal_review_current=True,
        internal_review_verdict="PASS_CLEAN",
    ) == "ready_to_publish"


def test_resolve_prepublish_workflow_state_returns_findings_when_internal_review_current_and_actionable():
    status_module = load_module("daedalus_workflows_change_delivery_status_rpre", "workflows/change_delivery/status.py")

    assert status_module.resolve_prepublish_workflow_state(
        local_candidate=True,
        single_pass_gate_satisfied=False,
        internal_review_current=True,
        internal_review_verdict="PASS_WITH_FINDINGS",
    ) == "pre_publish_review_findings"

    assert status_module.resolve_prepublish_workflow_state(
        local_candidate=True,
        single_pass_gate_satisfied=False,
        internal_review_current=True,
        internal_review_verdict="REWORK",
    ) == "pre_publish_review_findings"


def test_resolve_prepublish_workflow_state_defaults_to_awaiting_pre_publish_review():
    status_module = load_module("daedalus_workflows_change_delivery_status_rpre", "workflows/change_delivery/status.py")

    assert status_module.resolve_prepublish_workflow_state(
        local_candidate=True,
        single_pass_gate_satisfied=False,
        internal_review_current=False,
        internal_review_verdict=None,
    ) == "awaiting_pre_publish_review"

    # Even if verdict is set, it requires internal_review_current to branch into findings.
    assert status_module.resolve_prepublish_workflow_state(
        local_candidate=True,
        single_pass_gate_satisfied=False,
        internal_review_current=False,
        internal_review_verdict="REWORK",
    ) == "awaiting_pre_publish_review"


def test_apply_idle_ledger_transition_resets_active_lane_state():
    status_module = load_module("daedalus_workflows_change_delivery_status_idle", "workflows/change_delivery/status.py")
    ledger: dict = {
        "approval": {"status": "approved", "approvedAt": "t", "approvedHeadSha": "h", "pendingReason": None},
        "activeLane": 224,
        "workflowIdle": False,
        "workflowState": "implementing_local",
        "reviewState": "implementing_local",
        "reviewLoopState": "awaiting_reviews",
        "branch": "issue-224",
        "openActiveLanePr": "https://example.test/pr/1",
        "blockedReason": "something",
        "repairBrief": {"mustFix": []},
    }
    status_module.apply_idle_ledger_transition(ledger, now_iso="2026-04-23T00:00:00Z")
    assert ledger["activeLane"] is None
    assert ledger["workflowIdle"] is True
    assert ledger["workflowState"] == "idle"
    assert ledger["reviewState"] == "idle"
    assert ledger["reviewLoopState"] == "idle"
    assert ledger["branch"] is None
    assert ledger["openActiveLanePr"] is None
    assert ledger["blockedReason"] is None
    assert ledger["repairBrief"] is None
    assert ledger["updatedAt"] == "2026-04-23T00:00:00Z"
    assert ledger["approval"] == {
        "status": "not-approved",
        "approvedAt": None,
        "approvedHeadSha": None,
        "pendingReason": None,
    }


def test_apply_active_lane_error_ledger_transition_marks_blocked_state():
    status_module = load_module("daedalus_workflows_change_delivery_status_ale", "workflows/change_delivery/status.py")
    ledger: dict = {
        "approval": {"status": "approved", "approvedAt": "t", "approvedHeadSha": "h", "pendingReason": None},
        "workflowState": "implementing_local",
        "reviewState": "implementing_local",
        "reviewLoopState": "awaiting_reviews",
        "workflowIdle": True,
        "blockedReason": None,
    }
    error_payload = {"error": "multiple-active-lanes", "details": ["lane-224", "lane-225"]}
    status_module.apply_active_lane_error_ledger_transition(
        ledger,
        active_lane_error=error_payload,
        now_iso="2026-04-23T00:00:00Z",
    )
    assert ledger["workflowState"] == "blocked"
    assert ledger["reviewState"] == "blocked"
    assert ledger["reviewLoopState"] == "blocked"
    assert ledger["workflowIdle"] is False
    assert ledger["blockedReason"] == error_payload
    assert ledger["approval"]["status"] == "not-approved"
    assert ledger["approval"]["pendingReason"] == "multiple-active-lanes"
    assert ledger["updatedAt"] == "2026-04-23T00:00:00Z"


def _active_lane_baseline_ledger():
    return {
        "approval": {"status": "not-approved", "approvedAt": None, "approvedHeadSha": None, "pendingReason": None},
        "pr": {"checks": {"status": "queued"}},
    }


def test_apply_active_lane_ledger_transition_sets_approved_for_clean_publish_ready_merge():
    status_module = load_module("daedalus_workflows_change_delivery_status_alt", "workflows/change_delivery/status.py")
    ledger = _active_lane_baseline_ledger()
    status_module.apply_active_lane_ledger_transition(
        ledger,
        active_lane={"number": 224},
        open_pr={"number": 301, "url": "https://example.test/pr/301", "headRefName": "issue-224", "headRefOid": "prsha", "isDraft": False},
        implementation={
            "branch": "issue-224",
            "localHeadSha": "prsha",
            "commitsAhead": 0,
            "laneState": {},
        },
        reviews={"internalReview": {"reviewScope": "local-prepublish", "reviewedHeadSha": "prsha", "verdict": "PASS_CLEAN"}, "externalReview": {}},
        previous_internal_review={},
        publish_ready=True,
        review_loop_state="clean",
        merge_blocked=False,
        merge_blockers=[],
        now_iso="2026-04-23T00:00:00Z",
        repair_brief=None,
        operator_attention_needed=False,
    )
    assert ledger["workflowState"] == "approved"
    assert ledger["reviewState"] == "approved"
    assert ledger["approval"]["status"] == "approved"
    assert ledger["approval"]["approvedAt"] == "2026-04-23T00:00:00Z"
    assert ledger["approval"]["approvedHeadSha"] == "prsha"
    assert ledger["pr"]["number"] == 301
    assert ledger["prePublishGate"]["status"] == "ready"


def test_apply_active_lane_ledger_transition_forces_operator_attention_reason_when_flag_set():
    status_module = load_module("daedalus_workflows_change_delivery_status_alt", "workflows/change_delivery/status.py")
    ledger = _active_lane_baseline_ledger()
    status_module.apply_active_lane_ledger_transition(
        ledger,
        active_lane={"number": 224},
        open_pr=None,
        implementation={"branch": "issue-224", "localHeadSha": "localsha", "commitsAhead": 1, "laneState": {}},
        reviews={"claudeCode": {}, "codexCloud": {}},
        previous_internal_review={},
        publish_ready=False,
        review_loop_state="awaiting_reviews",
        merge_blocked=False,
        merge_blockers=[],
        now_iso="2026-04-23T00:00:00Z",
        repair_brief=None,
        operator_attention_needed=True,
    )
    assert ledger["workflowState"] == "operator_attention_required"
    assert ledger["reviewState"] == "operator_attention_required"
    assert ledger["blockedReason"] == "operator-attention-required"
    assert ledger["approval"]["pendingReason"] == "operator-attention-required"


def test_apply_active_lane_ledger_transition_prepublish_routes_through_claude_findings_when_brief_present():
    status_module = load_module("daedalus_workflows_change_delivery_status_alt", "workflows/change_delivery/status.py")
    ledger = _active_lane_baseline_ledger()
    status_module.apply_active_lane_ledger_transition(
        ledger,
        active_lane={"number": 224},
        open_pr=None,
        implementation={
            "branch": "issue-224",
            "localHeadSha": "head123",
            "commitsAhead": 1,
            "laneState": {},
        },
        reviews={
            "claudeCode": {"reviewedHeadSha": "head123", "verdict": "REWORK"},
            "codexCloud": {},
        },
        previous_internal_review={"reviewedHeadSha": "head123", "verdict": "REWORK"},
        publish_ready=False,
        review_loop_state="findings_open",
        merge_blocked=False,
        merge_blockers=[],
        now_iso="2026-04-23T00:00:00Z",
        repair_brief={"forHeadSha": "head123", "mustFix": [{"summary": "x"}], "shouldFix": []},
        operator_attention_needed=False,
    )
    assert ledger["workflowState"] == "pre_publish_review_findings"
    assert ledger["reviewState"] == "pre_publish_review_findings"
    assert ledger["approval"]["pendingReason"] == "open-review-findings"


def test_derive_latest_progress_returns_approved_event_when_published_pr_is_clean():
    status_module = load_module("daedalus_workflows_change_delivery_status_dlp", "workflows/change_delivery/status.py")

    progress = status_module.derive_latest_progress(
        implementation={"status": "under_review", "updatedAt": "2026-04-23T00:00:00Z"},
        ledger={"workflowState": "under_review"},
        open_pr={"number": 301},
        reviews={"externalReview": {"status": "completed", "verdict": "PASS_CLEAN", "updatedAt": "2026-04-23T00:05:00Z"}},
        review_loop_state="clean",
        merge_blocked=False,
        now_iso="2026-04-23T00:06:00Z",
    )
    assert progress == {"kind": "approved", "at": "2026-04-23T00:05:00Z"}


def test_derive_latest_progress_falls_back_to_implementation_status_and_updated_at():
    status_module = load_module("daedalus_workflows_change_delivery_status_dlp", "workflows/change_delivery/status.py")

    progress = status_module.derive_latest_progress(
        implementation={"status": "implementing_local", "updatedAt": "2026-04-23T00:04:00Z"},
        ledger={"workflowState": "implementing_local"},
        open_pr=None,
        reviews={},
        review_loop_state="awaiting_reviews",
        merge_blocked=False,
        now_iso="2026-04-23T00:05:00Z",
    )
    assert progress == {"kind": "implementing_local", "at": "2026-04-23T00:04:00Z"}


def test_derive_latest_progress_uses_now_when_no_implementation_updatedAt_and_unknown_when_no_state():
    status_module = load_module("daedalus_workflows_change_delivery_status_dlp", "workflows/change_delivery/status.py")

    progress = status_module.derive_latest_progress(
        implementation=None,
        ledger=None,
        open_pr=None,
        reviews=None,
        review_loop_state=None,
        merge_blocked=False,
        now_iso="2026-04-23T00:05:00Z",
    )
    assert progress == {"kind": "unknown", "at": "2026-04-23T00:05:00Z"}


def test_assemble_status_payload_returns_fully_shaped_status_dict():
    status_module = load_module("daedalus_workflows_change_delivery_status_asp", "workflows/change_delivery/status.py")

    reviews = {"internalReview": {"model": "claude-sonnet-4-6"}, "externalReview": {}, "rockClaw": {}}
    implementation = {
        "worktree": "/tmp/worktree",
        "branch": "issue-224",
        "session": "session-224",
        "sessionRuntime": "acpx-codex",
        "sessionName": "lane-224",
        "resumeSessionId": "sess-abc",
        "codexModel": "gpt-5.3-codex",
        "updatedAt": "2026-04-23T00:00:00Z",
        "laneState": {},
    }
    result = status_module.assemble_status_payload(
        now_iso="2026-04-23T00:01:00Z",
        engine_owner="hermes",
        repo_path="/tmp/repo",
        ledger_path="/tmp/ledger.json",
        health_path="/tmp/health.json",
        audit_log_path="/tmp/audit.jsonl",
        active_lane={"number": 224},
        active_lane_error=None,
        open_pr=None,
        ledger={"schemaVersion": 6, "workflowState": "implementing_local", "readyToClose": []},
        ledger_active_number=224,
        effective_workflow_state="implementing_local",
        effective_review_state="implementing_local",
        ledger_idle=False,
        effective_repair_brief=None,
        implementation=implementation,
        local_head_sha="localsha",
        worktree_branch="issue-224",
        worktree_commits_ahead=1,
        lane_state_path_str=None,
        lane_memo_path_str=None,
        active_session_health={"healthy": True},
        session_action_recommendation={"action": "continue-session"},
        nudge_preflight={"shouldNudge": False},
        acp_session_strategy={},
        publish_status="not_published",
        preferred_codex_model="gpt-5.3-codex",
        coder_agent_name="Internal_Coder_Agent",
        actor_labels={},
        reviews=reviews,
        review_loop_state="awaiting_reviews",
        merge_blocked=False,
        merge_blockers=[],
        pre_publish_review_preflight={"shouldRun": False},
        detailed_jobs={},
        hermes_job_names=[],
        missing_core_jobs=[],
        disabled_core_jobs=[],
        stale_core_jobs=[],
        broken_watchers=[],
        drift=[],
        stale_lane_reasons=[],
        health="healthy",
        legacy_watchdog_present=False,
        legacy_watchdog_mode="retired",
        inter_review_agent_model="claude-sonnet-4-6",
        next_action={"type": "noop", "reason": "no-forward-action-needed"},
    )
    assert result["updatedAt"] == "2026-04-23T00:01:00Z"
    assert result["activeLane"] == {"number": 224}
    assert result["health"] == "healthy"
    assert result["ledger"]["workflowState"] == "implementing_local"
    assert result["ledger"]["readyToCloseCount"] == 0
    assert result["implementation"]["publishStatus"] == "not_published"
    assert result["implementation"]["branch"] == "issue-224"
    assert result["preflight"]["prePublishReview"]["shouldRun"] is False
    assert "prePublishReview" not in result["reviews"]
    assert result["nextAction"]["reason"] == "no-forward-action-needed"
    # Model fields fall back to the provided inter_review_agent_model when ledger entry is missing.
    assert result["ledger"]["internalReviewerModel"] == "claude-sonnet-4-6"


def test_apply_ledger_reviews_and_header_writes_expected_keys():
    status_module = load_module("daedalus_workflows_change_delivery_status_alrh", "workflows/change_delivery/status.py")
    ledger: dict = {}
    status_module.apply_ledger_reviews_and_header(
        ledger,
        review_loop_state="awaiting_reviews",
        codex_model="gpt-5.3-codex",
        inter_review_agent_model="claude-sonnet-4-6",
        actor_labels={"coder": "x"},
        reviews={"rockClaw": {"a": 1}, "internalReview": {"b": 2}, "externalReview": {"c": 3}},
    )
    assert ledger["schemaVersion"] == 6
    assert ledger["reviewLoopState"] == "awaiting_reviews"
    assert ledger["internalReviewerModel"] == "claude-sonnet-4-6"
    assert "claudeModel" not in ledger
    assert "interReviewAgentModel" not in ledger
    assert ledger["codexModel"] == "gpt-5.3-codex"
    assert ledger["workflowActors"] == {"coder": "x"}
    assert ledger["approval"] == {}
    assert ledger["reviews"] == {"rockClaw": {"a": 1}, "internalReview": {"b": 2}, "externalReview": {"c": 3}}


def test_apply_ledger_implementation_merge_preserves_prior_ledger_keys():
    status_module = load_module("daedalus_workflows_change_delivery_status_alim", "workflows/change_delivery/status.py")
    ledger: dict = {"implementation": {"persistedExtra": "x", "lastDispatchAt": "old"}, "workflowState": "implementing_local"}
    status_module.apply_ledger_implementation_merge(
        ledger,
        active_lane={"number": 224},
        open_pr=None,
        implementation={
            "session": "session-224",
            "previousSession": None,
            "sessionRuntime": "acpx-codex",
            "sessionName": "lane-224",
            "codexModel": "gpt-5.3-codex",
            "resumeSessionId": "sess-abc",
            "worktree": "/tmp/worktree",
            "localHeadSha": "localsha",
            "publishStatus": "not_published",
            "updatedAt": "2026-04-23T00:01:00Z",
            "branch": "issue-224",
            "status": "implementing_local",
            "lastDispatchAttemptId": "attempt-1",
            "lastDispatchAt": "2026-04-23T00:00:00Z",
            "lastRestartAttemptId": None,
            "lastRestartAt": None,
            "laneState": {},
        },
        codex_model_fallback="gpt-5.3-codex",
        coder_agent_name="Internal_Coder_Agent",
    )
    impl = ledger["implementation"]
    # Prior extra key is preserved.
    assert impl["persistedExtra"] == "x"
    # Updated fields overwrite.
    assert impl["lastDispatchAt"] == "2026-04-23T00:00:00Z"
    assert impl["agentName"] == "Internal_Coder_Agent"
    assert impl["agentRole"] == "coder_agent"
    assert impl["status"] == "implementing_local"


def test_apply_ledger_implementation_merge_falls_back_to_implementing_for_active_no_pr():
    status_module = load_module("daedalus_workflows_change_delivery_status_alim", "workflows/change_delivery/status.py")
    ledger: dict = {"implementation": {}, "workflowState": "some_state"}
    status_module.apply_ledger_implementation_merge(
        ledger,
        active_lane={"number": 224},
        open_pr=None,
        implementation={
            "session": None,
            "previousSession": None,
            "sessionRuntime": "acpx-codex",
            "sessionName": "lane-224",
            "codexModel": None,
            "resumeSessionId": None,
            "worktree": None,
            "localHeadSha": None,
            "publishStatus": None,
            "updatedAt": None,
            "branch": None,
            "status": None,
            "lastDispatchAttemptId": None,
            "lastDispatchAt": None,
            "lastRestartAttemptId": None,
            "lastRestartAt": None,
            "laneState": {},
        },
        codex_model_fallback="gpt-5.3-codex",
        coder_agent_name="Internal_Coder_Agent",
    )
    # With active lane + no PR + implementation.status falsy, status defaults to "implementing".
    assert ledger["implementation"]["status"] == "implementing"
    assert ledger["implementation"]["codexModel"] == "gpt-5.3-codex"


def test_write_lane_state_skips_when_missing_worktree_or_issue(tmp_path):
    status_module = load_module("daedalus_workflows_change_delivery_status_wls", "workflows/change_delivery/status.py")
    assert status_module.write_lane_state(
        worktree=None,
        issue={"number": 1},
        open_pr=None,
        implementation={},
        reviews={},
        repair_brief=None,
        now_iso="2026-04-22T00:00:00Z",
        latest_progress=None,
    ) is None
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    assert status_module.write_lane_state(
        worktree=worktree,
        issue=None,
        open_pr=None,
        implementation={},
        reviews={},
        repair_brief=None,
        now_iso="2026-04-22T00:00:00Z",
        latest_progress=None,
    ) is None
