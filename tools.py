import argparse
import importlib.util
import io
import json
import os
import re
import shlex
import sqlite3
import subprocess
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any

from workflows.code_review.paths import (
    resolve_default_workflow_root as resolve_yoyopod_core_workflow_root,
    yoyopod_cli_argv,
)
from workflows.code_review.status import build_status as build_yoyopod_core_status

PLUGIN_DIR = Path(__file__).resolve().parent
DEFAULT_WORKFLOW_ROOT_ENV_VARS = ("DAEDALUS_WORKFLOW_ROOT",)


def resolve_default_workflow_root() -> Path:
    return resolve_yoyopod_core_workflow_root(plugin_dir=PLUGIN_DIR)


DEFAULT_WORKFLOW_ROOT = resolve_default_workflow_root()
DEFAULT_PROJECT_KEY = "yoyopod"
DEFAULT_INSTANCE_ID = "daedalus-plugin"
DEFAULT_SHADOW_SERVICE_INSTANCE_ID = "relay-shadow-service-1"
DEFAULT_SHADOW_SERVICE_NAME = "yoyopod-relay-shadow.service"
DEFAULT_ACTIVE_SERVICE_INSTANCE_ID = "relay-active-service-1"
DEFAULT_ACTIVE_SERVICE_NAME = "yoyopod-relay-active.service"
DEFAULT_SERVICE_INSTANCE_ID = DEFAULT_SHADOW_SERVICE_INSTANCE_ID
DEFAULT_SERVICE_NAME = DEFAULT_SHADOW_SERVICE_NAME
SERVICE_PROFILES = {
    "shadow": {
        "service_name": DEFAULT_SHADOW_SERVICE_NAME,
        "instance_id": DEFAULT_SHADOW_SERVICE_INSTANCE_ID,
        "description": "Daedalus shadow runtime",
        "runtime_command": "run-shadow",
    },
    "active": {
        "service_name": DEFAULT_ACTIVE_SERVICE_NAME,
        "instance_id": DEFAULT_ACTIVE_SERVICE_INSTANCE_ID,
        "description": "Daedalus active runtime",
        "runtime_command": "run-active",
    },
}


class DaedalusCommandError(Exception):
    pass


class DaedalusArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise DaedalusCommandError(f"{message}\n\n{self.format_usage().strip()}")


