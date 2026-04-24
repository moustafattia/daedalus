import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _minimal_config(tmp_path: Path) -> dict:
    return {
        "repoPath": str(tmp_path / "repo"),
        "cronJobsPath": str(tmp_path / "cron-jobs.json"),
        "ledgerPath": str(tmp_path / "ledger.json"),
        "healthPath": str(tmp_path / "health.json"),
        "auditLogPath": str(tmp_path / "audit.jsonl"),
        "engineOwner": "hermes",
        "activeLaneLabel": "active-lane",
        "coreJobNames": ["yoyopod-workflow-watchdog"],
        "hermesJobNames": ["yoyopod-workflow-milestone-telegram"],
        "sessionPolicy": {"codexModel": "gpt-5.3-codex-spark/high"},
        "reviewPolicy": {"claudeModel": "claude-sonnet-4-6"},
        "agentLabels": {"internalReviewerAgent": "Internal_Reviewer_Agent"},
    }


def test_make_workspace_exposes_config_constants_and_primitives(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    config = _minimal_config(tmp_path)
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    # Constants
    assert ws.WORKSPACE == tmp_path.resolve()
    assert ws.REPO_PATH == Path(config["repoPath"])
    assert ws.LEDGER_PATH == Path(config["ledgerPath"])
    assert ws.HEALTH_PATH == Path(config["healthPath"])
    assert ws.ACTIVE_LANE_LABEL == "active-lane"
    assert ws.ENGINE_OWNER == "hermes"
    assert ws.WORKFLOW_WATCHDOG_JOB_NAME == "yoyopod-workflow-watchdog"
    assert ws.INTER_REVIEW_AGENT_MODEL == "claude-sonnet-4-6"
    assert ws.INTERNAL_REVIEWER_AGENT_NAME == "Internal_Reviewer_Agent"
    assert ws.LANE_FAILURE_RETRY_BUDGET == 3
    # I/O primitives
    assert callable(ws._run)
    assert callable(ws._now_iso)
    assert callable(ws._iso_to_epoch)
    assert callable(ws.audit)
    assert callable(ws.load_jobs)
    assert callable(ws.load_ledger)


def test_workspace_engine_owner_selects_hermes_cron_jobs_path(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    config = _minimal_config(tmp_path)
    config["hermesCronJobsPath"] = str(tmp_path / "hermes-jobs.json")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    assert ws._jobs_store_path() == Path(config["hermesCronJobsPath"])

    config["engineOwner"] = "openclaw"
    ws2 = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    assert ws2._jobs_store_path() == Path(config["cronJobsPath"])


def test_workspace_audit_appends_jsonl(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    config = _minimal_config(tmp_path)
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    ws.audit("test-action", "hello world", value=42)
    audit_log = tmp_path / "audit.jsonl"
    assert audit_log.exists()
    line = audit_log.read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["action"] == "test-action"
    assert payload["summary"] == "hello world"
    assert payload["value"] == 42
    assert payload["at"].endswith("Z")


def test_workspace_load_and_save_ledger_roundtrip(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    config = _minimal_config(tmp_path)
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    ws.save_ledger({"workflowState": "implementing_local"})
    assert ws.load_ledger() == {"workflowState": "implementing_local"}


def test_iso_to_epoch_interprets_utc(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=_minimal_config(tmp_path))
    # 2024-01-01T00:00:00Z == 1704067200
    assert ws._iso_to_epoch("2024-01-01T00:00:00Z") == 1704067200


def test_load_workspace_from_config_reads_file(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    workspace_root = tmp_path / "workflow"
    config_dir = workspace_root / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "yoyopod-workflow.json"
    config_path.write_text(json.dumps(_minimal_config(tmp_path)), encoding="utf-8")

    ws = workspace_module.load_workspace_from_config(workspace_root=workspace_root, config_path=config_path)
    assert ws.WORKSPACE == workspace_root.resolve()
    assert ws.REPO_PATH == Path(_minimal_config(tmp_path)["repoPath"])


def test_workspace_exposes_adapter_module_loaders(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=_minimal_config(tmp_path))
    # Generic loader + one-liner facade helpers are all available.
    assert callable(ws._load_adapter_module)
    for loader_name in [
        "_load_adapter_status_module",
        "_load_adapter_actions_module",
        "_load_adapter_sessions_module",
        "_load_adapter_prompts_module",
        "_load_adapter_github_module",
        "_load_adapter_reviews_module",
        "_load_adapter_paths_module",
        "_load_adapter_workflow_module",
        "_load_adapter_health_module",
    ]:
        assert callable(getattr(ws, loader_name)), loader_name


def test_workspace_adapter_loader_raises_when_plugin_missing(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=_minimal_config(tmp_path))
    # No plugin dir under tmp_path/.hermes/plugins/hermes-relay; loading any adapter module raises.
    import pytest

    with pytest.raises(FileNotFoundError):
        ws._load_adapter_status_module()


def test_workspace_exposes_full_wrapper_facade(tmp_path):
    """The workspace accessor must own every attribute the legacy wrapper used to expose.

    This test pins the contract: when the wrapper is eventually deleted, the
    adapter orchestrator + cli will look up these names on ``ws`` directly.
    """
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=_minimal_config(tmp_path))

    # Orchestrator + reconcile + doctor
    for name in ("build_status", "build_status_raw", "reconcile", "doctor"):
        assert callable(getattr(ws, name)), name

    # _raw action runners (replace the retired wrapper's _raw functions)
    for name in (
        "publish_ready_pr_raw", "push_pr_update_raw", "merge_and_promote_raw",
        "dispatch_implementation_turn_raw", "restart_actor_session_raw",
        "dispatch_inter_review_agent_review_raw", "dispatch_claude_review_raw",
        "dispatch_repair_handoff_raw", "tick_raw",
        "_dispatch_lane_turn", "_maybe_dispatch_repair_handoff",
    ):
        assert callable(getattr(ws, name)), name

    # Operator commands
    for name in (
        "set_core_jobs_enabled", "wake_named_jobs", "wake_core_jobs",
        "_wake_jobs", "_managed_job_names",
    ):
        assert callable(getattr(ws, name)), name

    # Job-state helpers
    for name in (
        "_job_lookup", "_job_state_mapping", "_job_schedule_every_ms",
        "_job_next_run_ms", "_job_last_run_at_ms", "_job_last_status",
        "_job_last_run_status", "_job_last_duration_ms", "_job_last_error",
        "_job_delivery", "_summarize_job",
    ):
        assert callable(getattr(ws, name)), name

    # Review-lifecycle helpers
    for name in (
        "_new_inter_review_agent_run_id",
        "_extract_inter_review_agent_payload",
        "_render_inter_review_agent_prompt",
        "_run_inter_review_agent_review",
        "_audit_inter_review_agent_transition",
        "_mark_pr_ready_for_review",
        "_resolve_review_thread",
        "_resolve_codex_superseded_threads",
        "_inter_review_agent_preflight",
    ):
        assert callable(getattr(ws, name)), name

    # Status-building helpers
    for name in (
        "write_lane_state", "write_lane_memo", "_derive_latest_progress",
        "_derive_next_action", "_classify_lane_failure",
        "_normalize_implementation_for_active_lane",
    ):
        assert callable(getattr(ws, name)), name

    # Session + repair helpers
    for name in (
        "_codex_model_for_issue", "_coder_agent_name_for_model",
        "_actor_labels_payload", "_ensure_acpx_session", "_run_acpx_prompt",
        "_prepare_lane_worktree", "decide_lane_session_action",
        "render_lane_memo", "build_acp_session_strategy",
        "build_session_nudge_payload", "should_nudge_session",
        "record_session_nudge",
        "should_dispatch_claude_repair_handoff",
        "should_dispatch_codex_cloud_repair_handoff",
        "build_codex_cloud_repair_handoff_payload",
        "record_codex_cloud_repair_handoff",
        "_render_codex_cloud_repair_handoff_prompt",
        "build_claude_repair_handoff_payload",
        "record_claude_repair_handoff",
        "_render_claude_repair_handoff_prompt",
    ):
        assert callable(getattr(ws, name)), name


def test_workspace_managed_job_names_dedupes(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    config = _minimal_config(tmp_path)
    config["coreJobNames"] = ["a", "b", "a"]
    config["hermesJobNames"] = ["b", "c"]
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    assert ws._managed_job_names() == ["a", "b", "c"]


def test_workspace_summarize_job_returns_none_for_none(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=_minimal_config(tmp_path))
    assert ws._summarize_job(None) is None


def test_workspace_job_delivery_defaults(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=_minimal_config(tmp_path))
    assert ws._job_delivery({}) == {"mode": "none"}
    assert ws._job_delivery({"deliver": "telegram"}) == {"mode": "telegram"}
    assert ws._job_delivery({"delivery": {"mode": "x"}}) == {"mode": "x"}


def test_workspace_lane_operator_attention_reasons(tmp_path):
    workspace_module = load_module("hermes_relay_yoyopod_core_workspace_test", "adapters/yoyopod_core/workspace.py")
    config = _minimal_config(tmp_path)
    config["sessionPolicy"] = {
        "codexModel": "gpt-5.3-codex-spark/high",
        "laneOperatorAttentionRetryThreshold": 3,
        "laneOperatorAttentionNoProgressThreshold": 4,
    }
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    assert ws._lane_operator_attention_reasons(None) == []
    reasons = ws._lane_operator_attention_reasons({"failure": {"retryCount": 5}, "budget": {"noProgressTicks": 10}})
    assert any("failure-retry-count=5" in r for r in reasons)
    assert any("no-progress-ticks=10" in r for r in reasons)
    assert ws._lane_operator_attention_needed({"failure": {"retryCount": 5}}) is True
    assert ws._lane_operator_attention_needed({"failure": {"retryCount": 1}, "budget": {"noProgressTicks": 1}}) is False
