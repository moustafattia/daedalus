"""Workflow diagnostics and conservative local repair."""

from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from sprints.engine.db import ENGINE_STATE_TABLES
from sprints.engine import EngineStore
from sprints.services.codex_service import (
    codex_app_server_install,
    codex_app_server_status,
)
from sprints.core.bindings import available_runtime_presets, runtime_preset_config
from sprints.core.config import WorkflowConfig
from sprints.core.contracts import (
    ACTIVE_WORKFLOW_CONTRACT_RELATIVE_PATH,
    WorkflowContractError,
    active_workflow_contract_meta_path,
    active_workflow_contract_path,
    contract_sha256,
    find_workflow_contract_path,
    load_workflow_contract,
    render_workflow_markdown,
    snapshot_workflow_contract,
    workflow_contract_pointer_path,
    write_workflow_contract_pointer,
)
from sprints.services.daemon import workflow_daemon_install, workflow_daemon_status
from sprints.core.paths import repo_local_workflow_pointer_path, runtime_paths
from sprints.workflows.state_io import (
    WorkflowState,
    load_state,
    save_state,
    validate_state,
)
from sprints.workflows.sessions import (
    runtime_session_has_identity,
    scheduler_entry,
)
from sprints.core.validation import (
    build_readiness_recommendations,
    validate_workflow_contract,
)

_REQUIRED_WORKFLOW_DIRS = (
    Path("config"),
    Path("memory"),
    Path("state") / "sessions",
    Path("runtime") / "logs",
    Path("runtime") / "memory",
    Path("runtime") / "state" / "sprints",
    Path("workspace"),
)


def build_doctor_report(*, workflow_root: Path, fix: bool = False) -> dict[str, Any]:
    root = Path(workflow_root).expanduser().resolve()
    repairs: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    if fix:
        _ensure_required_dirs(root, repairs)
        _refresh_active_contract_snapshot(root, repairs, skipped)
        _refresh_workflow_contract_pointer(root, repairs, skipped)
        _repair_runtime_bindings(root, repairs, skipped)

    validation = validate_workflow_contract(root)
    checks = [_doctor_check_from_validation(check) for check in validation["checks"]]
    contract = _load_contract(root, skipped)
    config = _typed_config(root, contract, skipped)

    if config is not None:
        if fix:
            _ensure_config_dirs(config, repairs)
            _ensure_state_files(config, repairs, skipped)
            _refresh_repo_pointer(config, repairs, skipped)
            _reconcile_engine_projections(config, repairs, skipped)
            _repair_services(config, repairs, skipped)
        checks.extend(_state_checks(config))
        checks.extend(_engine_checks(config, fix=fix))
        checks.extend(_service_checks(config))

    checks.extend(_repair_checks(repairs, skipped))
    overall = _overall_status(checks)
    recommendations = build_readiness_recommendations(
        [
            {"name": check["code"], "status": check["status"], "detail": check["summary"]}
            for check in checks
        ],
        workflow=validation.get("workflow"),
        workflow_root=root,
        source_path=validation.get("source_path"),
    )
    return {
        "ok": overall != "fail",
        "overall_status": overall,
        "workflow": validation.get("workflow"),
        "workflow_root": str(root),
        "source_path": validation.get("source_path"),
        "fix": fix,
        "checks": checks,
        "repairs": repairs,
        "skipped_repairs": skipped,
        "recommendations": recommendations,
    }


def _ensure_required_dirs(root: Path, repairs: list[dict[str, Any]]) -> None:
    for rel_path in _REQUIRED_WORKFLOW_DIRS:
        path = root / rel_path
        if path.exists():
            continue
        path.mkdir(parents=True, exist_ok=True)
        _append_repair(
            repairs,
            action="create-dir",
            path=path,
            detail="created missing workflow directory",
        )


