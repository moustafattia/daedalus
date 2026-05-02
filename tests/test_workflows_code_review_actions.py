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


# Historical note: this file used to contain nine
# ``test_*_prefers_raw_wrapper_function_when_present`` tests that wrote a
# scaffold wrapper file at ``<workflow_root>/compat/legacy_workflow_wrapper.py`` and
# asserted that ``actions.publish_ready_pr(workflow_root)`` /
# ``merge_and_promote(workflow_root)`` / etc. loaded the wrapper and called
# its ``*_raw`` functions, plus a tenth test that exercised
# ``actions.tick(workflow_root)``. After the workspace-side wrapper script
# was retired and the live CLI + workspace code paths switched to calling
# ``ws.publish_ready_pr_raw()`` etc. directly, those ``workflow_root``-taking
# entrypoints became dead code and were removed from
# ``workflows/change_delivery/actions.py``. The tests below cover the live
# ``run_*`` functions that back the workspace shims.


class FakeCodeHost:
    def __init__(self):
        self.calls = []

    def mark_pull_request_ready(self, pr_number):
        self.calls.append(("ready", pr_number))
        return True

    def create_pull_request(self, **kwargs):
        self.calls.append(("create", kwargs))
        return "https://github.example/pull/301"

    def merge_pull_request(self, pr_number, **kwargs):
        self.calls.append(("merge", pr_number, kwargs))
        return {"ok": True}


def test_run_publish_ready_pr_reports_no_active_lane_when_reconcile_has_none():
    actions_module = load_module("daedalus_workflows_change_delivery_actions_ppr", "workflows/change_delivery/actions.py")

    captured: dict = {}

    def fake_reconcile(*, fix_watchers=False):
        captured.setdefault("reconcile", []).append({"fix_watchers": fix_watchers})
        return {"activeLane": None, "implementation": {}}

    def fake_run(*args, **kwargs):
        raise AssertionError("run_fn should not execute when there is no active lane")

    def fake_audit(*args, **kwargs):
        raise AssertionError("audit should not fire when nothing is published")

    result = actions_module.run_publish_ready_pr(
        reconcile_fn=fake_reconcile,
        run_fn=fake_run,
        audit_fn=fake_audit,
        code_host_client=FakeCodeHost(),
    )

    assert result == {"published": False, "reason": "no-active-lane"}
    assert len(captured["reconcile"]) == 1


