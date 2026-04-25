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
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
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
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
    config = _minimal_config(tmp_path)
    config["hermesCronJobsPath"] = str(tmp_path / "hermes-jobs.json")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    assert ws._jobs_store_path() == Path(config["hermesCronJobsPath"])

    config["engineOwner"] = "openclaw"
    ws2 = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    assert ws2._jobs_store_path() == Path(config["cronJobsPath"])


def test_workspace_audit_appends_jsonl(tmp_path):
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
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
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
    config = _minimal_config(tmp_path)
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    ws.save_ledger({"workflowState": "implementing_local"})
    assert ws.load_ledger() == {"workflowState": "implementing_local"}


def test_iso_to_epoch_interprets_utc(tmp_path):
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=_minimal_config(tmp_path))
    # 2024-01-01T00:00:00Z == 1704067200
    assert ws._iso_to_epoch("2024-01-01T00:00:00Z") == 1704067200


def test_load_workspace_from_config_reads_file(tmp_path):
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
    workspace_root = tmp_path / "workflow"
    config_dir = workspace_root / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "yoyopod-workflow.json"
    config_path.write_text(json.dumps(_minimal_config(tmp_path)), encoding="utf-8")

    ws = workspace_module.load_workspace_from_config(workspace_root=workspace_root, config_path=config_path)
    assert ws.WORKSPACE == workspace_root.resolve()
    assert ws.REPO_PATH == Path(_minimal_config(tmp_path)["repoPath"])


def test_workspace_exposes_adapter_module_loaders(tmp_path):
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
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
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=_minimal_config(tmp_path))
    # No plugin dir under tmp_path/.hermes/plugins/daedalus; loading any adapter module raises.
    import pytest

    with pytest.raises(FileNotFoundError):
        ws._load_adapter_status_module()


def test_workspace_exposes_full_wrapper_facade(tmp_path):
    """The workspace accessor must own every attribute the legacy wrapper used to expose.

    This test pins the contract: when the wrapper is eventually deleted, the
    adapter orchestrator + cli will look up these names on ``ws`` directly.
    """
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
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
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
    config = _minimal_config(tmp_path)
    config["coreJobNames"] = ["a", "b", "a"]
    config["hermesJobNames"] = ["b", "c"]
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=config)
    assert ws._managed_job_names() == ["a", "b", "c"]


def test_workspace_summarize_job_returns_none_for_none(tmp_path):
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=_minimal_config(tmp_path))
    assert ws._summarize_job(None) is None


def test_workspace_job_delivery_defaults(tmp_path):
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
    ws = workspace_module.make_workspace(workspace_root=tmp_path, config=_minimal_config(tmp_path))
    assert ws._job_delivery({}) == {"mode": "none"}
    assert ws._job_delivery({"deliver": "telegram"}) == {"mode": "telegram"}
    assert ws._job_delivery({"delivery": {"mode": "x"}}) == {"mode": "x"}


def test_workspace_lane_operator_attention_reasons(tmp_path):
    workspace_module = load_module("daedalus_workflows_code_review_workspace_test", "workflows/code_review/workspace.py")
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


