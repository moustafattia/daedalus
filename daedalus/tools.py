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

import yaml

from workflows.contract import (
    WorkflowContractError,
    load_workflow_contract,
    load_workflow_contract_file,
    render_workflow_markdown,
    workflow_yaml_path as legacy_workflow_config_path,
    workflow_markdown_path,
)
from workflows.change_delivery.paths import (
    derive_workflow_instance_name,
    plugin_runtime_path,
    project_key_for_workflow_root,
    repo_local_workflow_pointer_path,
    resolve_default_workflow_root as resolve_workflow_root_default,
    workflow_cli_argv,
)
from workflows.change_delivery.status import build_status as build_workflow_status

PLUGIN_DIR = Path(__file__).resolve().parent
DEFAULT_WORKFLOW_ROOT_ENV_VARS = ("DAEDALUS_WORKFLOW_ROOT",)


def resolve_default_workflow_root() -> Path:
    return resolve_workflow_root_default(plugin_dir=PLUGIN_DIR)


DEFAULT_WORKFLOW_ROOT = resolve_default_workflow_root()
DEFAULT_INSTANCE_ID = "daedalus-plugin"

DAEDALUS_TEMPLATE_UNIT_FILENAMES = {
    "active": "daedalus-active@.service",
    "shadow": "daedalus-shadow@.service",
}

DAEDALUS_INSTANCE_ID_FORMAT = "daedalus-{mode}-{workspace}"


def _instance_id_for(*, service_mode: str, workspace: str) -> str:
    return DAEDALUS_INSTANCE_ID_FORMAT.format(mode=service_mode, workspace=workspace)