def test_run_publish_ready_pr_marks_existing_draft_ready_without_pushing(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_ppr", "workflows/change_delivery/actions.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    call_order: list[str] = []

    def fake_reconcile(*, fix_watchers=False):
        call_order.append("reconcile")
        status = {
            "activeLane": {"number": 224, "title": "T"},
            "implementation": {"worktree": str(worktree), "branch": "issue-224"},
            "openPr": {"number": 301, "isDraft": True},
        }
        # Simulate the after-call reconcile reporting the PR is no longer a draft.
        if len(call_order) > 1:
            status["openPr"]["isDraft"] = False
        return status

    def fake_run(*args, **kwargs):
        raise AssertionError("should not invoke git when a PR already exists")

    code_host = FakeCodeHost()

    def fake_audit(*args, **kwargs):
        call_order.append("audit")

    result = actions_module.run_publish_ready_pr(
        reconcile_fn=fake_reconcile,
        run_fn=fake_run,
        audit_fn=fake_audit,
        code_host_client=code_host,
    )

    assert result["published"] is True
    assert result["prNumber"] == 301
    assert ("ready", 301) in code_host.calls
    assert call_order.count("reconcile") == 2


def test_run_push_pr_update_skips_when_pr_head_matches_local(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_ppu", "workflows/change_delivery/actions.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    def fake_reconcile(*, fix_watchers=False):
        return {
            "activeLane": {"number": 224},
            "openPr": {"number": 301, "headRefOid": "sha"},
            "implementation": {"worktree": str(worktree), "branch": "issue-224", "localHeadSha": "sha"},
        }

    def fake_run(*args, **kwargs):
        raise AssertionError("should not push when local already matches")

    def fake_audit(*args, **kwargs):
        raise AssertionError("should not audit on noop push")

    result = actions_module.run_push_pr_update(
        reconcile_fn=fake_reconcile,
        run_fn=fake_run,
        audit_fn=fake_audit,
    )
    assert result["pushed"] is False
    assert result["reason"] == "pr-already-current"


def test_run_push_pr_update_pushes_updated_head_and_audits(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_ppu", "workflows/change_delivery/actions.py")
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    reconcile_calls: list[bool] = []

    def fake_reconcile(*, fix_watchers=False):
        reconcile_calls.append(fix_watchers)
        return {
            "activeLane": {"number": 224},
            "openPr": {"number": 301, "headRefOid": "prsha"},
            "implementation": {"worktree": str(worktree), "branch": "issue-224", "localHeadSha": "localsha"},
        }

    runs: list[dict] = []

    def fake_run(command, cwd=None):
        runs.append({"command": command, "cwd": str(cwd) if cwd else None})
        class _C:
            stdout = "ok"
        return _C()

    audits: list[dict] = []

    def fake_audit(action, summary, **extra):
        audits.append({"action": action, "summary": summary, **extra})

    result = actions_module.run_push_pr_update(
        reconcile_fn=fake_reconcile,
        run_fn=fake_run,
        audit_fn=fake_audit,
    )
    assert result["pushed"] is True
    assert result["prNumber"] == 301
    assert runs[0]["command"] == ["git", "push", "origin", "HEAD:issue-224"]
    assert audits[0]["action"] == "push-pr-update"
    assert len(reconcile_calls) == 2


def test_run_merge_and_promote_skips_when_missing_active_lane_or_pr():
    actions_module = load_module("daedalus_workflows_change_delivery_actions_map", "workflows/change_delivery/actions.py")

    def fake_reconcile(*, fix_watchers=False):
        return {"activeLane": None, "openPr": None}

    result = actions_module.run_merge_and_promote(
        reconcile_fn=fake_reconcile,
        audit_fn=lambda *a, **k: None,
        issue_remove_label_fn=lambda *a, **k: None,
        issue_close_fn=lambda *a, **k: None,
        issue_add_label_fn=lambda *a, **k: None,
        issue_comment_fn=lambda *a, **k: None,
        pick_next_lane_issue_fn=lambda: None,
        now_iso_fn=lambda: "2026-04-23T00:00:00Z",
        active_lane_label="P0",
        code_host_client=FakeCodeHost(),
    )
    assert result == {"merged": False, "reason": "missing-active-lane-or-pr"}


def test_run_merge_and_promote_promotes_next_lane_after_merge():
    actions_module = load_module("daedalus_workflows_change_delivery_actions_map", "workflows/change_delivery/actions.py")
    reconcile_calls: list = []

    def fake_reconcile(*, fix_watchers=False):
        reconcile_calls.append(fix_watchers)
        return {
            "activeLane": {"number": 224, "title": "T"},
            "openPr": {"number": 301, "headRefOid": "prsha"},
            "derivedReviewLoopState": "clean",
            "derivedMergeBlocked": False,
            "reviews": {
                "externalReview": {
                    "required": True,
                    "reviewScope": "postpublish-pr",
                    "status": "completed",
                    "verdict": "PASS_CLEAN",
                    "reviewedHeadSha": "prsha",
                    "openFindingCount": 0,
                }
            },
        }

    calls: dict = {"audits": [], "issue": []}
    code_host = FakeCodeHost()

    def fake_audit(action, summary, **extra):
        calls["audits"].append({"action": action, "summary": summary, **extra})

    def fake_remove(issue_number, label):
        calls["issue"].append(("remove", issue_number, label))

    def fake_close(issue_number, comment):
        calls["issue"].append(("close", issue_number, comment))

    def fake_add(issue_number, label):
        calls["issue"].append(("add", issue_number, label))

    def fake_comment(issue_number, body):
        calls["issue"].append(("comment", issue_number, body))

    def fake_next():
        return {"number": 225}

    result = actions_module.run_merge_and_promote(
        reconcile_fn=fake_reconcile,
        audit_fn=fake_audit,
        issue_remove_label_fn=fake_remove,
        issue_close_fn=fake_close,
        issue_add_label_fn=fake_add,
        issue_comment_fn=fake_comment,
        pick_next_lane_issue_fn=fake_next,
        now_iso_fn=lambda: "2026-04-23T00:00:00Z",
        active_lane_label="P0",
        code_host_client=code_host,
    )
    assert result["merged"] is True
    assert result["mergedPrNumber"] == 301
    assert result["nextIssueNumber"] == 225
    assert code_host.calls[0] == ("merge", 301, {"squash": True, "delete_branch": True})
    assert ("remove", 224, "P0") in calls["issue"]
    assert ("add", 225, "P0") in calls["issue"]


def test_run_merge_and_promote_refuses_when_external_review_is_not_clean():
    actions_module = load_module("daedalus_workflows_change_delivery_actions_map", "workflows/change_delivery/actions.py")
    code_host = FakeCodeHost()

    def fake_reconcile(*, fix_watchers=False):
        return {
            "activeLane": {"number": 224, "title": "T"},
            "openPr": {"number": 301, "headRefOid": "prsha"},
            "derivedReviewLoopState": "awaiting_reviews",
            "derivedMergeBlocked": True,
            "derivedMergeBlockers": ["externalReview-pending"],
            "reviews": {
                "externalReview": {
                    "required": True,
                    "reviewScope": "postpublish-pr",
                    "status": "pending",
                    "verdict": None,
                    "reviewedHeadSha": "prsha",
                    "openFindingCount": 0,
                }
            },
        }

    result = actions_module.run_merge_and_promote(
        reconcile_fn=fake_reconcile,
        audit_fn=lambda *a, **k: None,
        issue_remove_label_fn=lambda *a, **k: None,
        issue_close_fn=lambda *a, **k: None,
        issue_add_label_fn=lambda *a, **k: None,
        issue_comment_fn=lambda *a, **k: None,
        pick_next_lane_issue_fn=lambda: None,
        now_iso_fn=lambda: "2026-04-23T00:00:00Z",
        active_lane_label="P0",
        code_host_client=code_host,
    )

    assert result["merged"] is False
    assert result["reason"] == "merge-gate-not-satisfied"
    assert code_host.calls == []


def test_run_ensure_active_lane_promotes_first_eligible_issue():
    actions_module = load_module("daedalus_workflows_change_delivery_actions_eal", "workflows/change_delivery/actions.py")
    calls: list[tuple] = []

    def fake_reconcile(*, fix_watchers=False):
        calls.append(("reconcile", fix_watchers))
        return {
            "health": "healthy",
            "activeLane": {"number": 225},
            "nextAction": {"type": "dispatch_implementation_turn"},
        }

    result = actions_module.run_ensure_active_lane(
        build_status_fn=lambda: {"activeLane": None, "activeLaneError": None},
        reconcile_fn=fake_reconcile,
        audit_fn=lambda action, summary, **extra: calls.append(("audit", action, extra)),
        issue_add_label_fn=lambda issue_number, label: calls.append(("add", issue_number, label)) or True,
        issue_comment_fn=lambda issue_number, body: calls.append(("comment", issue_number, body)) or True,
        pick_next_lane_issue_fn=lambda: {"number": 225, "title": "Next lane", "url": "https://example.test/issues/225"},
        active_lane_label="active-lane",
    )

    assert result["ok"] is True
    assert result["promoted"] is True
    assert result["issueNumber"] == 225
    assert result["after"] == {"health": "healthy", "activeLane": 225, "nextAction": "dispatch_implementation_turn"}
    assert ("add", 225, "active-lane") in calls
    assert any(call[0] == "comment" and call[1] == 225 for call in calls)
    assert ("reconcile", True) in calls


def test_run_ensure_active_lane_skips_when_active_lane_already_exists():
    actions_module = load_module("daedalus_workflows_change_delivery_actions_eal_skip", "workflows/change_delivery/actions.py")

    def should_not_run(*args, **kwargs):
        raise AssertionError("selection should not run when an active lane already exists")

    result = actions_module.run_ensure_active_lane(
        build_status_fn=lambda: {"activeLane": {"number": 224}},
        reconcile_fn=should_not_run,
        audit_fn=should_not_run,
        issue_add_label_fn=should_not_run,
        issue_comment_fn=should_not_run,
        pick_next_lane_issue_fn=should_not_run,
        active_lane_label="active-lane",
    )

    assert result == {
        "ok": True,
        "promoted": False,
        "reason": "active-lane-present",
        "issueNumber": 224,
    }


def test_run_ensure_active_lane_reports_selection_failure_without_raising():
    actions_module = load_module("daedalus_workflows_change_delivery_actions_eal_fail", "workflows/change_delivery/actions.py")

    def broken_selection():
        raise RuntimeError("gh unavailable")

    result = actions_module.run_ensure_active_lane(
        build_status_fn=lambda: {"activeLane": None},
        reconcile_fn=lambda **_kwargs: {},
        audit_fn=lambda *args, **kwargs: None,
        issue_add_label_fn=lambda *_args: True,
        issue_comment_fn=lambda *_args: True,
        pick_next_lane_issue_fn=broken_selection,
        active_lane_label="active-lane",
    )

    assert result["ok"] is False
    assert result["promoted"] is False
    assert result["reason"] == "lane-selection-failed"
    assert "gh unavailable" in result["error"]


def _dispatch_deps(tmp_path: Path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    state: dict = {"close_calls": 0, "run_prompt_calls": [], "save_ledger": []}

    def now_iso_fn():
        return "2026-04-23T00:00:00Z"

    def close_fn(*, worktree, session_name, runtime_name=None, runtime_kind=None):
        state["close_calls"] += 1

    def show_fn(*, worktree, session_name, runtime_name=None, runtime_kind=None):
        return {"record_id": "rec-123", "session_id": "sess-abc"}

    def run_stage_fn(*, worktree, session_name, prompt, actor_name, actor_cfg, runtime_name, runtime_kind, resume_session_id=None):
        state["run_prompt_calls"].append(
            {
                "session_name": session_name,
                "actor_name": actor_name,
                "actor_model": actor_cfg.get("model"),
                "runtime_name": runtime_name,
                "runtime_kind": runtime_kind,
            }
        )
        return "ok"

    def prepare_worktree_fn(*, worktree, branch, open_pr):
        return {"prepared": True}

    def get_issue_details_fn(number):
        return {"labels": []}

    def workflow_actors_payload_fn(actor):
        return {"implementationActor": actor}

    ledger = {"implementation": {}, "workflowState": "implementing_local"}

    def load_ledger_fn():
        return ledger

    def save_ledger_fn(payload):
        ledger.update(payload)
        state["save_ledger"].append(payload)

    def reconcile_fn(*, fix_watchers=False):
        return {"health": "healthy"}

    def audit_fn(action, summary, **extra):
        state.setdefault("audits", []).append({"action": action, "summary": summary, **extra})

    def render_prompt_fn(*, issue, issue_details, worktree, lane_memo_path, lane_state_path, open_pr, action, workflow_state):
        return f"prompt-for-{action}"

    return state, worktree, {
        "now_iso_fn": now_iso_fn,
        "close_session_fn": close_fn,
        "show_session_fn": show_fn,
        "run_stage_fn": run_stage_fn,
        "prepare_lane_worktree_fn": prepare_worktree_fn,
        "implementation_actor_name": "implementer",
        "implementation_actor_cfg": {
            "name": "Change_Implementer",
            "model": "gpt-5.3-codex-spark/high",
            "runtime": "acpx-codex",
        },
        "runtime_name": "acpx-codex",
        "runtime_kind": "acpx-codex",
        "get_issue_details_fn": get_issue_details_fn,
        "workflow_actors_payload_fn": workflow_actors_payload_fn,
        "load_ledger_fn": load_ledger_fn,
        "save_ledger_fn": save_ledger_fn,
        "reconcile_fn": reconcile_fn,
        "audit_fn": audit_fn,
        "render_implementation_dispatch_prompt_fn": render_prompt_fn,
    }


def test_run_dispatch_lane_turn_short_circuits_when_no_active_lane(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_rdlt", "workflows/change_delivery/actions.py")
    state, worktree, deps = _dispatch_deps(tmp_path)
    result = actions_module.run_dispatch_lane_turn(
        status={"activeLane": None, "implementation": {}, "ledger": {}, "reviews": {}},
        forced_action=None,
        audit_action="dispatch-implementation-turn",
        **deps,
    )
    assert result == {"dispatched": False, "reason": "no-active-lane"}
    assert state["run_prompt_calls"] == []


def test_run_dispatch_lane_turn_executes_continue_session_when_healthy(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_rdlt", "workflows/change_delivery/actions.py")
    state, worktree, deps = _dispatch_deps(tmp_path)
    status = {
        "activeLane": {"number": 224, "title": "T", "url": "https://example.test/issue/224"},
        "implementation": {
            "worktree": str(worktree),
            "branch": "issue-224",
            "sessionName": "lane-224",
            "actorModel": "gpt-5.3-codex",
            "resumeSessionId": "sess-abc",
            "sessionActionRecommendation": {"action": "continue-session"},
            "laneState": {},
        },
        "ledger": {"workflowState": "implementing_local"},
        "reviews": {},
        "openPr": None,
    }

    result = actions_module.run_dispatch_lane_turn(
        status=status,
        forced_action=None,
        audit_action="dispatch-implementation-turn",
        **deps,
    )

    assert result["dispatched"] is True
    assert result["action"] == "continue-session"
    assert result["issueNumber"] == 224
    assert result["sessionName"] == "lane-224"
    assert result["actorKey"] == "implementer"
    assert result["actorName"] == "Change_Implementer"
    assert state["close_calls"] == 0  # continue-session doesn't close
    assert state["run_prompt_calls"][0]["runtime_name"] == "acpx-codex"
    assert state["audits"][0]["action"] == "dispatch-implementation-turn"


def test_run_dispatch_lane_turn_records_codex_app_server_thread_metrics(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_codex", "workflows/change_delivery/actions.py")
    state, worktree, deps = _dispatch_deps(tmp_path)
    status = {
        "activeLane": {"number": 224, "title": "T", "url": "https://example.test/issue/224"},
        "implementation": {
            "worktree": str(worktree),
            "branch": "issue-224",
            "sessionName": "lane-224",
            "actorModel": "gpt-5.5",
            "resumeSessionId": "thread-existing",
            "sessionActionRecommendation": {"action": "continue-session"},
            "laneState": {},
        },
        "ledger": {"workflowState": "implementing_local"},
        "reviews": {},
        "openPr": None,
    }

    def run_stage_fn(*, worktree, session_name, prompt, actor_name, actor_cfg, runtime_name, runtime_kind, resume_session_id=None):
        return SimpleNamespace(
            runtime_result=SimpleNamespace(
                output="codex ok\n",
                session_id="thread-1",
                thread_id="thread-1",
                turn_id="turn-1",
                last_event="turn/completed",
                last_message="done",
                turn_count=1,
                tokens={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
                rate_limits={"requests_remaining": 99},
            ),
            session_handle={"record_id": "thread-1", "session_id": "thread-1"},
        )

    recorded = {}

    def record_runtime_result_fn(**kwargs):
        recorded.update(kwargs)
        return kwargs["metrics"]

    result = actions_module.run_dispatch_lane_turn(
        status=status,
        forced_action=None,
        audit_action="dispatch-implementation-turn",
        **{
            **deps,
            "run_stage_fn": run_stage_fn,
            "implementation_actor_cfg": {
                "name": "Change_Implementer",
                "model": "gpt-5.5",
                "runtime": "coder-runtime",
            },
            "runtime_name": "coder-runtime",
            "runtime_kind": "codex-app-server",
            "record_runtime_result_fn": record_runtime_result_fn,
        },
    )

    assert result["runtimeKind"] == "codex-app-server"
    assert result["runtimeName"] == "coder-runtime"
    assert result["threadId"] == "thread-1"
    assert result["turnId"] == "turn-1"
    assert result["promptResult"] == "codex ok"
    assert result["metrics"]["tokens"]["total_tokens"] == 18
    assert recorded["issue"]["number"] == 224
    impl = state["save_ledger"][-1]["implementation"]
    assert impl["runtimeKind"] == "codex-app-server"
    assert impl["session"] == "thread-1"
    assert impl["actorName"] == "Change_Implementer"
    assert impl["actorModel"] == "gpt-5.5"
    assert impl["resumeSessionId"] == "thread-1"
    assert impl["runtimeMetrics"]["rate_limits"] == {"requests_remaining": 99}


def test_run_dispatch_lane_turn_reconciles_runtime_error_after_local_head(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_reconciled_error", "workflows/change_delivery/actions.py")
    state, worktree, deps = _dispatch_deps(tmp_path)
    status = {
        "activeLane": {"number": 224, "title": "T", "url": "https://example.test/issue/224"},
        "implementation": {
            "worktree": str(worktree),
            "branch": "issue-224",
            "sessionName": "lane-224",
            "actorModel": "gpt-5.5",
            "resumeSessionId": "thread-existing",
            "sessionActionRecommendation": {"action": "continue-session"},
            "laneState": {},
        },
        "ledger": {"workflowState": "implementing_local"},
        "reviews": {},
        "openPr": None,
    }

    class RuntimeCompletedThenTimedOut(RuntimeError):
        def __init__(self):
            super().__init__("codex-app-server failed: timed out")
            self.result = SimpleNamespace(
                output="implemented\n",
                session_id="thread-1",
                thread_id="thread-1",
                turn_id="turn-1",
                last_event="item/agentMessage/delta",
                last_message=".",
                turn_count=1,
                tokens={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
                rate_limits=None,
            )

    def run_stage_fn(*, worktree, session_name, prompt, actor_name, actor_cfg, runtime_name, runtime_kind, resume_session_id=None):
        raise RuntimeCompletedThenTimedOut()

    def reconcile_fn(*, fix_watchers=False):
        return {
            "health": "healthy",
            "activeLane": {"number": 224},
            "implementation": {"localHeadSha": "head-after-error"},
            "nextAction": {"type": "run_internal_review", "reason": "prepublish-review-required"},
        }

    recorded = {}

    def record_runtime_result_fn(**kwargs):
        recorded.update(kwargs)
        return kwargs["metrics"]

    result = actions_module.run_dispatch_lane_turn(
        status=status,
        forced_action=None,
        audit_action="dispatch-implementation-turn",
        **{
            **deps,
            "run_stage_fn": run_stage_fn,
            "implementation_actor_cfg": {
                "name": "Change_Implementer",
                "model": "gpt-5.5",
                "runtime": "coder-runtime",
            },
            "reconcile_fn": reconcile_fn,
            "runtime_name": "coder-runtime",
            "runtime_kind": "codex-app-server",
            "record_runtime_result_fn": record_runtime_result_fn,
        },
    )

    assert result["dispatched"] is True
    assert result["reconciledAfterRuntimeError"] is True
    assert result["runtimeError"] == "codex-app-server failed: timed out"
    assert result["promptResult"] == "implemented"
    assert result["health"] == "healthy"
    assert recorded["metrics"]["thread_id"] == "thread-1"
    assert state["save_ledger"]


def test_run_dispatch_lane_turn_closes_session_for_restart(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_rdlt", "workflows/change_delivery/actions.py")
    state, worktree, deps = _dispatch_deps(tmp_path)
    status = {
        "activeLane": {"number": 224, "title": "T", "url": "https://example.test/issue/224"},
        "implementation": {
            "worktree": str(worktree),
            "branch": "issue-224",
            "sessionName": "lane-224",
            "sessionActionRecommendation": {"action": "restart-session"},
            "laneState": {},
        },
        "ledger": {"workflowState": "implementing_local"},
        "reviews": {},
        "openPr": None,
    }
    result = actions_module.run_dispatch_lane_turn(
        status=status,
        forced_action="restart-session",
        audit_action="restart-actor-session",
        **deps,
    )
    assert result["dispatched"] is True
    assert state["close_calls"] == 1


def _dispatch_review_deps(tmp_path: Path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    state: dict = {
        "ledger": {"reviews": {}},
        "save_ledger_calls": [],
        "audit_transitions": [],
        "run_review_calls": [],
    }

    def reconcile_fn(*, fix_watchers=False):
        return {
            "activeLane": {"number": 224, "title": "T", "url": "https://example.test/224"},
            "implementation": {"worktree": str(worktree), "actorModel": "gpt-5.3-codex"},
            "preflight": {"prePublishReview": {"shouldRun": True, "currentHeadSha": "head123"}},
        }

    def load_ledger_fn():
        return {**state["ledger"]}

    def save_ledger_fn(payload):
        import copy as _copy

        state["ledger"] = _copy.deepcopy(payload)
        state["save_ledger_calls"].append(_copy.deepcopy(payload))

    def audit_transition_fn(previous, current):
        state["audit_transitions"].append({"previous": previous, "current": current})

    iso_counter = iter(["2026-04-23T00:00:00Z", "2026-04-23T00:00:05Z"])

    def now_iso_fn():
        return next(iso_counter, "2026-04-23T01:00:00Z")

    def new_run_id_fn():
        return "run-001"

    def actor_labels_fn(model):
        return {}

    def run_review_fn(*, issue, worktree, lane_memo_path, lane_state_path, head_sha):
        state["run_review_calls"].append({"issue": issue, "head_sha": head_sha})
        return {
            "verdict": "PASS_CLEAN",
            "summary": "fine",
            "blockingFindings": [],
            "majorConcerns": [],
            "minorSuggestions": [],
            "requiredNextAction": None,
        }

    return state, {
        "reconcile_fn": reconcile_fn,
        "load_ledger_fn": load_ledger_fn,
        "save_ledger_fn": save_ledger_fn,
        "audit_inter_review_agent_transition_fn": audit_transition_fn,
        "run_inter_review_agent_review_fn": run_review_fn,
        "now_iso_fn": now_iso_fn,
        "new_inter_review_agent_run_id_fn": new_run_id_fn,
        "workflow_actors_payload_fn": lambda actor: {"implementationActor": actor},
        "inter_review_agent_model": "claude-sonnet-4-6",
        "internal_reviewer_agent_name": "Internal_Reviewer_Agent",
    }


def test_run_dispatch_inter_review_agent_review_short_circuits_when_no_active_lane(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_driar", "workflows/change_delivery/actions.py")
    state, deps = _dispatch_review_deps(tmp_path)

    def reconcile_fn(*, fix_watchers=False):
        return {"activeLane": None}

    deps["reconcile_fn"] = reconcile_fn
    result = actions_module.run_dispatch_inter_review_agent_review(**deps)
    assert result == {"dispatched": False, "reason": "no-active-lane"}
    assert state["run_review_calls"] == []


def test_run_dispatch_inter_review_agent_review_skips_when_preflight_blocks(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_driar", "workflows/change_delivery/actions.py")
    state, deps = _dispatch_review_deps(tmp_path)

    def reconcile_fn(*, fix_watchers=False):
        return {
            "activeLane": {"number": 224, "title": "T"},
            "implementation": {"worktree": str(tmp_path / "worktree")},
            "preflight": {"prePublishReview": {"shouldRun": False, "reasons": ["internal-review-cooldown"]}},
        }

    deps["reconcile_fn"] = reconcile_fn
    result = actions_module.run_dispatch_inter_review_agent_review(**deps)
    assert result["dispatched"] is False
    assert result["reason"] == "internal-review-preflight-blocked"


def test_run_dispatch_inter_review_agent_review_records_completed_review_on_success(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_driar", "workflows/change_delivery/actions.py")
    state, deps = _dispatch_review_deps(tmp_path)
    result = actions_module.run_dispatch_inter_review_agent_review(**deps)
    assert result["dispatched"] is True
    assert result["headSha"] == "head123"
    assert result["internalReviewerModel"] == "claude-sonnet-4-6"
    # Two save_ledger calls: running transition + completion transition
    assert len(state["save_ledger_calls"]) == 2
    first_saved = state["save_ledger_calls"][0]
    second_saved = state["save_ledger_calls"][1]
    assert first_saved["reviews"]["internalReview"]["status"] == "running"
    assert second_saved["reviews"]["internalReview"]["status"] == "completed"
    # Audit transitions called once per save
    assert len(state["audit_transitions"]) == 2


def test_run_dispatch_inter_review_agent_review_records_failed_review_and_reraises(tmp_path):
    actions_module = load_module("daedalus_workflows_change_delivery_actions_driar", "workflows/change_delivery/actions.py")
    state, deps = _dispatch_review_deps(tmp_path)

    class _FakeReviewError(RuntimeError):
        failure_class = "max_turns_exhausted"

    def failing_run(*, issue, worktree, lane_memo_path, lane_state_path, head_sha):
        raise _FakeReviewError("CLI exhausted turns")

    deps["run_inter_review_agent_review_fn"] = failing_run
    import pytest

    with pytest.raises(_FakeReviewError):
        actions_module.run_dispatch_inter_review_agent_review(**deps)

    # After failure path we should still have 2 ledger saves (running + failed)
    assert len(state["save_ledger_calls"]) == 2
    assert state["save_ledger_calls"][1]["reviews"]["internalReview"]["status"] == "failed"
    assert state["save_ledger_calls"][1]["reviews"]["internalReview"]["failureClass"] == "max_turns_exhausted"


def _tick_raw_deps():
    state: dict = {"reconcile": 0, "audits": [], "dispatched": []}

    def reconcile_fn(*, fix_watchers=False):
        state["reconcile"] += 1
        return {
            "health": "healthy",
            "activeLane": {"number": 224},
            "nextAction": {"type": "publish_ready_pr", "reason": "local-head-cleared-for-publish", "issueNumber": 224},
        }

    def audit_fn(action, summary, **extra):
        state["audits"].append({"action": action, "summary": summary, **extra})

    def dispatch_review_fn():
        state["dispatched"].append("review")
        return {"dispatched": True, "after": None}

    def dispatch_impl_fn():
        state["dispatched"].append("impl")
        return {"dispatched": True, "after": None}

    def publish_fn():
        state["dispatched"].append("publish")
        return {"published": True, "after": {"health": "healthy", "nextAction": {"type": "noop"}}}

    def push_fn():
        state["dispatched"].append("push")
        return {"pushed": True, "after": None}

    def merge_fn():
        state["dispatched"].append("merge")
        return {"merged": True, "after": None}

    return state, {
        "reconcile_fn": reconcile_fn,
        "audit_fn": audit_fn,
        "dispatch_inter_review_agent_review_fn": dispatch_review_fn,
        "dispatch_implementation_turn_fn": dispatch_impl_fn,
        "publish_ready_pr_fn": publish_fn,
        "push_pr_update_fn": push_fn,
        "merge_and_promote_fn": merge_fn,
    }


def test_run_tick_raw_returns_without_executing_when_next_action_is_noop():
    actions_module = load_module("daedalus_workflows_change_delivery_actions_rtr", "workflows/change_delivery/actions.py")
    state, deps = _tick_raw_deps()

    def reconcile_fn(*, fix_watchers=False):
        state["reconcile"] += 1
        return {"health": "healthy", "activeLane": None, "nextAction": {"type": "noop", "reason": "no-active-lane"}}

    deps["reconcile_fn"] = reconcile_fn
    result = actions_module.run_tick_raw(**deps)
    assert result["action"]["type"] == "noop"
    assert result["executed"] is None
    assert state["dispatched"] == []
    # Before + after reconcile
    assert state["reconcile"] == 2
    assert state["audits"][0]["action"] == "workflow-tick-action"


def test_run_tick_raw_dispatches_publish_branch_and_uses_returned_after():
    actions_module = load_module("daedalus_workflows_change_delivery_actions_rtr", "workflows/change_delivery/actions.py")
    state, deps = _tick_raw_deps()
    result = actions_module.run_tick_raw(**deps)
    assert state["dispatched"] == ["publish"]
    assert result["executed"]["published"] is True
    # When executed returns an "after", run_tick_raw should use it rather than reconciling again.
    assert state["reconcile"] == 1
    assert result["after"] == {"health": "healthy", "nextAction": {"type": "noop"}}


def test_run_tick_raw_dispatches_merge_branch_and_reconciles_after_when_executed_has_no_after():
    actions_module = load_module("daedalus_workflows_change_delivery_actions_rtr", "workflows/change_delivery/actions.py")
    state, deps = _tick_raw_deps()

    def reconcile_fn(*, fix_watchers=False):
        state["reconcile"] += 1
        return {
            "health": "healthy",
            "activeLane": {"number": 224},
            "nextAction": {"type": "merge_and_promote", "reason": "published-pr-approved"},
        }

    deps["reconcile_fn"] = reconcile_fn
    result = actions_module.run_tick_raw(**deps)
    assert state["dispatched"] == ["merge"]
    assert result["action"]["type"] == "merge_and_promote"
    # merge_fn returns after=None so run_tick_raw reconciles again -> 2 total
    assert state["reconcile"] == 2