def test_workspace_exposes_runtime_accessor_with_named_profiles(tmp_path):
    """make_workspace instantiates runtimes from the (bridged) config.

    In Phase 3 the runtime configs are derived from the old JSON shape's
    sessionPolicy / reviewPolicy; Phase 4 will swap the source to YAML
    runtimes: section. Either way, ws.runtime('acpx-codex') must return
    an object implementing the Runtime protocol.
    """
    import importlib
    import sys
    from pathlib import Path as _Path

    REPO_ROOT = _Path(__file__).resolve().parents[1]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    # Minimal old-shape JSON config that make_workspace accepts.
    config = {
        "repoPath": str(tmp_path / "repo"),
        "cronJobsPath": str(tmp_path / "cron.json"),
        "ledgerPath": str(tmp_path / "ledger.json"),
        "healthPath": str(tmp_path / "health.json"),
        "auditLogPath": str(tmp_path / "audit.jsonl"),
        "activeLaneLabel": "active-lane",
        "engineOwner": "hermes",
        "coreJobNames": [],
        "hermesJobNames": [],
        "staleness": {},
        "sessionPolicy": {
            "codexSessionFreshnessSeconds": 900,
            "codexSessionPokeGraceSeconds": 1800,
            "codexSessionNudgeCooldownSeconds": 600,
        },
        "reviewPolicy": {
            "interReviewAgentMaxTurns": 24,
            "interReviewAgentTimeoutSeconds": 1200,
        },
        "agentLabels": {},
    }
    workspace_mod = importlib.import_module("workflows.code_review.workspace")
    ws = workspace_mod.make_workspace(workspace_root=tmp_path, config=config)

    assert hasattr(ws, "runtime"), "workspace must expose `runtime(name)` accessor"

    acpx = ws.runtime("acpx-codex")
    claude = ws.runtime("claude-cli")

    # Duck-type: both respond to the protocol's four methods.
    for r in (acpx, claude):
        assert callable(getattr(r, "ensure_session", None))
        assert callable(getattr(r, "run_prompt", None))
        assert callable(getattr(r, "assess_health", None))
        assert callable(getattr(r, "close_session", None))


def test_workspace_runtime_accessor_errors_on_unknown_name(tmp_path):
    """Requesting an unknown runtime name raises KeyError with a helpful message."""
    import importlib
    import sys
    from pathlib import Path as _Path

    REPO_ROOT = _Path(__file__).resolve().parents[1]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    config = {
        "repoPath": str(tmp_path / "repo"),
        "cronJobsPath": str(tmp_path / "cron.json"),
        "ledgerPath": str(tmp_path / "ledger.json"),
        "healthPath": str(tmp_path / "health.json"),
        "auditLogPath": str(tmp_path / "audit.jsonl"),
        "activeLaneLabel": "active-lane",
        "engineOwner": "hermes",
        "coreJobNames": [],
        "hermesJobNames": [],
        "staleness": {},
        "sessionPolicy": {},
        "reviewPolicy": {},
        "agentLabels": {},
    }
    workspace_mod = importlib.import_module("workflows.code_review.workspace")
    ws = workspace_mod.make_workspace(workspace_root=tmp_path, config=config)

    import pytest
    with pytest.raises(KeyError) as exc:
        ws.runtime("nonexistent-runtime")
    assert "nonexistent-runtime" in str(exc.value)
    # Error message names the known runtime profiles
    assert "acpx-codex" in str(exc.value) or "claude-cli" in str(exc.value)


