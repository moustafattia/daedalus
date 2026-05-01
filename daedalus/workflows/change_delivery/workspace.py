from __future__ import annotations

import calendar
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from engine.audit import make_audit_fn as _engine_make_audit_fn
from engine.store import EngineStore
from engine.storage import append_jsonl as _append_jsonl
from engine.storage import load_optional_json as _load_optional_json
from engine.storage import write_json_atomic as _write_json
from engine.storage import write_text_atomic as _write_text
from workflows.change_delivery.migrations import get_ledger_field
from workflows.change_delivery.runtimes import build_runtimes
from workflows.shared.paths import runtime_paths


def _derive_lane_selection_cfg(yaml_cfg, *, active_lane_label):
    """Synthesize the parsed lane-selection config from the raw workflow config.

    Lazy-import to avoid a circular-import at module load (workspace is the
    central bootstrap site).
    """
    try:
        from .lane_selection import parse_config
    except ImportError:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "daedalus_lane_selection_for_workspace",
            Path(__file__).resolve().parent / "lane_selection.py",
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        parse_config = _mod.parse_config
    return parse_config(workflow_yaml=yaml_cfg or {}, active_lane_label=active_lane_label)


def _yaml_to_legacy_view(yaml_cfg: dict, workspace_root: "Path | None" = None) -> dict:
    """Project workflow config onto the internal workspace config shape.

    workspace_root must be supplied so that relative storage paths (e.g.
    ``memory/workflow-status.json``) are anchored to the workflow root dir
    rather than guessed from the repo path.
    """
    from pathlib import Path as _Path

    instance = yaml_cfg.get("instance", {}) or {}
    repo = yaml_cfg.get("repository", {}) or {}
    runtimes = yaml_cfg.get("runtimes", {}) or {}
    agents = yaml_cfg.get("agents", {}) or {}
    gates = yaml_cfg.get("gates", {}) or {}
    storage = yaml_cfg.get("storage", {}) or {}
    escalation = yaml_cfg.get("escalation", {}) or {}
    jobs_cfg = yaml_cfg.get("jobs", {}) or {}

    acpx = runtimes.get("acpx-codex", {}) or {}
    claude_cli = runtimes.get("claude-cli", {}) or {}

    coder_default = (agents.get("coder") or {}).get("default", {}) or {}
    coder_high = (agents.get("coder") or {}).get("high-effort", {}) or coder_default
    coder_escalated = (agents.get("coder") or {}).get("escalated", {}) or coder_default
    int_reviewer = agents.get("internal-reviewer", {}) or {}
    ext_reviewer = agents.get("external-reviewer", {}) or {}
    adv_reviewer = agents.get("advisory-reviewer", {}) or {}
    internal_review_gate = gates.get("internal-review", {}) or {}

    # Resolve storage paths relative to workspace_root when they aren't
    # absolute. The consumer code expects absolute paths for ledger/health/audit.
    # workspace_root is the preferred anchor; fall back to inferring from
    # local_path only when workspace_root is not provided (legacy codepath).
    def _abs_or_join(value: str, local_path: str) -> str:
        if not value:
            return value
        p = _Path(value)
        if p.is_absolute():
            return str(p)
        if workspace_root is not None:
            return str(_Path(workspace_root).resolve() / value)
        base = _Path(local_path).expanduser().resolve()
        return str(base.parent.parent / value) if local_path else value

    local_path = repo.get("local-path", "")
    watchdog_job_name = str(jobs_cfg.get("watchdog") or "workflow-watchdog")
    checker_job_name = str(jobs_cfg.get("checker") or "workflow-checker")
    internal_review_job_name = str(
        jobs_cfg.get("internal-review-runner") or "internal-review-runner"
    )
    milestone_notifier_job_name = str(
        jobs_cfg.get("milestone-notifier") or "workflow-milestone-notifier"
    )
    core_job_names = list(jobs_cfg.get("core") or [watchdog_job_name])
    support_job_names = list(jobs_cfg.get("support") or [milestone_notifier_job_name])

    return {
        "repoPath": local_path,
        "cronJobsPath": storage.get("cron-jobs-path", ""),
        "hermesCronJobsPath": storage.get("hermes-cron-jobs-path"),
        "ledgerPath": _abs_or_join(storage.get("ledger", "memory/workflow-status.json"), local_path),
        "healthPath": _abs_or_join(storage.get("health", "memory/workflow-health.json"), local_path),
        "auditLogPath": _abs_or_join(storage.get("audit-log", "memory/workflow-audit.jsonl"), local_path),
        "schedulerPath": _abs_or_join(storage.get("scheduler", "memory/workflow-scheduler.json"), local_path),
        "activeLaneLabel": repo.get("active-lane-label", "active-lane"),
        "engineOwner": instance.get("engine-owner", "openclaw"),
        "coreJobNames": core_job_names,
        "hermesJobNames": support_job_names,
        "issueWatcherNameRegex": jobs_cfg.get("issue-watcher-regex", r"issue-\d+-watch"),
        "jobNames": {
            "internalReviewRunner": internal_review_job_name,
            "workflowChecker": checker_job_name,
            "workflowWatchdog": watchdog_job_name,
            "milestoneNotifier": milestone_notifier_job_name,
        },
        "staleness": {
            "coreJobMissMultiplier": 2.5,
            "activeLaneWithoutPrMinutes": 45,
            "reviewHeadMissingMinutes": 20,
        },
        "reviewCache": {
            "codexCloudSeconds": ext_reviewer.get("cache-seconds", 1800),
            "claudeReviewRequestCooldownSeconds": internal_review_gate.get("request-cooldown-seconds", 1200),
        },
        "sessionPolicy": {
            "codexModel": coder_default.get("model", "gpt-5.3-codex-spark/high"),
            "codexModelLargeEffort": coder_high.get("model"),
            "codexModelEscalated": coder_escalated.get("model"),
            "codexEscalateRestartCount": escalation.get("restart-count-threshold", 2),
            "codexEscalateLocalReviewCount": escalation.get("local-review-count-threshold", 2),
            "codexEscalatePostpublishFindingCount": escalation.get("postpublish-finding-threshold", 3),
            "laneFailureRetryBudget": escalation.get("lane-failure-retry-budget", 3),
            "laneNoProgressTickBudget": escalation.get("no-progress-tick-budget", 3),
            "laneOperatorAttentionRetryThreshold": escalation.get("operator-attention-retry-threshold", 5),
            "laneOperatorAttentionNoProgressThreshold": escalation.get("operator-attention-no-progress-threshold", 5),
            "laneCounterIncrementMinSeconds": escalation.get("lane-counter-increment-min-seconds", 240),
            "codexSessionFreshnessSeconds": acpx.get("session-idle-freshness-seconds", 900),
            "codexSessionPokeGraceSeconds": acpx.get("session-idle-grace-seconds", 1800),
            "codexSessionNudgeCooldownSeconds": acpx.get("session-nudge-cooldown-seconds", 600),
        },
        "reviewPolicy": {
            "interReviewAgentPassWithFindingsReviews": internal_review_gate.get("pass-with-findings-tolerance", 1),
            "internalReviewerModel": int_reviewer.get("model", "claude-sonnet-4-6"),
            "interReviewAgentMaxTurns": claude_cli.get("max-turns-per-invocation", 24),
            "interReviewAgentTimeoutSeconds": claude_cli.get("timeout-seconds", 1200),
            "freezeCoderWhileInterReviewAgentRunning": int_reviewer.get("freeze-coder-while-running", True),
        },
        "agentLabels": {
            "internalCoderAgent": coder_default.get("name", "Internal_Coder_Agent"),
            "escalationCoderAgent": coder_escalated.get("name", "Escalation_Coder_Agent"),
            "internalReviewerAgent": int_reviewer.get("name", "Internal_Reviewer_Agent"),
            "externalReviewerAgent": ext_reviewer.get("name", "External_Reviewer_Agent"),
            "advisoryReviewerAgent": adv_reviewer.get("name", "Advisory_Reviewer_Agent"),
        },
    }


"""Workflow workspace.

The :class:`Workspace` type is the canonical holder of workflow-scoped config
and I/O primitives.

Two factories are provided:

* :func:`make_workspace` — builds a :class:`types.SimpleNamespace`-style
  workspace accessor. This is the primary API: adapter code (``cli``,
  ``orchestrator``, etc.) looks up workspace attributes by name, so any
  duck-typed accessor works.
* :func:`load_workspace_from_config` — convenience wrapper that reads the
  project workflow contract from either ``config/workflow.yaml`` or
  ``WORKFLOW.md`` and applies the same derived constants the workspace uses
  internally.
"""


DEFAULT_YAML_CONFIG_FILENAME = "config/workflow.yaml"
DEFAULT_WORKFLOW_MARKDOWN_FILENAME = "WORKFLOW.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML mapping file."""
    import yaml  # type: ignore[import]

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            f"{path} must contain a YAML mapping at the top level"
        )
    return data


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_to_epoch(value: str | None) -> int | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return int(calendar.timegm(time.strptime(value, fmt)))
        except Exception:
            continue
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        return None


def _ms_to_iso(value_ms: int | None) -> str | None:
    if value_ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value_ms / 1000))


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_json(command: list[str], cwd: Path | None = None) -> Any:
    completed = _run(command, cwd=cwd)
    return json.loads(completed.stdout or "null")


def _subprocess_failure_message(exc: subprocess.CalledProcessError) -> str:
    command = exc.cmd if isinstance(exc.cmd, list) else [str(exc.cmd)]
    header = f"Command failed with exit status {exc.returncode}: {' '.join(str(part) for part in command)}"
    stderr_text = (exc.stderr or "").strip()
    stdout_text = (exc.stdout or "").strip()
    details = [text for text in [stderr_text, stdout_text] if text]
    if not details:
        return header
    return header + "\n" + "\n".join(details)


def _build_adapter_module_loaders(workspace_root: Path) -> dict[str, Any]:
    """Return workflow-module loader closures cached per workspace.

    Each loader loads the corresponding module from the installed plugin's
    ``workflows/change_delivery/`` directory by file path so that the workspace
    accessor can call into modules without a package-level import.
    """
    import importlib.util as _importlib_util

    plugin_root = Path(__file__).resolve().parents[2]
    cache: dict[str, Any] = {}

    def _load_adapter_module(name: str):
        cached = cache.get(name)
        if cached is not None:
            return cached
        module_path = plugin_root / "workflows" / "change_delivery" / f"{name}.py"
        if not module_path.exists():
            raise FileNotFoundError(module_path)
        spec = _importlib_util.spec_from_file_location(f"daedalus_change_delivery_{name}", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"unable to load change_delivery {name} module from {module_path}")
        module = _importlib_util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cache[name] = module
        return module

    return {
        "_load_adapter_module": _load_adapter_module,
        "_load_adapter_status_module": lambda: _load_adapter_module("status"),
        "_load_adapter_actions_module": lambda: _load_adapter_module("actions"),
        "_load_adapter_sessions_module": lambda: _load_adapter_module("sessions"),
        "_load_adapter_prompts_module": lambda: _load_adapter_module("prompts"),
        "_load_adapter_github_module": lambda: _load_adapter_module("github"),
        "_load_adapter_reviews_module": lambda: _load_adapter_module("reviews"),
        "_load_adapter_paths_module": lambda: _load_adapter_module("paths"),
        "_load_adapter_workflow_module": lambda: _load_adapter_module("workflow"),
        "_load_adapter_health_module": lambda: _load_adapter_module("health"),
    }


def _make_audit_fn(
    *,
    audit_log_path,
    publisher=None,
):
    """Build an ``audit(action, summary, **extra)`` closure that:

      1. Always appends a JSONL row to ``audit_log_path``.
      2. If ``publisher`` is provided, calls ``publisher(action=..., summary=..., extra=...)``
         after the write. Publisher exceptions are swallowed — observability
         must never break workflow execution.
    """
    return _engine_make_audit_fn(
        audit_log_path=Path(audit_log_path),
        now_iso=_now_iso,
        publisher=publisher,
    )


