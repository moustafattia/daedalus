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


def test_render_implementation_dispatch_prompt_uses_compact_turn_for_continue_session():
    prompts_module = load_module("daedalus_workflows_change_delivery_prompts_test", "workflows/change_delivery/prompts.py")

    result = prompts_module.render_implementation_dispatch_prompt(
        issue={"number": 224, "title": "Issue 224", "url": "https://example.com/issues/224"},
        issue_details={"body": "Long issue body that should not appear on compact turns."},
        worktree=Path('/tmp/issue-224'),
        lane_memo_path=Path('/tmp/issue-224/.lane-memo.md'),
        lane_state_path=Path('/tmp/issue-224/.lane-state.json'),
        open_pr=None,
        action='continue-session',
        workflow_state='implementing_local',
    )

    assert 'Current turn context is intentionally compact to save tokens.' in result
    assert 'Issue summary:' not in result
    assert 'There is no open PR yet for this lane.' in result


def test_render_implementation_dispatch_prompt_includes_issue_summary_for_restart_session():
    prompts_module = load_module("daedalus_workflows_change_delivery_prompts_test", "workflows/change_delivery/prompts.py")

    result = prompts_module.render_implementation_dispatch_prompt(
        issue={"number": 224, "title": "Issue 224", "url": "https://example.com/issues/224"},
        issue_details={"body": "Full issue body for restart turns."},
        worktree=Path('/tmp/issue-224'),
        lane_memo_path=None,
        lane_state_path=None,
        open_pr={"number": 301, "url": "https://example.com/pull/301", "headRefOid": "abc123"},
        action='restart-session',
        workflow_state='ready_to_publish',
    )

    assert 'Issue summary:' in result
    assert 'Full issue body for restart turns.' in result
    assert 'Open PR: #301 https://example.com/pull/301' in result
    assert 'The local branch has already passed the Claude pre-publish gate.' in result


def test_render_implementation_dispatch_prompt_prepends_shared_workflow_policy():
    prompts_module = load_module("daedalus_workflows_change_delivery_prompts_test", "workflows/change_delivery/prompts.py")

    result = prompts_module.render_implementation_dispatch_prompt(
        issue={"number": 224, "title": "Issue 224", "url": "https://example.com/issues/224"},
        issue_details={"body": "Body."},
        worktree=Path('/tmp/issue-224'),
        lane_memo_path=None,
        lane_state_path=None,
        open_pr=None,
        action='restart-session',
        workflow_state='implementing_local',
        workflow_policy='Never widen scope.',
    )

    assert '# Shared Workflow Policy' in result
    assert 'Never widen scope.' in result
    assert '# Role-Specific Instructions' in result


def test_render_claude_repair_handoff_prompt_includes_review_summary_and_fix_lists():
    prompts_module = load_module("daedalus_workflows_change_delivery_prompts_test", "workflows/change_delivery/prompts.py")

    result = prompts_module.render_claude_repair_handoff_prompt(
        issue={"number": 224, "title": "Issue 224"},
        internal_review={"reviewedHeadSha": "abc123", "summary": "Claude found some stuff."},
        repair_brief={"mustFix": [{"summary": "Fix A"}], "shouldFix": [{"summary": "Fix B"}]},
        lane_memo_path=Path('/tmp/issue-224/.lane-memo.md'),
        lane_state_path=Path('/tmp/issue-224/.lane-state.json'),
        internal_reviewer_agent_name='Internal_Reviewer_Agent',
    )

    assert 'Internal_Reviewer_Agent pre-publish review found follow-up work for issue #224 on local head abc123.' in result
    assert 'Claude summary:' in result
    assert 'Claude found some stuff.' in result
    assert '- Fix A' in result
    assert '- Fix B' in result
    assert 'Do not publish yet.' in result


def test_render_external_reviewer_repair_handoff_prompt_includes_pr_url_and_guardrails():
    prompts_module = load_module("daedalus_workflows_change_delivery_prompts_test", "workflows/change_delivery/prompts.py")

    result = prompts_module.render_external_reviewer_repair_handoff_prompt(
        issue={"number": 224, "title": "Issue 224"},
        external_review={"reviewedHeadSha": "def456", "summary": "Codex Cloud found follow-up work."},
        repair_brief={"mustFix": [], "shouldFix": [{"summary": "Tighten edge case"}]},
        lane_memo_path=None,
        lane_state_path=None,
        pr_url='https://example.com/pull/301',
        external_reviewer_agent_name='External_Reviewer_Agent',
    )

    assert 'External_Reviewer_Agent review found follow-up work for issue #224 on published head def456.' in result
    assert 'PR: https://example.com/pull/301' in result
    assert 'External_Reviewer_Agent summary:' in result
    assert '- Tighten edge case' in result
    assert 'Do not publish .codex artifacts.' in result


def test_render_inter_review_agent_prompt_can_prepend_shared_workflow_policy(tmp_path):
    prompts_module = load_module("daedalus_workflows_change_delivery_prompts_test", "workflows/change_delivery/prompts.py")

    result = prompts_module.render_inter_review_agent_prompt(
        issue={"number": 224, "title": "Issue 224", "url": "https://example.com/issues/224"},
        worktree=tmp_path,
        lane_memo_path=None,
        lane_state_path=None,
        head_sha="abc123",
        workflow_policy="Do not guess.",
    )

    assert '# Shared Workflow Policy' in result
    assert 'Do not guess.' in result
    assert 'Scope: local-prepublish only' in result


def test_summarize_validation_and_render_lane_memo_capture_checks_progress_and_fix_lists():
    prompts_module = load_module("daedalus_workflows_change_delivery_prompts_test", "workflows/change_delivery/prompts.py")

    validation = prompts_module.summarize_validation(
        {
            "pr": {"checks": {"summary": "3/3 green"}},
            "implementation": {"status": "ready_to_publish"},
        }
    )
    memo = prompts_module.render_lane_memo(
        issue={"number": 224, "title": "Issue 224", "url": "https://example.com/issues/224"},
        worktree=Path('/tmp/issue-224'),
        branch='codex/issue-224-test',
        open_pr={"number": 301, "url": "https://example.com/pull/301", "headRefOid": "abc123"},
        repair_brief={"mustFix": [{"summary": "Fix A"}], "shouldFix": [{"summary": "Fix B"}]},
        latest_progress={"kind": "committed", "at": "2026-04-23T00:20:00Z"},
        validation_summary=validation,
        acp_strategy={"nudgeTool": "acpx codex prompt -s", "targetSessionKey": "lane-224", "resumeSessionId": "sess-1"},
    )

    assert validation == ["checks: 3/3 green", "implementation: ready_to_publish"]
    assert '# Lane Memo: Issue #224' in memo
    assert 'PR: #301 https://example.com/pull/301' in memo
    assert '- Fix A' in memo
    assert '- Fix B' in memo
    assert '- checks: 3/3 green' in memo
    assert '- committed at 2026-04-23T00:20:00Z' in memo
    assert '- Nudge via: acpx codex prompt -s -> lane-224' in memo


def test_prompt_templates_bundle_exists_with_three_files():
    from pathlib import Path
    bundle = Path(__file__).resolve().parents[1] / "daedalus" / "workflows" / "change_delivery" / "prompts"
    assert bundle.is_dir(), f"prompts bundle missing at {bundle}"
    names = sorted(p.name for p in bundle.glob("*.md"))
    assert names == [
        "coder.md",
        "external-reviewer-repair-handoff.md",
        "internal-reviewer.md",
        "repair-handoff.md",
    ]
