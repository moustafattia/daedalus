import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_decide_session_action_prefers_continue_for_healthy_session():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    result = sessions_module.decide_session_action(
        active_session_health={"healthy": True, "sessionName": "lane-224"},
        implementation_status="implementing_local",
        has_open_pr=False,
    )

    assert result == {"action": "continue-session", "reason": None, "sessionName": "lane-224"}


def test_decide_session_action_uses_poke_for_stale_open_session_that_can_poke():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    result = sessions_module.decide_session_action(
        active_session_health={"healthy": False, "canPoke": True, "reason": "stale-open-session", "sessionName": "lane-224"},
        implementation_status="implementing_local",
        has_open_pr=False,
    )

    assert result == {"action": "poke-session", "reason": "stale-open-session", "sessionName": "lane-224"}


def test_decide_session_action_requests_restart_for_active_lane_without_healthy_session():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    result = sessions_module.decide_session_action(
        active_session_health={"healthy": False, "reason": "missing-session-meta", "sessionName": None},
        implementation_status="implementing_local",
        has_open_pr=False,
    )

    assert result == {"action": "restart-session", "reason": "missing-session-meta", "sessionName": None}



def test_expected_lane_worktree_uses_tmp_issue_path():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    result = sessions_module.expected_lane_worktree(224)

    assert result == Path('/tmp/issue-224')



def test_expected_lane_branch_slugifies_issue_title():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    result = sessions_module.expected_lane_branch({"number": 224, "title": "[A07] God objects grew past every threshold the doc claims; 'thin composition shell' claim is false"})

    assert result == 'codex/issue-224-god-objects-grew-past-every-threshold-the-doc-cl'



def test_lane_acpx_session_name_uses_lane_prefix():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    result = sessions_module.lane_acpx_session_name(224)

    assert result == 'lane-224'



def test_issue_number_extractors_parse_branch_and_worktree():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    assert sessions_module.issue_number_from_branch('codex/issue-224-something') == 224
    assert sessions_module.issue_number_from_worktree('/tmp/issue-224') == 224



def test_implementation_lane_matches_when_any_lane_hint_matches():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    result = sessions_module.implementation_lane_matches(
        {
            'branch': 'codex/issue-224-something',
            'worktree': '/tmp/issue-999',
            'laneState': {'issue': {'number': 111}},
        },
        224,
    )

    assert result is True



def test_codex_model_for_issue_escalates_for_restart_pressure_and_large_effort_labels():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    escalated = sessions_module.codex_model_for_issue(
        {'number': 224, 'title': 'Issue 224', 'labels': [{'name': 'effort:large'}]},
        lane_state={'restart': {'count': 3}, 'review': {'localClaudeReviewCount': 0}},
        workflow_state='implementing_local',
        reviews={'codexCloud': {'openFindingCount': 0}},
        default_model='gpt-5.3-codex',
        high_effort_model='gpt-5.3-codex-spark/high',
        escalated_model='gpt-5.4',
        escalate_restart_count=3,
        escalate_local_review_count=2,
        escalate_postpublish_finding_count=5,
    )
    high_effort = sessions_module.codex_model_for_issue(
        {'number': 224, 'title': 'Issue 224', 'labels': [{'name': 'effort:large'}]},
        lane_state={'restart': {'count': 0}, 'review': {'localClaudeReviewCount': 0}},
        workflow_state='implementing_local',
        reviews={'codexCloud': {'openFindingCount': 0}},
        default_model='gpt-5.3-codex',
        high_effort_model='gpt-5.3-codex-spark/high',
        escalated_model='gpt-5.4',
        escalate_restart_count=3,
        escalate_local_review_count=2,
        escalate_postpublish_finding_count=5,
    )

    assert escalated == 'gpt-5.4'
    assert high_effort == 'gpt-5.3-codex-spark/high'



def test_actor_labels_payload_uses_escalation_name_for_escalated_model():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    result = sessions_module.actor_labels_payload(
        current_coder_model='gpt-5.4',
        default_model='gpt-5.3-codex',
        escalated_model='gpt-5.4',
        internal_coder_agent_name='Internal_Coder_Agent',
        escalation_coder_agent_name='Escalation_Coder_Agent',
        internal_reviewer_agent_name='Internal_Reviewer_Agent',
        internal_reviewer_model='claude-sonnet-4-6',
        external_reviewer_agent_name='External_Reviewer_Agent',
        advisory_reviewer_agent_name='Advisory_Reviewer_Agent',
    )

    assert result['currentCoderAgent'] == {'name': 'Escalation_Coder_Agent', 'model': 'gpt-5.4'}