def _make_comment_publisher(
    *,
    workflow_root,
    repo_slug,
    workflow_yaml,
    get_active_issue_number,
    get_workflow_state,
    get_is_operator_attention,
    run_fn=None,
):
    """Build the ``publisher`` callable consumed by ``_make_audit_fn``.

    Returns ``None`` when github-comments is disabled — the caller
    (``build_workspace``) wires that None into ``_make_audit_fn`` so
    nothing happens at the audit hook.
    """
    # Lazy import to avoid hard-coupling workspace.py to comments_publisher
    # before the rest of the workspace bootstrap is happy.
    try:
        from . import observability as _obs
        from . import comments_publisher as _pub
    except ImportError:
        _here = Path(__file__).resolve().parent
        import importlib.util as _ilu

        def _load(name):
            spec = _ilu.spec_from_file_location(
                f"daedalus_workflow_change_delivery_{name}", _here / f"{name}.py"
            )
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        _obs = _load("observability")
        _pub = _load("comments_publisher")

    workflow_root = Path(workflow_root)
    override_dir = workflow_root / "runtime" / "state" / "daedalus"
    state_dir = workflow_root / "runtime" / "state" / "lane-comments"

    # Always return a publisher callable so a later
    # ``/daedalus set-observability --github-comments on`` override can take
    # effect at the next audit event without a service restart. The publisher
    # below re-resolves the effective config on every call and short-circuits
    # when the result is disabled — that is the gate, not this bootstrap-time
    # lookup. (Earlier versions short-circuited here when initially-disabled,
    # which permanently severed the audit hook from the publisher and made
    # runtime overrides ineffective until a process restart.)

    def publisher(*, action, summary, extra):
        # Re-resolve the config every call so a /daedalus set-observability
        # toggle takes effect immediately, without restarting the service.
        eff = _obs.resolve_effective_config(
            workflow_yaml=workflow_yaml or {},
            override_dir=override_dir,
            workflow_name="change-delivery",
        )
        if not eff["github-comments"].get("enabled"):
            return
        issue_number = get_active_issue_number()
        if issue_number is None:
            return
        audit_event = {
            "at": _now_iso(),
            "action": action,
            "summary": summary,
            **(extra or {}),
        }
        _pub.publish_event(
            repo_slug=repo_slug,
            issue_number=issue_number,
            workflow_state=get_workflow_state(),
            is_operator_attention=get_is_operator_attention(),
            audit_event=audit_event,
            effective_config=eff,
            state_dir=state_dir,
            **({"run_fn": run_fn} if run_fn is not None else {}),
        )

    return publisher