def test_workspace_from_yaml_exposes_same_surface_as_legacy_json(tmp_path):
    """Given the new YAML shape, workspace exposes the same attribute surface
    callers have historically used (REPO_PATH, INTER_REVIEW_AGENT_MODEL, etc.)."""
    from pathlib import Path as _Path
    from workflows.code_review.workspace import make_workspace

    yaml_cfg = {
        "workflow": "code-review",
        "schema-version": 1,
        "instance": {"name": "yoyopod", "engine-owner": "hermes"},
        "repository": {
            "local-path": str(tmp_path / "repo"),
            "github-slug": "owner/repo",
            "active-lane-label": "active-lane",
        },
        "runtimes": {
            "acpx-codex": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": 900,
                "session-idle-grace-seconds": 1800,
                "session-nudge-cooldown-seconds": 600,
            },
            "claude-cli": {
                "kind": "claude-cli",
                "max-turns-per-invocation": 24,
                "timeout-seconds": 1200,
            },
        },
        "agents": {
            "coder": {
                "default": {"name": "Internal_Coder_Agent", "model": "gpt-5.3-codex-spark/high", "runtime": "acpx-codex"},
                "high-effort": {"name": "Internal_Coder_Agent", "model": "gpt-5.3-codex", "runtime": "acpx-codex"},
                "escalated": {"name": "Escalation_Coder_Agent", "model": "gpt-5.4", "runtime": "acpx-codex"},
            },
            "internal-reviewer": {
                "name": "Internal_Reviewer_Agent",
                "model": "claude-sonnet-4-6",
                "runtime": "claude-cli",
                "freeze-coder-while-running": True,
            },
            "external-reviewer": {
                "enabled": True, "name": "External_Reviewer_Agent",
                "provider": "codex-cloud", "cache-seconds": 1800,
            },
        },
        "gates": {
            "internal-review": {
                "pass-with-findings-tolerance": 1,
                "require-pass-clean-before-publish": True,
                "request-cooldown-seconds": 1200,
            },
            "external-review": {"required-for-merge": True},
            "merge": {"require-ci-acceptable": True},
        },
        "triggers": {"lane-selector": {"type": "github-label", "label": "active-lane"}},
        "storage": {
            "ledger": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
            "cron-jobs-path": str(tmp_path / "cron.json"),
            "hermes-cron-jobs-path": str(tmp_path / "hermes-cron.json"),
            "sessions-state": "state/sessions",
        },
        "codex-bot": {
            "logins": ["chatgpt-codex-connector"],
            "clean-reactions": ["+1"],
            "pending-reactions": ["eyes"],
        },
    }

    ws = make_workspace(workspace_root=tmp_path, config=yaml_cfg)

    # Legacy attribute surface still present after YAML config:
    assert str(ws.REPO_PATH) == str(tmp_path / "repo")
    assert ws.ACTIVE_LANE_LABEL == "active-lane"
    assert ws.ENGINE_OWNER == "hermes"
    assert ws.CODEX_MODEL_DEFAULT == "gpt-5.3-codex-spark/high"
    assert ws.CODEX_MODEL_HIGH_EFFORT == "gpt-5.3-codex"
    assert ws.CODEX_MODEL_ESCALATED == "gpt-5.4"
    assert ws.INTER_REVIEW_AGENT_MODEL == "claude-sonnet-4-6"
    assert ws.INTER_REVIEW_AGENT_MAX_TURNS == 24
    assert ws.INTER_REVIEW_AGENT_TIMEOUT_SECONDS == 1200
    assert ws.CODEX_SESSION_FRESHNESS_SECONDS == 900
    assert ws.CODEX_SESSION_POKE_GRACE_SECONDS == 1800
    assert ws.CODEX_SESSION_NUDGE_COOLDOWN_SECONDS == 600
    assert ws.INTERNAL_CODER_AGENT_NAME == "Internal_Coder_Agent"
    assert ws.ESCALATION_CODER_AGENT_NAME == "Escalation_Coder_Agent"
    assert ws.INTERNAL_REVIEWER_AGENT_NAME == "Internal_Reviewer_Agent"
    assert ws.EXTERNAL_REVIEWER_AGENT_NAME == "External_Reviewer_Agent"
    # Runtime accessor (from Task 3.4) works with YAML-derived runtimes
    assert callable(ws.runtime)
    assert hasattr(ws.runtime("acpx-codex"), "ensure_session")
    assert hasattr(ws.runtime("claude-cli"), "run_prompt")


def test_workspace_raises_on_agent_referencing_unknown_runtime(tmp_path):
    """YAML shape: agents pointing at runtimes that aren't declared raise ValueError."""
    from workflows.code_review.workspace import make_workspace

    cfg = {
        "workflow": "code-review",
        "schema-version": 1,
        "instance": {"name": "test", "engine-owner": "hermes"},
        "repository": {"local-path": str(tmp_path), "github-slug": "o/r", "active-lane-label": "active-lane"},
        "runtimes": {"acpx-codex": {"kind": "acpx-codex", "session-idle-freshness-seconds": 900, "session-idle-grace-seconds": 1800, "session-nudge-cooldown-seconds": 600}},
        "agents": {
            "coder": {"default": {"name": "C", "model": "m", "runtime": "nonexistent-runtime"}},
            "internal-reviewer": {"name": "R", "model": "m", "runtime": "acpx-codex"},
            "external-reviewer": {"enabled": False, "name": "E"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "github-label", "label": "l"}},
        "storage": {"ledger": "l", "health": "h", "audit-log": "a"},
    }
    import pytest
    with pytest.raises(ValueError) as exc:
        make_workspace(workspace_root=tmp_path, config=cfg)
    assert "nonexistent-runtime" in str(exc.value)