def test_build_and_record_session_nudge_payload_capture_session_issue_and_pr_context(tmp_path):
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    payload = sessions_module.build_session_nudge_payload(
        session_action={"action": "poke-session", "reason": "stale-open-session", "sessionName": "lane-224"},
        issue={"number": 224, "title": "Issue 224"},
        open_pr={"number": 301, "url": "https://example.com/pull/301", "headRefOid": "abc123"},
        lane_memo_path="/tmp/issue-224/.lane-memo.md",
        now_iso="2026-04-23T00:20:00Z",
    )

    seen = {}

    def fake_load(path):
        seen["loaded"] = path
        return {"schemaVersion": 1}

    state = sessions_module.record_session_nudge(
        worktree=tmp_path,
        payload=payload,
        lane_state_path_fn=lambda worktree: worktree / ".lane-state.json",
        load_optional_json_fn=fake_load,
        write_json_fn=lambda path, value: seen.setdefault("written", (path, value)),
    )

    assert payload["sessionName"] == "lane-224"
    assert payload["prNumber"] == 301
    assert payload["headSha"] == "abc123"
    assert state["sessionControl"]["lastNudge"]["reason"] == "stale-open-session"
    assert seen["written"][0] == tmp_path / ".lane-state.json"


def test_assess_codex_session_health_marks_recent_session_healthy_and_mid_stale_session_pokeable():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    healthy = sessions_module.assess_codex_session_health(
        {"name": "lane-224", "cwd": "/tmp/issue-224", "last_used_at": "2026-04-23T00:09:30Z"},
        Path("/tmp/issue-224"),
        now_epoch=1_776_903_000,
        freshness_seconds=120,
        poke_grace_seconds=600,
    )
    pokeable = sessions_module.assess_codex_session_health(
        {"name": "lane-224", "cwd": "/tmp/issue-224", "last_used_at": "2026-04-23T00:04:30Z"},
        Path("/tmp/issue-224"),
        now_epoch=1_776_903_000,
        freshness_seconds=120,
        poke_grace_seconds=600,
    )

    assert healthy == {
        "healthy": True,
        "reason": None,
        "sessionName": "lane-224",
        "lastUsedAt": "2026-04-23T00:09:30Z",
        "freshnessSeconds": 30,
        "canPoke": False,
    }
    assert pokeable == {
        "healthy": False,
        "reason": "stale-open-session",
        "sessionName": "lane-224",
        "lastUsedAt": "2026-04-23T00:04:30Z",
        "freshnessSeconds": 330,
        "canPoke": True,
    }


def test_assess_codex_session_health_rejects_closed_wrong_worktree_and_missing_last_used():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    closed = sessions_module.assess_codex_session_health(
        {"name": "lane-224", "closed": True, "last_used_at": "2026-04-23T00:09:30Z"},
        Path("/tmp/issue-224"),
        now_epoch=1_776_903_000,
    )
    wrong_worktree = sessions_module.assess_codex_session_health(
        {"name": "lane-224", "cwd": "/tmp/other", "last_used_at": "2026-04-23T00:09:30Z"},
        Path("/tmp/issue-224"),
        now_epoch=1_776_903_000,
    )
    missing_last_used = sessions_module.assess_codex_session_health(
        {"name": "lane-224", "cwd": "/tmp/issue-224"},
        Path("/tmp/issue-224"),
        now_epoch=1_776_903_000,
    )

    assert closed["reason"] == "closed-session"
    assert wrong_worktree["reason"] == "wrong-worktree"
    assert missing_last_used["reason"] == "missing-last-used"


def test_build_acp_session_strategy_supports_acpx_and_legacy_runtime_shapes():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    acpx = sessions_module.build_acp_session_strategy(
        implementation_session_key="legacy-key",
        session_action={"action": "restart-session"},
        lane_state={"sessionControl": {"targetSessionKey": "old-target", "resumeSessionId": "old-resume"}},
        session_runtime="acpx-codex",
        session_name="lane-224",
        resume_session_id="resume-123",
    )
    legacy = sessions_module.build_acp_session_strategy(
        implementation_session_key="legacy-key",
        session_action={"action": "continue-session"},
        lane_state={"sessionControl": {"targetSessionKey": "fallback-target", "resumeSessionId": "fallback-resume"}},
        session_runtime="acp",
    )

    assert acpx == {
        "runtime": "acpx-codex",
        "spawnMode": "session",
        "nudgeTool": "acpx codex prompt -s",
        "targetSessionKey": "lane-224",
        "resumeSessionId": "resume-123",
        "preferredAction": "restart-session",
    }
    assert legacy == {
        "runtime": "acp",
        "spawnMode": "session",
        "nudgeTool": "sessions_send",
        "targetSessionKey": "legacy-key",
        "resumeSessionId": "fallback-resume",
        "preferredAction": "continue-session",
    }