def make_workspace(*, workspace_root: Path, config: dict[str, Any]) -> SimpleNamespace:
    """Build the workspace accessor used by adapter CLI / orchestrator code.

    Returns a :class:`types.SimpleNamespace` bundling project-scoped config
    constants and stdlib-backed I/O primitives for the configured workflow.
    """

    workspace_root = Path(workspace_root).resolve()

    # Detect workflow-contract shape (top-level `workflow:` + `runtimes:` +
    # `agents:`) and project it onto the internal config view the existing
    # implementation body still consumes.
    if "workflow" in config and "runtimes" in config and "agents" in config:
        yaml_cfg = config
        config = _yaml_to_legacy_view(config, workspace_root=workspace_root)
    else:
        yaml_cfg = None
    workflow_policy = str((yaml_cfg or {}).get("workflow-policy") or "").strip()

    # -- paths -----------------------------------------------------------
    repo_path = Path(config["repoPath"])
    cron_jobs_path = Path(config["cronJobsPath"])
    hermes_cron_jobs_path = Path(config.get("hermesCronJobsPath") or (Path.home() / ".hermes/cron/jobs.json"))
    ledger_path = Path(config["ledgerPath"])
    from workflows.change_delivery.migrations import migrate_persisted_ledger
    migrate_persisted_ledger(ledger_path)
    health_path = Path(config["healthPath"])
    audit_log_path = Path(config["auditLogPath"])
    scheduler_path = Path(config.get("schedulerPath") or (workspace_root / "memory/workflow-scheduler.json"))
    db_path = runtime_paths(workspace_root)["db_path"]
    engine_store = EngineStore(
        db_path=db_path,
        workflow="change-delivery",
        now_iso=_now_iso,
        now_epoch=time.time,
    )
    sessions_state_path = workspace_root / "state/sessions"

    # -- config constants ------------------------------------------------
    engine_owner = str(config.get("engineOwner", "openclaw"))
    active_lane_label = str(config.get("activeLaneLabel", "active-lane"))
    lane_selection_cfg = _derive_lane_selection_cfg(yaml_cfg, active_lane_label=active_lane_label)
    core_job_names = list(config.get("coreJobNames", []))
    hermes_job_names = list(config.get("hermesJobNames", []))
    issue_watcher_re = re.compile(str(config.get("issueWatcherNameRegex", r"issue-\d+-watch")))
    job_names = config.get("jobNames", {}) or {}
    issue_branch_re = re.compile(r"(?:^|/)issue-(\d+)(?:\b|[-_])")
    issue_worktree_re = re.compile(r"issue-(\d+)(?:\b|[-_])")
    severity_badge_re = re.compile(r"!\[P(\d+) Badge", re.IGNORECASE)

    staleness = config.get("staleness", {}) or {}
    miss_multiplier = float(staleness.get("coreJobMissMultiplier", 2.5))
    lane_no_pr_minutes = int(staleness.get("activeLaneWithoutPrMinutes", 45))
    review_head_missing_minutes = int(staleness.get("reviewHeadMissingMinutes", 20))

    review_cache = config.get("reviewCache", {}) or {}
    codex_cloud_cache_seconds = int(review_cache.get("codexCloudSeconds", 1800))
    claude_review_request_cooldown_seconds = int(review_cache.get("claudeReviewRequestCooldownSeconds", 1200))
    internal_review_job_name = str(job_names.get("internalReviewRunner") or "internal-review-runner")
    workflow_checker_job_name = str(job_names.get("workflowChecker") or "workflow-checker")
    workflow_watchdog_job_name = str(job_names.get("workflowWatchdog") or "workflow-watchdog")
    milestone_notifier_job_name = str(job_names.get("milestoneNotifier") or "workflow-milestone-notifier")

    session_policy = config.get("sessionPolicy", {}) or {}
    codex_model_default = str(session_policy.get("codexModel", "gpt-5.3-codex-spark/high"))
    codex_model_high_effort = str(
        session_policy.get("codexModelLargeEffort")
        or session_policy.get("codexModelHighEffort")
        or "gpt-5.3-codex"
    )
    codex_model_escalated = str(session_policy.get("codexModelEscalated") or "gpt-5.4")
    codex_escalate_restart_count = int(session_policy.get("codexEscalateRestartCount", 2))
    codex_escalate_local_review_count = int(session_policy.get("codexEscalateLocalReviewCount", 2))
    codex_escalate_postpublish_finding_count = int(session_policy.get("codexEscalatePostpublishFindingCount", 3))
    lane_failure_retry_budget = int(session_policy.get("laneFailureRetryBudget", 3))
    lane_no_progress_tick_budget = int(session_policy.get("laneNoProgressTickBudget", 3))
    lane_operator_attention_retry_threshold = int(session_policy.get("laneOperatorAttentionRetryThreshold", 5))
    lane_operator_attention_no_progress_threshold = int(session_policy.get("laneOperatorAttentionNoProgressThreshold", 5))
    lane_counter_increment_min_seconds = int(session_policy.get("laneCounterIncrementMinSeconds", 240))
    codex_session_freshness_seconds = int(session_policy.get("codexSessionFreshnessSeconds", 900))
    codex_session_poke_grace_seconds = int(session_policy.get("codexSessionPokeGraceSeconds", 1800))
    codex_session_nudge_cooldown_seconds = int(session_policy.get("codexSessionNudgeCooldownSeconds", 600))

    review_policy = config.get("reviewPolicy", {}) or {}
    inter_review_agent_pass_with_findings_reviews = int(
        review_policy.get("interReviewAgentPassWithFindingsReviews")
        or review_policy.get("internalReviewerAgentPassWithFindingsReviews")
        or review_policy.get("claudePassWithFindingsReviews", 1)
    )
    inter_review_agent_model = str(
        review_policy.get("internalReviewerModel")
        or "claude-sonnet-4-6"
    )
    inter_review_agent_max_turns = int(
        review_policy.get("interReviewAgentMaxTurns")
        or review_policy.get("internalReviewerAgentMaxTurns")
        or review_policy.get("claudeReviewMaxTurns", 12)
    )
    inter_review_agent_timeout_seconds = int(
        review_policy.get("interReviewAgentTimeoutSeconds")
        or review_policy.get("internalReviewerAgentTimeoutSeconds")
        or review_policy.get("claudeReviewTimeoutSeconds", 1200)
    )
    inter_review_agent_freeze_coder_while_running = bool(
        review_policy.get(
            "freezeCoderWhileInterReviewAgentRunning",
            review_policy.get(
                "freezeCoderWhileInternalReviewAgentRunning",
                review_policy.get("freezeCoderWhileClaudeReviewRunning", True),
            ),
        )
    )

    agent_labels = config.get("agentLabels", {}) or {}
    internal_coder_agent_name = str(agent_labels.get("internalCoderAgent", "Internal_Coder_Agent"))
    escalation_coder_agent_name = str(agent_labels.get("escalationCoderAgent", "Escalation_Coder_Agent"))
    internal_reviewer_agent_name = str(agent_labels.get("internalReviewerAgent", "Internal_Reviewer_Agent"))
    external_reviewer_agent_name = str(agent_labels.get("externalReviewerAgent", "External_Reviewer_Agent"))
    advisory_reviewer_agent_name = str(agent_labels.get("advisoryReviewerAgent", "Advisory_Reviewer_Agent"))

    def _jobs_store_path() -> Path:
        return hermes_cron_jobs_path if engine_owner == "hermes" else cron_jobs_path

    def load_jobs() -> dict[str, Any]:
        return _load_json(_jobs_store_path())

    def load_ledger() -> dict[str, Any]:
        return _load_json(ledger_path)

    def save_jobs(payload: dict[str, Any]) -> None:
        _write_json(_jobs_store_path(), payload)

    def save_ledger(payload: dict[str, Any]) -> None:
        _write_json(ledger_path, payload)

    def load_scheduler() -> dict[str, Any]:
        return engine_store.load_scheduler()

    def save_scheduler(payload: dict[str, Any]) -> None:
        engine_store.save_scheduler(
            retry_entries={},
            running_entries={},
            codex_totals=payload.get("codex_totals") or payload.get("codexTotals") or {},
            codex_threads=payload.get("codex_threads") or payload.get("codexThreads") or {},
            now_iso=payload.get("updatedAt") or _now_iso(),
            now_epoch=time.time(),
        )
        _write_json(scheduler_path, payload)

    # Wire the comment publisher (returns None when observability is disabled —
    # the audit hook then becomes a pure log-write with no GitHub I/O).
    _ns_holder: dict[str, Any] = {}

    def _ns_load_ledger() -> dict[str, Any]:
        ns_obj = _ns_holder.get("ns")
        if ns_obj is None or not hasattr(ns_obj, "load_ledger"):
            return {}
        try:
            return ns_obj.load_ledger() or {}
        except Exception:
            return {}

    _repo_slug = ((yaml_cfg or {}).get("repository") or {}).get("github-slug") or ""
    _publisher = _make_comment_publisher(
        workflow_root=workspace_root,
        repo_slug=_repo_slug,
        workflow_yaml=yaml_cfg or {},
        get_active_issue_number=lambda: (_ns_load_ledger().get("activeLane") or {}).get("number"),
        get_workflow_state=lambda: _ns_load_ledger().get("workflowState") or "unknown",
        get_is_operator_attention=lambda: (
            _ns_load_ledger().get("workflowState") == "operator_attention_required"
        ),
    )
    from workflows.change_delivery.webhooks import build_webhooks, compose_audit_subscribers

    _webhooks = build_webhooks((yaml_cfg or {}).get("webhooks") or [], run_fn=_run)

    def _adapt_legacy_publisher(legacy_pub):
        """The legacy comments publisher takes (action=, summary=, extra=).
        Compose-style subscribers receive a single audit_event dict. Adapt."""
        if legacy_pub is None:
            return None
        def _sub(audit_event):
            legacy_pub(
                action=audit_event.get("action") or "",
                summary=audit_event.get("summary") or "",
                extra={k: v for k, v in audit_event.items() if k not in ("action", "summary", "at")},
            )
        return _sub

    def _adapt_webhook(wh):
        """Wrap a Webhook into a (audit_event)->None subscriber that respects matches()."""
        def _sub(audit_event):
            if not wh.matches(audit_event):
                return
            wh.deliver(audit_event)
        return _sub

    _subscribers = []
    _legacy = _adapt_legacy_publisher(_publisher)
    if _legacy is not None:
        _subscribers.append(_legacy)
    for _wh in _webhooks:
        _subscribers.append(_adapt_webhook(_wh))

    _fanout_publisher = compose_audit_subscribers(_subscribers) if _subscribers else None
    audit = _make_audit_fn(audit_log_path=audit_log_path, publisher=_fanout_publisher)

    # Pre-declared so closures below can resolve them once ``ns`` is built.
    # Bindings happen after ``ns`` is created, below.
    ns = SimpleNamespace(
        # --- workspace globals ---
        WORKSPACE=workspace_root,
        CONFIG=config,
        WORKFLOW_YAML=yaml_cfg or {},
        WORKFLOW_POLICY=workflow_policy,
        ENGINE_STORE=engine_store,
        ACTIVE_CANCEL_EVENT=None,
        DEFAULT_CONFIG_PATH=workspace_root / DEFAULT_WORKFLOW_MARKDOWN_FILENAME,
        SESSIONS_STATE_PATH=sessions_state_path,
        REPO_PATH=repo_path,
        REPO_SLUG=_repo_slug,
        CRON_JOBS_PATH=cron_jobs_path,
        HERMES_CRON_JOBS_PATH=hermes_cron_jobs_path,
        LEDGER_PATH=ledger_path,
        HEALTH_PATH=health_path,
        AUDIT_LOG_PATH=audit_log_path,
        SCHEDULER_PATH=scheduler_path,
        ACTIVE_LANE_LABEL=active_lane_label,
        LANE_SELECTION_CFG=lane_selection_cfg,
        ENGINE_OWNER=engine_owner,
        CORE_JOB_NAMES=core_job_names,
        HERMES_JOB_NAMES=hermes_job_names,
        ISSUE_WATCHER_RE=issue_watcher_re,
        ISSUE_BRANCH_RE=issue_branch_re,
        ISSUE_WORKTREE_RE=issue_worktree_re,
        SEVERITY_BADGE_RE=severity_badge_re,
        MISS_MULTIPLIER=miss_multiplier,
        LANE_NO_PR_MINUTES=lane_no_pr_minutes,
        REVIEW_HEAD_MISSING_MINUTES=review_head_missing_minutes,
        CLAUDE_REVIEW_JOB_NAME=internal_review_job_name,
        WORKFLOW_CHECKER_JOB_NAME=workflow_checker_job_name,
        WORKFLOW_WATCHDOG_JOB_NAME=workflow_watchdog_job_name,
        CODEX_WATCHDOG_JOB_NAME=workflow_watchdog_job_name,
        TELEGRAM_JOB_NAME=milestone_notifier_job_name,
        CLAUDE_TRIGGER_STATES={"under_review", "findings_open", "revalidating"},
        CODEX_CLOUD_CACHE_SECONDS=codex_cloud_cache_seconds,
        CLAUDE_REVIEW_REQUEST_COOLDOWN_SECONDS=claude_review_request_cooldown_seconds,
        CODEX_SESSION_POLICY=session_policy,
        CODEX_MODEL_DEFAULT=codex_model_default,
        CODEX_MODEL_HIGH_EFFORT=codex_model_high_effort,
        CODEX_MODEL_ESCALATED=codex_model_escalated,
        CODEX_ESCALATE_RESTART_COUNT=codex_escalate_restart_count,
        CODEX_ESCALATE_LOCAL_REVIEW_COUNT=codex_escalate_local_review_count,
        CODEX_ESCALATE_POSTPUBLISH_FINDING_COUNT=codex_escalate_postpublish_finding_count,
        LANE_FAILURE_RETRY_BUDGET=lane_failure_retry_budget,
        LANE_NO_PROGRESS_TICK_BUDGET=lane_no_progress_tick_budget,
        LANE_OPERATOR_ATTENTION_RETRY_THRESHOLD=lane_operator_attention_retry_threshold,
        LANE_OPERATOR_ATTENTION_NO_PROGRESS_THRESHOLD=lane_operator_attention_no_progress_threshold,
        LANE_COUNTER_INCREMENT_MIN_SECONDS=lane_counter_increment_min_seconds,
        CODEX_MODEL=codex_model_default,
        CODEX_SESSION_FRESHNESS_SECONDS=codex_session_freshness_seconds,
        CODEX_SESSION_POKE_GRACE_SECONDS=codex_session_poke_grace_seconds,
        CODEX_SESSION_NUDGE_COOLDOWN_SECONDS=codex_session_nudge_cooldown_seconds,
        REVIEW_POLICY=review_policy,
        INTER_REVIEW_AGENT_PASS_WITH_FINDINGS_REVIEWS=inter_review_agent_pass_with_findings_reviews,
        INTER_REVIEW_AGENT_MODEL=inter_review_agent_model,
        INTER_REVIEW_AGENT_MAX_TURNS=inter_review_agent_max_turns,
        INTER_REVIEW_AGENT_TIMEOUT_SECONDS=inter_review_agent_timeout_seconds,
        INTER_REVIEW_AGENT_FREEZE_CODER_WHILE_RUNNING=inter_review_agent_freeze_coder_while_running,
        # Back-compat aliases for older call-sites still using "CLAUDE" names.
        CLAUDE_MODEL=inter_review_agent_model,
        CLAUDE_REVIEW_MAX_TURNS=inter_review_agent_max_turns,
        CLAUDE_REVIEW_TIMEOUT_SECONDS=inter_review_agent_timeout_seconds,
        CLAUDE_REVIEW_FREEZE_CODER_WHILE_RUNNING=inter_review_agent_freeze_coder_while_running,
        CLAUDE_PASS_WITH_FINDINGS_REVIEWS=inter_review_agent_pass_with_findings_reviews,
        AGENT_LABELS=agent_labels,
        INTERNAL_CODER_AGENT_NAME=internal_coder_agent_name,
        ESCALATION_CODER_AGENT_NAME=escalation_coder_agent_name,
        INTERNAL_REVIEWER_AGENT_NAME=internal_reviewer_agent_name,
        EXTERNAL_REVIEWER_AGENT_NAME=external_reviewer_agent_name,
        ADVISORY_REVIEWER_AGENT_NAME=advisory_reviewer_agent_name,
        CODEX_BOT_LOGINS={"chatgpt-codex-connector", "chatgpt-codex-connector[bot]"},
        CODEX_CLEAN_REACTIONS={"+1"},
        CODEX_PENDING_REACTIONS={"eyes"},
        # --- I/O and time primitives ---
        _run=_run,
        _run_json=_run_json,
        _load_json=_load_json,
        _write_json=_write_json,
        _append_jsonl=_append_jsonl,
        _load_optional_json=_load_optional_json,
        _write_text=_write_text,
        _now_ms=_now_ms,
        _now_iso=_now_iso,
        _iso_to_epoch=_iso_to_epoch,
        _ms_to_iso=_ms_to_iso,
        _subprocess_failure_message=_subprocess_failure_message,
        # --- ledger / jobs / audit ---
        _jobs_store_path=_jobs_store_path,
        load_jobs=load_jobs,
        load_ledger=load_ledger,
        load_scheduler=load_scheduler,
        save_jobs=save_jobs,
        save_ledger=save_ledger,
        save_scheduler=save_scheduler,
        audit=audit,
    )
    # Bind ns into the publisher's holder so its lambdas can read load_ledger().
    _ns_holder["ns"] = ns
    # Adapter module loaders (cached per-workspace).
    for loader_name, loader_fn in _build_adapter_module_loaders(workspace_root).items():
        setattr(ns, loader_name, loader_fn)
    _install_wrapper_adapter_shims(ns)

    # -- external reviewer ----------------------------------------------
    # Build the external reviewer once; downstream shims delegate to it.
    from workflows.change_delivery.reviewers import ReviewerContext, build_reviewer

    # Resolve config from agents.external-reviewer. The legacy top-level
    # codex-bot block fallback was removed in Phase D-2; operators must use
    # the modern agents.external-reviewer.{logins,clean-reactions,pending-reactions}
    # form.
    _yaml_agents = (yaml_cfg or {}).get("agents", {}) or {}
    ext_reviewer_cfg = dict(_yaml_agents.get("external-reviewer") or {})

    ext_reviewer_cfg["repo-slug"] = ext_reviewer_cfg.get("repo-slug") or _repo_slug

    reviewer_ctx = ReviewerContext(
        run_json=ns._run_json,
        repo_path=ns.REPO_PATH,
        repo_slug=ext_reviewer_cfg["repo-slug"],
        iso_to_epoch=ns._iso_to_epoch,
        now_epoch=time.time,
        extract_severity=ns._extract_severity,
        extract_summary=ns._extract_summary,
        agent_name=ns.EXTERNAL_REVIEWER_AGENT_NAME,
    )
    ns.reviewer = build_reviewer(ext_reviewer_cfg, ws_context=reviewer_ctx)

    # -- runtimes -------------------------------------------------------
    # Legacy JSON configs synthesize runtime profiles from session/review
    # policy fields. Repo-owned WORKFLOW.md configs use their top-level
    # runtimes block directly, so shared runtimes such as codex-app-server are
    # configured in one place and selected by agents.*.runtime.
    _session_policy = config.get("sessionPolicy", {}) or {}
    _review_policy = config.get("reviewPolicy", {}) or {}

    _legacy_runtimes_cfg = {
        "acpx-codex": {
            "kind": "acpx-codex",
            "session-idle-freshness-seconds": int(
                _session_policy.get("codexSessionFreshnessSeconds", 900)
            ),
            "session-idle-grace-seconds": int(
                _session_policy.get("codexSessionPokeGraceSeconds", 1800)
            ),
            "session-nudge-cooldown-seconds": int(
                _session_policy.get("codexSessionNudgeCooldownSeconds", 600)
            ),
        },
        "claude-cli": {
            "kind": "claude-cli",
            "max-turns-per-invocation": int(
                _review_policy.get("interReviewAgentMaxTurns")
                or _review_policy.get("internalReviewerAgentMaxTurns")
                or _review_policy.get("claudeReviewMaxTurns", 24)
            ),
            "timeout-seconds": int(
                _review_policy.get("interReviewAgentTimeoutSeconds")
                or _review_policy.get("internalReviewerAgentTimeoutSeconds")
                or _review_policy.get("claudeReviewTimeoutSeconds", 1200)
            ),
        },
    }
    _runtimes_cfg = {
        str(name): dict(profile or {})
        for name, profile in (((yaml_cfg or {}).get("runtimes") or _legacy_runtimes_cfg).items())
    }

    _runtimes = build_runtimes(_runtimes_cfg, run=ns._run, run_json=ns._run_json)
    ns.RUNTIME_PROFILES = _runtimes_cfg

    def _runtime_accessor(name: str):
        if name not in _runtimes:
            raise KeyError(
                f"unknown runtime profile {name!r}; known: {sorted(_runtimes)}"
            )
        return _runtimes[name]

    def _close_runtimes() -> None:
        for runtime in _runtimes.values():
            close = getattr(runtime, "close", None)
            if callable(close):
                close()

    ns.runtime = _runtime_accessor
    ns.close = _close_runtimes

    # YAML-shape cross-reference validation: every agent's runtime: field must
    # name a key in the top-level runtimes: mapping. The schema doesn't enforce
    # this (it's a structural-vs-referential distinction); the factory does.
    if yaml_cfg is not None:
        yaml_agents = yaml_cfg.get("agents", {}) or {}
        known_runtimes = set((yaml_cfg.get("runtimes", {}) or {}).keys())
        for tier_name, tier in (yaml_agents.get("coder") or {}).items():
            rt = tier.get("runtime")
            if rt and rt not in known_runtimes:
                raise ValueError(
                    f"agents.coder.{tier_name}.runtime={rt!r} not defined in runtimes: "
                    f"{sorted(known_runtimes)}"
                )
        int_rev = yaml_agents.get("internal-reviewer", {}) or {}
        rt = int_rev.get("runtime")
        if rt and rt not in known_runtimes:
            raise ValueError(
                f"agents.internal-reviewer.runtime={rt!r} not defined in runtimes: "
                f"{sorted(known_runtimes)}"
            )

    return ns