def _refresh_active_contract_snapshot(
    root: Path, repairs: list[dict[str, Any]], skipped: list[dict[str, Any]]
) -> None:
    meta_path = active_workflow_contract_meta_path(root)
    active_path = active_workflow_contract_path(root)
    source_path: Path | None = None
    expected_active_hash = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _append_skip(
                skipped,
                action="refresh-contract-snapshot",
                path=meta_path,
                detail=f"metadata unreadable: {exc}",
            )
            return
        raw_source = str(meta.get("source_path") or "").strip()
        if raw_source:
            source_path = Path(raw_source).expanduser().resolve()
        expected_active_hash = str(meta.get("contract_sha256") or "").strip()
    elif active_path.exists():
        return
    else:
        discovered = find_workflow_contract_path(root)
        if discovered is not None and discovered != active_path:
            source_path = discovered

    if source_path is None or not source_path.exists():
        return
    try:
        source_text = source_path.read_text(encoding="utf-8")
        source_hash = contract_sha256(source_text)
        active_text = active_path.read_text(encoding="utf-8") if active_path.exists() else ""
        active_hash = contract_sha256(active_text) if active_text else ""
    except OSError as exc:
        _append_skip(
            skipped,
            action="refresh-contract-snapshot",
            path=source_path,
            detail=str(exc),
        )
        return
    if active_hash == source_hash:
        return
    if active_hash and expected_active_hash and active_hash != expected_active_hash:
        _append_skip(
            skipped,
            action="refresh-contract-snapshot",
            path=active_path,
            detail="active contract has local edits; not overwriting from source",
        )
        return
    meta = snapshot_workflow_contract(
        workflow_root=root,
        source_path=source_path,
        source_ref="doctor --fix",
    )
    _append_repair(
        repairs,
        action="refresh-contract-snapshot",
        path=active_path,
        detail="refreshed active workflow contract from source",
        before=active_hash or None,
        after=meta.get("contract_sha256"),
    )


def _refresh_workflow_contract_pointer(
    root: Path, repairs: list[dict[str, Any]], skipped: list[dict[str, Any]]
) -> None:
    active_path = root / ACTIVE_WORKFLOW_CONTRACT_RELATIVE_PATH
    if not active_path.exists():
        return
    pointer_path = workflow_contract_pointer_path(root)
    before = None
    if pointer_path.exists():
        try:
            before = pointer_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            _append_skip(
                skipped,
                action="refresh-workflow-pointer",
                path=pointer_path,
                detail=str(exc),
            )
            return
    target = str(active_path.resolve())
    if before == target:
        return
    write_workflow_contract_pointer(root, active_path)
    _append_repair(
        repairs,
        action="refresh-workflow-pointer",
        path=pointer_path,
        detail="pointed workflow root at active contract snapshot",
        before=before,
        after=target,
    )


def _repair_runtime_bindings(
    root: Path, repairs: list[dict[str, Any]], skipped: list[dict[str, Any]]
) -> None:
    contract = _load_contract(root, skipped)
    if contract is None:
        return
    config = copy.deepcopy(contract.config)
    actors = config.get("actors")
    runtimes = config.get("runtimes")
    if not isinstance(actors, dict) or not isinstance(runtimes, dict):
        return
    changed: list[str] = []
    preset_names = set(available_runtime_presets())
    existing_runtime_names = sorted(str(name) for name in runtimes)
    fallback_runtime = existing_runtime_names[0] if len(existing_runtime_names) == 1 else ""

    for actor_name, actor_cfg in actors.items():
        if not isinstance(actor_cfg, dict):
            continue
        runtime_name = str(actor_cfg.get("runtime") or "").strip()
        if runtime_name and runtime_name in runtimes:
            continue
        if runtime_name in preset_names:
            runtimes[runtime_name] = runtime_preset_config(runtime_name)
            changed.append(f"added runtime profile {runtime_name!r}")
            continue
        if runtime_name == "codex":
            runtimes[runtime_name] = runtime_preset_config("codex-app-server")
            changed.append("added runtime profile 'codex' from codex-app-server preset")
            continue
        if fallback_runtime:
            before = runtime_name or None
            actor_cfg["runtime"] = fallback_runtime
            changed.append(
                f"bound actor {actor_name!r} from {before!r} to {fallback_runtime!r}"
            )
            continue
        _append_skip(
            skipped,
            action="repair-runtime-binding",
            path=contract.source_path,
            detail=f"actor {actor_name!r} references missing runtime {runtime_name!r}; no unambiguous repair",
        )

    if not changed:
        return
    contract.source_path.write_text(
        render_workflow_markdown(config=config, prompt_template=contract.prompt_template),
        encoding="utf-8",
    )
    _append_repair(
        repairs,
        action="repair-runtime-binding",
        path=contract.source_path,
        detail="; ".join(changed),
    )


def _ensure_config_dirs(config: WorkflowConfig, repairs: list[dict[str, Any]]) -> None:
    for path in (
        config.storage.state_path.parent,
        config.storage.audit_log_path.parent,
        runtime_paths(config.workflow_root)["db_path"].parent,
        runtime_paths(config.workflow_root)["event_log_path"].parent,
    ):
        if path.exists():
            continue
        path.mkdir(parents=True, exist_ok=True)
        _append_repair(
            repairs,
            action="create-dir",
            path=path,
            detail="created configured storage directory",
        )