def test_build_acp_session_strategy_supports_codex_app_server_threads():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_codex_strategy", "workflows/change_delivery/sessions.py")

    result = sessions_module.build_acp_session_strategy(
        implementation_session_key="legacy-key",
        session_action={"action": "continue-session"},
        lane_state={"sessionControl": {"targetSessionKey": "old-target", "resumeSessionId": "old-thread"}},
        session_runtime="codex-app-server",
        session_name="lane-224",
        resume_session_id="thread-224",
    )

    assert result == {
        "runtime": "codex-app-server",
        "spawnMode": "thread",
        "nudgeTool": "codex app-server turn/start",
        "targetSessionKey": "lane-224",
        "resumeSessionId": "thread-224",
        "preferredAction": "continue-session",
    }


def test_should_nudge_session_blocks_recent_same_head_but_allows_other_cases():
    sessions_module = load_module("daedalus_workflows_change_delivery_sessions_test", "workflows/change_delivery/sessions.py")

    blocked = sessions_module.should_nudge_session(
        lane_state={"sessionControl": {"lastNudge": {"sessionName": "lane-224", "headSha": "abc123", "at": "2026-04-23T00:09:30Z"}}},
        session_action={"action": "poke-session", "sessionName": "lane-224"},
        current_head_sha="abc123",
        now_epoch=1_776_903_000,
        cooldown_seconds=120,
    )
    allowed = sessions_module.should_nudge_session(
        lane_state={"sessionControl": {"lastNudge": {"sessionName": "lane-224", "headSha": "abc123", "at": "2026-04-23T00:09:30Z"}}},
        session_action={"action": "poke-session", "sessionName": "lane-224"},
        current_head_sha="def456",
        now_epoch=1_776_903_000,
        cooldown_seconds=120,
    )
    no_action = sessions_module.should_nudge_session(
        lane_state={},
        session_action={"action": "continue-session", "sessionName": "lane-224"},
        current_head_sha="abc123",
        now_epoch=1_776_903_000,
        cooldown_seconds=120,
    )

    assert blocked == {"shouldNudge": False, "reason": "recent-nudge-same-head"}
    assert allowed == {"shouldNudge": True, "reason": None}
    assert no_action == {"shouldNudge": False, "reason": "not-poke-session"}


def test_ensure_session_via_runtime_routes_through_workspace_runtime_accessor():
    """sessions.ensure_session_via_runtime(workspace=..., runtime_name=..., ...) must
    resolve the runtime via ws.runtime() and delegate to its ensure_session."""
    from pathlib import Path
    from workflows.change_delivery.runtimes import SessionHandle
    from workflows.change_delivery import sessions

    captured = {}

    class FakeRuntime:
        def ensure_session(self, *, worktree, session_name, model, resume_session_id=None):
            captured["called"] = (worktree, session_name, model, resume_session_id)
            return SessionHandle(record_id="rec-1", session_id="sess-1", name=session_name)

    class FakeWs:
        def runtime(self, name):
            captured["runtime_name"] = name
            return FakeRuntime()

    handle = sessions.ensure_session_via_runtime(
        workspace=FakeWs(),
        runtime_name="acpx-codex",
        worktree=Path("/tmp/wt"),
        session_name="lane-224",
        model="gpt-5.3-codex-spark/high",
    )
    assert captured["runtime_name"] == "acpx-codex"
    assert captured["called"] == (Path("/tmp/wt"), "lane-224", "gpt-5.3-codex-spark/high", None)
    assert handle.record_id == "rec-1"


def test_run_prompt_via_runtime_delegates_to_ws_runtime():
    from pathlib import Path
    from workflows.change_delivery import sessions

    captured = {}

    class FakeRuntime:
        def run_prompt(self, *, worktree, session_name, prompt, model):
            captured["called"] = (worktree, session_name, prompt, model)
            return "stdout-output"

    class FakeWs:
        def runtime(self, name):
            captured["runtime_name"] = name
            return FakeRuntime()

    out = sessions.run_prompt_via_runtime(
        workspace=FakeWs(),
        runtime_name="claude-cli",
        worktree=Path("/tmp/wt"),
        session_name="inter-review:abc",
        prompt="review this",
        model="claude-sonnet-4-6",
    )
    assert captured["runtime_name"] == "claude-cli"
    assert out == "stdout-output"


def test_close_session_via_runtime_delegates_to_ws_runtime():
    from pathlib import Path
    from workflows.change_delivery import sessions

    captured = {}

    class FakeRuntime:
        def close_session(self, *, worktree, session_name):
            captured["called"] = (worktree, session_name)

    class FakeWs:
        def runtime(self, name):
            captured["runtime_name"] = name
            return FakeRuntime()

    sessions.close_session_via_runtime(
        workspace=FakeWs(),
        runtime_name="acpx-codex",
        worktree=Path("/tmp/wt"),
        session_name="lane-224",
    )
    assert captured["runtime_name"] == "acpx-codex"
    assert captured["called"] == (Path("/tmp/wt"), "lane-224")