def _install_wrapper_adapter_shims(ns: SimpleNamespace) -> None:
    """Install the trivial wrapper-side adapter delegation shims onto ``ns``.

    These are the helpers the retired wrapper used to expose at module level
    (``_lane_state_path``, ``_pr_ready_for_review`` etc.). Binding them here
    means the adapter CLI + orchestrator can call ``ws._name`` without the
    wrapper needing to redeclare them.
    """

    def set_active_cancel_event(event):
        ns.ACTIVE_CANCEL_EVENT = event
        return None

    def _object_value(value, *keys):
        for key in keys:
            if isinstance(value, dict) and key in value:
                return value.get(key)
            if hasattr(value, key):
                return getattr(value, key)
        return None

    def _runtime_metrics_payload(value):
        if value is None or isinstance(value, str):
            return {}
        tokens = _object_value(value, "tokens")
        return {
            "session_id": _object_value(value, "session_id", "sessionId"),
            "thread_id": _object_value(value, "thread_id", "threadId"),
            "turn_id": _object_value(value, "turn_id", "turnId"),
            "last_event": _object_value(value, "last_event", "lastEvent"),
            "last_message": _object_value(value, "last_message", "lastMessage"),
            "turn_count": int(_object_value(value, "turn_count", "turnCount") or 0),
            "tokens": tokens if isinstance(tokens, dict) else {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "rate_limits": _object_value(value, "rate_limits", "rateLimits"),
        }

    def _lane_state_path(worktree):
        return ns._load_adapter_paths_module().lane_state_path(worktree)

    def _lane_memo_path(worktree):
        return ns._load_adapter_paths_module().lane_memo_path(worktree)

    def _issue_number_from_branch(branch):
        return ns._load_adapter_sessions_module().issue_number_from_branch(branch)

    def _issue_number_from_worktree(worktree):
        return ns._load_adapter_sessions_module().issue_number_from_worktree(worktree)

    def _expected_lane_worktree(issue_number):
        return ns._load_adapter_sessions_module().expected_lane_worktree(issue_number)

    def _expected_lane_branch(issue):
        return ns._load_adapter_sessions_module().expected_lane_branch(issue)

    def _lane_acpx_session_name(issue_number):
        return ns._load_adapter_sessions_module().lane_acpx_session_name(issue_number)

    def _issue_label_names(issue):
        return ns._load_adapter_github_module().issue_label_names(issue)

    def _inter_review_agent_target_head(review):
        return ns._load_adapter_reviews_module().inter_review_agent_target_head(review)

    def _inter_review_agent_is_running_on_head(review, head_sha):
        return ns._load_adapter_reviews_module().inter_review_agent_is_running_on_head(
            review,
            head_sha,
            target_head_fn=ns._inter_review_agent_target_head,
        )

    def _classify_inter_review_agent_failure_text(text):
        return ns._load_adapter_reviews_module().classify_inter_review_agent_failure_text(text)

    def _json_object_or_none(text):
        return ns._load_adapter_reviews_module().json_object_or_none(text)

    def _extract_json_object(text):
        return ns._load_adapter_reviews_module().extract_json_object(text)

    def _inter_review_agent_failure_message(exc):
        return ns._load_adapter_reviews_module().inter_review_agent_failure_message(
            exc, json_object_or_none_fn=ns._json_object_or_none,
        )

    def _inter_review_agent_failure_class(exc):
        return ns._load_adapter_reviews_module().inter_review_agent_failure_class(
            exc, classify_failure_text_fn=ns._classify_inter_review_agent_failure_text,
        )

    def _implementation_lane_matches(implementation, lane_number):
        return ns._load_adapter_sessions_module().implementation_lane_matches(implementation, lane_number)

    def _snapshot_lane_artifacts(worktree):
        return ns._load_adapter_sessions_module().snapshot_lane_artifacts(worktree)

    def _pr_ready_for_review(open_pr):
        return ns._load_adapter_reviews_module().pr_ready_for_review(open_pr)

    def _has_local_candidate(local_head_sha, commits_ahead):
        return ns._load_adapter_reviews_module().has_local_candidate(local_head_sha, commits_ahead)

    def _current_inter_review_agent_matches_local_head(review, local_head_sha):
        return ns._load_adapter_reviews_module().current_inter_review_agent_matches_local_head(review, local_head_sha)

    def _local_inter_review_agent_review_count(review, lane_state=None):
        return ns._load_adapter_reviews_module().local_inter_review_agent_review_count(review, lane_state)

    def _extract_severity(body):
        return ns._load_adapter_reviews_module().extract_severity(body)

    def _extract_summary(body):
        return ns._load_adapter_reviews_module().extract_summary(body)

    def _review_bucket(review):
        return ns._load_adapter_reviews_module().review_bucket(review)

    def _summarize_validation(ledger):
        return ns._load_adapter_prompts_module().summarize_validation(ledger)

    def _checks_acceptable(pr):
        return ns._load_adapter_reviews_module().checks_acceptable(pr)

    def _determine_review_loop_state(reviews, *, has_pr):
        return ns._load_adapter_reviews_module().determine_review_loop_state(reviews, has_pr=has_pr)

    from urllib.parse import quote as _quote

    # -- workspace-aware shims (reference ns constants + primitives) ----

    def _legacy_watchdog_present(*, managed_job_names, job_map):
        return ns.WORKFLOW_WATCHDOG_JOB_NAME in managed_job_names or ns.WORKFLOW_WATCHDOG_JOB_NAME in job_map

    def _legacy_watchdog_mode(*, managed_job_names, job_map):
        if ns.ENGINE_OWNER != "hermes":
            return "primary_dispatcher"
        return (
            "fallback_reconciler"
            if ns._legacy_watchdog_present(managed_job_names=managed_job_names, job_map=job_map)
            else "retired"
        )

    def _set_job_next_run_ms(job, next_run_ms):
        if isinstance(job.get("state"), dict) or ns.ENGINE_OWNER != "hermes":
            job.setdefault("state", {})["nextRunAtMs"] = next_run_ms
        else:
            job["next_run_at"] = ns._ms_to_iso(next_run_ms)

    def _set_job_updated_ms(job, updated_ms):
        if "updatedAtMs" in job or isinstance(job.get("state"), dict) or ns.ENGINE_OWNER != "hermes":
            job["updatedAtMs"] = updated_ms
        else:
            job["updated_at"] = ns._ms_to_iso(updated_ms)

    def _show_acpx_session(*, worktree, session_name):
        issue_number = ns._scheduler_issue_number_from_session(worktree=worktree, session_name=session_name)
        thread_id = ns._codex_thread_for_issue_number(issue_number)
        if thread_id:
            entry = ((ns.load_scheduler().get("codex_threads") or {}).get(ns._scheduler_issue_key(issue_number)) or {})
            return {
                "name": session_name,
                "closed": False,
                "cwd": str(worktree) if worktree is not None else None,
                "last_used_at": entry.get("updated_at"),
                "session_id": thread_id,
                "record_id": thread_id,
            }
        return ns._load_adapter_sessions_module().show_acpx_session(
            worktree=worktree, session_name=session_name, run_json=ns._run_json,
        )

    def _get_issue_details(issue_number):
        return ns._load_adapter_github_module().get_issue_details(
            issue_number, repo_path=ns.REPO_PATH, run_json=ns._run_json,
        )

    def _close_acpx_session(*, worktree, session_name):
        issue_number = ns._scheduler_issue_number_from_session(worktree=worktree, session_name=session_name)
        ns._clear_codex_thread_for_issue_number(issue_number)
        return ns._load_adapter_sessions_module().close_acpx_session(
            worktree=worktree, session_name=session_name, run=ns._run,
        )

    def _inter_review_agent_started_epoch(review):
        return ns._load_adapter_reviews_module().inter_review_agent_started_epoch(
            review, iso_to_epoch_fn=ns._iso_to_epoch,
        )

    def _git_branch(path):
        return ns._load_adapter_status_module().git_branch(path, run=ns._run)

    def _is_git_repo(path):
        return ns._load_adapter_sessions_module().is_git_repo(path, run=ns._run)

    def _restore_lane_artifacts(worktree, artifacts):
        return ns._load_adapter_sessions_module().restore_lane_artifacts(
            worktree, artifacts, write_text=ns._write_text,
        )

    def _git_commits_ahead(path):
        return ns._load_adapter_status_module().git_commits_ahead(path, run=ns._run)

    def _git_head_sha(path):
        return ns._load_adapter_status_module().git_head_sha(path, run=ns._run)

    def _inter_review_agent_pending_seed():
        return ns._load_adapter_reviews_module().inter_review_agent_pending_seed(
            model=ns.INTER_REVIEW_AGENT_MODEL,
        )

    def _session_record_files(session_name):
        if not session_name:
            return []
        encoded = _quote(session_name, safe="")
        return sorted(ns.SESSIONS_STATE_PATH.glob(f"{encoded}*json*"))

    def _synthesize_repair_brief(reviews, head_sha):
        return ns._load_adapter_reviews_module().synthesize_repair_brief(
            reviews, head_sha=head_sha, now_iso=ns._now_iso(),
        )

    def _collect_broken_watchers(jobs_payload):
        return ns._load_adapter_health_module().collect_broken_watchers(
            jobs_payload, issue_watcher_re=ns.ISSUE_WATCHER_RE,
        )

    def build_status():
        return ns._load_adapter_status_module().build_status(ns.WORKSPACE)

    def _pick_next_lane_issue():
        return ns._load_adapter_github_module().pick_next_lane_issue_from_repo(
            ns.REPO_PATH,
            run_json=ns._run_json,
            active_lane_label=ns.ACTIVE_LANE_LABEL,
            lane_selection_cfg=ns.LANE_SELECTION_CFG,
        )

    def _issue_add_label(issue_number, label):
        return ns._load_adapter_github_module().issue_add_label(
            issue_number, label, repo_path=ns.REPO_PATH, run=ns._run,
        )

    def _issue_remove_label(issue_number, label):
        return ns._load_adapter_github_module().issue_remove_label(
            issue_number, label, repo_path=ns.REPO_PATH, run=ns._run,
        )

    def _issue_comment(issue_number, body):
        return ns._load_adapter_github_module().issue_comment(
            issue_number, body, repo_path=ns.REPO_PATH, run=ns._run,
        )

    def _issue_close(issue_number, comment=None):
        return ns._load_adapter_github_module().issue_close(
            issue_number, comment, repo_path=ns.REPO_PATH, run=ns._run,
        )

    def publish_ready_pr():
        return ns._load_adapter_actions_module().publish_ready_pr(ns.WORKSPACE)

    def push_pr_update():
        return ns._load_adapter_actions_module().push_pr_update(ns.WORKSPACE)

    def merge_and_promote():
        return ns._load_adapter_actions_module().merge_and_promote(ns.WORKSPACE)

    def dispatch_inter_review_agent_review():
        return ns._load_adapter_actions_module().dispatch_inter_review_agent_review(ns.WORKSPACE)

    def dispatch_claude_review():
        return ns._load_adapter_actions_module().dispatch_claude_review(ns.WORKSPACE)

    def dispatch_repair_handoff():
        return ns._load_adapter_actions_module().dispatch_repair_handoff(ns.WORKSPACE)

    def tick():
        return ns._load_adapter_actions_module().tick(ns.WORKSPACE)

    def dispatch_implementation_turn():
        return ns._load_adapter_actions_module().dispatch_implementation_turn(ns.WORKSPACE)

    def restart_actor_session():
        return ns.restart_actor_session_raw()

    # -----------------------------------------------------------------
    # Job-state helpers — pure reads over the cron jobs payload.
    # -----------------------------------------------------------------

    def _managed_job_names():
        names = []
        for name in [*ns.CORE_JOB_NAMES, *ns.HERMES_JOB_NAMES]:
            if name and name not in names:
                names.append(name)
        return names

    def _job_lookup(jobs_payload):
        return {job["name"]: job for job in jobs_payload.get("jobs", [])}

    def _job_state_mapping(job):
        state = job.get("state")
        return state if isinstance(state, dict) else {}

    def _job_schedule_every_ms(job):
        schedule = job.get("schedule", {}) or {}
        if schedule.get("kind") == "every":
            every_ms = schedule.get("everyMs")
            return int(every_ms) if every_ms is not None else None
        if schedule.get("kind") == "interval":
            minutes = schedule.get("minutes")
            if minutes is None:
                return None
            return int(minutes) * 60 * 1000
        return None

    def _job_next_run_ms(job):
        state = ns._job_state_mapping(job)
        next_run = state.get("nextRunAtMs")
        if next_run is not None:
            return int(next_run)
        next_run_iso = job.get("next_run_at")
        epoch = ns._iso_to_epoch(next_run_iso)
        return None if epoch is None else int(epoch * 1000)

    def _job_last_run_at_ms(job):
        state = ns._job_state_mapping(job)
        last_run = state.get("lastRunAtMs")
        if last_run is not None:
            return int(last_run)
        last_run_iso = job.get("last_run_at")
        epoch = ns._iso_to_epoch(last_run_iso)
        return None if epoch is None else int(epoch * 1000)

    def _job_last_status(job):
        state = ns._job_state_mapping(job)
        return state.get("lastStatus", job.get("last_status"))

    def _job_last_run_status(job):
        state = ns._job_state_mapping(job)
        return state.get("lastRunStatus", job.get("last_status"))

    def _job_last_duration_ms(job):
        state = ns._job_state_mapping(job)
        return state.get("lastDurationMs")

    def _job_last_error(job):
        state = ns._job_state_mapping(job)
        return state.get("lastError", job.get("last_error"))

    def _job_delivery(job):
        delivery = job.get("delivery")
        if isinstance(delivery, dict) and delivery:
            return delivery
        deliver = job.get("deliver")
        if deliver in {None, "", "local"}:
            return {"mode": deliver or "none"}
        return {"mode": deliver}

    def _summarize_job(job):
        if job is None:
            return None
        schedule = job.get("schedule", {})
        next_run = ns._job_next_run_ms(job)
        stale = False
        every_ms = ns._job_schedule_every_ms(job)
        if job.get("enabled") and every_ms is not None and next_run is not None:
            allowed = int(every_ms * ns.MISS_MULTIPLIER)
            stale = ns._now_ms() > int(next_run) + allowed
        return {
            "name": job.get("name"),
            "enabled": bool(job.get("enabled")),
            "schedule": schedule,
            "lastRunStatus": ns._job_last_run_status(job),
            "lastStatus": ns._job_last_status(job),
            "lastRunAtMs": ns._job_last_run_at_ms(job),
            "lastDurationMs": ns._job_last_duration_ms(job),
            "nextRunAtMs": next_run,
            "lastError": ns._job_last_error(job),
            "delivery": ns._job_delivery(job),
            "stale": stale,
        }

    # -----------------------------------------------------------------
    # Active lane / issue GitHub wrappers.
    # -----------------------------------------------------------------

    def _get_active_lane():
        return ns._load_adapter_github_module().get_active_lane_from_repo(
            ns.REPO_PATH,
            run_json=ns._run_json,
            active_lane_label=ns.ACTIVE_LANE_LABEL,
        )

    def _get_open_pr_for_issue(issue_number):
        return ns._load_adapter_github_module().get_open_pr_for_issue(
            issue_number,
            repo_path=ns.REPO_PATH,
            run_json=ns._run_json,
            issue_number_from_branch_fn=ns._issue_number_from_branch,
        )

    # -----------------------------------------------------------------
    # ACPX / codex-session helpers.
    # -----------------------------------------------------------------

    def _slugify_issue_title(title):
        text = re.sub(r"^\[[^\]]+\]\s*", "", title or "").strip().lower()
        text = re.sub(r"[^a-z0-9]+", "-", text)
        text = re.sub(r"-+", "-", text).strip("-")
        return text[:48] or "lane"

    def _normalize_acpx_session_meta(payload):
        if not payload:
            return None
        return {
            "name": payload.get("name"),
            "closed": bool(payload.get("closed")),
            "cwd": payload.get("cwd"),
            "last_used_at": payload.get("lastUsedAt") or payload.get("last_used_at"),
            "session_id": payload.get("acpSessionId") or payload.get("acpxSessionId"),
            "record_id": payload.get("acpxRecordId") or payload.get("acpx_record_id"),
        }

    def _acpx_session_stream_path(acpx_record_id):
        if not acpx_record_id:
            return None
        return Path.home() / ".acpx" / "sessions" / f"{acpx_record_id}.stream.ndjson"

    def _latest_acpx_prompt_error(acpx_record_id):
        path = ns._acpx_session_stream_path(acpx_record_id)
        if path is None or not path.exists():
            return None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return None
        for raw_line in reversed(lines):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            error = payload.get("error")
            if isinstance(error, dict):
                return error
        return None

    def _fallback_codex_model_for_prompt_error(*, acpx_record_id, codex_model, exc):
        error = ns._latest_acpx_prompt_error(acpx_record_id)
        error_data = error.get("data") if isinstance(error, dict) else None
        codex_error_info = (error_data or {}).get("codex_error_info") if isinstance(error_data, dict) else None
        if codex_error_info == "usage_limit_exceeded" and codex_model != ns.CODEX_MODEL_ESCALATED:
            return ns.CODEX_MODEL_ESCALATED
        combined_output = "\n".join(part for part in [exc.stdout or "", exc.stderr or ""] if part).lower()
        if "usage limit" in combined_output and codex_model != ns.CODEX_MODEL_ESCALATED:
            return ns.CODEX_MODEL_ESCALATED
        return None

    def _load_latest_session_meta(session_name):
        files = ns._session_record_files(session_name)
        if not files:
            return None
        latest = max(files, key=lambda p: p.stat().st_mtime)
        try:
            return json.loads(latest.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return None

    def _load_implementation_session_meta(implementation, worktree):
        return ns._load_adapter_status_module().load_implementation_session_meta(
            implementation,
            worktree,
            show_acpx_session_fn=ns._show_acpx_session,
            load_latest_session_meta_fn=ns._load_latest_session_meta,
        )

    def _scheduler_issue_key(issue_number):
        if issue_number in (None, ""):
            return None
        return f"lane:{issue_number}"

    def _scheduler_issue_number_from_session(*, worktree=None, session_name=None):
        issue_number = ns._issue_number_from_worktree(worktree)
        if issue_number is not None:
            return issue_number
        match = re.search(r"(\d+)", str(session_name or ""))
        return int(match.group(1)) if match else None

    def _codex_thread_for_issue_number(issue_number):
        key = ns._scheduler_issue_key(issue_number)
        if not key:
            return None
        entry = ((ns.load_scheduler().get("codex_threads") or {}).get(key) or {})
        thread_id = str(entry.get("thread_id") or "").strip()
        return thread_id or None

    def _clear_codex_thread_for_issue_number(issue_number):
        key = ns._scheduler_issue_key(issue_number)
        if not key:
            return False
        scheduler = ns.load_scheduler()
        threads = dict(scheduler.get("codex_threads") or {})
        if key not in threads:
            return False
        threads.pop(key, None)
        scheduler["workflow"] = "change-delivery"
        scheduler["updatedAt"] = ns._now_iso()
        scheduler["codex_threads"] = threads
        scheduler.setdefault("codex_totals", {})
        ns.save_scheduler(scheduler)
        return True

    def _record_coder_runtime_result(*, issue, session_name, runtime_name, runtime_kind, result, metrics, at):
        metrics = dict(metrics or {})
        if runtime_kind != "codex-app-server":
            return metrics
        key = ns._scheduler_issue_key((issue or {}).get("number"))
        if not key:
            return metrics
        scheduler = ns.load_scheduler()
        threads = dict(scheduler.get("codex_threads") or {})
        thread_id = str(metrics.get("thread_id") or metrics.get("session_id") or "").strip()
        if thread_id:
            threads[key] = {
                "issue_id": key,
                "issue_number": (issue or {}).get("number"),
                "identifier": f"#{(issue or {}).get('number')}",
                "session_name": session_name,
                "runtime_name": runtime_name,
                "runtime_kind": runtime_kind,
                "thread_id": thread_id,
                "turn_id": metrics.get("turn_id"),
                "status": "completed",
                "cancel_requested": False,
                "cancel_reason": None,
                "run_id": getattr(ns, "CURRENT_ENGINE_RUN_ID", None),
                "updated_at": at,
            }
        totals = dict(scheduler.get("codex_totals") or {})
        tokens = metrics.get("tokens") or {}
        totals["input_tokens"] = int(totals.get("input_tokens") or 0) + int(tokens.get("input_tokens") or 0)
        totals["output_tokens"] = int(totals.get("output_tokens") or 0) + int(tokens.get("output_tokens") or 0)
        totals["total_tokens"] = int(totals.get("total_tokens") or 0) + int(tokens.get("total_tokens") or 0)
        totals["turn_count"] = int(totals.get("turn_count") or 0) + int(metrics.get("turn_count") or 0)
        if metrics.get("rate_limits") is not None:
            totals["rate_limits"] = metrics.get("rate_limits")
        scheduler.update(
            {
                "workflow": "change-delivery",
                "updatedAt": ns._now_iso(),
                "codex_threads": threads,
                "codex_totals": totals,
            }
        )
        ns.save_scheduler(scheduler)
        return metrics

    def _record_coder_runtime_progress(*, issue_number, session_name, runtime_name, runtime_kind, worktree, result):
        if runtime_kind != "codex-app-server":
            return None
        metrics = ns._runtime_metrics_payload(result)
        thread_id = str(metrics.get("thread_id") or metrics.get("session_id") or "").strip()
        if not thread_id:
            return None
        key = ns._scheduler_issue_key(issue_number)
        if not key:
            return None
        scheduler = ns.load_scheduler()
        threads = dict(scheduler.get("codex_threads") or {})
        prior = dict(threads.get(key) or {})
        threads[key] = {
            **prior,
            "issue_id": key,
            "issue_number": issue_number,
            "identifier": f"#{issue_number}",
            "session_name": session_name,
            "runtime_name": runtime_name,
            "runtime_kind": runtime_kind,
            "thread_id": thread_id,
            "turn_id": metrics.get("turn_id") or prior.get("turn_id"),
            "worktree": str(worktree) if worktree is not None else prior.get("worktree"),
            "status": "running",
            "last_event": metrics.get("last_event"),
            "last_message": metrics.get("last_message"),
            "run_id": getattr(ns, "CURRENT_ENGINE_RUN_ID", None) or prior.get("run_id"),
            "updated_at": ns._now_iso(),
        }
        scheduler.update(
            {
                "workflow": "change-delivery",
                "updatedAt": ns._now_iso(),
                "codex_threads": threads,
                "codex_totals": scheduler.get("codex_totals") or {},
            }
        )
        ns.save_scheduler(scheduler)
        return metrics

    def _interrupt_active_coder_turn(*, issue_number=None, reason="cancel-requested"):
        scheduler = ns.load_scheduler()
        threads = dict(scheduler.get("codex_threads") or {})
        key = ns._scheduler_issue_key(issue_number) if issue_number is not None else None
        if key is None:
            running_entries = [
                (entry_key, entry)
                for entry_key, entry in threads.items()
                if isinstance(entry, dict) and entry.get("status") == "running"
            ]
            if len(running_entries) != 1:
                return {"interrupted": False, "reason": "missing-active-turn"}
            key, entry = running_entries[0]
        else:
            entry = threads.get(key) or {}
        thread_id = str((entry or {}).get("thread_id") or "").strip()
        turn_id = str((entry or {}).get("turn_id") or "").strip()
        runtime_name = str((entry or {}).get("runtime_name") or "").strip()
        if not thread_id or not turn_id or not runtime_name:
            return {"interrupted": False, "reason": "missing-active-turn"}
        runtime = ns.runtime(runtime_name)
        interrupter = getattr(runtime, "interrupt_turn", None)
        if not callable(interrupter):
            return {"interrupted": False, "reason": "runtime-does-not-support-interrupt"}
        worktree = Path(entry.get("worktree")) if entry.get("worktree") else None
        try:
            interrupted = bool(interrupter(thread_id=thread_id, turn_id=turn_id, worktree=worktree))
        except Exception as exc:
            return {"interrupted": False, "reason": f"{type(exc).__name__}: {exc}"}
        updated_entry = {
            **entry,
            "status": "canceling" if interrupted else entry.get("status"),
            "cancel_requested": interrupted,
            "cancel_reason": reason if interrupted else entry.get("cancel_reason"),
            "updated_at": ns._now_iso(),
        }
        threads[key] = updated_entry
        scheduler.update(
            {
                "workflow": "change-delivery",
                "updatedAt": ns._now_iso(),
                "codex_threads": threads,
                "codex_totals": scheduler.get("codex_totals") or {},
            }
        )
        ns.save_scheduler(scheduler)
        return {
            "interrupted": interrupted,
            "issue_id": key,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "reason": reason,
        }

    def _coder_agent_tiers():
        return (((getattr(ns, "WORKFLOW_YAML", {}) or {}).get("agents") or {}).get("coder") or {})

    def _coder_runtime_name_for_model(model):
        tiers = ns._coder_agent_tiers()
        model_text = str(model or "")
        for tier in tiers.values():
            if not isinstance(tier, dict):
                continue
            if model_text and str(tier.get("model") or "") == model_text and tier.get("runtime"):
                return str(tier.get("runtime"))
        default_tier = tiers.get("default") if isinstance(tiers, dict) else {}
        if isinstance(default_tier, dict) and default_tier.get("runtime"):
            return str(default_tier.get("runtime"))
        return "acpx-codex"

    def _runtime_kind(runtime_name):
        return str(((getattr(ns, "RUNTIME_PROFILES", {}) or {}).get(runtime_name) or {}).get("kind") or runtime_name)

    def _coder_runtime_kind_for_model(model):
        return ns._runtime_kind(ns._coder_runtime_name_for_model(model))

    def _should_escalate_codex_model(*, lane_state=None, workflow_state=None, reviews=None):
        return ns._load_adapter_sessions_module().should_escalate_codex_model(
            lane_state=lane_state,
            workflow_state=workflow_state,
            reviews=reviews,
            escalate_restart_count=ns.CODEX_ESCALATE_RESTART_COUNT,
            escalate_local_review_count=ns.CODEX_ESCALATE_LOCAL_REVIEW_COUNT,
            escalate_postpublish_finding_count=ns.CODEX_ESCALATE_POSTPUBLISH_FINDING_COUNT,
        )

    def _codex_model_for_issue(issue, *, lane_state=None, workflow_state=None, reviews=None):
        return ns._load_adapter_sessions_module().codex_model_for_issue(
            issue,
            lane_state=lane_state,
            workflow_state=workflow_state,
            reviews=reviews,
            default_model=ns.CODEX_MODEL_DEFAULT,
            high_effort_model=ns.CODEX_MODEL_HIGH_EFFORT,
            escalated_model=ns.CODEX_MODEL_ESCALATED,
            escalate_restart_count=ns.CODEX_ESCALATE_RESTART_COUNT,
            escalate_local_review_count=ns.CODEX_ESCALATE_LOCAL_REVIEW_COUNT,
            escalate_postpublish_finding_count=ns.CODEX_ESCALATE_POSTPUBLISH_FINDING_COUNT,
        )

    def _coder_agent_name_for_model(model):
        return ns._load_adapter_sessions_module().coder_agent_name_for_model(
            model,
            escalated_model=ns.CODEX_MODEL_ESCALATED,
            internal_coder_agent_name=ns.INTERNAL_CODER_AGENT_NAME,
            escalation_coder_agent_name=ns.ESCALATION_CODER_AGENT_NAME,
        )

    def _actor_labels_payload(current_coder_model):
        return ns._load_adapter_sessions_module().actor_labels_payload(
            current_coder_model=current_coder_model,
            default_model=ns.CODEX_MODEL_DEFAULT,
            escalated_model=ns.CODEX_MODEL_ESCALATED,
            internal_coder_agent_name=ns.INTERNAL_CODER_AGENT_NAME,
            escalation_coder_agent_name=ns.ESCALATION_CODER_AGENT_NAME,
            internal_reviewer_agent_name=ns.INTERNAL_REVIEWER_AGENT_NAME,
            internal_reviewer_model=ns.INTER_REVIEW_AGENT_MODEL,
            external_reviewer_agent_name=ns.EXTERNAL_REVIEWER_AGENT_NAME,
            advisory_reviewer_agent_name=ns.ADVISORY_REVIEWER_AGENT_NAME,
        )

    def _ensure_acpx_session(*, worktree, session_name, codex_model, resume_session_id=None):
        runtime_name = ns._coder_runtime_name_for_model(codex_model)
        runtime_kind = ns._runtime_kind(runtime_name)
        if runtime_kind == "codex-app-server":
            issue_number = ns._scheduler_issue_number_from_session(worktree=worktree, session_name=session_name)
            resume_session_id = ns._codex_thread_for_issue_number(issue_number) or resume_session_id
        return ns._load_adapter_sessions_module().ensure_session_via_runtime(
            workspace=ns,
            runtime_name=runtime_name,
            worktree=worktree,
            session_name=session_name,
            model=codex_model,
            resume_session_id=resume_session_id,
        )

    def _run_acpx_prompt(*, worktree, session_name, prompt, codex_model):
        runtime_name = ns._coder_runtime_name_for_model(codex_model)
        runtime_kind = ns._runtime_kind(runtime_name)
        issue_number = ns._scheduler_issue_number_from_session(worktree=worktree, session_name=session_name)
        return ns._load_adapter_sessions_module().run_prompt_via_runtime(
            workspace=ns,
            runtime_name=runtime_name,
            worktree=worktree,
            session_name=session_name,
            prompt=prompt,
            model=codex_model,
            cancel_event=ns.ACTIVE_CANCEL_EVENT,
            progress_callback=(
                lambda result: ns._record_coder_runtime_progress(
                    issue_number=issue_number,
                    session_name=session_name,
                    runtime_name=runtime_name,
                    runtime_kind=runtime_kind,
                    worktree=worktree,
                    result=result,
                )
                if runtime_kind == "codex-app-server"
                else None
            ),
        )

    # -----------------------------------------------------------------
    # Review lifecycle helpers.
    # -----------------------------------------------------------------

    def _new_inter_review_agent_run_id():
        import uuid as _uuid
        return f"inter-review-agent:{_uuid.uuid4()}"

    def _extract_inter_review_agent_payload(raw_output):
        return ns._load_adapter_reviews_module().extract_inter_review_agent_payload(
            raw_output,
            json_object_or_none_fn=ns._json_object_or_none,
            extract_json_object_fn=ns._extract_json_object,
        )

    def _render_inter_review_agent_prompt(*, issue, worktree, lane_memo_path, lane_state_path, head_sha):
        return ns._load_adapter_prompts_module().render_inter_review_agent_prompt(
            issue=issue,
            worktree=worktree,
            lane_memo_path=lane_memo_path,
            lane_state_path=lane_state_path,
            head_sha=head_sha,
            workflow_policy=ns.WORKFLOW_POLICY,
        )

    def _run_inter_review_agent_review(*, issue, worktree, lane_memo_path, lane_state_path, head_sha):
        adapter_reviews = ns._load_adapter_reviews_module()
        try:
            return adapter_reviews.run_inter_review_agent_review(
                issue=issue,
                worktree=worktree,
                lane_memo_path=lane_memo_path,
                lane_state_path=lane_state_path,
                head_sha=head_sha,
                run_fn=ns._run,
                inter_review_agent_model=ns.INTER_REVIEW_AGENT_MODEL,
                inter_review_agent_max_turns=ns.INTER_REVIEW_AGENT_MAX_TURNS,
                error_cls=subprocess.CalledProcessError,
            )
        except adapter_reviews.InterReviewAgentError as exc:
            raise ns.InterReviewAgentError(
                str(exc),
                failure_class=getattr(exc, "failure_class", "review_subprocess_failed"),
            ) from exc

    def _audit_inter_review_agent_transition(previous_review, current_review):
        ns._load_adapter_reviews_module().audit_inter_review_agent_transition(
            previous_review=previous_review,
            current_review=current_review,
            audit_fn=ns.audit,
            internal_reviewer_agent_name=ns.INTERNAL_REVIEWER_AGENT_NAME,
            target_head_fn=ns._inter_review_agent_target_head,
        )

    # -----------------------------------------------------------------
    # Prompts + handoff + next-action helpers.
    # -----------------------------------------------------------------

    def _render_implementation_dispatch_prompt(*, issue, issue_details, worktree, lane_memo_path, lane_state_path, open_pr, action, workflow_state):
        return ns._load_adapter_prompts_module().render_implementation_dispatch_prompt(
            issue=issue,
            issue_details=issue_details,
            worktree=worktree,
            lane_memo_path=lane_memo_path,
            lane_state_path=lane_state_path,
            open_pr=open_pr,
            action=action,
            workflow_state=workflow_state,
            workflow_policy=ns.WORKFLOW_POLICY,
        )

    def _normalize_implementation_for_active_lane(implementation, *, active_lane, open_pr):
        selected_codex_model = ns._codex_model_for_issue(
            active_lane,
            lane_state=(implementation or {}).get("laneState"),
            workflow_state=(implementation or {}).get("status"),
        )
        session_runtime = ns._coder_runtime_kind_for_model(selected_codex_model)
        resume_session_id = None
        if session_runtime == "codex-app-server":
            resume_session_id = ns._codex_thread_for_issue_number((active_lane or {}).get("number"))
        impl = dict(implementation or {})
        if not active_lane:
            return impl
        return ns._load_adapter_status_module().normalize_implementation_for_active_lane(
            impl,
            active_lane=active_lane,
            open_pr=open_pr,
            selected_codex_model=selected_codex_model,
            session_runtime=session_runtime,
            resume_session_id=resume_session_id,
        )

    def _prepare_lane_worktree(*, worktree, branch, open_pr):
        import shutil as _shutil
        return ns._load_adapter_sessions_module().prepare_lane_worktree(
            worktree=worktree,
            branch=branch,
            open_pr=open_pr,
            repo_path=ns.REPO_PATH,
            run=ns._run,
            is_git_repo=ns._is_git_repo,
            snapshot_lane_artifacts_fn=ns._snapshot_lane_artifacts,
            restore_lane_artifacts_fn=ns._restore_lane_artifacts,
            rmtree=_shutil.rmtree,
        )

    def _latest_lane_progress_epoch(implementation, lane_state):
        impl = implementation or {}
        state = lane_state or {}
        state_impl = state.get("implementation") or {}
        active_health = impl.get("activeSessionHealth") or {}
        candidates = [
            state_impl.get("lastMeaningfulProgressAt"),
            state_impl.get("activeSessionLastUsedAt"),
            active_health.get("lastUsedAt"),
            impl.get("updatedAt"),
        ]
        epochs = [epoch for epoch in (ns._iso_to_epoch(value) for value in candidates if value) if epoch is not None]
        return max(epochs) if epochs else None

    def _single_pass_local_claude_gate_satisfied(review, local_head_sha, lane_state=None):
        return ns._load_adapter_reviews_module().single_pass_local_claude_gate_satisfied(
            review,
            local_head_sha,
            lane_state,
            pass_with_findings_reviews=ns.CLAUDE_PASS_WITH_FINDINGS_REVIEWS,
        )

    def _fetch_external_review_pr_body_signal(pr_number):
        return ns.reviewer.fetch_pr_body_signal(pr_number)

    def _fetch_external_review(pr_number, current_head_sha, cached_review=None):
        return ns.reviewer.fetch_review(
            pr_number=pr_number,
            current_head_sha=current_head_sha,
            cached_review=cached_review,
        )

    def _external_review_placeholder(*, required, status, summary):
        return ns.reviewer.placeholder(required=required, status=status, summary=summary)

    def _normalize_review(review, *, required=True, pending_summary, agent_name=None, agent_role=None):
        return ns._load_adapter_reviews_module().normalize_review(
            review,
            required=required,
            pending_summary=pending_summary,
            agent_name=agent_name,
            agent_role=agent_role,
        )

    def _inter_review_agent_superseded(review, *, superseded_by_head_sha, now_iso):
        return ns._load_adapter_reviews_module().inter_review_agent_superseded(
            review,
            superseded_by_head_sha=superseded_by_head_sha,
            now_iso=now_iso,
            target_head_fn=ns._inter_review_agent_target_head,
        )

    def _inter_review_agent_timed_out(review, *, now_iso):
        return ns._load_adapter_reviews_module().inter_review_agent_timed_out(
            review,
            now_iso=now_iso,
            target_head_fn=ns._inter_review_agent_target_head,
            started_epoch_fn=ns._inter_review_agent_started_epoch,
            now_epoch_fn=lambda: int(time.time()),
        )

    def _normalize_local_inter_review_agent_seed(review, *, local_head_sha, now_iso):
        return ns._load_adapter_reviews_module().normalize_local_inter_review_agent_seed(
            review,
            local_head_sha=local_head_sha,
            now_iso=now_iso,
            model=ns.INTER_REVIEW_AGENT_MODEL,
            timeout_seconds=ns.INTER_REVIEW_AGENT_TIMEOUT_SECONDS,
            target_head_fn=ns._inter_review_agent_target_head,
            started_epoch_fn=ns._inter_review_agent_started_epoch,
            now_epoch_fn=lambda: int(time.time()),
            current_head_match_fn=ns._current_inter_review_agent_matches_local_head,
        )

    def _assess_codex_session_health(session_meta, worktree, now_epoch=None, freshness_seconds=None, poke_grace_seconds=None):
        return ns._load_adapter_sessions_module().assess_codex_session_health(
            session_meta,
            worktree,
            now_epoch=now_epoch,
            freshness_seconds=freshness_seconds if freshness_seconds is not None else ns.CODEX_SESSION_FRESHNESS_SECONDS,
            poke_grace_seconds=poke_grace_seconds if poke_grace_seconds is not None else ns.CODEX_SESSION_POKE_GRACE_SECONDS,
        )

    def decide_lane_session_action(*, active_session_health, implementation_status, has_open_pr):
        return ns._load_adapter_sessions_module().decide_session_action(
            active_session_health=active_session_health,
            implementation_status=implementation_status,
            has_open_pr=has_open_pr,
        )

    def render_lane_memo(*, issue, worktree, branch, open_pr, repair_brief, latest_progress, validation_summary, acp_strategy=None):
        return ns._load_adapter_prompts_module().render_lane_memo(
            issue=issue,
            worktree=worktree,
            branch=branch,
            open_pr=open_pr,
            repair_brief=repair_brief,
            latest_progress=latest_progress,
            validation_summary=validation_summary,
            acp_strategy=acp_strategy,
        )

    def build_acp_session_strategy(*, implementation_session_key, session_action, lane_state, session_runtime=None, session_name=None, resume_session_id=None):
        return ns._load_adapter_sessions_module().build_acp_session_strategy(
            implementation_session_key=implementation_session_key,
            session_action=session_action,
            lane_state=lane_state,
            session_runtime=session_runtime,
            session_name=session_name,
            resume_session_id=resume_session_id,
        )

    def build_session_nudge_payload(*, session_action, issue, open_pr, lane_memo_path, now_iso):
        return ns._load_adapter_sessions_module().build_session_nudge_payload(
            session_action=session_action,
            issue=issue,
            open_pr=open_pr,
            lane_memo_path=lane_memo_path,
            now_iso=now_iso,
        )

    def should_nudge_session(*, lane_state, session_action, current_head_sha, now_epoch=None, cooldown_seconds=None):
        return ns._load_adapter_sessions_module().should_nudge_session(
            lane_state=lane_state,
            session_action=session_action,
            current_head_sha=current_head_sha,
            now_epoch=now_epoch,
            cooldown_seconds=cooldown_seconds if cooldown_seconds is not None else ns.CODEX_SESSION_NUDGE_COOLDOWN_SECONDS,
        )

    def record_session_nudge(*, worktree, payload):
        return ns._load_adapter_sessions_module().record_session_nudge(
            worktree=worktree,
            payload=payload,
            lane_state_path_fn=ns._lane_state_path,
            load_optional_json_fn=ns._load_optional_json,
            write_json_fn=ns._write_json,
        )

    def should_dispatch_claude_repair_handoff(*, lane_state, session_action, internal_review, repair_brief, workflow_state, current_head_sha, has_open_pr):
        return ns._load_adapter_reviews_module().should_dispatch_claude_repair_handoff(
            lane_state=lane_state,
            session_action=session_action,
            internal_review=internal_review,
            repair_brief=repair_brief,
            workflow_state=workflow_state,
            current_head_sha=current_head_sha,
            has_open_pr=has_open_pr,
        )

    def should_dispatch_external_review_repair_handoff(*, lane_state, session_action, external_review, repair_brief, workflow_state, current_head_sha, has_open_pr):
        return ns._load_adapter_reviews_module().should_dispatch_external_review_repair_handoff(
            lane_state=lane_state,
            session_action=session_action,
            external_review=external_review,
            repair_brief=repair_brief,
            workflow_state=workflow_state,
            current_head_sha=current_head_sha,
            has_open_pr=has_open_pr,
        )

    def build_external_review_repair_handoff_payload(*, session_action, issue, external_review, repair_brief, lane_memo_path, lane_state_path, now_iso):
        return ns._load_adapter_reviews_module().build_external_review_repair_handoff_payload(
            session_action=session_action,
            issue=issue,
            external_review=external_review,
            repair_brief=repair_brief,
            lane_memo_path=lane_memo_path,
            lane_state_path=lane_state_path,
            now_iso=now_iso,
        )

    def record_external_review_repair_handoff(*, worktree, payload):
        return ns._load_adapter_reviews_module().record_external_review_repair_handoff(
            worktree=worktree,
            payload=payload,
            lane_state_path_fn=ns._lane_state_path,
            load_optional_json_fn=ns._load_optional_json,
            write_json_fn=ns._write_json,
        )

    def _render_external_review_repair_handoff_prompt(*, issue, external_review, repair_brief, lane_memo_path, lane_state_path, pr_url):
        return ns._load_adapter_prompts_module().render_external_reviewer_repair_handoff_prompt(
            issue=issue,
            external_review=external_review,
            repair_brief=repair_brief,
            lane_memo_path=lane_memo_path,
            lane_state_path=lane_state_path,
            pr_url=pr_url,
            external_reviewer_agent_name=ns.EXTERNAL_REVIEWER_AGENT_NAME,
            workflow_policy=ns.WORKFLOW_POLICY,
        )

    def build_claude_repair_handoff_payload(*, session_action, issue, internal_review, repair_brief, lane_memo_path, lane_state_path, now_iso):
        return ns._load_adapter_reviews_module().build_claude_repair_handoff_payload(
            session_action=session_action,
            issue=issue,
            internal_review=internal_review,
            repair_brief=repair_brief,
            lane_memo_path=lane_memo_path,
            lane_state_path=lane_state_path,
            now_iso=now_iso,
        )

    def record_claude_repair_handoff(*, worktree, payload):
        return ns._load_adapter_reviews_module().record_claude_repair_handoff(
            worktree=worktree,
            payload=payload,
            lane_state_path_fn=ns._lane_state_path,
            load_optional_json_fn=ns._load_optional_json,
            write_json_fn=ns._write_json,
        )

    def _render_claude_repair_handoff_prompt(*, issue, internal_review, repair_brief, lane_memo_path, lane_state_path):
        return ns._load_adapter_prompts_module().render_claude_repair_handoff_prompt(
            issue=issue,
            internal_review=internal_review,
            repair_brief=repair_brief,
            lane_memo_path=lane_memo_path,
            lane_state_path=lane_state_path,
            internal_reviewer_agent_name=ns.INTERNAL_REVIEWER_AGENT_NAME,
            workflow_policy=ns.WORKFLOW_POLICY,
        )

    def _classify_lane_failure(*, implementation, reviews, preflight):
        return ns._load_adapter_reviews_module().classify_lane_failure(
            implementation=implementation,
            reviews=reviews,
            preflight=preflight,
        )

    def _increment_no_progress_ticks(*, existing, latest_progress, now_iso=None):
        latest_progress = latest_progress or {}
        if latest_progress.get("kind") in {"approved", "merged"}:
            return 0
        prev_impl = existing.get("implementation") or {}
        prev_budget = existing.get("budget") or {}
        same_at = (prev_impl.get("lastMeaningfulProgressAt") or None) == (latest_progress.get("at") or None)
        same_kind = (prev_impl.get("lastMeaningfulProgressKind") or None) == (latest_progress.get("kind") or None)
        if same_at and same_kind and latest_progress.get("at"):
            previous_evaluated_epoch = ns._iso_to_epoch(prev_budget.get("lastEvaluatedAt"))
            now_epoch = ns._iso_to_epoch(now_iso) if now_iso else None
            if (
                previous_evaluated_epoch is not None
                and now_epoch is not None
                and (now_epoch - previous_evaluated_epoch) < ns.LANE_COUNTER_INCREMENT_MIN_SECONDS
            ):
                return int(prev_budget.get("noProgressTicks") or 0)
            return int(prev_budget.get("noProgressTicks") or 0) + 1
        return 0

    def _lane_operator_attention_reasons(lane_state):
        lane_state = lane_state or {}
        failure_state = lane_state.get("failure") or {}
        budget_state = lane_state.get("budget") or {}
        reasons = []
        if int(failure_state.get("retryCount") or 0) >= ns.LANE_OPERATOR_ATTENTION_RETRY_THRESHOLD:
            reasons.append(f"operator-attention-required:failure-retry-count={int(failure_state.get('retryCount') or 0)}")
        if int(budget_state.get("noProgressTicks") or 0) >= ns.LANE_OPERATOR_ATTENTION_NO_PROGRESS_THRESHOLD:
            reasons.append(f"operator-attention-required:no-progress-ticks={int(budget_state.get('noProgressTicks') or 0)}")
        return reasons

    def _lane_operator_attention_needed(lane_state):
        return bool(ns._lane_operator_attention_reasons(lane_state))

    def _derive_latest_progress(*, implementation, ledger, open_pr, reviews, review_loop_state, merge_blocked, now_iso):
        return ns._load_adapter_status_module().derive_latest_progress(
            implementation=implementation,
            ledger=ledger,
            open_pr=open_pr,
            reviews=reviews,
            review_loop_state=review_loop_state,
            merge_blocked=merge_blocked,
            now_iso=now_iso,
        )

    def write_lane_state(*, worktree, issue, open_pr, implementation, reviews, repair_brief, now_iso, latest_progress, preflight=None):
        return ns._load_adapter_status_module().write_lane_state(
            worktree=worktree,
            issue=issue,
            open_pr=open_pr,
            implementation=implementation,
            reviews=reviews,
            repair_brief=repair_brief,
            now_iso=now_iso,
            latest_progress=latest_progress,
            preflight=preflight,
            cooldown_seconds=ns.LANE_COUNTER_INCREMENT_MIN_SECONDS,
        )

    def write_lane_memo(*, worktree, issue, branch, open_pr, repair_brief, latest_progress, validation_summary, acp_strategy=None):
        return ns._load_adapter_status_module().write_lane_memo(
            worktree=worktree,
            issue=issue,
            branch=branch,
            open_pr=open_pr,
            repair_brief=repair_brief,
            latest_progress=latest_progress,
            validation_summary=validation_summary,
            acp_strategy=acp_strategy,
        )

    def _mark_pr_ready_for_review(pr_number):
        return ns._load_adapter_reviews_module().mark_pr_ready_for_review(
            pr_number,
            run_fn=ns._run,
            cwd=ns.REPO_PATH,
            repo_slug=ns.REPO_SLUG,
        )

    def _resolve_review_thread(thread_id):
        return ns._load_adapter_reviews_module().resolve_review_thread(
            thread_id,
            run_json_fn=ns._run_json,
            cwd=ns.REPO_PATH,
        )

    def _resolve_codex_superseded_threads(review, *, current_head_sha):
        return ns._load_adapter_reviews_module().resolve_codex_superseded_threads(
            review,
            current_head_sha=current_head_sha,
            resolve_review_thread_fn=ns._resolve_review_thread,
        )

    def _inter_review_agent_preflight(*, active_lane, open_pr, workflow_state, pr_ledger, inter_review_agent_review, inter_review_agent_job, local_head_sha, implementation_commits_ahead, single_pass_gate_satisfied=False):
        return ns._load_adapter_reviews_module().inter_review_agent_preflight(
            active_lane=active_lane,
            open_pr=open_pr,
            workflow_state=workflow_state,
            pr_ledger=pr_ledger,
            inter_review_agent_review=inter_review_agent_review,
            inter_review_agent_job=inter_review_agent_job,
            local_head_sha=local_head_sha,
            implementation_commits_ahead=implementation_commits_ahead,
            single_pass_gate_satisfied=single_pass_gate_satisfied,
            pr_ready_for_review_fn=ns._pr_ready_for_review,
            has_local_candidate_fn=ns._has_local_candidate,
            checks_acceptable_fn=ns._checks_acceptable,
            target_head_fn=ns._inter_review_agent_target_head,
            started_epoch_fn=ns._inter_review_agent_started_epoch,
            now_ms_fn=ns._now_ms,
            now_epoch_fn=lambda: int(time.time()),
            timeout_seconds=ns.INTER_REVIEW_AGENT_TIMEOUT_SECONDS,
            request_cooldown_seconds=ns.CLAUDE_REVIEW_REQUEST_COOLDOWN_SECONDS,
        )

    def _derive_next_action(*, active_lane, open_pr, health, implementation, reviews, repair_brief, preflight, workflow_state, review_loop_state, merge_blocked):
        impl = implementation or {}
        lane_state = impl.get('laneState') or {}
        status_like = {
            'activeLane': active_lane,
            'openPr': open_pr,
            'health': health,
            'implementation': impl,
            'reviews': reviews or {},
            'repairBrief': repair_brief,
            'preflight': preflight or {},
            'ledger': {'workflowState': workflow_state},
            'derivedReviewLoopState': review_loop_state,
            'derivedMergeBlocked': bool(merge_blocked),
            'staleLaneReasons': ns._lane_operator_attention_reasons(lane_state),
            'nextAction': {
                'type': 'noop',
                'reason': 'no-forward-action-needed',
                'issueNumber': active_lane.get('number') if active_lane else None,
                'headSha': impl.get('localHeadSha'),
            },
        }
        return ns._load_adapter_workflow_module().derive_next_action(
            status_like,
            failure_retry_budget=ns.LANE_FAILURE_RETRY_BUDGET,
            no_progress_tick_budget=ns.LANE_NO_PROGRESS_TICK_BUDGET,
        )

    # -----------------------------------------------------------------
    # Orchestrator wrappers: build_status_raw + reconcile + doctor.
    # -----------------------------------------------------------------

    def build_status_raw():
        return ns._load_adapter_module("orchestrator").build_status_raw(ns)

    def reconcile(*, write_health: bool = True, fix_watchers: bool = False):
        return ns._load_adapter_module("orchestrator").reconcile(
            ns, write_health=write_health, fix_watchers=fix_watchers,
        )

    def doctor(*, fix_watchers: bool = True):
        before = ns.build_status()
        after = before
        if before["health"] != "healthy" or fix_watchers:
            after = ns.reconcile(fix_watchers=fix_watchers)
        engine_checks = ns.ENGINE_STORE.doctor()
        ns.audit(
            "doctor",
            f"Doctor checked workflow: {before['health']} -> {after['health']}",
            before=before["health"],
            after=after["health"],
            engineChecks=engine_checks,
        )
        return {
            "before": before,
            "after": after,
            "engineChecks": engine_checks,
            "fixed": before["health"] != after["health"] or bool(after.get("actionsTaken", {}).get("jobs")),
        }

    # -----------------------------------------------------------------
    # Cron operator commands: pause / resume / wake.
    # -----------------------------------------------------------------

    def _wake_jobs(jobs_payload, names):
        now_ms = ns._now_ms()
        touched = []
        wanted = set(names)
        for job in jobs_payload.get("jobs", []):
            if job.get("name") not in wanted:
                continue
            ns._set_job_next_run_ms(job, now_ms)
            ns._set_job_updated_ms(job, now_ms)
            touched.append(str(job.get("name")))
        return touched

    def set_core_jobs_enabled(enabled, *, wake_now: bool = False):
        jobs_payload = ns.load_jobs()
        now_ms = ns._now_ms()
        touched = []
        managed_job_names = ns._managed_job_names()
        for job in jobs_payload.get("jobs", []):
            if job.get("name") in managed_job_names:
                job["enabled"] = enabled
                ns._set_job_updated_ms(job, now_ms)
                if wake_now:
                    ns._set_job_next_run_ms(job, now_ms)
                touched.append(job.get("name"))
        ns.save_jobs(jobs_payload)
        status = ns.build_status()
        ns._write_json(ns.HEALTH_PATH, status)
        ns.audit(
            "resume" if enabled else "pause",
            f"Set core jobs enabled={enabled}",
            jobs=touched,
            wakeNow=wake_now,
            health=status["health"],
        )
        return status

    def wake_named_jobs(names):
        jobs_payload = ns.load_jobs()
        touched = ns._wake_jobs(jobs_payload, names)
        ns.save_jobs(jobs_payload)
        status = ns.build_status()
        ns._write_json(ns.HEALTH_PATH, status)
        ns.audit(
            "wake-jobs",
            "Woke named jobs",
            jobs=touched,
            health=status["health"],
        )
        return {"jobs": touched, "health": status["health"]}

    def wake_core_jobs():
        return ns.set_core_jobs_enabled(True, wake_now=True)

    # -----------------------------------------------------------------
    # Action runners: adapter-owned, wrapper no longer needed.
    # -----------------------------------------------------------------

    def publish_ready_pr_raw():
        return ns._load_adapter_actions_module().run_publish_ready_pr(
            reconcile_fn=ns.reconcile,
            run_fn=ns._run,
            audit_fn=ns.audit,
            mark_pr_ready_for_review_fn=ns._mark_pr_ready_for_review,
            repo_slug=ns.REPO_SLUG,
            repo_path=ns.REPO_PATH,
        )

    def push_pr_update_raw():
        return ns._load_adapter_actions_module().run_push_pr_update(
            reconcile_fn=ns.reconcile,
            run_fn=ns._run,
            audit_fn=ns.audit,
        )

    def merge_and_promote_raw():
        return ns._load_adapter_actions_module().run_merge_and_promote(
            reconcile_fn=ns.reconcile,
            run_fn=ns._run,
            audit_fn=ns.audit,
            issue_remove_label_fn=ns._issue_remove_label,
            issue_close_fn=ns._issue_close,
            issue_add_label_fn=ns._issue_add_label,
            issue_comment_fn=ns._issue_comment,
            pick_next_lane_issue_fn=ns._pick_next_lane_issue,
            now_iso_fn=ns._now_iso,
            active_lane_label=ns.ACTIVE_LANE_LABEL,
            repo_slug=ns.REPO_SLUG,
            repo_path=ns.REPO_PATH,
        )

    def dispatch_inter_review_agent_review_raw():
        return ns._load_adapter_actions_module().run_dispatch_inter_review_agent_review(
            reconcile_fn=ns.reconcile,
            load_ledger_fn=ns.load_ledger,
            save_ledger_fn=ns.save_ledger,
            audit_inter_review_agent_transition_fn=ns._audit_inter_review_agent_transition,
            run_inter_review_agent_review_fn=lambda **kw: ns._run_inter_review_agent_review(**kw),
            now_iso_fn=ns._now_iso,
            new_inter_review_agent_run_id_fn=ns._new_inter_review_agent_run_id,
            actor_labels_payload_fn=ns._actor_labels_payload,
            inter_review_agent_model=ns.INTER_REVIEW_AGENT_MODEL,
            internal_reviewer_agent_name=ns.INTERNAL_REVIEWER_AGENT_NAME,
        )

    def dispatch_claude_review_raw():
        return ns.dispatch_inter_review_agent_review_raw()

    def _maybe_dispatch_repair_handoff(*, status, ledger, now_iso, codex_model, lane_state_override=None):
        return ns._load_adapter_reviews_module().maybe_dispatch_repair_handoff(
            status=status,
            ledger=ledger,
            now_iso=now_iso,
            codex_model=codex_model,
            run_prompt_fn=ns._run_acpx_prompt,
            audit_fn=ns.audit,
            lane_state_override=lane_state_override,
            lane_state_path_fn=ns._lane_state_path,
            load_optional_json_fn=ns._load_optional_json,
            write_json_fn=ns._write_json,
            internal_reviewer_agent_name=ns.INTERNAL_REVIEWER_AGENT_NAME,
            external_reviewer_agent_name=ns.EXTERNAL_REVIEWER_AGENT_NAME,
        )

    def dispatch_repair_handoff_raw():
        status = ns.build_status()
        ledger = ns.load_ledger()
        impl = status.get("implementation") or {}
        codex_model = impl.get("codexModel") or ns._codex_model_for_issue(
            status.get("activeLane"),
            lane_state=impl.get("laneState"),
            workflow_state=(status.get("ledger") or {}).get("workflowState"),
            reviews=status.get("reviews") or {},
        )
        result, changed = ns._maybe_dispatch_repair_handoff(
            status=status,
            ledger=ledger,
            now_iso=status.get("updatedAt") or ns._now_iso(),
            codex_model=codex_model,
        )
        if changed:
            ns.save_ledger(ledger)
        after = ns.build_status()
        return {**result, "after": after}

    def tick_raw():
        def audit_with_run_id(action, summary, **extra):
            run_id = getattr(ns, "CURRENT_ENGINE_RUN_ID", None)
            if run_id and not extra.get("run_id"):
                extra["run_id"] = run_id
            return ns.audit(action, summary, **extra)

        return ns._load_adapter_actions_module().run_tick_raw(
            reconcile_fn=ns.reconcile,
            audit_fn=audit_with_run_id,
            dispatch_inter_review_agent_review_fn=ns.dispatch_inter_review_agent_review,
            dispatch_implementation_turn_fn=ns.dispatch_implementation_turn,
            publish_ready_pr_fn=ns.publish_ready_pr,
            push_pr_update_fn=ns.push_pr_update,
            merge_and_promote_fn=ns.merge_and_promote,
        )

    def _dispatch_lane_turn(*, status, forced_action=None, audit_action="dispatch-implementation-turn"):
        impl = status.get("implementation") or {}
        codex_model = impl.get("codexModel") or ns._codex_model_for_issue(
            status.get("activeLane"),
            lane_state=impl.get("laneState"),
            workflow_state=(status.get("ledger") or {}).get("workflowState"),
            reviews=status.get("reviews") or {},
        )
        runtime_name = ns._coder_runtime_name_for_model(codex_model)
        return ns._load_adapter_actions_module().run_dispatch_lane_turn(
            status=status,
            forced_action=forced_action,
            audit_action=audit_action,
            now_iso_fn=ns._now_iso,
            close_acpx_session_fn=ns._close_acpx_session,
            ensure_acpx_session_fn=ns._ensure_acpx_session,
            show_acpx_session_fn=ns._show_acpx_session,
            run_prompt_fn=ns._run_acpx_prompt,
            prepare_lane_worktree_fn=ns._prepare_lane_worktree,
            codex_model_for_issue_fn=ns._codex_model_for_issue,
            get_issue_details_fn=ns._get_issue_details,
            fallback_codex_model_for_prompt_error_fn=ns._fallback_codex_model_for_prompt_error,
            coder_agent_name_for_model_fn=ns._coder_agent_name_for_model,
            actor_labels_payload_fn=ns._actor_labels_payload,
            load_ledger_fn=ns.load_ledger,
            save_ledger_fn=ns.save_ledger,
            reconcile_fn=ns.reconcile,
            audit_fn=ns.audit,
            render_implementation_dispatch_prompt_fn=ns._render_implementation_dispatch_prompt,
            runtime_name=runtime_name,
            runtime_kind=ns._runtime_kind(runtime_name),
            record_runtime_result_fn=ns._record_coder_runtime_result,
            error_cls=subprocess.CalledProcessError,
        )

    def dispatch_implementation_turn_raw():
        status = ns.reconcile(fix_watchers=True)
        return ns._dispatch_lane_turn(
            status=status,
            forced_action=None,
            audit_action="dispatch-implementation-turn",
        )

    def restart_actor_session_raw():
        status = ns.reconcile(fix_watchers=True)
        return ns._dispatch_lane_turn(
            status=status,
            forced_action="restart-session",
            audit_action="restart-actor-session",
        )

    # Rebind the user-facing action functions to call the raw implementations
    # directly. This replaces the earlier shims above (which went through
    # ``_load_adapter_actions_module().publish_ready_pr(ns.WORKSPACE)``), cutting
    # the circular wrapper dependency.
    def publish_ready_pr():
        return ns.publish_ready_pr_raw()

    def push_pr_update():
        return ns.push_pr_update_raw()

    def merge_and_promote():
        return ns.merge_and_promote_raw()

    def dispatch_inter_review_agent_review():
        return ns.dispatch_inter_review_agent_review_raw()

    def dispatch_claude_review():
        return ns.dispatch_claude_review_raw()

    def dispatch_repair_handoff():
        return ns.dispatch_repair_handoff_raw()

    def tick():
        engine_run = ns.ENGINE_STORE.start_run(mode="tick")
        previous_run_id = getattr(ns, "CURRENT_ENGINE_RUN_ID", None)
        ns.CURRENT_ENGINE_RUN_ID = engine_run["run_id"]
        try:
            result = ns.tick_raw()
            action = result.get("action") if isinstance(result, dict) else {}
            executed = result.get("executed") if isinstance(result, dict) else None
            action_type = (action or {}).get("type")
            completed = 1 if executed is not None else 0
            final_run = ns.ENGINE_STORE.complete_run(
                engine_run["run_id"],
                selected_count=1 if action_type and action_type != "noop" else 0,
                completed_count=completed,
                metadata={
                    "action_type": action_type,
                    "reason": (action or {}).get("reason"),
                    "executed": executed is not None,
                },
            )
            if isinstance(result, dict):
                result["engineRun"] = final_run
            return result
        except Exception as exc:
            try:
                ns.ENGINE_STORE.fail_run(
                    engine_run["run_id"],
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            raise
        finally:
            ns.CURRENT_ENGINE_RUN_ID = previous_run_id

    def dispatch_implementation_turn():
        return ns.dispatch_implementation_turn_raw()

    def restart_actor_session():
        return ns.restart_actor_session_raw()

    def build_status():
        from workflows.change_delivery.status import normalize_status as _normalize_status

        return _normalize_status(ns.build_status_raw(), ns.WORKSPACE)

    # Back-compat aliases ------------------------------------------------
    # Reference local function variables directly — those are bound by `def`
    # in the current scope; dereferencing via ``ns.`` would fail here because
    # the ``setattr(ns, …)`` loop below hasn't run yet.
    _new_review_run_id = _new_inter_review_agent_run_id
    _claude_review_target_head = _inter_review_agent_target_head
    _claude_review_started_epoch = _inter_review_agent_started_epoch
    _claude_review_is_running_on_head = _inter_review_agent_is_running_on_head
    _classify_claude_review_failure_text = _classify_inter_review_agent_failure_text
    _extract_claude_review_payload = _extract_inter_review_agent_payload
    _claude_review_failure_message = _inter_review_agent_failure_message
    _claude_review_failure_class = _inter_review_agent_failure_class
    _render_claude_review_prompt = _render_inter_review_agent_prompt
    _run_claude_change_delivery = _run_inter_review_agent_review
    _audit_claude_review_transition = _audit_inter_review_agent_transition

    # Expose the InterReviewAgentError class so callers using
    # ``workspace.InterReviewAgentError`` can catch it. We bind lazily — if the
    # plugin payload isn't present (e.g. unit tests under a temp root), the
    # attribute is simply absent and the adapter reviews helper resolves it at
    # call time via ``ns._load_adapter_reviews_module()`` instead.
    try:
        ns.InterReviewAgentError = ns._load_adapter_reviews_module().InterReviewAgentError
        ns.ClaudeReviewError = ns.InterReviewAgentError
    except FileNotFoundError:
        pass

    for _shim_name, _shim_fn in list(locals().items()):
        if _shim_name in {"_install_wrapper_adapter_shims", "ns", "_quote"}:
            continue
        if callable(_shim_fn):
            setattr(ns, _shim_name, _shim_fn)


def load_workspace_from_config(
    *,
    workspace_root: Path,
    config_path: Path | None = None,
) -> SimpleNamespace:
    """Convenience factory: read the workflow contract and build a workspace."""
    from workflows.contract import load_workflow_contract, load_workflow_contract_file

    workspace_root = Path(workspace_root)
    if config_path is not None:
        path = Path(config_path)
        if path.suffix.lower() not in {".yaml", ".yml", ".md"}:
            raise ValueError(f"workflow config must be YAML or WORKFLOW.md: {path}")
        config = load_workflow_contract_file(path).config
        return make_workspace(workspace_root=workspace_root, config=config)

    return make_workspace(
        workspace_root=workspace_root, config=load_workflow_contract(workspace_root).config
    )