def _ensure_state_files(
    config: WorkflowConfig,
    repairs: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> None:
    if not config.storage.state_path.exists():
        save_state(
            config.storage.state_path,
            WorkflowState.initial(
                workflow=config.workflow_name, first_stage=config.first_stage
            ),
        )
        _append_repair(
            repairs,
            action="create-state",
            path=config.storage.state_path,
            detail="created missing workflow state file",
        )
    else:
        try:
            payload = json.loads(config.storage.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _append_skip(
                skipped,
                action="repair-state",
                path=config.storage.state_path,
                detail=f"state file is unreadable; refusing overwrite: {exc}",
            )
        else:
            if isinstance(payload, dict):
                changed = []
                if not payload.get("workflow"):
                    payload["workflow"] = config.workflow_name
                    changed.append("workflow")
                if not payload.get("status"):
                    payload["status"] = "idle"
                    changed.append("status")
                if "lanes" not in payload:
                    payload["lanes"] = {}
                    changed.append("lanes")
                if changed:
                    config.storage.state_path.write_text(
                        json.dumps(payload, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    _append_repair(
                        repairs,
                        action="repair-state",
                        path=config.storage.state_path,
                        detail="filled missing state fields: " + ", ".join(changed),
                    )
    if not config.storage.audit_log_path.exists():
        config.storage.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        config.storage.audit_log_path.touch()
        _append_repair(
            repairs,
            action="create-audit-log",
            path=config.storage.audit_log_path,
            detail="created missing workflow audit log",
        )


def _refresh_repo_pointer(
    config: WorkflowConfig, repairs: list[dict[str, Any]], skipped: list[dict[str, Any]]
) -> None:
    repository = config.raw.get("repository") if isinstance(config.raw, dict) else {}
    repo_path_raw = (
        repository.get("local-path") or repository.get("local_path")
        if isinstance(repository, dict)
        else None
    )
    if not repo_path_raw:
        return
    repo_path = Path(str(repo_path_raw)).expanduser()
    if not repo_path.is_absolute():
        repo_path = (config.workflow_root / repo_path).resolve()
    if not repo_path.is_dir():
        _append_skip(
            skipped,
            action="refresh-repo-pointer",
            path=repo_path,
            detail="repository.local-path is not an existing directory",
        )
        return
    pointer_path = repo_local_workflow_pointer_path(repo_path)
    before = None
    if pointer_path.exists():
        try:
            before = pointer_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            _append_skip(
                skipped,
                action="refresh-repo-pointer",
                path=pointer_path,
                detail=str(exc),
            )
            return
    after = str(config.workflow_root)
    if before == after:
        return
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(after + "\n", encoding="utf-8")
    _append_repair(
        repairs,
        action="refresh-repo-pointer",
        path=pointer_path,
        detail="pointed repository checkout at workflow root",
        before=before,
        after=after,
    )


def _reconcile_engine_projections(
    config: WorkflowConfig,
    repairs: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> None:
    try:
        state = load_state(
            config.storage.state_path,
            workflow=config.workflow_name,
            first_stage=config.first_stage,
        )
        validate_state(config, state)
    except Exception as exc:
        _append_skip(
            skipped,
            action="reconcile-engine-projections",
            path=config.storage.state_path,
            detail=str(exc),
        )
        return
    store = _engine_store(config)
    before = _scheduler_fingerprint(store.read_scheduler())
    running_entries, runtime_entries, runtime_totals, retry_entries = (
        _scheduler_entries_from_state(state)
    )
    store.save_scheduler(
        retry_entries=retry_entries,
        running_entries=running_entries,
        runtime_totals=runtime_totals,
        runtime_sessions=runtime_entries,
    )
    after = _scheduler_fingerprint(store.read_scheduler())
    if before == after:
        return
    _append_repair(
        repairs,
        action="reconcile-engine-projections",
        path=runtime_paths(config.workflow_root)["db_path"],
        detail="reconciled engine scheduler projections from workflow state",
        before=before,
        after=after,
    )


def _repair_services(
    config: WorkflowConfig,
    repairs: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> None:
    if shutil.which("systemctl") is None:
        _append_skip(
            skipped,
            action="repair-services",
            path=config.workflow_root,
            detail="systemctl not found; service unit repair skipped",
        )
        return
    try:
        daemon_status = workflow_daemon_status(workflow_root=config.workflow_root)
        if not daemon_status.get("installed"):
            installed = workflow_daemon_install(workflow_root=config.workflow_root)
            _append_repair(
                repairs,
                action="install-workflow-daemon-unit",
                path=Path(str(installed.get("unit_path"))),
                detail="installed missing workflow daemon systemd user unit",
            )
    except Exception as exc:
        _append_skip(
            skipped,
            action="install-workflow-daemon-unit",
            path=config.workflow_root,
            detail=str(exc),
        )
    if not _uses_codex_app_server(config):
        return
    try:
        codex_status = codex_app_server_status(workflow_root=config.workflow_root)
        if not codex_status.get("installed"):
            installed = codex_app_server_install(workflow_root=config.workflow_root)
            _append_repair(
                repairs,
                action="install-codex-app-server-unit",
                path=Path(str(installed.get("unit_path"))),
                detail="installed missing codex-app-server systemd user unit",
            )
    except Exception as exc:
        _append_skip(
            skipped,
            action="install-codex-app-server-unit",
            path=config.workflow_root,
            detail=str(exc),
        )


def _state_checks(config: WorkflowConfig) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not config.storage.state_path.exists():
        return [
            _doctor_check(
                "state-file",
                "fail",
                f"missing workflow state file: {config.storage.state_path}",
            )
        ]
    try:
        state = load_state(
            config.storage.state_path,
            workflow=config.workflow_name,
            first_stage=config.first_stage,
        )
        validate_state(config, state)
    except Exception as exc:
        checks.append(_doctor_check("state-file", "fail", str(exc)))
    else:
        checks.append(_doctor_check("state-file", "pass", str(config.storage.state_path)))
    checks.append(
        _doctor_check(
            "audit-log",
            "pass" if config.storage.audit_log_path.exists() else "warn",
            str(config.storage.audit_log_path)
            if config.storage.audit_log_path.exists()
            else f"missing audit log: {config.storage.audit_log_path}",
        )
    )
    return checks


def _engine_checks(config: WorkflowConfig, *, fix: bool) -> list[dict[str, Any]]:
    store = _engine_store(config)
    if fix:
        return [
            _doctor_check(str(check.get("name")), str(check.get("status")), str(check.get("detail")))
            for check in store.doctor()
        ]
    db_path = runtime_paths(config.workflow_root)["db_path"]
    if not db_path.exists():
        return [_doctor_check("engine-db", "warn", f"missing engine DB: {db_path}")]
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        return [_doctor_check("engine-db", "fail", str(exc))]
    try:
        missing = [
            name
            for name in ENGINE_STATE_TABLES
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
        ]
    finally:
        conn.close()
    if missing:
        return [
            _doctor_check(
                "engine-schema",
                "fail",
                "missing: " + ", ".join(missing),
            )
        ]
    return [_doctor_check("engine-schema", "pass", "ok")]


def _service_checks(config: WorkflowConfig) -> list[dict[str, Any]]:
    if shutil.which("systemctl") is None:
        return [_doctor_check("services", "warn", "systemctl not found")]
    checks: list[dict[str, Any]] = []
    try:
        status = workflow_daemon_status(workflow_root=config.workflow_root)
        checks.append(
            _doctor_check(
                "workflow-daemon-service",
                "pass" if status.get("installed") else "warn",
                "installed" if status.get("installed") else "missing unit file",
            )
        )
    except Exception as exc:
        checks.append(_doctor_check("workflow-daemon-service", "warn", str(exc)))
    if _uses_codex_app_server(config):
        try:
            status = codex_app_server_status(workflow_root=config.workflow_root)
            checks.append(
                _doctor_check(
                    "codex-app-server-service",
                    "pass" if status.get("installed") else "warn",
                    "installed" if status.get("installed") else "missing unit file",
                )
            )
        except Exception as exc:
            checks.append(_doctor_check("codex-app-server-service", "warn", str(exc)))
    return checks


def _scheduler_entries_from_state(
    state: WorkflowState,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
    dict[str, dict[str, Any]],
]:
    running_entries: dict[str, dict[str, Any]] = {}
    runtime_entries: dict[str, dict[str, Any]] = {}
    retry_entries: dict[str, dict[str, Any]] = {}
    runtime_totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "turn_count": 0,
    }
    last_rate_limits: dict[str, Any] | None = None
    for lane in state.lanes.values():
        if not isinstance(lane, dict):
            continue
        lane_id = str(lane.get("lane_id") or "").strip()
        if not lane_id:
            continue
        entry = scheduler_entry(lane)
        if str(lane.get("status") or "") == "running":
            running_entries[lane_id] = entry
        session = lane.get("runtime_session")
        if isinstance(session, dict) and runtime_session_has_identity(session):
            runtime_entries[lane_id] = entry
            tokens = session.get("tokens") if isinstance(session.get("tokens"), dict) else {}
            runtime_totals["input_tokens"] += int(tokens.get("input_tokens") or 0)
            runtime_totals["output_tokens"] += int(tokens.get("output_tokens") or 0)
            runtime_totals["total_tokens"] += int(tokens.get("total_tokens") or 0)
            runtime_totals["turn_count"] += int(session.get("turn_count") or 0)
            rate_limits = session.get("rate_limits")
            if isinstance(rate_limits, dict):
                last_rate_limits = rate_limits
        pending = lane.get("pending_retry")
        if str(lane.get("status") or "") == "retry_queued" and isinstance(pending, dict):
            retry_entries[lane_id] = {
                **entry,
                "attempt": int(pending.get("attempt") or lane.get("attempt") or 0),
                "current_attempt": pending.get("current_attempt"),
                "due_at_epoch": pending.get("due_at_epoch"),
                "error": pending.get("reason"),
                "delay_type": "workflow-retry",
                "run_id": entry.get("run_id"),
            }
    if last_rate_limits is not None:
        runtime_totals["rate_limits"] = last_rate_limits
    return running_entries, runtime_entries, runtime_totals, retry_entries


def _scheduler_fingerprint(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {
            "running": [],
            "retry_queue": [],
            "runtime_sessions": [],
        }
    return {
        "running": sorted(str(item.get("issue_id") or item.get("work_id") or "") for item in payload.get("running") or []),
        "retry_queue": sorted(str(item.get("issue_id") or item.get("work_id") or "") for item in payload.get("retry_queue") or []),
        "runtime_sessions": sorted(str(key) for key in (payload.get("runtime_sessions") or {}).keys()),
    }


def _uses_codex_app_server(config: WorkflowConfig) -> bool:
    return any(
        str(runtime.kind or "").strip() == "codex-app-server"
        for runtime in config.runtimes.values()
    )


def _load_contract(root: Path, skipped: list[dict[str, Any]]) -> Any | None:
    try:
        return load_workflow_contract(root)
    except (FileNotFoundError, WorkflowContractError, OSError, UnicodeDecodeError) as exc:
        _append_skip(
            skipped,
            action="load-contract",
            path=root,
            detail=str(exc),
        )
        return None


def _typed_config(
    root: Path, contract: Any | None, skipped: list[dict[str, Any]]
) -> WorkflowConfig | None:
    if contract is None:
        return None
    try:
        return WorkflowConfig.from_raw(raw=contract.config, workflow_root=root)
    except Exception as exc:
        _append_skip(
            skipped,
            action="load-config",
            path=contract.source_path,
            detail=str(exc),
        )
        return None


def _doctor_check_from_validation(check: dict[str, Any]) -> dict[str, Any]:
    return _doctor_check(
        str(check.get("name") or "check"),
        str(check.get("status") or "info"),
        str(check.get("detail") or ""),
        items=check.get("items"),
    )


def _repair_checks(
    repairs: list[dict[str, Any]], skipped: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    checks = [
        _doctor_check(
            f"repair:{item['action']}",
            "pass",
            str(item.get("detail") or item.get("path") or ""),
        )
        for item in repairs
    ]
    checks.extend(
        _doctor_check(
            f"repair-skipped:{item['action']}",
            "warn",
            str(item.get("detail") or item.get("path") or ""),
        )
        for item in skipped
    )
    return checks


def _doctor_check(
    code: str, status: str, summary: str, **extra: Any
) -> dict[str, Any]:
    payload = {"code": code, "status": status, "summary": summary}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(check.get("status") or "").lower() for check in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def _append_repair(
    repairs: list[dict[str, Any]],
    *,
    action: str,
    path: Path,
    detail: str,
    before: Any = None,
    after: Any = None,
) -> None:
    repairs.append(
        {
            "action": action,
            "status": "changed",
            "path": str(path),
            "detail": detail,
            **({"before": before} if before is not None else {}),
            **({"after": after} if after is not None else {}),
        }
    )


def _append_skip(
    skipped: list[dict[str, Any]],
    *,
    action: str,
    path: Path,
    detail: str,
) -> None:
    normalized_path = str(path)
    if any(
        item.get("action") == action
        and item.get("path") == normalized_path
        and item.get("detail") == detail
        for item in skipped
    ):
        return
    skipped.append(
        {
            "action": action,
            "status": "skipped",
            "path": normalized_path,
            "detail": detail,
        }
    )


def _engine_store(config: WorkflowConfig) -> EngineStore:
    return EngineStore(
        db_path=runtime_paths(config.workflow_root)["db_path"],
        workflow=config.workflow_name,
    )