SERVICE_PROFILES = {
    "shadow": {
        "template_unit": DAEDALUS_TEMPLATE_UNIT_FILENAMES["shadow"],
        "description": "Daedalus shadow orchestrator",
        "runtime_command": "run-shadow",
    },
    "active": {
        "template_unit": DAEDALUS_TEMPLATE_UNIT_FILENAMES["active"],
        "description": "Daedalus active orchestrator",
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
    return build_workflow_status(workflow_root)


def _compatibility_pairs() -> set[tuple[str | None, str | None]]:
    return {
        ("publish_ready_pr", "publish_pr"),
        ("merge_and_promote", "merge_pr"),
        ("run_internal_review", "request_internal_review"),
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


def _resolve_service_name(
    *, service_name: str | None = None, service_mode: str = "shadow", workspace: str
) -> str:
    return service_name or _instance_unit_name(service_mode, workspace)


def _resolve_service_instance_id(
    *, instance_id: str | None = None, service_mode: str = "shadow", workspace: str
) -> str:
    return instance_id or _instance_id_for(service_mode=service_mode, workspace=workspace)


def _service_template_path(*, service_mode: str = "shadow") -> Path:
    return _systemd_user_dir() / _template_unit_filename(service_mode)


def _service_instance_name(*, service_mode: str = "shadow", workspace: str) -> str:
    return _instance_unit_name(service_mode, workspace)


def _expected_plugin_runtime_path(workflow_root: Path) -> Path:
    del workflow_root
    return plugin_runtime_path(plugin_dir=PLUGIN_DIR)



def _render_service_unit(
    *,
    workflow_root: Path,
    project_key: str,
    instance_id: str,
    interval_seconds: int,
    service_mode: str = "shadow",
) -> str:
    return _render_template_unit(mode=service_mode)


def _template_unit_filename(mode: str) -> str:
    if mode not in DAEDALUS_TEMPLATE_UNIT_FILENAMES:
        raise DaedalusCommandError(f"unknown service mode: {mode}")
    return DAEDALUS_TEMPLATE_UNIT_FILENAMES[mode]


def _instance_unit_name(mode: str, workspace: str) -> str:
    template = _template_unit_filename(mode)
    # daedalus-active@.service -> daedalus-active@<workspace>.service
    return template.replace("@.service", f"@{workspace}.service")


def _render_template_unit(*, mode: str) -> str:
    if mode not in DAEDALUS_TEMPLATE_UNIT_FILENAMES:
        raise DaedalusCommandError(f"unknown service mode: {mode}")
    description = f"Daedalus {mode} orchestrator (workspace=%i)"
    runtime_command = f"run-{mode}"
    # PATH is captured at unit-render time and embedded so the runtime can
    # find user-installed CLIs (gh, codex, claude, etc.) under ~/.local/bin.
    # systemd's default user PATH is minimal (/usr/bin:/bin) and would
    # cause FileNotFoundError on those tools at first subprocess call.
    service_path = os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    return "\n".join([
        "[Unit]",
        f"Description={description}",
        "After=default.target",
        "",
        "[Service]",
        "Type=simple",
        "WorkingDirectory=%h/.hermes/workflows/%i",
        f"Environment=PATH={service_path}",
        "Environment=PYTHONUNBUFFERED=1",
        (
            # Use absolute /usr/bin/python3 (system Python 3.11) so we get the
            # pyyaml/jsonschema deps the installer's _check_runtime_deps verified
            # against. /usr/bin/env python3 with a non-empty PATH may resolve
            # to homebrew python or a node-managed python that lacks pyyaml.
            f"ExecStart=/usr/bin/python3 %h/.hermes/plugins/daedalus/runtime.py "
            f"{runtime_command} --workflow-root %h/.hermes/workflows/%i "
            f"--project-key %i --instance-id daedalus-{mode}-%i "
            f"--interval-seconds 30 --json"
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
            f"Daedalus plugin runtime not found at {plugin_runtime_path}; install the plugin into ~/.hermes/plugins/daedalus before installing the service"
        )
    workspace = workflow_root.name
    resolved_service_name = _resolve_service_name(
        service_name=service_name, service_mode=service_mode, workspace=workspace
    )
    resolved_instance_id = _resolve_service_instance_id(
        instance_id=instance_id, service_mode=service_mode, workspace=workspace
    )
    template_path = _service_template_path(service_mode=service_mode)
    template_path.parent.mkdir(parents=True, exist_ok=True)
    unit_text = _render_service_unit(
        workflow_root=workflow_root,
        project_key=project_key,
        instance_id=resolved_instance_id,
        interval_seconds=interval_seconds,
        service_mode=service_mode,
    )
    template_path.write_text(unit_text, encoding="utf-8")
    reload_result = _run_systemctl("daemon-reload")
    return {
        "installed": reload_result.get("ok", False),
        "service_mode": service_mode,
        "service_name": resolved_service_name,
        "instance_id": resolved_instance_id,
        "unit_path": str(template_path),
        "daemon_reload": reload_result,
    }


def _validate_workflow_contract_preflight(workflow_root: Path) -> dict[str, Any]:
    import jsonschema
    from workflows import load_workflow

    contract = load_workflow_contract(workflow_root)
    config = contract.config
    workflow_name = config.get("workflow")
    if not workflow_name:
        raise DaedalusCommandError(
            f"{contract.source_path} is missing top-level `workflow:` field"
        )
    module = load_workflow(str(workflow_name))
    schema = yaml.safe_load(module.CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(config, schema)
    schema_version = int(config.get("schema-version", 1))
    if schema_version not in module.SUPPORTED_SCHEMA_VERSIONS:
        raise DaedalusCommandError(
            f"workflow {workflow_name!r} does not support schema-version={schema_version}; "
            f"supported: {list(module.SUPPORTED_SCHEMA_VERSIONS)}"
        )
    preflight_fn = getattr(module, "run_preflight", None)
    if callable(preflight_fn):
        result = preflight_fn(config)
        if not getattr(result, "ok", True):
            raise DaedalusCommandError(
                f"workflow preflight failed for {workflow_name!r}: "
                f"code={getattr(result, 'error_code', None)} "
                f"detail={getattr(result, 'error_detail', None)}"
            )
        return {
            "ok": True,
            "workflow": workflow_name,
            "schema_version": schema_version,
            "preflight": {
                "ok": True,
                "error_code": getattr(result, "error_code", None),
                "error_detail": getattr(result, "error_detail", None),
            },
            "source_path": str(contract.source_path),
        }
    return {
        "ok": True,
        "workflow": workflow_name,
        "schema_version": schema_version,
        "preflight": {"ok": True, "error_code": None, "error_detail": None},
        "source_path": str(contract.source_path),
    }


def service_up(
    *,
    workflow_root: Path,
    project_key: str,
    instance_id: str | None,
    interval_seconds: int,
    service_name: str | None = None,
    service_mode: str = "active",
) -> dict[str, Any]:
    daedalus = _load_daedalus_module(workflow_root)
    init_result = daedalus.init_daedalus_db(workflow_root=workflow_root, project_key=project_key)
    preflight_result = _validate_workflow_contract_preflight(workflow_root)

    install_result = install_supervised_service(
        workflow_root=workflow_root,
        project_key=project_key,
        instance_id=instance_id,
        interval_seconds=interval_seconds,
        service_name=service_name,
        service_mode=service_mode,
    )
    if not install_result.get("installed"):
        daemon_reload = install_result.get("daemon_reload") or {}
        raise DaedalusCommandError(
            "unable to install systemd service: "
            f"{daemon_reload.get('stderr') or daemon_reload.get('stdout') or 'daemon-reload failed'}"
        )

    enable_result = service_control(
        "enable",
        workflow_root=workflow_root,
        service_name=service_name,
        service_mode=service_mode,
    )
    if not enable_result.get("ok"):
        raise DaedalusCommandError(
            "unable to enable systemd service: "
            f"{enable_result.get('stderr') or enable_result.get('stdout') or enable_result.get('returncode')}"
        )

    start_result = service_control(
        "start",
        workflow_root=workflow_root,
        service_name=service_name,
        service_mode=service_mode,
    )
    if not start_result.get("ok"):
        raise DaedalusCommandError(
            "unable to start systemd service: "
            f"{start_result.get('stderr') or start_result.get('stdout') or start_result.get('returncode')}"
        )

    service_status_result = service_status(
        workflow_root=workflow_root,
        service_name=service_name,
        service_mode=service_mode,
    )
    return {
        "ok": True,
        "workflow_root": str(workflow_root),
        "project_key": project_key,
        "service_mode": service_mode,
        "init": init_result,
        "preflight": preflight_result,
        "service_install": install_result,
        "service_enable": enable_result,
        "service_start": start_result,
        "service_status": service_status_result,
    }


def uninstall_supervised_service(
    *,
    workflow_root: Path,
    service_name: str | None = None,
    service_mode: str = "shadow",
) -> dict[str, Any]:
    workspace = workflow_root.name
    resolved_service_name = _resolve_service_name(
        service_name=service_name, service_mode=service_mode, workspace=workspace
    )
    template_path = _service_template_path(service_mode=service_mode)
    stop_result = _run_systemctl("stop", resolved_service_name)
    disable_result = _run_systemctl("disable", resolved_service_name)
    removed = False
    if template_path.exists():
        template_path.unlink()
        removed = True
    reload_result = _run_systemctl("daemon-reload")
    return {
        "uninstalled": removed or stop_result.get("ok") or disable_result.get("ok"),
        "service_mode": service_mode,
        "service_name": resolved_service_name,
        "unit_path": str(template_path),
        "removed_unit_file": removed,
        "stop": stop_result,
        "disable": disable_result,
        "daemon_reload": reload_result,
    }


def service_control(
    action: str,
    *,
    workflow_root: Path,
    service_name: str | None = None,
    service_mode: str = "shadow",
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    extra_args = extra_args or []
    workspace = workflow_root.name
    resolved_service_name = _resolve_service_name(
        service_name=service_name, service_mode=service_mode, workspace=workspace
    )
    result = _run_systemctl(action, *extra_args, resolved_service_name)
    return {
        "action": action,
        "service_mode": service_mode,
        "service_name": resolved_service_name,
        **result,
    }


def service_status(
    *,
    workflow_root: Path,
    service_name: str | None = None,
    service_mode: str = "shadow",
) -> dict[str, Any]:
    workspace = workflow_root.name
    resolved_service_name = _resolve_service_name(
        service_name=service_name, service_mode=service_mode, workspace=workspace
    )
    template_path = _service_template_path(service_mode=service_mode)
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
        "unit_path": str(template_path),
        "installed": template_path.exists(),
    }


def _expected_supervised_service_mode(
    runtime_status: dict[str, Any], *, workspace: str
) -> str | None:
    current_mode = runtime_status.get("current_mode")
    owner_instance_id = runtime_status.get("active_orchestrator_instance_id")
    for service_mode in SERVICE_PROFILES:
        expected_instance_id = _instance_id_for(service_mode=service_mode, workspace=workspace)
        if current_mode == service_mode and owner_instance_id == expected_instance_id:
            return service_mode
    return None


def _evaluate_service_supervision(
    *,
    runtime_status: dict[str, Any],
    service_info: dict[str, Any] | None,
    workflow_root: Path,
) -> dict[str, Any]:
    workspace = workflow_root.name
    expected_service_mode = _expected_supervised_service_mode(runtime_status, workspace=workspace)
    if not expected_service_mode:
        return {
            "expected_service_mode": None,
            "healthy": True,
            "reasons": [],
            "summary": "Runtime is not using a supervised service profile",
        }
    service_info = service_info or service_status(
        workflow_root=workflow_root, service_mode=expected_service_mode
    )
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


def service_logs(
    *,
    workflow_root: Path,
    service_name: str | None = None,
    service_mode: str = "shadow",
    lines: int = 50,
) -> dict[str, Any]:
    workspace = workflow_root.name
    resolved_service_name = _resolve_service_name(
        service_name=service_name, service_mode=service_mode, workspace=workspace
    )
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

    expected_service_mode = _expected_supervised_service_mode(
        runtime_status, workspace=workflow_root.name
    )
    if expected_service_mode:
        service_info = service_status(
            workflow_root=workflow_root, service_mode=expected_service_mode
        )
        service_health = _evaluate_service_supervision(
            runtime_status=runtime_status,
            service_info=service_info,
            workflow_root=workflow_root,
        )
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


def _lazy_cmd_watch(args, parser):
    """Lazy import so importing tools.py doesn't pull rich into every CLI invocation."""
    try:
        from watch import cmd_watch
    except ImportError:
        path = PLUGIN_DIR / "watch.py"
        spec = importlib.util.spec_from_file_location("daedalus_watch_for_cli", path)
        if spec is None or spec.loader is None:
            raise DaedalusCommandError(f"unable to load watch module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cmd_watch = module.cmd_watch
    return cmd_watch(args, parser)


def _workflow_template_path(workflow_name: str) -> Path:
    templates = {
        "change-delivery": PLUGIN_DIR / "workflows" / "change_delivery" / "workflow.template.md",
    }
    path = templates.get(workflow_name)
    if path is None:
        raise DaedalusCommandError(f"no bundled workflow template for {workflow_name!r}")
    return path


_GITHUB_REMOTE_RE = re.compile(
    r"^(?:git@github\.com:|ssh://git@github\.com/|https?://(?:www\.)?github\.com/)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def _git_stdout(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise DaedalusCommandError(f"`git {' '.join(args)}` failed in {cwd}: {detail}")
    return completed.stdout.strip()


def _discover_git_repo_root(start_path: Path | None) -> Path:
    start = (start_path or Path.cwd()).expanduser().resolve()
    if not start.exists():
        raise DaedalusCommandError(f"repo path does not exist: {start}")
    cwd = start.parent if start.is_file() else start
    try:
        repo_root = _git_stdout("rev-parse", "--show-toplevel", cwd=cwd)
    except DaedalusCommandError as exc:
        raise DaedalusCommandError(
            "bootstrap must run inside a git repository or use --repo-path pointing at one"
        ) from exc
    return Path(repo_root).expanduser().resolve()


def _github_slug_from_remote_url(remote_url: str) -> str:
    match = _GITHUB_REMOTE_RE.match(remote_url.strip())
    if not match:
        raise DaedalusCommandError(
            "unable to derive --github-slug from git origin; use a GitHub remote or pass --github-slug explicitly"
        )
    owner = match.group("owner").strip()
    repo = match.group("repo").strip()
    if not owner or not repo:
        raise DaedalusCommandError(
            "unable to derive --github-slug from git origin; use a GitHub remote or pass --github-slug explicitly"
        )
    return f"{owner}/{repo}"


def bootstrap_workflow_root(
    *,
    repo_path: Path | None,
    workflow_name: str,
    workflow_root: Path | None,
    github_slug: str | None,
    active_lane_label: str,
    engine_owner: str,
    force: bool,
) -> dict[str, Any]:
    repo_root = _discover_git_repo_root(repo_path)
    remote_url = None
    resolved_github_slug = (github_slug or "").strip()
    if not resolved_github_slug:
        remote_url = _git_stdout("remote", "get-url", "origin", cwd=repo_root)
        resolved_github_slug = _github_slug_from_remote_url(remote_url)

    try:
        instance_name = derive_workflow_instance_name(
            github_slug=resolved_github_slug,
            workflow_name=workflow_name,
        )
    except ValueError as exc:
        raise DaedalusCommandError(f"--github-slug {resolved_github_slug!r} is invalid: {exc}") from exc

    resolved_workflow_root = (
        workflow_root.expanduser().resolve()
        if workflow_root is not None
        else (Path.home() / ".hermes" / "workflows" / instance_name).resolve()
    )

    result = scaffold_workflow_root(
        workflow_root=resolved_workflow_root,
        workflow_name=workflow_name,
        repo_path=repo_root,
        github_slug=resolved_github_slug,
        active_lane_label=active_lane_label,
        engine_owner=engine_owner,
        force=force,
    )
    pointer_path = repo_local_workflow_pointer_path(repo_root)
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(str(resolved_workflow_root) + "\n", encoding="utf-8")
    result.update(
        {
            "bootstrap": True,
            "detected_repo_root": str(repo_root),
            "remote_url": remote_url,
            "repo_pointer_path": str(pointer_path),
            "next_edit_path": result["contract_path"],
            "next_command": "hermes daedalus service-up",
        }
    )
    return result


def scaffold_workflow_root(
    *,
    workflow_root: Path,
    workflow_name: str,
    repo_path: Path | None,
    github_slug: str,
    active_lane_label: str,
    engine_owner: str,
    force: bool,
) -> dict[str, Any]:
    root = workflow_root.expanduser().resolve()
    contract_path = workflow_markdown_path(root)
    legacy_config_path = legacy_workflow_config_path(root)
    existing_contract_path = contract_path if contract_path.exists() else legacy_config_path
    if existing_contract_path.exists() and not force:
        raise DaedalusCommandError(
            f"refusing to overwrite existing workflow contract: {existing_contract_path} "
            "(pass --force to replace it)"
        )

    template_path = _workflow_template_path(workflow_name)
    try:
        template_contract = load_workflow_contract_file(template_path)
    except (WorkflowContractError, OSError, UnicodeDecodeError) as exc:
        raise DaedalusCommandError(f"unable to load workflow template {template_path}: {exc}") from exc
    config = dict(template_contract.config)
    workflow_policy = template_contract.prompt_template

    resolved_github_slug = github_slug.strip()
    if not resolved_github_slug:
        raise DaedalusCommandError("--github-slug cannot be blank")
    try:
        resolved_instance_name = derive_workflow_instance_name(
            github_slug=resolved_github_slug,
            workflow_name=workflow_name,
        )
    except ValueError as exc:
        raise DaedalusCommandError(f"--github-slug {resolved_github_slug!r} is invalid: {exc}") from exc
    if root.name != resolved_instance_name:
        expected_root = root.parent / resolved_instance_name
        raise DaedalusCommandError(
            "workflow root directory name must follow <owner>-<repo>-<workflow-type>: "
            f"expected {expected_root} for github-slug={resolved_github_slug!r} "
            f"and workflow={workflow_name!r}"
        )

    resolved_repo_path = repo_path.expanduser() if repo_path is not None else (root / "workspace" / "repo")
    if not resolved_repo_path.is_absolute():
        resolved_repo_path = root / resolved_repo_path
    resolved_repo_path = resolved_repo_path.resolve()

    config["workflow"] = workflow_name
    instance_cfg = config.setdefault("instance", {})
    repository_cfg = config.setdefault("repository", {})
    triggers_cfg = config.setdefault("triggers", {})
    lane_selector_cfg = triggers_cfg.setdefault("lane-selector", {})

    instance_cfg["name"] = resolved_instance_name
    instance_cfg["engine-owner"] = engine_owner
    repository_cfg["local-path"] = str(resolved_repo_path)
    repository_cfg["github-slug"] = resolved_github_slug
    repository_cfg["active-lane-label"] = active_lane_label
    lane_selector_cfg["label"] = active_lane_label

    created_dirs = [
        root / "config",
        root / "memory",
        root / "state" / "sessions",
        root / "runtime" / "logs",
        root / "runtime" / "memory",
        root / "runtime" / "state" / "daedalus",
        root / "workspace",
        resolved_repo_path,
        root / "config",
    ]
    for path in created_dirs:
        path.mkdir(parents=True, exist_ok=True)

    contract_path.write_text(
        render_workflow_markdown(config=config, prompt_template=workflow_policy),
        encoding="utf-8",
    )
    if force and legacy_config_path.exists():
        legacy_config_path.unlink()
    return {
        "ok": True,
        "workflow_root": str(root),
        "contract_path": str(contract_path),
        "config_path": str(contract_path),
        "workflow": workflow_name,
        "instance_name": resolved_instance_name,
        "engine_owner": engine_owner,
        "repo_path": str(resolved_repo_path),
        "github_slug": resolved_github_slug,
        "active_lane_label": active_lane_label,
        "force": force,
    }


def cmd_scaffold_workflow(args, parser) -> str:
    result = scaffold_workflow_root(
        workflow_root=Path(args.workflow_root),
        workflow_name=args.workflow,
        repo_path=Path(args.repo_path) if args.repo_path else None,
        github_slug=args.github_slug,
        active_lane_label=args.active_lane_label,
        engine_owner=args.engine_owner,
        force=args.force,
    )
    if getattr(args, "json", False):
        return json.dumps(result, indent=2, sort_keys=True)
    lines = [
        f"scaffolded workflow root: {result['workflow_root']}",
        f"contract: {result['contract_path']}",
        f"workflow: {result['workflow']}",
        f"instance: {result['instance_name']}",
        f"repo-path: {result['repo_path']}",
        f"github-slug: {result['github_slug']}",
    ]
    return "\n".join(lines)


def cmd_bootstrap_workflow(args, parser) -> str:
    result = bootstrap_workflow_root(
        repo_path=Path(args.repo_path) if args.repo_path else None,
        workflow_name=args.workflow,
        workflow_root=Path(args.workflow_root) if args.workflow_root else None,
        github_slug=args.github_slug,
        active_lane_label=args.active_lane_label,
        engine_owner=args.engine_owner,
        force=args.force,
    )
    if getattr(args, "json", False):
        return json.dumps(result, indent=2, sort_keys=True)
    lines = [
        f"bootstrapped workflow root: {result['workflow_root']}",
        f"contract: {result['contract_path']}",
        f"repo-path: {result['repo_path']}",
        f"github-slug: {result['github_slug']}",
        f"repo pointer: {result['repo_pointer_path']}",
        f"edit next: {result['next_edit_path']}",
        f"then run: {result['next_command']}",
    ]
    if result.get("remote_url"):
        lines.insert(4, f"origin: {result['remote_url']}")
    return "\n".join(lines)


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


def cmd_migrate_systemd(args, parser) -> str:
    """Migrate relay-era systemd units to Daedalus template units.

    Operator-explicit. Removes old workflow-specific relay unit files
    (tolerant of missing units), installs new daedalus
    template units, runs daemon-reload.
    """
    import subprocess

    workflow_root = args.workflow_root.expanduser().resolve()
    workspace = workflow_root.name
    systemd_dir = _systemd_user_dir()
    systemd_dir.mkdir(parents=True, exist_ok=True)

    actions: list[str] = []

    # 1. Stop + disable old units (tolerant of missing units)
    for old_name in (
        f"{workspace}-relay-active.service",
        f"{workspace}-relay-shadow.service",
    ):
        old_path = systemd_dir / old_name
        if old_path.exists():
            subprocess.run(
                ["systemctl", "--user", "stop", old_name],
                check=False, capture_output=True,
            )
            subprocess.run(
                ["systemctl", "--user", "disable", old_name],
                check=False, capture_output=True,
            )
            old_path.unlink()
            actions.append(f"removed old unit {old_name}")

    # 2. Install new template units (overwrite if exists)
    for mode in ("active", "shadow"):
        template_filename = _template_unit_filename(mode)
        template_path = systemd_dir / template_filename
        template_path.write_text(_render_template_unit(mode=mode), encoding="utf-8")
        actions.append(f"installed template unit {template_filename}")

    # 3. systemctl daemon-reload
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        check=False, capture_output=True,
    )
    actions.append("daemon-reload")

    lines = [f"migrate-systemd complete (workspace={workspace}):"]
    lines.extend(f"  - {a}" for a in actions)
    lines.append(
        f"to start active mode: systemctl --user start {_instance_unit_name('active', workspace)}"
    )
    return "\n".join(lines)


def cmd_set_observability(args, parser) -> str:
    """``/daedalus set-observability --workflow X --github-comments on|off|unset``."""
    try:
        from observability_overrides import set_override, unset_override
    except ImportError:
        path = PLUGIN_DIR / "observability_overrides.py"
        spec = importlib.util.spec_from_file_location("daedalus_observability_overrides_for_cli", path)
        if spec is None or spec.loader is None:
            raise DaedalusCommandError(f"unable to load observability_overrides from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        set_override = module.set_override
        unset_override = module.unset_override

    workflow_root = Path(args.workflow_root).expanduser().resolve()
    state_dir = workflow_root / "runtime" / "state" / "daedalus"
    workflow_name = args.workflow
    setting = (args.github_comments or "").strip().lower()

    if setting == "on":
        set_override(state_dir, workflow_name=workflow_name, github_comments_enabled=True)
        return f"observability override set: {workflow_name}.github-comments = on"
    if setting == "off":
        set_override(state_dir, workflow_name=workflow_name, github_comments_enabled=False)
        return f"observability override set: {workflow_name}.github-comments = off"
    if setting == "unset":
        unset_override(state_dir, workflow_name=workflow_name)
        return f"observability override removed for {workflow_name}"
    raise DaedalusCommandError(
        f"--github-comments must be one of: on, off, unset (got {args.github_comments!r})"
    )


def cmd_get_observability(args, parser) -> str:
    """``/daedalus get-observability --workflow X``: show effective config + source."""
    try:
        from workflows.change_delivery.observability import resolve_effective_config
    except ImportError:
        path = PLUGIN_DIR / "workflows" / "change_delivery" / "observability.py"
        spec = importlib.util.spec_from_file_location("daedalus_observability_for_cli", path)
        if spec is None or spec.loader is None:
            raise DaedalusCommandError(f"unable to load observability resolver from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        resolve_effective_config = module.resolve_effective_config

    workflow_root = Path(args.workflow_root).expanduser().resolve()
    workflow_name = args.workflow
    workflow_yaml = {}
    try:
        workflow_yaml = load_workflow_contract(workflow_root).config
    except FileNotFoundError:
        workflow_yaml = {}
    except (WorkflowContractError, OSError, UnicodeDecodeError) as exc:
        return f"error reading workflow contract: {exc}"

    eff = resolve_effective_config(
        workflow_yaml=workflow_yaml,
        override_dir=workflow_root / "runtime" / "state" / "daedalus",
        workflow_name=workflow_name,
    )
    gh = eff["github-comments"]
    source = eff["source"]["github-comments"]
    include_events = gh.get("include-events")
    if not include_events:
        # Empty list = explicit firehose ("everything"); flag the risk visibly
        # so an operator who sees this output understands every audit action
        # will become a comment update.
        include_events_display = "[] (FIREHOSE — every audit action)"
    else:
        include_events_display = ", ".join(include_events)
    lines = [
        f"workflow: {workflow_name}",
        f"github-comments.enabled: {gh.get('enabled')} (source: {source})",
        f"github-comments.mode: {gh.get('mode')}",
        f"github-comments.include-events: {include_events_display}",
    ]
    return "\n".join(lines)


def configure_subcommands(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    sub = parser.add_subparsers(dest="daedalus_command")
    sub.required = True
    default_workflow_root_str = str(resolve_default_workflow_root())
    default_workflow_root_path = resolve_default_workflow_root()

    init_cmd = sub.add_parser("init", help="Initialize Daedalus DB and filesystem paths.")
    init_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    init_cmd.add_argument("--project-key")
    init_cmd.add_argument("--json", action="store_true")
    init_cmd.set_defaults(func=run_cli_command)

    start_cmd = sub.add_parser("start", help="Bootstrap Daedalus runtime and acquire runtime lease.")
    start_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    start_cmd.add_argument("--project-key")
    start_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    start_cmd.add_argument("--mode", default="shadow", choices=["shadow", "active", "maintenance"])
    start_cmd.add_argument("--json", action="store_true")
    start_cmd.set_defaults(func=run_cli_command)

    status_cmd = sub.add_parser("status", help="Show Daedalus runtime status.")
    status_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    status_cmd.set_defaults(func=run_cli_command)

    report_cmd = sub.add_parser("shadow-report", help="Summarize the live legacy lane, Daedalus shadow decision, compatibility, and recent shadow actions.")
    report_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    report_cmd.add_argument("--recent-actions-limit", type=int, default=5)
    report_cmd.add_argument("--json", action="store_true")
    report_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    report_cmd.set_defaults(func=run_cli_command)

    doctor_cmd = sub.add_parser("doctor", help="Diagnose Daedalus runtime freshness, lease ownership, shadow parity, and active-lane consistency.")
    doctor_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    doctor_cmd.add_argument("--recent-actions-limit", type=int, default=5)
    doctor_cmd.add_argument("--json", action="store_true")
    doctor_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    doctor_cmd.set_defaults(func=run_cli_command)

    service_install_cmd = sub.add_parser("service-install", help="Install the supervised Daedalus systemd user service.")
    service_install_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_install_cmd.add_argument("--project-key")
    service_install_cmd.add_argument("--instance-id")
    service_install_cmd.add_argument("--interval-seconds", type=int, default=30)
    service_install_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_install_cmd.add_argument("--service-name")
    service_install_cmd.add_argument("--json", action="store_true")
    service_install_cmd.set_defaults(func=run_cli_command)

    service_up_cmd = sub.add_parser(
        "service-up",
        help="Initialize, preflight, install, enable, and start the supervised Daedalus systemd user service.",
    )
    service_up_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_up_cmd.add_argument("--project-key")
    service_up_cmd.add_argument("--instance-id")
    service_up_cmd.add_argument("--interval-seconds", type=int, default=30)
    service_up_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="active")
    service_up_cmd.add_argument("--service-name")
    service_up_cmd.add_argument("--json", action="store_true")
    service_up_cmd.set_defaults(func=run_cli_command)

    service_uninstall_cmd = sub.add_parser("service-uninstall", help="Remove the supervised Daedalus systemd user service.")
    service_uninstall_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_uninstall_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_uninstall_cmd.add_argument("--service-name")
    service_uninstall_cmd.add_argument("--json", action="store_true")
    service_uninstall_cmd.set_defaults(func=run_cli_command)

    service_start_cmd = sub.add_parser("service-start", help="Start the supervised Daedalus systemd user service.")
    service_start_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_start_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_start_cmd.add_argument("--service-name")
    service_start_cmd.add_argument("--json", action="store_true")
    service_start_cmd.set_defaults(func=run_cli_command)

    service_stop_cmd = sub.add_parser("service-stop", help="Stop the supervised Daedalus systemd user service.")
    service_stop_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_stop_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_stop_cmd.add_argument("--service-name")
    service_stop_cmd.add_argument("--json", action="store_true")
    service_stop_cmd.set_defaults(func=run_cli_command)

    service_restart_cmd = sub.add_parser("service-restart", help="Restart the supervised Daedalus systemd user service.")
    service_restart_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_restart_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_restart_cmd.add_argument("--service-name")
    service_restart_cmd.add_argument("--json", action="store_true")
    service_restart_cmd.set_defaults(func=run_cli_command)

    service_enable_cmd = sub.add_parser("service-enable", help="Enable the supervised Daedalus systemd user service.")
    service_enable_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_enable_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_enable_cmd.add_argument("--service-name")
    service_enable_cmd.add_argument("--json", action="store_true")
    service_enable_cmd.set_defaults(func=run_cli_command)

    service_disable_cmd = sub.add_parser("service-disable", help="Disable the supervised Daedalus systemd user service.")
    service_disable_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_disable_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_disable_cmd.add_argument("--service-name")
    service_disable_cmd.add_argument("--json", action="store_true")
    service_disable_cmd.set_defaults(func=run_cli_command)

    service_status_cmd = sub.add_parser("service-status", help="Show supervised Daedalus systemd user service status.")
    service_status_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_status_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_status_cmd.add_argument("--service-name")
    service_status_cmd.add_argument("--json", action="store_true")
    service_status_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    service_status_cmd.set_defaults(func=run_cli_command)

    service_logs_cmd = sub.add_parser("service-logs", help="Show recent logs for the supervised Daedalus systemd user service.")
    service_logs_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_logs_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="shadow")
    service_logs_cmd.add_argument("--service-name")
    service_logs_cmd.add_argument("--lines", type=int, default=50)
    service_logs_cmd.add_argument("--json", action="store_true")
    service_logs_cmd.set_defaults(func=run_cli_command)

    ingest_cmd = sub.add_parser("ingest-live", help="Ingest current workflow status into Daedalus shadow state.")
    ingest_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    ingest_cmd.add_argument("--json", action="store_true")
    ingest_cmd.set_defaults(func=run_cli_command)

    heartbeat_cmd = sub.add_parser("heartbeat", help="Refresh Daedalus runtime lease and heartbeat timestamp.")
    heartbeat_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    heartbeat_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    heartbeat_cmd.add_argument("--ttl-seconds", type=int, default=60)
    heartbeat_cmd.add_argument("--json", action="store_true")
    heartbeat_cmd.set_defaults(func=run_cli_command)

    iterate_cmd = sub.add_parser("iterate-shadow", help="Run one shadow-mode loop iteration.")
    iterate_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    iterate_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    iterate_cmd.add_argument("--json", action="store_true")
    iterate_cmd.set_defaults(func=run_cli_command)

    run_cmd = sub.add_parser("run-shadow", help="Run the shadow-mode loop shell for one or more iterations.")
    run_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    run_cmd.add_argument("--project-key")
    run_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    run_cmd.add_argument("--interval-seconds", type=int, default=30)
    run_cmd.add_argument("--max-iterations", type=int)
    run_cmd.add_argument("--json", action="store_true")
    run_cmd.set_defaults(func=run_cli_command)

    active_gate_status_cmd = sub.add_parser("active-gate-status", help="Show Daedalus active-execution gate state.")
    active_gate_status_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    active_gate_status_cmd.add_argument("--json", action="store_true")
    active_gate_status_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    active_gate_status_cmd.set_defaults(func=run_cli_command)

    set_active_execution_cmd = sub.add_parser("set-active-execution", help="Enable or disable Daedalus active execution.")
    set_active_execution_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    set_active_execution_cmd.add_argument("--enabled", required=True, choices=["true", "false"])
    set_active_execution_cmd.add_argument("--json", action="store_true")
    set_active_execution_cmd.set_defaults(func=run_cli_command)

    iterate_active_cmd = sub.add_parser("iterate-active", help="Run one guarded active-mode loop iteration.")
    iterate_active_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    iterate_active_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    iterate_active_cmd.add_argument("--json", action="store_true")
    iterate_active_cmd.set_defaults(func=run_cli_command)

    run_active_cmd = sub.add_parser("run-active", help="Run the guarded active-mode loop shell for one or more iterations.")
    run_active_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    run_active_cmd.add_argument("--project-key")
    run_active_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    run_active_cmd.add_argument("--interval-seconds", type=int, default=30)
    run_active_cmd.add_argument("--max-iterations", type=int)
    run_active_cmd.add_argument("--json", action="store_true")
    run_active_cmd.set_defaults(func=run_cli_command)

    request_active_cmd = sub.add_parser("request-active-actions", help="Derive and persist active requested actions for one lane.")
    request_active_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    request_active_cmd.add_argument("--lane-id", required=True)
    request_active_cmd.add_argument("--json", action="store_true")
    request_active_cmd.set_defaults(func=run_cli_command)

    execute_action_cmd = sub.add_parser("execute-action", help="Execute one active requested action by action id.")
    execute_action_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    execute_action_cmd.add_argument("--action-id", required=True)
    execute_action_cmd.add_argument("--json", action="store_true")
    execute_action_cmd.set_defaults(func=run_cli_command)

    analyze_failure_cmd = sub.add_parser("analyze-failure", help="Run bounded failure analysis for a recorded failure id.")
    analyze_failure_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
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
        default=default_workflow_root_path,
        help="Workflow root to migrate (default: %(default)s)",
    )
    migrate_fs_cmd.set_defaults(handler=cmd_migrate_filesystem, func=run_cli_command)

    migrate_systemd_cmd = sub.add_parser(
        "migrate-systemd",
        help="Migrate relay-era systemd units to daedalus template units.",
    )
    migrate_systemd_cmd.add_argument(
        "--workflow-root",
        type=Path,
        default=default_workflow_root_path,
    )
    migrate_systemd_cmd.set_defaults(handler=cmd_migrate_systemd, func=run_cli_command)

    watch_cmd = sub.add_parser(
        "watch",
        help="Live operator TUI: lanes, alerts, recent events.",
    )
    watch_cmd.add_argument("--workflow-root", type=Path, default=default_workflow_root_path)
    watch_cmd.add_argument("--once", action="store_true", help="Render one frame and exit (default when stdout is not a TTY).")
    watch_cmd.add_argument("--interval", type=float, default=2.0, help="Poll interval in live mode.")
    watch_cmd.set_defaults(handler=_lazy_cmd_watch, func=run_cli_command)

    set_obs_cmd = sub.add_parser(
        "set-observability",
        help="Override observability config for a workflow (writes runtime override file).",
    )
    set_obs_cmd.add_argument("--workflow-root", type=Path, default=default_workflow_root_path)
    set_obs_cmd.add_argument("--workflow", required=True, help="Workflow name (e.g. change-delivery)")
    set_obs_cmd.add_argument("--github-comments", choices=["on", "off", "unset"], required=True)
    set_obs_cmd.set_defaults(handler=cmd_set_observability, func=run_cli_command)

    get_obs_cmd = sub.add_parser(
        "get-observability",
        help="Show effective observability config + which layer (default/yaml/override) won.",
    )
    get_obs_cmd.add_argument("--workflow-root", type=Path, default=default_workflow_root_path)
    get_obs_cmd.add_argument("--workflow", required=True)
    get_obs_cmd.set_defaults(handler=cmd_get_observability, func=run_cli_command)

    scaffold_cmd = sub.add_parser(
        "scaffold-workflow",
        help="Create a new workflow root and starter WORKFLOW.md.",
    )
    scaffold_cmd.add_argument(
        "--workflow-root",
        type=Path,
        required=True,
        help="Workflow root to create. Directory name must be <owner>-<repo>-<workflow-type>.",
    )
    scaffold_cmd.add_argument("--workflow", default="change-delivery", choices=["change-delivery"])
    scaffold_cmd.add_argument("--repo-path", type=Path)
    scaffold_cmd.add_argument("--github-slug", required=True)
    scaffold_cmd.add_argument("--active-lane-label", default="active-lane")
    scaffold_cmd.add_argument("--engine-owner", default="hermes", choices=["hermes", "openclaw"])
    scaffold_cmd.add_argument("--force", action="store_true")
    scaffold_cmd.add_argument("--json", action="store_true")
    scaffold_cmd.set_defaults(handler=cmd_scaffold_workflow, func=run_cli_command)

    bootstrap_cmd = sub.add_parser(
        "bootstrap",
        help="Infer repo settings from the current git checkout and scaffold a starter WORKFLOW.md.",
    )
    bootstrap_cmd.add_argument("--repo-path", type=Path, help="Git checkout to inspect (defaults to current working directory).")
    bootstrap_cmd.add_argument("--workflow-root", type=Path, help="Optional explicit workflow root override.")
    bootstrap_cmd.add_argument("--workflow", default="change-delivery", choices=["change-delivery"])
    bootstrap_cmd.add_argument("--github-slug", help="Override the inferred GitHub slug from git origin.")
    bootstrap_cmd.add_argument("--active-lane-label", default="active-lane")
    bootstrap_cmd.add_argument("--engine-owner", default="hermes", choices=["hermes", "openclaw"])
    bootstrap_cmd.add_argument("--force", action="store_true")
    bootstrap_cmd.add_argument("--json", action="store_true")
    bootstrap_cmd.set_defaults(handler=cmd_bootstrap_workflow, func=run_cli_command)

    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = DaedalusArgumentParser(prog="daedalus", description="Daedalus operator control surface.")
    return configure_subcommands(parser)


def _run_wrapper_json_command(*, workflow_root: Path, command: str) -> dict[str, Any]:
    """Run a workflow CLI command via the plugin-side entrypoint."""
    argv = workflow_cli_argv(workflow_root, *shlex.split(command))
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


def _record_operator_command_event(*, workflow_root: Path, args: argparse.Namespace, daedalus: Any) -> None:
    now_iso = daedalus._now_iso()
    arguments_json = {}
    for key, value in vars(args).items():
        # Skip non-serializable argparse plumbing. ``handler`` is a function
        # reference set by the new string-returning subcommands (watch,
        # set-observability, get-observability) — it would crash json.dumps.
        if key in {"func", "handler", "json", "_command_source"}:
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
            "project_key": daedalus._project_key_for(workflow_root),
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


def _resolved_project_key(*, daedalus: Any, workflow_root: Path, project_key: str | None) -> str:
    if project_key:
        return project_key
    return daedalus._project_key_for(workflow_root)


def _resolve_format(format_arg: str | None, json_flag: bool | None) -> str:
    """Resolve the effective output format from ``--format`` and ``--json``.

    The legacy ``--json`` flag wins when set so existing scripts don't get
    silently downgraded. Otherwise, ``--format`` is honored. Default is text.
    """
    if json_flag:
        return "json"
    if format_arg == "json":
        return "json"
    return "text"


def execute_namespace(args: argparse.Namespace) -> dict[str, Any]:
    workflow_root = Path(args.workflow_root).resolve() if hasattr(args, "workflow_root") else None
    daedalus = _load_daedalus_module(workflow_root) if workflow_root is not None else None
    if workflow_root is not None and daedalus is not None and getattr(args, "daedalus_command", None):
        _record_operator_command_event(workflow_root=workflow_root, args=args, daedalus=daedalus)
    command = getattr(args, "daedalus_command", None)
    project_key_commands = {"init", "start", "service-install", "service-up", "run-shadow", "run-active"}
    resolved_project_key = (
        _resolved_project_key(
            daedalus=daedalus,
            workflow_root=workflow_root,
            project_key=getattr(args, "project_key", None),
        )
        if workflow_root is not None and daedalus is not None and command in project_key_commands
        else None
    )
    paths = daedalus._runtime_paths(workflow_root) if daedalus is not None else None

    if args.daedalus_command == "init":
        return daedalus.init_daedalus_db(workflow_root=workflow_root, project_key=resolved_project_key)
    if args.daedalus_command == "start":
        return daedalus.bootstrap_runtime(
            workflow_root=workflow_root,
            project_key=resolved_project_key,
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
            project_key=resolved_project_key,
            instance_id=args.instance_id,
            interval_seconds=args.interval_seconds,
            service_name=args.service_name,
            service_mode=args.service_mode,
        )
    if args.daedalus_command == "service-up":
        return service_up(
            workflow_root=workflow_root,
            project_key=resolved_project_key,
            instance_id=args.instance_id,
            interval_seconds=args.interval_seconds,
            service_name=args.service_name,
            service_mode=args.service_mode,
        )
    if args.daedalus_command == "service-uninstall":
        return uninstall_supervised_service(
            workflow_root=workflow_root,
            service_name=args.service_name,
            service_mode=args.service_mode,
        )
    if args.daedalus_command == "service-start":
        return service_control(
            "start",
            workflow_root=workflow_root,
            service_name=args.service_name,
            service_mode=args.service_mode,
        )
    if args.daedalus_command == "service-stop":
        return service_control(
            "stop",
            workflow_root=workflow_root,
            service_name=args.service_name,
            service_mode=args.service_mode,
        )
    if args.daedalus_command == "service-restart":
        return service_control(
            "restart",
            workflow_root=workflow_root,
            service_name=args.service_name,
            service_mode=args.service_mode,
        )
    if args.daedalus_command == "service-enable":
        return service_control(
            "enable",
            workflow_root=workflow_root,
            service_name=args.service_name,
            service_mode=args.service_mode,
        )
    if args.daedalus_command == "service-disable":
        return service_control(
            "disable",
            workflow_root=workflow_root,
            service_name=args.service_name,
            service_mode=args.service_mode,
        )
    if args.daedalus_command == "service-status":
        return service_status(
            workflow_root=workflow_root,
            service_name=args.service_name,
            service_mode=args.service_mode,
        )
    if args.daedalus_command == "service-logs":
        return service_logs(
            workflow_root=workflow_root,
            service_name=args.service_name,
            service_mode=args.service_mode,
            lines=args.lines,
        )
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
            project_key=resolved_project_key,
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
            project_key=resolved_project_key,
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


def render_result(
    command: str,
    result: dict[str, Any],
    *,
    json_output: bool | None = None,
    output_format: str | None = None,
) -> str:
    # Resolve effective format. New callers pass output_format; legacy callers pass json_output.
    if output_format is None:
        output_format = "json" if json_output else "text"
    if output_format == "json":
        return json.dumps(result, indent=2, sort_keys=True)
    if command == "init":
        return f"initialized db={result.get('db_path')} project={result.get('project_key')}"
    if command == "start":
        return (
            f"runtime={result.get('runtime_status')} instance={result.get('instance_id')} "
            f"mode={result.get('mode')}"
        )
    if command == "status":
        try:
            from formatters import format_status as _fmt_status
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "daedalus_formatters_for_render", PLUGIN_DIR / "formatters.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _fmt_status = mod.format_status
        return _fmt_status(result)
    if command == "shadow-report":
        try:
            from formatters import format_shadow_report as _fmt
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "daedalus_formatters_for_shadow", PLUGIN_DIR / "formatters.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _fmt = mod.format_shadow_report
        return _fmt(result)
    if command == "doctor":
        try:
            from formatters import format_doctor as _fmt
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "daedalus_formatters_for_doctor", PLUGIN_DIR / "formatters.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _fmt = mod.format_doctor
        return _fmt(result)
    if command == "service-install":
        return f"service installed mode={result.get('service_mode')} unit={result.get('unit_path')} ok={result.get('installed')}"
    if command == "service-up":
        status = result.get("service_status") or {}
        preflight = result.get("preflight") or {}
        return (
            f"service up mode={result.get('service_mode')} "
            f"workflow={preflight.get('workflow')} "
            f"enabled={status.get('enabled')} active={status.get('active')} "
            f"service={status.get('service_name')}"
        )
    if command == "service-uninstall":
        return f"service uninstalled mode={result.get('service_mode')} unit={result.get('unit_path')} ok={result.get('uninstalled')}"
    if command in {"service-start", "service-stop", "service-restart", "service-enable", "service-disable"}:
        return f"{result.get('action')} mode={result.get('service_mode')} {result.get('service_name')} ok={result.get('ok')} stdout={result.get('stdout')} stderr={result.get('stderr')}".strip()
    if command == "service-status":
        try:
            from formatters import format_service_status as _fmt
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "daedalus_formatters_for_service_status", PLUGIN_DIR / "formatters.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _fmt = mod.format_service_status
        return _fmt(result)
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
        try:
            from formatters import format_active_gate_status as _fmt
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "daedalus_formatters_for_active_gate", PLUGIN_DIR / "formatters.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _fmt = mod.format_active_gate_status
        return _fmt(result)
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
    regardless of what the workflow contract declares.
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
        if args.daedalus_command == "migrate-systemd":
            return cmd_migrate_systemd(args, parser)
        # New commands return strings directly (not dicts), so they bypass
        # execute_namespace which only knows about the legacy dict-returning
        # branches. Without these explicit routes the new subcommands fall
        # through to "unknown daedalus command".
        if args.daedalus_command == "watch":
            return _lazy_cmd_watch(args, parser)
        if args.daedalus_command == "set-observability":
            return cmd_set_observability(args, parser)
        if args.daedalus_command == "get-observability":
            return cmd_get_observability(args, parser)
        if args.daedalus_command == "scaffold-workflow":
            return cmd_scaffold_workflow(args, parser)
        if args.daedalus_command == "bootstrap":
            return cmd_bootstrap_workflow(args, parser)
        result = execute_namespace(args)
        fmt = _resolve_format(getattr(args, "format", None), getattr(args, "json", False))
        return render_result(args.daedalus_command, result, output_format=fmt)
    except DaedalusCommandError as exc:
        return f"daedalus error: {exc}"
    except SystemExit:
        detail = stderr_buffer.getvalue().strip()
        return f"daedalus error: {detail or parser.format_usage().strip()}"
    except Exception as exc:
        return f"daedalus error: unexpected {type(exc).__name__}: {exc}"


def run_cli_command(args: argparse.Namespace) -> None:
    args._command_source = "cli"
    # Some subcommands have handlers that return strings directly, not dicts.
    # ``execute_namespace`` only knows about the legacy dict-returning commands,
    # so without this branch the new (string-returning) commands would fall
    # through to ``unknown daedalus command``. This mirrors the special-cases
    # in ``execute_raw_args`` for the slash-command path.
    string_returning = {
        "migrate-filesystem",
        "migrate-systemd",
        "watch",
        "set-observability",
        "get-observability",
        "scaffold-workflow",
        "bootstrap",
    }
    if getattr(args, "daedalus_command", None) in string_returning:
        handler = getattr(args, "handler", None)
        if handler is not None:
            print(handler(args, parser=None))
            return
    fmt = _resolve_format(getattr(args, "format", None), getattr(args, "json", False))
    print(render_result(args.daedalus_command, execute_namespace(args), output_format=fmt))


if __name__ == "__main__":
    import sys
    result = execute_raw_args(" ".join(sys.argv[1:]))
    print(result)
    sys.exit(0 if not result.startswith("daedalus error:") else 1)