def _load_daedalus_module(workflow_root: Path):
    module_path = PLUGIN_DIR / "runtime.py"
    spec = importlib.util.spec_from_file_location("daedalus_runtime", module_path)
    if spec is None or spec.loader is None:
        raise DaedalusCommandError(f"unable to load Daedalus runtime from plugin package: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_project_status(workflow_root: Path) -> dict[str, Any]:
    return build_yoyopod_core_status(workflow_root)


def _compatibility_pairs() -> set[tuple[str | None, str | None]]:
    return {
        ("publish_ready_pr", "publish_pr"),
        ("merge_and_promote", "merge_pr"),
        ("run_claude_review", "request_internal_review"),
        ("dispatch_codex_turn", "dispatch_implementation_turn"),
        ("dispatch_codex_turn", "dispatch_repair_handoff"),
        ("push_pr_update", "push_pr_update"),
        ("noop", "noop"),
        ("noop", None),
    }


def _active_lane_from_legacy_status(legacy_status: dict[str, Any]) -> dict[str, Any]:
    active_lane = legacy_status.get("activeLane")
    if isinstance(active_lane, dict):
        return {
            "issue_number": active_lane.get("number"),
            "issue_title": active_lane.get("title"),
            "issue_url": active_lane.get("url"),
        }
    if active_lane is None:
        return {"issue_number": None, "issue_title": None, "issue_url": None}
    return {
        "issue_number": active_lane,
        "issue_title": None,
        "issue_url": None,
    }


def _parse_issue_number_from_text(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value:
        return None
    patterns = [
        r"issue[-_/](\d+)",
        r"/issues/(\d+)",
        r"lane[-_/](\d+)",
        r"yoyopod-issue-(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _legacy_issue_refs(legacy_status: dict[str, Any]) -> dict[str, int | None]:
    active_lane = _active_lane_from_legacy_status(legacy_status)
    implementation = legacy_status.get("implementation") or {}
    ledger = legacy_status.get("ledger") or {}
    open_pr = legacy_status.get("openPr") or {}
    next_action = legacy_status.get("nextAction") or {}
    return {
        "active_lane": active_lane.get("issue_number"),
        "ledger_active_lane": ledger.get("activeLane"),
        "next_action_issue": next_action.get("issueNumber"),
        "implementation_branch_issue": _parse_issue_number_from_text(implementation.get("branch")),
        "implementation_worktree_issue": _parse_issue_number_from_text(implementation.get("worktree")),
        "implementation_session_issue": _parse_issue_number_from_text(implementation.get("sessionName")),
        "open_pr_branch_issue": _parse_issue_number_from_text(open_pr.get("headRefName")),
        "open_pr_title_issue": _parse_issue_number_from_text(open_pr.get("title")),
    }


def _make_check(code: str, status: str, severity: str, summary: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "status": status,
        "severity": severity,
        "summary": summary,
        "details": details or {},
    }


def _systemd_user_dir() -> Path:
    override = os.environ.get("DAEDALUS_SYSTEMD_USER_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".config" / "systemd" / "user").resolve()


def _service_profile(service_mode: str) -> dict[str, str]:
    profile = SERVICE_PROFILES.get(service_mode)
    if profile is None:
        raise DaedalusCommandError(f"unknown service mode: {service_mode}")
    return profile


def _resolve_service_name(*, service_name: str | None = None, service_mode: str = "shadow") -> str:
    return service_name or _service_profile(service_mode)["service_name"]


def _resolve_service_instance_id(*, instance_id: str | None = None, service_mode: str = "shadow") -> str:
    return instance_id or _service_profile(service_mode)["instance_id"]


def _service_unit_path(service_name: str | None = None, service_mode: str = "shadow") -> Path:
    return _systemd_user_dir() / _resolve_service_name(service_name=service_name, service_mode=service_mode)


def _expected_plugin_runtime_path(workflow_root: Path) -> Path:
    return workflow_root / ".hermes" / "plugins" / "hermes-relay" / "runtime.py"



def _render_service_unit(
    *,
    workflow_root: Path,
    project_key: str,
    instance_id: str,
    interval_seconds: int,
    service_mode: str = "shadow",
) -> str:
    profile = _service_profile(service_mode)
    service_path = os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    plugin_runtime = ".hermes/plugins/hermes-relay/runtime.py"
    return "\n".join([
        "[Unit]",
        f"Description={profile['description']}",
        "After=default.target",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={workflow_root}",
        f"Environment=PATH={service_path}",
        "Environment=PYTHONUNBUFFERED=1",
        (
            f"ExecStart=/usr/bin/env python3 {plugin_runtime} {profile['runtime_command']} "
            f"--workflow-root {workflow_root} --project-key {project_key} "
            f"--instance-id {instance_id} --interval-seconds {interval_seconds} --json"
        ),
        "Restart=always",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def _run_systemctl(*args: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "command": ["systemctl", "--user", *args],
    }


def install_supervised_service(
    *,
    workflow_root: Path,
    project_key: str,
    instance_id: str | None,
    interval_seconds: int,
    service_name: str | None = None,
    service_mode: str = "shadow",
) -> dict[str, Any]:
    plugin_runtime_path = _expected_plugin_runtime_path(workflow_root)
    if not plugin_runtime_path.exists():
        raise DaedalusCommandError(
            f"Daedalus plugin runtime not found at {plugin_runtime_path}; install/copy the plugin payload into the workflow root before installing the service"
        )
    resolved_service_name = _resolve_service_name(service_name=service_name, service_mode=service_mode)
    resolved_instance_id = _resolve_service_instance_id(instance_id=instance_id, service_mode=service_mode)
    unit_path = _service_unit_path(service_name=resolved_service_name, service_mode=service_mode)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_text = _render_service_unit(
        workflow_root=workflow_root,
        project_key=project_key,
        instance_id=resolved_instance_id,
        interval_seconds=interval_seconds,
        service_mode=service_mode,
    )
    unit_path.write_text(unit_text, encoding="utf-8")
    reload_result = _run_systemctl("daemon-reload")
    return {
        "installed": reload_result.get("ok", False),
        "service_mode": service_mode,
        "service_name": resolved_service_name,
        "instance_id": resolved_instance_id,
        "unit_path": str(unit_path),
        "daemon_reload": reload_result,
    }


def uninstall_supervised_service(*, service_name: str | None = None, service_mode: str = "shadow") -> dict[str, Any]:
    resolved_service_name = _resolve_service_name(service_name=service_name, service_mode=service_mode)
    unit_path = _service_unit_path(service_name=resolved_service_name, service_mode=service_mode)
    stop_result = _run_systemctl("stop", resolved_service_name)
    disable_result = _run_systemctl("disable", resolved_service_name)
    removed = False
    if unit_path.exists():
        unit_path.unlink()
        removed = True
    reload_result = _run_systemctl("daemon-reload")
    return {
        "uninstalled": removed or stop_result.get("ok") or disable_result.get("ok"),
        "service_mode": service_mode,
        "service_name": resolved_service_name,
        "unit_path": str(unit_path),
        "removed_unit_file": removed,
        "stop": stop_result,
        "disable": disable_result,
        "daemon_reload": reload_result,
    }


def service_control(action: str, *, service_name: str | None = None, service_mode: str = "shadow", extra_args: list[str] | None = None) -> dict[str, Any]:
    extra_args = extra_args or []
    resolved_service_name = _resolve_service_name(service_name=service_name, service_mode=service_mode)
    result = _run_systemctl(action, *extra_args, resolved_service_name)
    return {
        "action": action,
        "service_mode": service_mode,
        "service_name": resolved_service_name,
        **result,
    }


def service_status(*, service_name: str | None = None, service_mode: str = "shadow") -> dict[str, Any]:
    resolved_service_name = _resolve_service_name(service_name=service_name, service_mode=service_mode)
    active = _run_systemctl("is-active", resolved_service_name)
    enabled = _run_systemctl("is-enabled", resolved_service_name)
    show = _run_systemctl(
        "show",
        "--property=Id,Names,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,ExecMainPID,ExecMainStatus,Result",
        resolved_service_name,
    )
    props: dict[str, Any] = {}
    if show.get("ok") and show.get("stdout"):
        for line in show["stdout"].splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                props[key] = value
    return {
        "service_mode": service_mode,
        "service_name": resolved_service_name,
        "active": active.get("stdout") or ("active" if active.get("ok") else "unknown"),
        "enabled": enabled.get("stdout") or ("enabled" if enabled.get("ok") else "unknown"),
        "properties": props,
        "active_check": active,
        "enabled_check": enabled,
        "show": show,
        "unit_path": str(_service_unit_path(service_name=resolved_service_name, service_mode=service_mode)),
        "installed": _service_unit_path(service_name=resolved_service_name, service_mode=service_mode).exists(),
    }


def _expected_supervised_service_mode(runtime_status: dict[str, Any]) -> str | None:
    current_mode = runtime_status.get("current_mode")
    owner_instance_id = runtime_status.get("active_orchestrator_instance_id")
    for service_mode, profile in SERVICE_PROFILES.items():
        if current_mode == service_mode and owner_instance_id == profile.get("instance_id"):
            return service_mode
    return None


def _evaluate_service_supervision(*, runtime_status: dict[str, Any], service_info: dict[str, Any] | None) -> dict[str, Any]:
    expected_service_mode = _expected_supervised_service_mode(runtime_status)
    if not expected_service_mode:
        return {
            "expected_service_mode": None,
            "healthy": True,
            "reasons": [],
            "summary": "Runtime is not using a supervised service profile",
        }
    service_info = service_info or service_status(service_mode=expected_service_mode)
    reasons = []
    if not service_info.get("installed"):
        reasons.append("service-missing")
    if service_info.get("active") != "active":
        reasons.append("service-inactive")
    if service_info.get("enabled") != "enabled":
        reasons.append("service-disabled")
    healthy = not reasons
    return {
        "expected_service_mode": expected_service_mode,
        "healthy": healthy,
        "reasons": reasons,
        "summary": (
            f"{expected_service_mode} Daedalus service supervision healthy"
            if healthy
            else f"{expected_service_mode} Daedalus service supervision unhealthy"
        ),
    }


def service_logs(*, service_name: str | None = None, service_mode: str = "shadow", lines: int = 50) -> dict[str, Any]:
    resolved_service_name = _resolve_service_name(service_name=service_name, service_mode=service_mode)
    completed = subprocess.run(
        ["journalctl", "--user", "-u", resolved_service_name, "-n", str(lines), "--no-pager", "-o", "cat"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "service_mode": service_mode,
        "service_name": resolved_service_name,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "lines": lines,
    }


def build_shadow_report(*, workflow_root: Path, recent_actions_limit: int = 5) -> dict[str, Any]:
    daedalus = _load_daedalus_module(workflow_root)
    runtime_status = daedalus.get_runtime_status(workflow_root=workflow_root)
    if runtime_status.get("runtime_status") == "missing":
        raise DaedalusCommandError("Daedalus runtime is not initialized; run `daedalus start` first")

    legacy_status = _build_project_status(workflow_root)
    now_iso = daedalus._now_iso()
    now_epoch = daedalus._iso_to_epoch(now_iso)
    ingest = daedalus.ingest_legacy_status(
        workflow_root=workflow_root,
        legacy_status=legacy_status,
        now_iso=now_iso,
    )
    lane_id = ingest.get("lane_id")
    legacy_lane = _active_lane_from_legacy_status(legacy_status)
    legacy_action = legacy_status.get("nextAction") or {}
    derived_action = None
    active_lane = None
    lease_info = None
    warnings = []
    service_info = None
    service_health = None
    gate = daedalus.evaluate_active_execution_gate(
        workflow_root=workflow_root,
        legacy_status=legacy_status,
    )
    owner_summary = {
        "primary_owner": gate.get("primary_owner"),
        "relay_primary": gate.get("primary_owner") == daedalus.RELAY_OWNER,
        "active_execution_enabled": (gate.get("execution") or {}).get("active_execution_enabled"),
        "gate_allowed": gate.get("allowed"),
        "gate_reasons": gate.get("reasons") or [],
    }

    expected_service_mode = _expected_supervised_service_mode(runtime_status)
    if expected_service_mode:
        service_info = service_status(service_mode=expected_service_mode)
        service_health = _evaluate_service_supervision(runtime_status=runtime_status, service_info=service_info)
        owner_summary["service_healthy"] = service_health.get("healthy")
        if not service_health.get("healthy"):
            warnings.append(
                f"{expected_service_mode} Daedalus service unhealthy: " + ", ".join(service_health.get("reasons") or [])
            )
    else:
        owner_summary["service_healthy"] = None

    paths = daedalus._runtime_paths(workflow_root)
    conn = sqlite3.connect(paths["db_path"])
    conn.row_factory = sqlite3.Row
    try:
        lease_row = conn.execute(
            """
            SELECT lease_scope, lease_key, owner_instance_id, owner_role, acquired_at, expires_at, released_at, release_reason
            FROM leases
            WHERE lease_scope=? AND lease_key=?
            """,
            (daedalus.RUNTIME_LEASE_SCOPE, daedalus.RUNTIME_LEASE_KEY),
        ).fetchone()
        if lease_row:
            lease = dict(lease_row)
            expires_epoch = daedalus._iso_to_epoch(lease.get("expires_at"))
            heartbeat_epoch = daedalus._iso_to_epoch(runtime_status.get("latest_heartbeat_at"))
            heartbeat_age_seconds = (
                max(0, now_epoch - heartbeat_epoch)
                if now_epoch is not None and heartbeat_epoch is not None
                else None
            )
            expired = bool(
                lease.get("released_at")
                or (expires_epoch is not None and now_epoch is not None and now_epoch > expires_epoch)
            )
            stale_reasons = []
            if lease.get("released_at"):
                stale_reasons.append("lease-released")
            if expires_epoch is not None and now_epoch is not None and now_epoch > expires_epoch:
                stale_reasons.append("lease-expired")
            if heartbeat_age_seconds is not None and heartbeat_age_seconds > 120:
                stale_reasons.append("heartbeat-old")
            if runtime_status.get("active_orchestrator_instance_id") and lease.get("owner_instance_id") != runtime_status.get("active_orchestrator_instance_id"):
                stale_reasons.append("owner-mismatch")
            lease_info = {
                "owner_instance_id": lease.get("owner_instance_id"),
                "owner_role": lease.get("owner_role"),
                "acquired_at": lease.get("acquired_at"),
                "expires_at": lease.get("expires_at"),
                "released_at": lease.get("released_at"),
                "release_reason": lease.get("release_reason"),
                "heartbeat_age_seconds": heartbeat_age_seconds,
                "expired": expired,
                "stale": bool(stale_reasons),
                "stale_reasons": stale_reasons,
            }
            if stale_reasons:
                warnings.append(
                    "stale runtime heartbeat/lease: " + ", ".join(stale_reasons)
                )
        else:
            lease_info = {
                "owner_instance_id": None,
                "owner_role": None,
                "acquired_at": None,
                "expires_at": None,
                "released_at": None,
                "release_reason": None,
                "heartbeat_age_seconds": None,
                "expired": False,
                "stale": True,
                "stale_reasons": ["lease-missing"],
            }
            warnings.append("stale runtime heartbeat/lease: lease-missing")

        if lane_id:
            lane_row = conn.execute("SELECT * FROM lanes WHERE lane_id=?", (lane_id,)).fetchone()
            if lane_row:
                lane = dict(lane_row)
                actor_row = conn.execute(
                    "SELECT * FROM lane_actors WHERE actor_id=?",
                    (lane.get("active_actor_id"),),
                ).fetchone()
                actor = dict(actor_row) if actor_row else {}
                reviews = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM lane_reviews WHERE lane_id=? ORDER BY reviewer_scope, updated_at DESC",
                        (lane_id,),
                    ).fetchall()
                ]
                derived_actions = daedalus.derive_shadow_actions_for_lane(
                    lane_row=lane,
                    reviews=reviews,
                    actor_row=actor,
                )
                derived_action = derived_actions[0] if derived_actions else None
                active_lane = {
                    "lane_id": lane.get("lane_id"),
                    "issue_number": lane.get("issue_number"),
                    "issue_title": lane.get("issue_title") or legacy_lane.get("issue_title"),
                    "issue_url": lane.get("issue_url") or legacy_lane.get("issue_url"),
                    "workflow_state": lane.get("workflow_state"),
                    "review_state": lane.get("review_state"),
                    "merge_state": lane.get("merge_state"),
                    "branch_name": lane.get("branch_name"),
                    "current_head_sha": lane.get("current_head_sha"),
                    "active_pr_number": lane.get("active_pr_number"),
                    "worktree_path": lane.get("worktree_path"),
                    "actor_backend": lane.get("actor_backend"),
                }

        recent_shadow_actions = [
            dict(row)
            for row in conn.execute(
                """
                SELECT a.lane_id,
                       l.issue_number,
                       a.action_type,
                       a.action_reason,
                       a.target_head_sha,
                       a.status,
                       a.requested_at
                FROM lane_actions a
                LEFT JOIN lanes l ON l.lane_id = a.lane_id
                WHERE a.action_mode='shadow'
                ORDER BY a.requested_at DESC
                LIMIT ?
                """,
                (recent_actions_limit,),
            ).fetchall()
        ]
        recent_failures = daedalus.query_recent_failures(
            workflow_root=workflow_root,
            limit=recent_actions_limit,
            unresolved_only=True,
            now_iso=now_iso,
        )
    finally:
        conn.close()

    urgency_rank = {"info": 0, "warning": 1, "critical": 2}
    highest_failure = max(
        recent_failures,
        key=lambda failure: urgency_rank.get(failure.get("urgency") or "info", 0),
        default=None,
    )
    active_failure_summary = {
        "failure_count": len(recent_failures),
        "highest_urgency": (highest_failure or {}).get("urgency"),
        "oldest_failure_age_seconds": max(
            (failure.get("failure_age_seconds") or 0) for failure in recent_failures
        ) if recent_failures else 0,
    }

    relay_action_type = derived_action.get("action_type") if derived_action else None
    compatible = (legacy_action.get("type"), relay_action_type) in _compatibility_pairs()
    if recent_failures:
        warnings.append(
            f"unresolved active failures present [{active_failure_summary.get('highest_urgency')}]: "
            + ", ".join(failure.get("failure_class") or "unknown" for failure in recent_failures[:3])
        )

    return {
        "report_generated_at": now_iso,
        "runtime": runtime_status,
        "heartbeat": lease_info,
        "service": service_info,
        "service_health": service_health,
        "owner_summary": owner_summary,
        "warnings": warnings,
        "active_failure_summary": active_failure_summary,
        "active_lane": active_lane or {
            "lane_id": lane_id,
            "issue_number": legacy_lane.get("issue_number"),
            "issue_title": legacy_lane.get("issue_title"),
            "issue_url": legacy_lane.get("issue_url"),
        },
        "legacy": {
            "status_updated_at": legacy_status.get("updatedAt"),
            "next_action_type": legacy_action.get("type"),
            "reason": legacy_action.get("reason"),
            "head_sha": legacy_action.get("headSha"),
        },
        "relay": {
            "derived_action_type": relay_action_type,
            "reason": derived_action.get("reason") if derived_action else None,
            "target_head_sha": derived_action.get("target_head_sha") if derived_action else None,
            "compatible": compatible,
        },
        "recent_shadow_actions": recent_shadow_actions,
        "recent_failures": recent_failures,
    }


def build_doctor_report(*, workflow_root: Path, recent_actions_limit: int = 5) -> dict[str, Any]:
    shadow_report = build_shadow_report(
        workflow_root=workflow_root,
        recent_actions_limit=recent_actions_limit,
    )
    daedalus = _load_daedalus_module(workflow_root)
    legacy_status = _build_project_status(workflow_root)
    runtime = shadow_report.get("runtime") or {}
    heartbeat = shadow_report.get("heartbeat") or {}
    active_lane = shadow_report.get("active_lane") or {}
    relay_decision = shadow_report.get("relay") or {}
    stale_reasons = heartbeat.get("stale_reasons") or []
    legacy_refs = _legacy_issue_refs(legacy_status)
    recent_failures = shadow_report.get("recent_failures") or []
    failure_summary = shadow_report.get("active_failure_summary") or {}
    service = shadow_report.get("service") or {}
    service_health = shadow_report.get("service_health") or {}
    checks = []

    checks.append(
        _make_check(
            code="missing_lease",
            status="fail" if "lease-missing" in stale_reasons else "pass",
            severity="critical",
            summary=(
                "Runtime lease row missing"
                if "lease-missing" in stale_reasons
                else "Runtime lease row present"
            ),
            details={
                "lease_owner": heartbeat.get("owner_instance_id"),
                "expires_at": heartbeat.get("expires_at"),
            },
        )
    )

    stale_status = "pass"
    stale_severity = "info"
    stale_summary = "Runtime heartbeat and lease look fresh"
    if stale_reasons:
        stale_status = "fail" if any(
            reason in {"lease-expired", "lease-released", "lease-missing"}
            for reason in stale_reasons
        ) else "warn"
        stale_severity = "critical" if stale_status == "fail" else "warning"
        stale_summary = "Runtime heartbeat/lease is stale"
    checks.append(
        _make_check(
            code="stale_runtime",
            status=stale_status,
            severity=stale_severity,
            summary=stale_summary,
            details={
                "latest_heartbeat_at": runtime.get("latest_heartbeat_at"),
                "heartbeat_age_seconds": heartbeat.get("heartbeat_age_seconds"),
                "expires_at": heartbeat.get("expires_at"),
                "stale_reasons": stale_reasons,
            },
        )
    )

    split_brain_reasons = []
    runtime_owner = runtime.get("active_orchestrator_instance_id")
    lease_owner = heartbeat.get("owner_instance_id")
    if runtime_owner and lease_owner and runtime_owner != lease_owner:
        split_brain_reasons.append("runtime-owner-differs-from-lease-owner")
    if runtime.get("runtime_status") == "running" and any(
        reason in {"lease-expired", "lease-released", "lease-missing"}
        for reason in stale_reasons
    ):
        split_brain_reasons.append("running-without-valid-lease")
    split_status = "warn" if split_brain_reasons else "pass"
    split_severity = "critical" if split_brain_reasons and runtime.get("current_mode") == "active" else "warning"
    checks.append(
        _make_check(
            code="split_brain_risk",
            status=split_status,
            severity=split_severity if split_brain_reasons else "info",
            summary=(
                "Split-brain risk detected"
                if split_brain_reasons
                else "No split-brain risk detected from runtime/lease ownership"
            ),
            details={
                "runtime_owner": runtime_owner,
                "lease_owner": lease_owner,
                "reasons": split_brain_reasons,
                "mode": runtime.get("current_mode"),
            },
        )
    )

    consistency_values = {k: v for k, v in legacy_refs.items() if v is not None}
    unique_issue_numbers = sorted(set(consistency_values.values()))
    inconsistency_reasons = []
    if len(unique_issue_numbers) > 1:
        inconsistency_reasons.append("legacy-status-issue-number-mismatch")
    relay_issue_number = active_lane.get("issue_number")
    active_issue_number = legacy_refs.get("active_lane")
    if relay_issue_number is not None and active_issue_number is not None and relay_issue_number != active_issue_number:
        inconsistency_reasons.append("relay-vs-legacy-active-lane-mismatch")
    checks.append(
        _make_check(
            code="active_lane_consistency",
            status="warn" if inconsistency_reasons else "pass",
            severity="warning" if inconsistency_reasons else "info",
            summary=(
                "Active lane references are inconsistent"
                if inconsistency_reasons
                else "Active lane references are internally consistent"
            ),
            details={
                "references": legacy_refs,
                "unique_issue_numbers": unique_issue_numbers,
                "relay_issue_number": relay_issue_number,
                "reasons": inconsistency_reasons,
            },
        )
    )

    checks.append(
        _make_check(
            code="shadow_parity",
            status="pass" if relay_decision.get("compatible") else "warn",
            severity="warning" if not relay_decision.get("compatible") else "info",
            summary=(
                "Daedalus shadow decision matches legacy semantics"
                if relay_decision.get("compatible")
                else "Daedalus shadow decision disagrees with legacy next action"
            ),
            details={
                "legacy_next_action": shadow_report.get("legacy", {}).get("next_action_type"),
                "relay_next_action": relay_decision.get("derived_action_type"),
                "legacy_reason": shadow_report.get("legacy", {}).get("reason"),
                "relay_reason": relay_decision.get("reason"),
            },
        )
    )

    service_check_status = "pass"
    service_check_severity = "info"
    service_check_summary = service_health.get("summary") or "Runtime is not using a supervised service profile"
    if service_health.get("expected_service_mode") and not service_health.get("healthy"):
        service_check_status = "fail"
        service_check_severity = "critical"
    checks.append(
        _make_check(
            code="service_supervision",
            status=service_check_status,
            severity=service_check_severity,
            summary=service_check_summary,
            details={
                "expected_service_mode": service_health.get("expected_service_mode"),
                "healthy": service_health.get("healthy"),
                "reasons": service_health.get("reasons") or [],
                "service_name": service.get("service_name"),
                "installed": service.get("installed"),
                "enabled": service.get("enabled"),
                "active": service.get("active"),
            },
        )
    )

    active_lane_id = active_lane.get("lane_id")
    stuck_dispatched_actions = daedalus.query_stuck_dispatched_actions(
        workflow_root=workflow_root,
        lane_id=active_lane_id,
        now_iso=shadow_report.get("report_generated_at"),
        limit=10,
    ) if active_lane_id else []

    checks.append(
        _make_check(
            code="stuck_dispatched_actions",
            status="fail" if stuck_dispatched_actions else "pass",
            severity="critical" if stuck_dispatched_actions else "info",
            summary=(
                "Stuck dispatched actions require the new dispatcher_lost reaper"
                if stuck_dispatched_actions
                else "No stuck dispatched actions detected"
            ),
            details={
                "lane_id": active_lane_id,
                "timeout_seconds": daedalus.DISPATCHED_ACTION_TIMEOUT_SECONDS,
                "count": len(stuck_dispatched_actions),
                "actions": [
                    {
                        "action_id": action.get("action_id"),
                        "action_type": action.get("action_type"),
                        "dispatched_at": action.get("dispatched_at"),
                        "dispatched_age_seconds": action.get("dispatched_age_seconds"),
                        "retry_count": action.get("retry_count"),
                        "recovery_attempt_count": action.get("recovery_attempt_count"),
                    }
                    for action in stuck_dispatched_actions
                ],
            },
        )
    )

    highest_failure_urgency = failure_summary.get("highest_urgency")
    if not recent_failures:
        failure_status = "pass"
        failure_severity = "info"
        failure_summary_text = "No unresolved active execution failures recorded"
    elif highest_failure_urgency == "critical":
        failure_status = "fail"
        failure_severity = "critical"
        failure_summary_text = "Critical unresolved active execution failures detected"
    else:
        failure_status = "warn"
        failure_severity = "warning"
        failure_summary_text = "Active execution failures exist but bounded recovery is still in progress"
    checks.append(
        _make_check(
            code="active_execution_failures",
            status=failure_status,
            severity=failure_severity,
            summary=failure_summary_text,
            details={
                "failure_count": len(recent_failures),
                "highest_urgency": highest_failure_urgency,
                "oldest_failure_age_seconds": failure_summary.get("oldest_failure_age_seconds"),
                "failures": [
                    {
                        "failure_id": failure.get("failure_id"),
                        "failure_class": failure.get("failure_class"),
                        "lane_id": failure.get("lane_id"),
                        "issue_number": failure.get("issue_number"),
                        "detected_at": failure.get("detected_at"),
                        "failure_age_seconds": failure.get("failure_age_seconds"),
                        "urgency": failure.get("urgency"),
                        "analyst_status": failure.get("analyst_status"),
                        "recommended_action": failure.get("analyst_recommended_action"),
                        "confidence": failure.get("analyst_confidence"),
                        "root_cause": failure.get("root_cause"),
                        "recovery_state": failure.get("recovery_state"),
                        "recovery_action_type": failure.get("recovery_action_type"),
                        "recovery_action_status": failure.get("recovery_action_status"),
                        "summary": failure.get("analyst_summary"),
                    }
                    for failure in recent_failures
                ],
            },
        )
    )

    overall_status = "healthy"
    if any(check["status"] == "fail" and check["severity"] == "critical" for check in checks):
        overall_status = "critical"
    elif any(check["status"] != "pass" for check in checks):
        overall_status = "warning"

    return {
        "report_generated_at": shadow_report.get("report_generated_at"),
        "overall_status": overall_status,
        "checks": checks,
        "runtime": runtime,
        "heartbeat": heartbeat,
        "owner_summary": shadow_report.get("owner_summary"),
        "active_lane": active_lane,
        "legacy": shadow_report.get("legacy"),
        "relay": relay_decision,
        "recent_shadow_actions": shadow_report.get("recent_shadow_actions"),
        "recent_failures": recent_failures,
    }


def cmd_migrate_filesystem(args, parser) -> str:
    """Run the filesystem migrator for the given workflow root.

    Operator-explicit invocation. init_daedalus_db also calls the
    migrator transparently on startup; this CLI is for manual
    operator runs (e.g. during cutover or when investigating drift).
    """
    try:
        from migration import migrate_filesystem_state
    except ImportError:
        path = PLUGIN_DIR / "migration.py"
        spec = importlib.util.spec_from_file_location("daedalus_migration_for_cli", path)
        if spec is None or spec.loader is None:
            raise DaedalusCommandError(f"unable to load migration module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        migrate_filesystem_state = module.migrate_filesystem_state

    workflow_root = args.workflow_root
    descriptions = migrate_filesystem_state(workflow_root)
    if not descriptions:
        return f"no migration needed (workflow_root={workflow_root})"
    lines = [f"migrated filesystem state under {workflow_root}:"]
    lines.extend(f"  - {d}" for d in descriptions)
    return "\n".join(lines)


def configure_subcommands(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    sub = parser.add_subparsers(dest="daedalus_command")
    sub.required = True

    init_cmd = sub.add_parser("init", help="Initialize Daedalus DB and filesystem paths.")
    init_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    init_cmd.add_argument("--project-key", default=DEFAULT_PROJECT_KEY)
    init_cmd.add_argument("--json", action="store_true")
    init_cmd.set_defaults(func=run_cli_command)

    start_cmd = sub.add_parser("start", help="Bootstrap Daedalus runtime and acquire runtime lease.")
    start_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    start_cmd.add_argument("--project-key", default=DEFAULT_PROJECT_KEY)
    start_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    start_cmd.add_argument("--mode", default="shadow", choices=["shadow", "active", "maintenance"])
    start_cmd.add_argument("--json", action="store_true")
    start_cmd.set_defaults(func=run_cli_command)

    status_cmd = sub.add_parser("status", help="Show Daedalus runtime status.")
    status_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.set_defaults(func=run_cli_command)

    report_cmd = sub.add_parser("shadow-report", help="Summarize the live legacy lane, Daedalus shadow decision, compatibility, and recent shadow actions.")
    report_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    report_cmd.add_argument("--recent-actions-limit", type=int, default=5)
    report_cmd.add_argument("--json", action="store_true")
    report_cmd.set_defaults(func=run_cli_command)

    doctor_cmd = sub.add_parser("doctor", help="Diagnose Daedalus runtime freshness, lease ownership, shadow parity, and active-lane consistency.")
    doctor_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    doctor_cmd.add_argument("--recent-actions-limit", type=int, default=5)
    doctor_cmd.add_argument("--json", action="store_true")
    doctor_cmd.set_defaults(func=run_cli_command)

    service_install_cmd = sub.add_parser("service-install", help="Install the supervised Daedalus systemd user service.")
    service_install_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    service_install_cmd.add_argument("--project-key", default=DEFAULT_PROJECT_KEY)
    service_install_cmd.add_argument("--instance-id")
    service_install_cmd.add_argument("--interval-seconds", type=int, default=30)
    service_install_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_install_cmd.add_argument("--service-name")
    service_install_cmd.add_argument("--json", action="store_true")
    service_install_cmd.set_defaults(func=run_cli_command)

    service_uninstall_cmd = sub.add_parser("service-uninstall", help="Remove the supervised Daedalus systemd user service.")
    service_uninstall_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_uninstall_cmd.add_argument("--service-name")
    service_uninstall_cmd.add_argument("--json", action="store_true")
    service_uninstall_cmd.set_defaults(func=run_cli_command)

    service_start_cmd = sub.add_parser("service-start", help="Start the supervised Daedalus systemd user service.")
    service_start_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_start_cmd.add_argument("--service-name")
    service_start_cmd.add_argument("--json", action="store_true")
    service_start_cmd.set_defaults(func=run_cli_command)

    service_stop_cmd = sub.add_parser("service-stop", help="Stop the supervised Daedalus systemd user service.")
    service_stop_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_stop_cmd.add_argument("--service-name")
    service_stop_cmd.add_argument("--json", action="store_true")
    service_stop_cmd.set_defaults(func=run_cli_command)

    service_restart_cmd = sub.add_parser("service-restart", help="Restart the supervised Daedalus systemd user service.")
    service_restart_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_restart_cmd.add_argument("--service-name")
    service_restart_cmd.add_argument("--json", action="store_true")
    service_restart_cmd.set_defaults(func=run_cli_command)

    service_enable_cmd = sub.add_parser("service-enable", help="Enable the supervised Daedalus systemd user service.")
    service_enable_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_enable_cmd.add_argument("--service-name")
    service_enable_cmd.add_argument("--json", action="store_true")
    service_enable_cmd.set_defaults(func=run_cli_command)

    service_disable_cmd = sub.add_parser("service-disable", help="Disable the supervised Daedalus systemd user service.")
    service_disable_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_disable_cmd.add_argument("--service-name")
    service_disable_cmd.add_argument("--json", action="store_true")
    service_disable_cmd.set_defaults(func=run_cli_command)

    service_status_cmd = sub.add_parser("service-status", help="Show supervised Daedalus systemd user service status.")
    service_status_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_status_cmd.add_argument("--service-name")
    service_status_cmd.add_argument("--json", action="store_true")
    service_status_cmd.set_defaults(func=run_cli_command)

    service_logs_cmd = sub.add_parser("service-logs", help="Show recent logs for the supervised Daedalus systemd user service.")
    service_logs_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_logs_cmd.add_argument("--service-name")
    service_logs_cmd.add_argument("--lines", type=int, default=50)
    service_logs_cmd.add_argument("--json", action="store_true")
    service_logs_cmd.set_defaults(func=run_cli_command)

    ingest_cmd = sub.add_parser("ingest-live", help="Ingest current legacy YoYoPod workflow status into Daedalus shadow state.")
    ingest_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    ingest_cmd.add_argument("--json", action="store_true")
    ingest_cmd.set_defaults(func=run_cli_command)

    heartbeat_cmd = sub.add_parser("heartbeat", help="Refresh Daedalus runtime lease and heartbeat timestamp.")
    heartbeat_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    heartbeat_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    heartbeat_cmd.add_argument("--ttl-seconds", type=int, default=60)
    heartbeat_cmd.add_argument("--json", action="store_true")
    heartbeat_cmd.set_defaults(func=run_cli_command)

    iterate_cmd = sub.add_parser("iterate-shadow", help="Run one shadow-mode loop iteration.")
    iterate_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    iterate_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    iterate_cmd.add_argument("--json", action="store_true")
    iterate_cmd.set_defaults(func=run_cli_command)

    run_cmd = sub.add_parser("run-shadow", help="Run the shadow-mode loop shell for one or more iterations.")
    run_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    run_cmd.add_argument("--project-key", default=DEFAULT_PROJECT_KEY)
    run_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    run_cmd.add_argument("--interval-seconds", type=int, default=30)
    run_cmd.add_argument("--max-iterations", type=int)
    run_cmd.add_argument("--json", action="store_true")
    run_cmd.set_defaults(func=run_cli_command)

    active_gate_status_cmd = sub.add_parser("active-gate-status", help="Show Daedalus active-execution gate state.")
    active_gate_status_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    active_gate_status_cmd.add_argument("--json", action="store_true")
    active_gate_status_cmd.set_defaults(func=run_cli_command)

    set_active_execution_cmd = sub.add_parser("set-active-execution", help="Enable or disable Daedalus active execution.")
    set_active_execution_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    set_active_execution_cmd.add_argument("--enabled", required=True, choices=["true", "false"])
    set_active_execution_cmd.add_argument("--json", action="store_true")
    set_active_execution_cmd.set_defaults(func=run_cli_command)

    iterate_active_cmd = sub.add_parser("iterate-active", help="Run one guarded active-mode loop iteration.")
    iterate_active_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    iterate_active_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    iterate_active_cmd.add_argument("--json", action="store_true")
    iterate_active_cmd.set_defaults(func=run_cli_command)

    run_active_cmd = sub.add_parser("run-active", help="Run the guarded active-mode loop shell for one or more iterations.")
    run_active_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    run_active_cmd.add_argument("--project-key", default=DEFAULT_PROJECT_KEY)
    run_active_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    run_active_cmd.add_argument("--interval-seconds", type=int, default=30)
    run_active_cmd.add_argument("--max-iterations", type=int)
    run_active_cmd.add_argument("--json", action="store_true")
    run_active_cmd.set_defaults(func=run_cli_command)

    request_active_cmd = sub.add_parser("request-active-actions", help="Derive and persist active requested actions for one lane.")
    request_active_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    request_active_cmd.add_argument("--lane-id", required=True)
    request_active_cmd.add_argument("--json", action="store_true")
    request_active_cmd.set_defaults(func=run_cli_command)

    execute_action_cmd = sub.add_parser("execute-action", help="Execute one active requested action by action id.")
    execute_action_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    execute_action_cmd.add_argument("--action-id", required=True)
    execute_action_cmd.add_argument("--json", action="store_true")
    execute_action_cmd.set_defaults(func=run_cli_command)

    analyze_failure_cmd = sub.add_parser("analyze-failure", help="Run bounded failure analysis for a recorded failure id.")
    analyze_failure_cmd.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    analyze_failure_cmd.add_argument("--failure-id", required=True)
    analyze_failure_cmd.add_argument("--json", action="store_true")
    analyze_failure_cmd.set_defaults(func=run_cli_command)

    migrate_fs_cmd = sub.add_parser(
        "migrate-filesystem",
        help="Migrate relay-era filesystem paths to daedalus paths.",
    )
    migrate_fs_cmd.add_argument(
        "--workflow-root",
        type=Path,
        default=DEFAULT_WORKFLOW_ROOT,
        help="Workflow root to migrate (default: %(default)s)",
    )
    migrate_fs_cmd.set_defaults(handler=cmd_migrate_filesystem, func=run_cli_command)

    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = DaedalusArgumentParser(prog="daedalus", description="Daedalus operator control surface.")
    return configure_subcommands(parser)


def _run_wrapper_json_command(*, workflow_root: Path, command: str) -> dict[str, Any]:
    """Run a YoYoPod workflow CLI command via the plugin-side entrypoint."""
    argv = yoyopod_cli_argv(workflow_root, *shlex.split(command))
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=workflow_root,
        check=False,
    )
    if completed.returncode != 0:
        raise DaedalusCommandError(
            completed.stderr.strip() or completed.stdout.strip() or f"wrapper command failed: {command}"
        )
    return json.loads(completed.stdout)


def _record_operator_command_event(*, workflow_root: Path, args: argparse.Namespace) -> None:
    daedalus = _load_daedalus_module(workflow_root)
    now_iso = daedalus._now_iso()
    arguments_json = {}
    for key, value in vars(args).items():
        if key in {"func", "json", "_command_source"}:
            continue
        if isinstance(value, Path):
            arguments_json[key] = str(value)
        else:
            arguments_json[key] = value
    daedalus.append_daedalus_event(
        event_log_path=daedalus._runtime_paths(workflow_root)["event_log_path"],
        event={
            "event_id": f"evt:operator_command_received:{args.daedalus_command}:{now_iso}",
            "event_type": "operator_command_received",
            "event_version": 1,
            "created_at": now_iso,
            "producer": "Workflow_Orchestrator",
            "project_key": "yoyopod",
            "lane_id": None,
            "issue_number": None,
            "head_sha": None,
            "causal_event_id": None,
            "causal_action_id": None,
            "dedupe_key": f"operator_command_received:{args.daedalus_command}:{now_iso}",
            "payload": {
                "command_name": args.daedalus_command,
                "command_source": getattr(args, "_command_source", None) or "cli",
                "operator_identity": os.environ.get("USER"),
                "arguments_json": arguments_json,
            },
        },
    )


def execute_namespace(args: argparse.Namespace) -> dict[str, Any]:
    workflow_root = Path(args.workflow_root).resolve() if hasattr(args, "workflow_root") else None
    if workflow_root is not None and getattr(args, "daedalus_command", None):
        _record_operator_command_event(workflow_root=workflow_root, args=args)
    daedalus = _load_daedalus_module(workflow_root) if workflow_root is not None else None
    paths = daedalus._runtime_paths(workflow_root) if daedalus is not None else None

    if args.daedalus_command == "init":
        return daedalus.init_daedalus_db(workflow_root=workflow_root, project_key=args.project_key)
    if args.daedalus_command == "start":
        return daedalus.bootstrap_runtime(
            workflow_root=workflow_root,
            project_key=args.project_key,
            instance_id=args.instance_id,
            mode=args.mode,
        )
    if args.daedalus_command == "status":
        return daedalus.get_runtime_status(workflow_root=workflow_root)
    if args.daedalus_command == "shadow-report":
        return build_shadow_report(
            workflow_root=workflow_root,
            recent_actions_limit=args.recent_actions_limit,
        )
    if args.daedalus_command == "doctor":
        return build_doctor_report(
            workflow_root=workflow_root,
            recent_actions_limit=args.recent_actions_limit,
        )
    if args.daedalus_command == "service-install":
        return install_supervised_service(
            workflow_root=workflow_root,
            project_key=args.project_key,
            instance_id=args.instance_id,
            interval_seconds=args.interval_seconds,
            service_name=args.service_name,
            service_mode=args.service_mode,
        )
    if args.daedalus_command == "service-uninstall":
        return uninstall_supervised_service(service_name=args.service_name, service_mode=args.service_mode)
    if args.daedalus_command == "service-start":
        return service_control("start", service_name=args.service_name, service_mode=args.service_mode)
    if args.daedalus_command == "service-stop":
        return service_control("stop", service_name=args.service_name, service_mode=args.service_mode)
    if args.daedalus_command == "service-restart":
        return service_control("restart", service_name=args.service_name, service_mode=args.service_mode)
    if args.daedalus_command == "service-enable":
        return service_control("enable", service_name=args.service_name, service_mode=args.service_mode)
    if args.daedalus_command == "service-disable":
        return service_control("disable", service_name=args.service_name, service_mode=args.service_mode)
    if args.daedalus_command == "service-status":
        return service_status(service_name=args.service_name, service_mode=args.service_mode)
    if args.daedalus_command == "service-logs":
        return service_logs(service_name=args.service_name, service_mode=args.service_mode, lines=args.lines)
    if args.daedalus_command == "ingest-live":
        return daedalus.ingest_live_legacy_status(workflow_root=workflow_root)
    if args.daedalus_command == "heartbeat":
        return daedalus.refresh_runtime_lease(
            workflow_root=workflow_root,
            instance_id=args.instance_id,
            ttl_seconds=args.ttl_seconds,
        )
    if args.daedalus_command == "iterate-shadow":
        return daedalus.run_shadow_iteration(
            workflow_root=workflow_root,
            instance_id=args.instance_id,
        )
    if args.daedalus_command == "run-shadow":
        return daedalus.run_shadow_loop(
            workflow_root=workflow_root,
            project_key=args.project_key,
            instance_id=args.instance_id,
            interval_seconds=args.interval_seconds,
            max_iterations=args.max_iterations,
        )
    if args.daedalus_command == "active-gate-status":
        legacy_status = _run_wrapper_json_command(workflow_root=workflow_root, command="status --json")
        return daedalus.evaluate_active_execution_gate(
            workflow_root=workflow_root,
            legacy_status=legacy_status,
        )
    if args.daedalus_command == "set-active-execution":
        daedalus.set_execution_control(
            workflow_root=workflow_root,
            active_execution_enabled=(args.enabled == "true"),
            metadata={"source": "relay-control", "enabled": args.enabled},
        )
        legacy_status = _run_wrapper_json_command(workflow_root=workflow_root, command="status --json")
        return {
            "requested_enabled": (args.enabled == "true"),
            "gate": daedalus.evaluate_active_execution_gate(workflow_root=workflow_root, legacy_status=legacy_status),
        }
    if args.daedalus_command == "iterate-active":
        return daedalus.run_active_iteration(
            workflow_root=workflow_root,
            instance_id=args.instance_id,
        )
    if args.daedalus_command == "run-active":
        return daedalus.run_active_loop(
            workflow_root=workflow_root,
            project_key=args.project_key,
            instance_id=args.instance_id,
            interval_seconds=args.interval_seconds,
            max_iterations=args.max_iterations,
        )
    if args.daedalus_command == "request-active-actions":
        return daedalus.request_active_actions_for_lane(
            workflow_root=workflow_root,
            lane_id=args.lane_id,
        )
    if args.daedalus_command == "execute-action":
        return daedalus.execute_requested_action(
            workflow_root=workflow_root,
            action_id=args.action_id,
        )
    if args.daedalus_command == "analyze-failure":
        return daedalus.analyze_failure(
            workflow_root=workflow_root,
            failure_id=args.failure_id,
        )
    raise DaedalusCommandError(f"unknown daedalus command: {args.daedalus_command}")


def render_result(command: str, result: dict[str, Any], *, json_output: bool) -> str:
    if json_output:
        return json.dumps(result, indent=2, sort_keys=True)
    if command == "init":
        return f"initialized db={result.get('db_path')} project={result.get('project_key')}"
    if command == "start":
        return (
            f"runtime={result.get('runtime_status')} instance={result.get('instance_id')} "
            f"mode={result.get('mode')}"
        )
    if command == "status":
        return (
            f"runtime={result.get('runtime_status')} mode={result.get('current_mode')} "
            f"owner={result.get('active_orchestrator_instance_id')} lanes={result.get('lane_count')}"
        )
    if command == "shadow-report":
        runtime = result.get("runtime") or {}
        heartbeat = result.get("heartbeat") or {}
        service = result.get("service") or {}
        owner_summary = result.get("owner_summary") or {}
        lane = result.get("active_lane") or {}
        legacy = result.get("legacy") or {}
        relay = result.get("relay") or {}
        recent_actions = result.get("recent_shadow_actions") or []
        warnings = result.get("warnings") or []
        lines = [
            "shadow-report",
            f"runtime: {runtime.get('runtime_status')} mode={runtime.get('current_mode')} owner={runtime.get('active_orchestrator_instance_id')}",
            f"heartbeat: {runtime.get('latest_heartbeat_at')} age={heartbeat.get('heartbeat_age_seconds')}s expires={heartbeat.get('expires_at')}",
        ]
        if service:
            lines.append(
                f"service: {service.get('service_mode')} installed={service.get('installed')} enabled={service.get('enabled')} active={service.get('active')}"
            )
        if owner_summary:
            lines.append(
                f"owner summary: primary={owner_summary.get('primary_owner')} relay_primary={owner_summary.get('relay_primary')} "
                f"active_execution_enabled={owner_summary.get('active_execution_enabled')} gate_allowed={owner_summary.get('gate_allowed')}"
            )
        lines.extend([
            f"live lane: issue={lane.get('issue_number')} lane={lane.get('lane_id')} state={lane.get('workflow_state')}/{lane.get('review_state')}/{lane.get('merge_state')}",
            f"legacy next: {legacy.get('next_action_type')} reason={legacy.get('reason')}",
            f"relay next: {relay.get('derived_action_type')} reason={relay.get('reason')}",
            f"compatible: {relay.get('compatible')}",
        ])
        if warnings:
            lines.append("warnings:")
            for warning in warnings:
                lines.append(f"- {warning}")
        if recent_actions:
            lines.append("recent shadow actions:")
            for action in recent_actions:
                lines.append(
                    f"- {action.get('requested_at')} issue={action.get('issue_number')} lane={action.get('lane_id')} {action.get('action_type')} status={action.get('status')}"
                )
        recent_failures = result.get("recent_failures") or []
        if recent_failures:
            lines.append("recent failures:")
            for failure in recent_failures:
                lines.append(
                    f"- {failure.get('detected_at')} issue={failure.get('issue_number')} lane={failure.get('lane_id')} "
                    f"class={failure.get('failure_class')} recommended={failure.get('analyst_recommended_action')} "
                    f"confidence={failure.get('analyst_confidence')} recovery={failure.get('recovery_state')} "
                    f"urgency={failure.get('urgency')} age={failure.get('failure_age_seconds')}s"
                )
        return "\n".join(lines)
    if command == "doctor":
        lines = [
            "daedalus-doctor",
            f"overall: {result.get('overall_status')}",
        ]
        for check in result.get("checks") or []:
            lines.append(
                f"- {check.get('status').upper()} {check.get('code')}: {check.get('summary')}"
            )
            if check.get("code") == "active_execution_failures":
                for failure in ((check.get("details") or {}).get("failures") or []):
                    lines.append(
                        f"  failure={failure.get('failure_id')} class={failure.get('failure_class')} "
                        f"recommended={failure.get('recommended_action')} confidence={failure.get('confidence')} "
                        f"recovery={failure.get('recovery_state')} urgency={failure.get('urgency')} age={failure.get('failure_age_seconds')}s"
                    )
        return "\n".join(lines)
    if command == "service-install":
        return f"service installed mode={result.get('service_mode')} unit={result.get('unit_path')} ok={result.get('installed')}"
    if command == "service-uninstall":
        return f"service uninstalled mode={result.get('service_mode')} unit={result.get('unit_path')} ok={result.get('uninstalled')}"
    if command in {"service-start", "service-stop", "service-restart", "service-enable", "service-disable"}:
        return f"{result.get('action')} mode={result.get('service_mode')} {result.get('service_name')} ok={result.get('ok')} stdout={result.get('stdout')} stderr={result.get('stderr')}".strip()
    if command == "service-status":
        props = result.get("properties") or {}
        return (
            f"service={result.get('service_name')} mode={result.get('service_mode')} installed={result.get('installed')} enabled={result.get('enabled')} "
            f"active={result.get('active')} pid={props.get('ExecMainPID')} file={props.get('FragmentPath') or result.get('unit_path')}"
        )
    if command == "service-logs":
        output = result.get("stdout") or result.get("stderr") or ""
        return output if output else f"no logs for {result.get('service_name')}"
    if command == "ingest-live":
        return f"ingested lane={result.get('lane_id')} actor={result.get('actor_id')}"
    if command == "heartbeat":
        return f"heartbeat instance={result.get('instance_id')} at={result.get('heartbeat_at')}"
    if command == "iterate-shadow":
        comparison = result.get("comparison") or {}
        return (
            f"iteration={result.get('iteration_status')} lane={comparison.get('lane_id')} "
            f"legacy={comparison.get('legacy_action_type')} relay={comparison.get('relay_action_type')} "
            f"compatible={comparison.get('compatible')}"
        )
    if command == "run-shadow":
        comparison = ((result.get("last_result") or {}).get("comparison") or {})
        return (
            f"loop={result.get('loop_status')} iterations={result.get('iterations')} "
            f"lane={comparison.get('lane_id')} compatible={comparison.get('compatible')}"
        )
    if command == "active-gate-status":
        execution = result.get("execution") or {}
        return (
            f"allowed={result.get('allowed')} active_execution_enabled={execution.get('active_execution_enabled')} "
            f"reasons={','.join(result.get('reasons') or [])}"
        )
    if command == "set-active-execution":
        gate = result.get("gate") or {}
        execution = gate.get("execution") or {}
        return (
            f"requested_enabled={result.get('requested_enabled')} allowed={gate.get('allowed')} "
            f"active_execution_enabled={execution.get('active_execution_enabled')} reasons={','.join(gate.get('reasons') or [])}"
        )
    if command == "iterate-active":
        executed = result.get("executed_action") or {}
        return (
            f"iteration={result.get('iteration_status')} action={executed.get('action_type')} "
            f"executed={executed.get('executed')}"
        )
    if command == "run-active":
        executed = ((result.get("last_result") or {}).get("executed_action") or {})
        return (
            f"loop={result.get('loop_status')} iterations={result.get('iterations')} "
            f"action={executed.get('action_type')} executed={executed.get('executed')}"
        )
    if command == "request-active-actions":
        if isinstance(result, list):
            first = result[0] if result else {}
            return f"requested={len(result)} action={first.get('action_type')} id={first.get('action_id')}"
        return str(result)
    if command == "execute-action":
        return f"executed={result.get('executed')} action={result.get('action_id')} type={result.get('action_type')}"
    if command == "analyze-failure":
        analysis = result.get("analysis") or {}
        return (
            f"ok={result.get('ok')} failure={result.get('failure_id')} action={result.get('action_id')} "
            f"recommended_action={analysis.get('recommended_action')} confidence={analysis.get('confidence')}"
        )
    return json.dumps(result, sort_keys=True)


def execute_workflow_command(raw_args: str) -> str:
    """Slash command handler for ``/workflow <name> <cmd> [args]``.

    Bare invocation (no args): lists available workflows under ``workflows/``.
    Single arg (workflow name): shows that workflow's ``--help``.
    Full invocation: routes through ``workflows.run_cli`` with
    ``require_workflow=<name>`` so the dispatcher pins the named module
    regardless of what the workflow.yaml declares.
    """
    workflow_root = resolve_default_workflow_root()
    parts = raw_args.strip().split() if raw_args else []

    try:
        from workflows import list_workflows, run_cli
    except ImportError:
        wfpath = PLUGIN_DIR / "workflows" / "__init__.py"
        spec = importlib.util.spec_from_file_location("daedalus_workflows", wfpath)
        if spec is None or spec.loader is None:
            return "daedalus error: unable to load workflows dispatcher"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        list_workflows = module.list_workflows
        run_cli = module.run_cli

    if not parts:
        names = list_workflows()
        return ("available workflows: " + ", ".join(names)) if names else "no workflows installed"

    name, *cmd_args = parts

    try:
        if not cmd_args:
            cmd_args = ["--help"]
        rc = run_cli(workflow_root, cmd_args, require_workflow=name)
        return f"workflow '{name}' exited with status {rc}" if rc != 0 else "ok"
    except Exception as exc:
        return f"daedalus error: {exc}"


def execute_raw_args(raw_args: str) -> str:
    parser = build_parser()
    argv = shlex.split(raw_args) if raw_args.strip() else ["status"]
    stderr_buffer = io.StringIO()
    try:
        with redirect_stderr(stderr_buffer):
            args = parser.parse_args(argv)
        args._command_source = "plugin-command"
        if args.daedalus_command == "migrate-filesystem":
            return cmd_migrate_filesystem(args, parser)
        result = execute_namespace(args)
        return render_result(args.daedalus_command, result, json_output=getattr(args, "json", False))
    except DaedalusCommandError as exc:
        return f"daedalus error: {exc}"
    except SystemExit:
        detail = stderr_buffer.getvalue().strip()
        return f"daedalus error: {detail or parser.format_usage().strip()}"
    except Exception as exc:
        return f"daedalus error: unexpected {type(exc).__name__}: {exc}"


def run_cli_command(args: argparse.Namespace) -> None:
    args._command_source = "cli"
    print(render_result(args.daedalus_command, execute_namespace(args), json_output=getattr(args, "json", False)))


if __name__ == "__main__":
    import sys
    result = execute_raw_args(" ".join(sys.argv[1:]))
    print(result)
    sys.exit(0 if not result.startswith("daedalus error:") else 1)
