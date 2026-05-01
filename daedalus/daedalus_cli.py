import argparse
import http.client
import importlib.util
import ipaddress
import io
import json
import os
import re
import shlex
import sqlite3
import subprocess
import time
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from engine.state import (
    read_engine_events,
    read_engine_events_for_run,
    read_engine_run,
    read_engine_runs,
    read_engine_scheduler_state,
)
from engine.retention import normalize_event_retention
from engine.store import EngineStore
from workflows.contract import (
    WorkflowContractError,
    find_repo_workflow_contract_path,
    load_workflow_contract,
    load_workflow_contract_file,
    render_workflow_markdown,
    workflow_contract_pointer_path,
    workflow_named_markdown_path,
    workflow_yaml_path as legacy_workflow_config_path,
    workflow_markdown_path,
    write_workflow_contract_pointer,
)
from workflows.validation import validate_workflow_contract
from workflows.shared.paths import (
    derive_workflow_instance_name,
    plugin_runtime_path,
    project_key_for_workflow_root,
    repo_local_workflow_pointer_path,
    resolve_default_workflow_root as resolve_workflow_root_default,
    runtime_paths,
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
CODEX_APP_SERVER_SERVICE_PREFIX = "daedalus-codex-app-server"
DEFAULT_CODEX_APP_SERVER_LISTEN = "ws://127.0.0.1:4500"
DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH = "/readyz"


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
            f"ExecStart=/usr/bin/python3 %h/.hermes/plugins/daedalus/daedalus_cli.py "
            f"service-loop --workflow-root %h/.hermes/workflows/%i "
            f"--project-key %i --instance-id daedalus-{mode}-%i "
            f"--interval-seconds 30 --service-mode {mode} --json"
        ),
        "Restart=always",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def _codex_app_server_service_name(*, workflow_root: Path, service_name: str | None = None) -> str:
    if service_name:
        return service_name if service_name.endswith(".service") else f"{service_name}.service"
    return f"{CODEX_APP_SERVER_SERVICE_PREFIX}@{workflow_root.name}.service"


def _codex_app_server_unit_path(service_name: str) -> Path:
    return _systemd_user_dir() / service_name


def _absolute_secret_path(value: str, *, flag_name: str) -> str:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        raise DaedalusCommandError(f"{flag_name} must be an absolute path")
    return str(path)


def _codex_app_server_ws_auth_args(
    *,
    ws_token_file: str | None = None,
    ws_token_sha256: str | None = None,
    ws_shared_secret_file: str | None = None,
    ws_issuer: str | None = None,
    ws_audience: str | None = None,
    ws_max_clock_skew_seconds: int | None = None,
) -> tuple[list[str], dict[str, Any] | None]:
    token_file = str(ws_token_file or "").strip()
    token_sha256 = str(ws_token_sha256 or "").strip()
    shared_secret_file = str(ws_shared_secret_file or "").strip()
    issuer = str(ws_issuer or "").strip()
    audience = str(ws_audience or "").strip()

    if token_file and token_sha256:
        raise DaedalusCommandError("use either --ws-token-file or --ws-token-sha256, not both")
    if (token_file or token_sha256) and shared_secret_file:
        raise DaedalusCommandError("capability-token and signed-bearer-token auth modes are mutually exclusive")
    if (issuer or audience or ws_max_clock_skew_seconds is not None) and not shared_secret_file:
        raise DaedalusCommandError("--ws-issuer, --ws-audience, and --ws-max-clock-skew-seconds require --ws-shared-secret-file")
    if ws_max_clock_skew_seconds is not None and ws_max_clock_skew_seconds < 0:
        raise DaedalusCommandError("--ws-max-clock-skew-seconds must be non-negative")

    if token_file:
        path = _absolute_secret_path(token_file, flag_name="--ws-token-file")
        return (
            ["--ws-auth", "capability-token", "--ws-token-file", path],
            {"mode": "capability-token", "token_file": path},
        )
    if token_sha256:
        return (
            ["--ws-auth", "capability-token", "--ws-token-sha256", token_sha256],
            {"mode": "capability-token", "token_sha256": token_sha256},
        )
    if shared_secret_file:
        path = _absolute_secret_path(shared_secret_file, flag_name="--ws-shared-secret-file")
        args = ["--ws-auth", "signed-bearer-token", "--ws-shared-secret-file", path]
        summary: dict[str, Any] = {"mode": "signed-bearer-token", "shared_secret_file": path}
        if issuer:
            args.extend(["--ws-issuer", issuer])
            summary["issuer"] = issuer
        if audience:
            args.extend(["--ws-audience", audience])
            summary["audience"] = audience
        if ws_max_clock_skew_seconds is not None:
            args.extend(["--ws-max-clock-skew-seconds", str(ws_max_clock_skew_seconds)])
            summary["max_clock_skew_seconds"] = ws_max_clock_skew_seconds
        return args, summary
    return [], None


def _render_codex_app_server_unit(
    *,
    listen: str,
    codex_command: str = "codex",
    ws_token_file: str | None = None,
    ws_token_sha256: str | None = None,
    ws_shared_secret_file: str | None = None,
    ws_issuer: str | None = None,
    ws_audience: str | None = None,
    ws_max_clock_skew_seconds: int | None = None,
) -> str:
    service_path = os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    command_parts = shlex.split(str(codex_command).strip() or "codex")
    auth_args, _auth_summary = _codex_app_server_ws_auth_args(
        ws_token_file=ws_token_file,
        ws_token_sha256=ws_token_sha256,
        ws_shared_secret_file=ws_shared_secret_file,
        ws_issuer=ws_issuer,
        ws_audience=ws_audience,
        ws_max_clock_skew_seconds=ws_max_clock_skew_seconds,
    )
    exec_start = shlex.join(["/usr/bin/env", *command_parts, "app-server", "--listen", listen, *auth_args])
    return "\n".join([
        "[Unit]",
        "Description=Daedalus Codex app-server (workspace=%i)",
        "After=default.target",
        "",
        "[Service]",
        "Type=simple",
        "WorkingDirectory=%h",
        f"Environment=PATH={service_path}",
        "Environment=PYTHONUNBUFFERED=1",
        f"ExecStart={exec_start}",
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
    validate_contract: bool = True,
) -> dict[str, Any]:
    plugin_runtime_path = _expected_plugin_runtime_path(workflow_root)
    if not plugin_runtime_path.exists():
        raise DaedalusCommandError(
            f"Daedalus plugin runtime not found at {plugin_runtime_path}; install the plugin into ~/.hermes/plugins/daedalus before installing the service"
        )
    if validate_contract:
        preflight_result = _validate_workflow_contract_preflight_for_service(
            workflow_root=workflow_root,
            service_mode=service_mode,
        )
        workflow_name = str(preflight_result.get("workflow") or "")
    else:
        workflow_name = _assert_service_mode_supported(workflow_root=workflow_root, service_mode=service_mode)
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
        "workflow": workflow_name,
        "service_mode": service_mode,
        "service_name": resolved_service_name,
        "instance_id": resolved_instance_id,
        "unit_path": str(template_path),
        "daemon_reload": reload_result,
    }


def _validate_workflow_contract_preflight(workflow_root: Path) -> dict[str, Any]:
    return _validate_workflow_contract_preflight_for_service(
        workflow_root=workflow_root,
        service_mode=None,
    )


def build_validate_report(*, workflow_root: Path, service_mode: str | None = None) -> dict[str, Any]:
    return validate_workflow_contract(workflow_root, service_mode=service_mode)


def _validation_failure_summary(report: dict[str, Any]) -> str:
    failures = report.get("failures") or []
    if not failures:
        return "workflow contract validation failed"
    lines = ["workflow contract validation failed:"]
    for check in failures[:8]:
        lines.append(f"- {check.get('name')}: {check.get('detail')}")
        for item in (check.get("items") or [])[:5]:
            path = item.get("path") or "<root>"
            message = item.get("message") or item
            lines.append(f"  {path}: {message}")
    if len(failures) > 8:
        lines.append(f"- ... {len(failures) - 8} more failing checks")
    return "\n".join(lines)


def _assert_workflow_validation_ok(report: dict[str, Any]) -> None:
    if not report.get("ok"):
        raise DaedalusCommandError(_validation_failure_summary(report))


def _validate_workflow_contract_preflight_for_service(
    *,
    workflow_root: Path,
    service_mode: str | None,
) -> dict[str, Any]:
    report = build_validate_report(workflow_root=workflow_root, service_mode=service_mode)
    _assert_workflow_validation_ok(report)
    return {
        "ok": True,
        "workflow": report.get("workflow"),
        "schema_version": report.get("schema_version"),
        "source_path": report.get("source_path"),
        "checks": report.get("checks") or [],
        "warnings": report.get("warnings") or [],
    }


def _workflow_name_for_root(workflow_root: Path) -> str:
    contract = load_workflow_contract(workflow_root)
    workflow_name = str(contract.config.get("workflow") or "").strip()
    if not workflow_name:
        raise DaedalusCommandError(f"{contract.source_path} is missing top-level `workflow:` field")
    return workflow_name


def _assert_service_mode_supported(*, workflow_root: Path, service_mode: str) -> str:
    workflow_name = _workflow_name_for_root(workflow_root)
    if workflow_name == "issue-runner" and service_mode != "active":
        raise DaedalusCommandError(
            "issue-runner supports only active supervised mode; use --service-mode active"
        )
    return workflow_name


def _load_issue_runner_workspace(workflow_root: Path):
    try:
        from workflows.issue_runner.workspace import load_workspace_from_config
    except ImportError:
        path = PLUGIN_DIR / "workflows" / "issue_runner" / "workspace.py"
        spec = importlib.util.spec_from_file_location("daedalus_issue_runner_workspace_for_tools", path)
        if spec is None or spec.loader is None:
            raise DaedalusCommandError(f"unable to load issue-runner workspace module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        load_workspace_from_config = module.load_workspace_from_config
    return load_workspace_from_config(workspace_root=workflow_root)


def _build_issue_runner_status(workflow_root: Path) -> dict[str, Any]:
    return _load_issue_runner_workspace(workflow_root).build_status()


def _build_issue_runner_doctor(workflow_root: Path) -> dict[str, Any]:
    return _load_issue_runner_workspace(workflow_root).doctor()


def _run_event_id(event: dict[str, Any]) -> str | None:
    value = event.get("run_id") or event.get("runId")
    return str(value) if value not in (None, "") else None


def _workflow_audit_path(workflow_root: Path, workflow_name: str) -> Path:
    paths = runtime_paths(workflow_root)
    if workflow_name != "issue-runner":
        return paths["event_log_path"].parent / "workflow-audit.jsonl"
    contract = load_workflow_contract(workflow_root)
    storage_cfg = contract.config.get("storage") or {}
    raw = str(storage_cfg.get("audit-log") or "memory/workflow-audit.jsonl").strip()
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (workflow_root / path).resolve()


def _read_jsonl_events(path: Path, *, limit: int = 500) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _run_timeline_for_cli(workflow_root: Path, workflow_name: str, run_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    paths = runtime_paths(workflow_root)
    engine_events = read_engine_events_for_run(
        paths["db_path"],
        workflow=workflow_name,
        run_id=run_id,
        limit=max(limit, 1),
    )
    if engine_events:
        return [{**event, "source": "engine-events"} for event in engine_events]
    source_paths = [paths["event_log_path"], _workflow_audit_path(workflow_root, workflow_name)]
    timeline: list[dict[str, Any]] = []
    for path in dict.fromkeys(source_paths):
        for event in _read_jsonl_events(path, limit=max(limit * 5, limit)):
            if _run_event_id(event) == run_id:
                timeline.append({**event, "source_path": str(path)})
    timeline.sort(key=lambda item: str(item.get("at") or item.get("created_at") or item.get("time") or ""))
    return timeline[-limit:]


def build_runs_report(
    *,
    workflow_root: Path,
    action: str = "list",
    run_id: str | None = None,
    limit: int = 20,
    stale_seconds: int = 600,
) -> dict[str, Any]:
    workflow_root = Path(workflow_root).resolve()
    workflow_name = _workflow_name_for_root(workflow_root)
    db_path = runtime_paths(workflow_root)["db_path"]
    now_epoch = time.time()
    if action == "show":
        if not run_id:
            raise DaedalusCommandError("runs show requires a run_id")
        run = read_engine_run(db_path, workflow=workflow_name, run_id=run_id)
        if run is None:
            raise DaedalusCommandError(f"unknown engine run: {run_id}")
        age_seconds = max(int(now_epoch - float(run.get("started_at_epoch") or now_epoch)), 0)
        return {
            "mode": "show",
            "workflow": workflow_name,
            "run": {
                **run,
                "age_seconds": age_seconds,
                "stale": run.get("status") == "running" and age_seconds > stale_seconds,
            },
            "timeline": _run_timeline_for_cli(workflow_root, workflow_name, run_id, limit=max(limit, 1)),
        }

    runs = read_engine_runs(db_path, workflow=workflow_name, limit=max(limit, 1) * 5)
    enriched = []
    for run in runs:
        age_seconds = max(int(now_epoch - float(run.get("started_at_epoch") or now_epoch)), 0)
        item = {
            **run,
            "age_seconds": age_seconds,
            "stale": run.get("status") == "running" and age_seconds > stale_seconds,
        }
        if action == "failed" and item.get("status") != "failed":
            continue
        if action == "stale" and not item.get("stale"):
            continue
        enriched.append(item)
        if len(enriched) >= limit:
            break
    return {
        "mode": action,
        "workflow": workflow_name,
        "counts": {
            "shown": len(enriched),
            "failed": len([run for run in enriched if run.get("status") == "failed"]),
            "running": len([run for run in enriched if run.get("status") == "running"]),
            "stale": len([run for run in enriched if run.get("stale")]),
        },
        "runs": enriched,
    }


def _workflow_event_retention(workflow_root: Path) -> dict[str, Any]:
    try:
        contract = load_workflow_contract(workflow_root)
    except (FileNotFoundError, WorkflowContractError, OSError):
        return {}
    retention = contract.config.get("retention") or {}
    if not isinstance(retention, dict):
        return {}
    events = retention.get("events") or {}
    return events if isinstance(events, dict) else {}


def build_events_report(
    *,
    workflow_root: Path,
    action: str = "list",
    run_id: str | None = None,
    work_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 50,
    order: str = "desc",
    max_age_days: float | None = None,
    max_rows: int | None = None,
) -> dict[str, Any]:
    workflow_root = Path(workflow_root).resolve()
    workflow_name = _workflow_name_for_root(workflow_root)
    store = EngineStore(db_path=runtime_paths(workflow_root)["db_path"], workflow=workflow_name)
    filters = {
        "run_id": run_id,
        "work_id": work_id,
        "event_type": event_type,
        "severity": severity,
    }
    retention_cfg = normalize_event_retention(_workflow_event_retention(workflow_root))
    if max_age_days is not None:
        retention_cfg["configured"] = True
        retention_cfg["max_age_days"] = max_age_days
        retention_cfg["max_age_seconds"] = max_age_days * 86400
    if max_rows is not None:
        retention_cfg["configured"] = True
        retention_cfg["max_rows"] = max_rows
    if action == "stats":
        return {
            "mode": "stats",
            "workflow": workflow_name,
            "stats": store.event_stats(retention_cfg),
        }
    if action == "prune":
        if not retention_cfg.get("configured"):
            raise DaedalusCommandError(
                "events prune requires --max-age-days, --max-rows, or retention.events in WORKFLOW.md"
            )
        result = store.prune_events(
            max_age_seconds=retention_cfg.get("max_age_seconds"),
            max_rows=retention_cfg.get("max_rows"),
        )
        return {
            "mode": "prune",
            "workflow": workflow_name,
            "retention": retention_cfg,
            **result,
        }
    events = read_engine_events(
        runtime_paths(workflow_root)["db_path"],
        workflow=workflow_name,
        run_id=run_id,
        work_id=work_id,
        event_type=event_type,
        severity=severity,
        limit=max(limit, 1),
        order=order,
    )
    return {
        "mode": "list",
        "workflow": workflow_name,
        "filters": {key: value for key, value in filters.items() if value not in (None, "")},
        "counts": {"shown": len(events)},
        "events": events,
    }


def service_loop(
    *,
    workflow_root: Path,
    project_key: str | None,
    instance_id: str | None,
    interval_seconds: int,
    max_iterations: int | None,
    service_mode: str,
) -> dict[str, Any]:
    workflow_name = _assert_service_mode_supported(workflow_root=workflow_root, service_mode=service_mode)
    if workflow_name == "change-delivery":
        daedalus = _load_daedalus_module(workflow_root)
        resolved_project_key = project_key or daedalus._project_key_for(workflow_root)
        resolved_instance_id = instance_id or _instance_id_for(service_mode=service_mode, workspace=workflow_root.name)
        if service_mode == "shadow":
            return daedalus.run_shadow_loop(
                workflow_root=workflow_root,
                project_key=resolved_project_key,
                instance_id=resolved_instance_id,
                interval_seconds=interval_seconds,
                max_iterations=max_iterations,
            )
        return daedalus.run_active_loop(
            workflow_root=workflow_root,
            project_key=resolved_project_key,
            instance_id=resolved_instance_id,
            interval_seconds=interval_seconds,
            max_iterations=max_iterations,
        )
    if workflow_name == "issue-runner":
        workspace = _load_issue_runner_workspace(workflow_root)
        payload = workspace.run_loop(
            interval_seconds=interval_seconds,
            max_iterations=max_iterations,
        )
        return {
            "workflow": workflow_name,
            "service_mode": service_mode,
            **payload,
        }
    raise DaedalusCommandError(f"unsupported workflow for service-loop: {workflow_name}")


def service_up(
    *,
    workflow_root: Path,
    project_key: str,
    instance_id: str | None,
    interval_seconds: int,
    service_name: str | None = None,
    service_mode: str = "active",
) -> dict[str, Any]:
    preflight_result = _validate_workflow_contract_preflight_for_service(
        workflow_root=workflow_root,
        service_mode=service_mode,
    )
    workflow_name = preflight_result.get("workflow")
    if workflow_name == "change-delivery":
        daedalus = _load_daedalus_module(workflow_root)
        init_result = daedalus.init_daedalus_db(workflow_root=workflow_root, project_key=project_key)
    else:
        init_result = {
            "ok": True,
            "workflow": workflow_name,
            "skipped": True,
            "reason": "workflow-managed runtime does not require daedalus.db bootstrap",
        }

    install_result = install_supervised_service(
        workflow_root=workflow_root,
        project_key=project_key,
        instance_id=instance_id,
        interval_seconds=interval_seconds,
        service_name=service_name,
        service_mode=service_mode,
        validate_contract=False,
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


def codex_app_server_install(
    *,
    workflow_root: Path,
    listen: str = DEFAULT_CODEX_APP_SERVER_LISTEN,
    service_name: str | None = None,
    codex_command: str = "codex",
    ws_token_file: str | None = None,
    ws_token_sha256: str | None = None,
    ws_shared_secret_file: str | None = None,
    ws_issuer: str | None = None,
    ws_audience: str | None = None,
    ws_max_clock_skew_seconds: int | None = None,
) -> dict[str, Any]:
    resolved_service_name = _codex_app_server_service_name(
        workflow_root=workflow_root,
        service_name=service_name,
    )
    _auth_args, auth_summary = _codex_app_server_ws_auth_args(
        ws_token_file=ws_token_file,
        ws_token_sha256=ws_token_sha256,
        ws_shared_secret_file=ws_shared_secret_file,
        ws_issuer=ws_issuer,
        ws_audience=ws_audience,
        ws_max_clock_skew_seconds=ws_max_clock_skew_seconds,
    )
    unit_path = _codex_app_server_unit_path(resolved_service_name)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(
        _render_codex_app_server_unit(
            listen=listen,
            codex_command=codex_command,
            ws_token_file=ws_token_file,
            ws_token_sha256=ws_token_sha256,
            ws_shared_secret_file=ws_shared_secret_file,
            ws_issuer=ws_issuer,
            ws_audience=ws_audience,
            ws_max_clock_skew_seconds=ws_max_clock_skew_seconds,
        ),
        encoding="utf-8",
    )
    reload_result = _run_systemctl("daemon-reload")
    return {
        "ok": reload_result.get("ok", False),
        "action": "install",
        "workflow_root": str(workflow_root),
        "service_name": resolved_service_name,
        "unit_path": str(unit_path),
        "listen": listen,
        "codex_command": codex_command,
        "ws_auth": auth_summary,
        "daemon_reload": reload_result,
    }


def codex_app_server_up(
    *,
    workflow_root: Path,
    listen: str = DEFAULT_CODEX_APP_SERVER_LISTEN,
    service_name: str | None = None,
    codex_command: str = "codex",
    ws_token_file: str | None = None,
    ws_token_sha256: str | None = None,
    ws_shared_secret_file: str | None = None,
    ws_issuer: str | None = None,
    ws_audience: str | None = None,
    ws_max_clock_skew_seconds: int | None = None,
) -> dict[str, Any]:
    install_result = codex_app_server_install(
        workflow_root=workflow_root,
        listen=listen,
        service_name=service_name,
        codex_command=codex_command,
        ws_token_file=ws_token_file,
        ws_token_sha256=ws_token_sha256,
        ws_shared_secret_file=ws_shared_secret_file,
        ws_issuer=ws_issuer,
        ws_audience=ws_audience,
        ws_max_clock_skew_seconds=ws_max_clock_skew_seconds,
    )
    if not install_result.get("ok"):
        daemon_reload = install_result.get("daemon_reload") or {}
        raise DaedalusCommandError(
            "unable to install codex-app-server service: "
            f"{daemon_reload.get('stderr') or daemon_reload.get('stdout') or 'daemon-reload failed'}"
        )
    resolved_service_name = str(install_result["service_name"])
    enable_result = _run_systemctl("enable", resolved_service_name)
    if not enable_result.get("ok"):
        raise DaedalusCommandError(
            "unable to enable codex-app-server service: "
            f"{enable_result.get('stderr') or enable_result.get('stdout') or enable_result.get('returncode')}"
        )
    start_result = _run_systemctl("start", resolved_service_name)
    if not start_result.get("ok"):
        raise DaedalusCommandError(
            "unable to start codex-app-server service: "
            f"{start_result.get('stderr') or start_result.get('stdout') or start_result.get('returncode')}"
        )
    return {
        "ok": True,
        "action": "up",
        "workflow_root": str(workflow_root),
        "service_name": resolved_service_name,
        "listen": listen,
        "install": install_result,
        "enable": enable_result,
        "start": start_result,
        "status": codex_app_server_status(
            workflow_root=workflow_root,
            service_name=resolved_service_name,
            endpoint=listen,
        ),
    }


def codex_app_server_down(
    *,
    workflow_root: Path,
    service_name: str | None = None,
) -> dict[str, Any]:
    resolved_service_name = _codex_app_server_service_name(
        workflow_root=workflow_root,
        service_name=service_name,
    )
    stop_result = _run_systemctl("stop", resolved_service_name)
    disable_result = _run_systemctl("disable", resolved_service_name)
    return {
        "ok": stop_result.get("ok", False) or disable_result.get("ok", False),
        "action": "down",
        "workflow_root": str(workflow_root),
        "service_name": resolved_service_name,
        "stop": stop_result,
        "disable": disable_result,
        "status": codex_app_server_status(workflow_root=workflow_root, service_name=resolved_service_name),
    }


def codex_app_server_restart(
    *,
    workflow_root: Path,
    service_name: str | None = None,
    endpoint: str = DEFAULT_CODEX_APP_SERVER_LISTEN,
    healthcheck_path: str = DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH,
) -> dict[str, Any]:
    resolved_service_name = _codex_app_server_service_name(
        workflow_root=workflow_root,
        service_name=service_name,
    )
    restart_result = _run_systemctl("restart", resolved_service_name)
    return {
        "ok": restart_result.get("ok", False),
        "action": "restart",
        "workflow_root": str(workflow_root),
        "service_name": resolved_service_name,
        "restart": restart_result,
        "status": codex_app_server_status(
            workflow_root=workflow_root,
            service_name=resolved_service_name,
            endpoint=endpoint,
            healthcheck_path=healthcheck_path,
        ),
    }


def codex_app_server_logs(
    *,
    workflow_root: Path,
    service_name: str | None = None,
    lines: int = 50,
) -> dict[str, Any]:
    resolved_service_name = _codex_app_server_service_name(
        workflow_root=workflow_root,
        service_name=service_name,
    )
    completed = subprocess.run(
        ["journalctl", "--user", "-u", resolved_service_name, "-n", str(lines), "--no-pager", "-o", "cat"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "action": "logs",
        "workflow_root": str(workflow_root),
        "service_name": resolved_service_name,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "lines": lines,
    }


def _codex_app_server_readyz(
    *,
    endpoint: str,
    healthcheck_path: str = DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH,
) -> dict[str, Any]:
    parsed = urlparse(str(endpoint or ""))
    path = str(healthcheck_path or DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH)
    if not path.startswith("/"):
        path = f"/{path}"
    if parsed.scheme != "ws":
        return {
            "ok": None,
            "checked": False,
            "endpoint": endpoint,
            "path": path,
            "reason": "readyz probe requires ws:// endpoint",
        }
    if not parsed.hostname or not parsed.port:
        return {
            "ok": False,
            "checked": True,
            "endpoint": endpoint,
            "path": path,
            "reason": "endpoint requires host and port",
        }
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
    except OSError as exc:
        return {
            "ok": False,
            "checked": True,
            "endpoint": endpoint,
            "path": path,
            "reason": str(exc),
        }
    finally:
        connection.close()
    return {
        "ok": response.status == 200,
        "checked": True,
        "endpoint": endpoint,
        "path": path,
        "status": response.status,
        "reason": None if response.status == 200 else f"HTTP {response.status}",
    }


def codex_app_server_status(
    *,
    workflow_root: Path,
    service_name: str | None = None,
    endpoint: str = DEFAULT_CODEX_APP_SERVER_LISTEN,
    healthcheck_path: str = DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH,
) -> dict[str, Any]:
    resolved_service_name = _codex_app_server_service_name(
        workflow_root=workflow_root,
        service_name=service_name,
    )
    unit_path = _codex_app_server_unit_path(resolved_service_name)
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
        "ok": active.get("ok", False),
        "action": "status",
        "workflow_root": str(workflow_root),
        "service_name": resolved_service_name,
        "unit_path": str(unit_path),
        "installed": unit_path.exists(),
        "active": active.get("stdout") or ("active" if active.get("ok") else "unknown"),
        "enabled": enabled.get("stdout") or ("enabled" if enabled.get("ok") else "unknown"),
        "ready": _codex_app_server_readyz(endpoint=endpoint, healthcheck_path=healthcheck_path),
        "properties": props,
        "active_check": active,
        "enabled_check": enabled,
        "show": show,
    }


def _codex_app_server_unit_tokens(unit_path: Path) -> list[str]:
    if not unit_path.exists():
        return []
    try:
        text = unit_path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            try:
                return shlex.split(line.split("=", 1)[1])
            except ValueError:
                return []
    return []


def _codex_app_server_token_value(tokens: list[str], flag: str) -> str | None:
    try:
        index = tokens.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(tokens):
        return None
    return tokens[index + 1]


def _codex_app_server_listen_from_unit(unit_path: Path) -> str | None:
    tokens = _codex_app_server_unit_tokens(unit_path)
    return _codex_app_server_token_value(tokens, "--listen")


def _codex_app_server_auth_summary_from_unit(unit_path: Path) -> dict[str, Any] | None:
    tokens = _codex_app_server_unit_tokens(unit_path)
    auth_mode = _codex_app_server_token_value(tokens, "--ws-auth")
    if not auth_mode:
        return None
    summary: dict[str, Any] = {"mode": auth_mode, "source": "unit"}
    token_file = _codex_app_server_token_value(tokens, "--ws-token-file")
    token_sha256 = _codex_app_server_token_value(tokens, "--ws-token-sha256")
    shared_secret_file = _codex_app_server_token_value(tokens, "--ws-shared-secret-file")
    issuer = _codex_app_server_token_value(tokens, "--ws-issuer")
    audience = _codex_app_server_token_value(tokens, "--ws-audience")
    max_skew = _codex_app_server_token_value(tokens, "--ws-max-clock-skew-seconds")
    if token_file:
        summary["token_file"] = token_file
    if token_sha256:
        summary["token_sha256"] = token_sha256
    if shared_secret_file:
        summary["shared_secret_file"] = shared_secret_file
    if issuer:
        summary["issuer"] = issuer
    if audience:
        summary["audience"] = audience
    if max_skew:
        try:
            summary["max_clock_skew_seconds"] = int(max_skew)
        except ValueError:
            summary["max_clock_skew_seconds"] = max_skew
    return summary


def _codex_app_server_endpoint_is_loopback(endpoint: str) -> bool:
    hostname = urlparse(str(endpoint or "")).hostname
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _load_codex_scheduler_snapshot(workflow_root: Path) -> dict[str, Any]:
    db_path = runtime_paths(workflow_root)["db_path"]
    workflow_names: list[str] = []
    try:
        contract = load_workflow_contract(workflow_root)
        workflow_name = str(contract.config.get("workflow") or "").strip()
        if workflow_name:
            workflow_names.append(workflow_name)
    except Exception:
        pass
    if not workflow_names:
        workflow_names = ["issue-runner", "change-delivery"]

    scheduler: dict[str, Any] | None = None
    for workflow_name in workflow_names:
        scheduler = read_engine_scheduler_state(
            db_path,
            workflow=workflow_name,
            now_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            now_epoch=time.time(),
        )
        if scheduler is None:
            continue
        raw_threads = scheduler.get("codex_threads") or scheduler.get("codexThreads") or {}
        totals = scheduler.get("codex_totals") or scheduler.get("codexTotals") or {}
        if raw_threads or totals or workflow_name == workflow_names[-1]:
            break

    if scheduler is None:
        return {
            "ok": True,
            "path": str(db_path),
            "exists": False,
            "threads": [],
            "totals": {},
            "invalid_thread_count": 0,
            "error": None,
        }
    raw_threads = scheduler.get("codex_threads") or scheduler.get("codexThreads") or {}
    threads: list[dict[str, Any]] = []
    invalid_thread_count = 0
    if isinstance(raw_threads, dict):
        for issue_id, raw_entry in sorted(raw_threads.items(), key=lambda item: str(item[0])):
            if not isinstance(raw_entry, dict):
                invalid_thread_count += 1
                continue
            thread_id = raw_entry.get("thread_id") or raw_entry.get("threadId")
            if not str(thread_id or "").strip():
                invalid_thread_count += 1
            issue_number = raw_entry.get("issue_number") or raw_entry.get("issueNumber")
            threads.append(
                {
                    "issue_id": raw_entry.get("issue_id") or issue_id,
                    "issue_number": issue_number,
                    "identifier": raw_entry.get("identifier") or (f"#{issue_number}" if issue_number else issue_id),
                    "session_name": raw_entry.get("session_name") or raw_entry.get("sessionName"),
                    "runtime_name": raw_entry.get("runtime_name") or raw_entry.get("runtimeName"),
                    "runtime_kind": raw_entry.get("runtime_kind") or raw_entry.get("runtimeKind"),
                    "thread_id": thread_id,
                    "turn_id": raw_entry.get("turn_id") or raw_entry.get("turnId"),
                    "status": raw_entry.get("status"),
                    "cancel_requested": bool(raw_entry.get("cancel_requested") or raw_entry.get("cancelRequested") or False),
                    "cancel_reason": raw_entry.get("cancel_reason") or raw_entry.get("cancelReason"),
                    "updated_at": raw_entry.get("updated_at") or raw_entry.get("updatedAt"),
                }
            )
    totals = scheduler.get("codex_totals") or scheduler.get("codexTotals") or {}
    return {
        "ok": invalid_thread_count == 0,
        "path": str(db_path),
        "exists": True,
        "threads": threads,
        "totals": totals if isinstance(totals, dict) else {},
        "invalid_thread_count": invalid_thread_count,
        "error": None,
    }


def _codex_app_server_doctor_check(
    name: str,
    status: str,
    detail: str,
    *,
    severity: str = "critical",
    remedy: str | None = None,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "status": status,
        "severity": severity,
        "detail": detail,
    }
    if remedy:
        payload["remedy"] = remedy
    return payload


def _codex_app_server_secret_paths(auth_summary: dict[str, Any] | None) -> list[str]:
    if not auth_summary:
        return []
    paths = []
    for key in ("token_file", "shared_secret_file"):
        value = str(auth_summary.get(key) or "").strip()
        if value:
            paths.append(value)
    return paths


def codex_app_server_doctor(
    *,
    workflow_root: Path,
    mode: str = "managed",
    service_name: str | None = None,
    endpoint: str | None = None,
    healthcheck_path: str = DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH,
    ws_token_file: str | None = None,
    ws_token_sha256: str | None = None,
    ws_shared_secret_file: str | None = None,
    ws_issuer: str | None = None,
    ws_audience: str | None = None,
    ws_max_clock_skew_seconds: int | None = None,
) -> dict[str, Any]:
    if mode not in {"managed", "external"}:
        raise DaedalusCommandError("--mode must be managed or external")

    resolved_service_name = _codex_app_server_service_name(
        workflow_root=workflow_root,
        service_name=service_name,
    )
    unit_path = _codex_app_server_unit_path(resolved_service_name)
    effective_endpoint = str(endpoint or "").strip()
    if not effective_endpoint and mode == "managed":
        effective_endpoint = _codex_app_server_listen_from_unit(unit_path) or DEFAULT_CODEX_APP_SERVER_LISTEN
    if not effective_endpoint:
        effective_endpoint = DEFAULT_CODEX_APP_SERVER_LISTEN

    _auth_args, cli_auth_summary = _codex_app_server_ws_auth_args(
        ws_token_file=ws_token_file,
        ws_token_sha256=ws_token_sha256,
        ws_shared_secret_file=ws_shared_secret_file,
        ws_issuer=ws_issuer,
        ws_audience=ws_audience,
        ws_max_clock_skew_seconds=ws_max_clock_skew_seconds,
    )
    unit_auth_summary = _codex_app_server_auth_summary_from_unit(unit_path) if mode == "managed" else None
    auth_summary = cli_auth_summary or unit_auth_summary
    if auth_summary and auth_summary is cli_auth_summary:
        auth_summary = {**auth_summary, "source": "cli"}

    checks: list[dict[str, Any]] = []
    status_result: dict[str, Any] | None = None
    if mode == "managed":
        status_result = codex_app_server_status(
            workflow_root=workflow_root,
            service_name=resolved_service_name,
            endpoint=effective_endpoint,
            healthcheck_path=healthcheck_path,
        )
        checks.append(
            _codex_app_server_doctor_check(
                "managed-unit-file",
                "pass" if unit_path.exists() else "fail",
                str(unit_path),
                remedy="run `hermes daedalus codex-app-server install` or `up`",
            )
        )
        active = str(status_result.get("active") or "unknown")
        checks.append(
            _codex_app_server_doctor_check(
                "managed-service-active",
                "pass" if active == "active" else "fail",
                active,
                remedy="run `hermes daedalus codex-app-server up` or inspect `logs`",
            )
        )
        enabled = str(status_result.get("enabled") or "unknown")
        checks.append(
            _codex_app_server_doctor_check(
                "managed-service-enabled",
                "pass" if enabled == "enabled" else "warn",
                enabled,
                severity="warning",
                remedy="run `hermes daedalus codex-app-server up` if the listener should start on login",
            )
        )
        ready = status_result.get("ready") or {}
    else:
        ready = _codex_app_server_readyz(endpoint=effective_endpoint, healthcheck_path=healthcheck_path)
        checks.append(
            _codex_app_server_doctor_check(
                "managed-unit-file",
                "skip",
                "external mode uses a listener started outside Daedalus",
                severity="info",
            )
        )

    parsed_endpoint = urlparse(effective_endpoint)
    endpoint_shape_ok = parsed_endpoint.scheme == "ws" and bool(parsed_endpoint.hostname) and bool(parsed_endpoint.port)
    checks.append(
        _codex_app_server_doctor_check(
            "endpoint-shape",
            "pass" if endpoint_shape_ok else "fail",
            effective_endpoint,
            remedy="use a ws://host:port endpoint such as ws://127.0.0.1:4500",
        )
    )

    if ready.get("ok") is True:
        checks.append(_codex_app_server_doctor_check("readyz", "pass", f"{effective_endpoint}{healthcheck_path}"))
    elif ready.get("checked") is False:
        checks.append(
            _codex_app_server_doctor_check(
                "readyz",
                "warn",
                str(ready.get("reason") or "readiness probe was skipped"),
                severity="warning",
            )
        )
    else:
        checks.append(
            _codex_app_server_doctor_check(
                "readyz",
                "fail",
                str(ready.get("reason") or "readiness probe failed"),
                remedy="start the listener or inspect `hermes daedalus codex-app-server logs`",
            )
        )

    missing_secret_paths = [path for path in _codex_app_server_secret_paths(auth_summary) if not Path(path).exists()]
    if missing_secret_paths:
        checks.append(
            _codex_app_server_doctor_check(
                "websocket-auth",
                "fail",
                "missing secret file(s): " + ", ".join(missing_secret_paths),
                remedy="create the configured secret files or reinstall the Codex app-server unit with valid auth flags",
            )
        )
    elif auth_summary:
        checks.append(
            _codex_app_server_doctor_check(
                "websocket-auth",
                "pass",
                f"{auth_summary.get('mode')} from {auth_summary.get('source', 'config')}",
            )
        )
    elif _codex_app_server_endpoint_is_loopback(effective_endpoint):
        checks.append(
            _codex_app_server_doctor_check(
                "websocket-auth",
                "pass",
                "loopback endpoint does not require WebSocket auth",
            )
        )
    else:
        checks.append(
            _codex_app_server_doctor_check(
                "websocket-auth",
                "fail",
                "non-loopback endpoint has no declared WebSocket auth",
                remedy="use --ws-token-file, --ws-token-sha256, or --ws-shared-secret-file",
            )
        )

    scheduler = _load_codex_scheduler_snapshot(workflow_root)
    if scheduler.get("error"):
        checks.append(
            _codex_app_server_doctor_check(
                "scheduler-thread-map",
                "fail",
                f"{scheduler.get('path')}: {scheduler.get('error')}",
                remedy="repair the shared engine SQLite state",
            )
        )
    elif not scheduler.get("exists"):
        checks.append(
            _codex_app_server_doctor_check(
                "scheduler-thread-map",
                "warn",
                f"{scheduler.get('path')} does not exist yet",
                severity="warning",
            )
        )
    elif scheduler.get("invalid_thread_count"):
        checks.append(
            _codex_app_server_doctor_check(
                "scheduler-thread-map",
                "fail",
                f"{scheduler.get('invalid_thread_count')} Codex thread mapping(s) are missing thread_id",
                remedy="let the workflow retry the affected work item or clear the invalid mapping",
            )
        )
    else:
        checks.append(
            _codex_app_server_doctor_check(
                "scheduler-thread-map",
                "pass",
                f"{len(scheduler.get('threads') or [])} Codex thread mapping(s)",
            )
        )

    ok = all(check.get("status") != "fail" for check in checks)
    return {
        "ok": ok,
        "action": "doctor",
        "mode": mode,
        "workflow_root": str(workflow_root),
        "service_name": resolved_service_name,
        "unit_path": str(unit_path),
        "endpoint": effective_endpoint,
        "healthcheck_path": healthcheck_path,
        "ws_auth": auth_summary,
        "status": status_result,
        "ready": ready,
        "scheduler": scheduler,
        "threads": scheduler.get("threads") or [],
        "checks": checks,
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
    workflow_name = _assert_service_mode_supported(workflow_root=workflow_root, service_mode=service_mode)
    workspace = workflow_root.name
    resolved_service_name = _resolve_service_name(
        service_name=service_name, service_mode=service_mode, workspace=workspace
    )
    result = _run_systemctl(action, *extra_args, resolved_service_name)
    return {
        "action": action,
        "workflow": workflow_name,
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
    workflow_name = _assert_service_mode_supported(workflow_root=workflow_root, service_mode=service_mode)
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
        "workflow": workflow_name,
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
    workflow_name = _assert_service_mode_supported(workflow_root=workflow_root, service_mode=service_mode)
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
        "workflow": workflow_name,
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
    engine_store = EngineStore(
        db_path=paths["db_path"],
        workflow="change-delivery",
        now_iso=lambda: now_iso,
        now_epoch=lambda: float(now_epoch or time.time()),
    )
    lease_info = engine_store.lease_status(
        lease_scope=daedalus.RUNTIME_LEASE_SCOPE,
        lease_key=daedalus.RUNTIME_LEASE_KEY,
        heartbeat_at=runtime_status.get("latest_heartbeat_at"),
        active_owner_instance_id=runtime_status.get("active_orchestrator_instance_id"),
    )
    if lease_info.get("stale"):
        warnings.append(
            "stale runtime heartbeat/lease: " + ", ".join(lease_info.get("stale_reasons") or [])
        )

    conn = sqlite3.connect(paths["db_path"])
    conn.row_factory = sqlite3.Row
    try:
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

    event_retention = _workflow_event_retention(workflow_root)
    event_stats = EngineStore(
        db_path=runtime_paths(workflow_root)["db_path"],
        workflow="change-delivery",
    ).event_stats(event_retention)
    retention = event_stats.get("retention") or {}
    retention_reasons = []
    if not retention.get("configured") and event_stats.get("total_events"):
        retention_reasons.append("not-configured")
    if retention.get("excess_rows"):
        retention_reasons.append("row-limit-exceeded")
    if retention.get("age_overdue"):
        retention_reasons.append("age-limit-exceeded")
    checks.append(
        _make_check(
            code="engine_event_retention",
            status="warn" if retention_reasons else "pass",
            severity="warning" if retention_reasons else "info",
            summary=(
                "Engine event retention needs attention"
                if retention_reasons
                else "No engine event retention issue detected"
            ),
            details={
                "reasons": retention_reasons,
                "total_events": event_stats.get("total_events"),
                "oldest_event_at": event_stats.get("oldest_event_at"),
                "oldest_age_seconds": event_stats.get("oldest_age_seconds"),
                "retention": retention,
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
    """Lazy import so importing the CLI doesn't pull rich into every invocation."""
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
        "issue-runner": PLUGIN_DIR / "workflows" / "issue_runner" / "workflow.template.md",
    }
    path = templates.get(workflow_name)
    if path is None:
        raise DaedalusCommandError(f"no bundled workflow template for {workflow_name!r}")
    return path


_REMOTE_OWNER_REPO_RE = re.compile(
    r"(?P<owner>[^/:]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
_REMOTE_SCP_RE = re.compile(
    r"^[^@]+@[^:]+:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
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


def _repo_slug_from_remote_url(remote_url: str) -> str:
    raw = remote_url.strip()
    match = _REMOTE_SCP_RE.match(raw) or _REMOTE_OWNER_REPO_RE.search(raw)
    if not match:
        raise DaedalusCommandError(
            "unable to derive --repo-slug from git origin; pass --repo-slug owner/repo explicitly"
        )
    owner = match.group("owner").strip()
    repo = match.group("repo").strip()
    if not owner or not repo:
        raise DaedalusCommandError(
            "unable to derive --repo-slug from git origin; pass --repo-slug owner/repo explicitly"
        )
    return f"{owner}/{repo}"


def _repo_workflow_contract_candidates(repo_root: Path) -> list[Path]:
    return sorted(
        path.resolve()
        for path in repo_root.glob("WORKFLOW*.md")
        if path.is_file()
    )


def _single_repo_contract_path(repo_root: Path) -> Path:
    default_path = repo_root / "WORKFLOW.md"
    if default_path.exists():
        return default_path
    candidates = _repo_workflow_contract_candidates(repo_root)
    if len(candidates) == 1:
        return candidates[0]
    raise DaedalusCommandError(
        "unable to infer a single workflow contract path; "
        "use explicit workflow naming and bootstrap one workflow at a time"
    )


def _prepare_repo_contract_paths(
    *,
    repo_root: Path,
    workflow_name: str,
    force: bool,
) -> tuple[Path, list[tuple[Path, Path]]]:
    repo_root = repo_root.resolve()
    default_path = repo_root / "WORKFLOW.md"
    named_path = workflow_named_markdown_path(repo_root, workflow_name)

    if named_path.exists():
        return named_path, []

    if default_path.exists():
        try:
            existing_contract = load_workflow_contract_file(default_path)
        except (WorkflowContractError, OSError, UnicodeDecodeError) as exc:
            raise DaedalusCommandError(
                f"{default_path} exists but is not a Daedalus workflow contract; "
                "expected YAML front matter with a top-level `workflow:` field"
            ) from exc
        existing_workflow = str(existing_contract.config.get("workflow") or "").strip()
        if existing_workflow == workflow_name:
            return default_path, []
        if not existing_workflow:
            raise DaedalusCommandError(
                f"{default_path} exists but is not a Daedalus workflow contract; "
                "expected YAML front matter with a top-level `workflow:` field"
            )
        migrated_path = workflow_named_markdown_path(repo_root, existing_workflow)
        if migrated_path.exists():
            raise DaedalusCommandError(
                f"cannot promote {default_path.name} into multi-workflow form because "
                f"{migrated_path.name} already exists; Daedalus will not overwrite "
                "repo-owned workflow contracts"
            )
        return named_path, [(default_path, migrated_path)]

    existing = find_repo_workflow_contract_path(repo_root, workflow_name=workflow_name)
    if existing is not None:
        return existing, []

    if _repo_workflow_contract_candidates(repo_root):
        return named_path, []
    return default_path, []


def _git_branch_exists(branch_name: str, *, cwd: Path) -> bool:
    completed = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _current_git_branch(cwd: Path) -> str | None:
    completed = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    branch = completed.stdout.strip()
    return branch or None


def _ensure_bootstrap_branch(*, repo_root: Path, workflow_name: str) -> str:
    branch_name = f"daedalus/bootstrap-{workflow_name}"
    current_branch = _current_git_branch(repo_root)
    if current_branch == branch_name:
        return branch_name
    if _git_branch_exists(branch_name, cwd=repo_root):
        _git_stdout("checkout", branch_name, cwd=repo_root)
        return branch_name
    _git_stdout("checkout", "-b", branch_name, cwd=repo_root)
    return branch_name


def _git_path_is_tracked(*, repo_root: Path, path: Path) -> bool:
    try:
        relpath = str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return False
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relpath],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _commit_bootstrap_contract(
    *,
    repo_root: Path,
    workflow_name: str,
    paths: list[Path],
) -> dict[str, Any]:
    branch_name = _ensure_bootstrap_branch(repo_root=repo_root, workflow_name=workflow_name)
    relpaths = []
    for path in paths:
        resolved = path.resolve()
        try:
            relpath = str(resolved.relative_to(repo_root.resolve()))
        except ValueError as exc:
            raise DaedalusCommandError(f"cannot commit path outside repo root: {resolved}") from exc
        if resolved.exists() or _git_path_is_tracked(repo_root=repo_root, path=resolved):
            relpaths.append(relpath)
    relpaths = sorted(set(relpaths))
    subprocess.run(
        ["git", "add", "--", *relpaths],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *relpaths],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    committed = False
    commit_sha = None
    commit_message = f"Add {workflow_name} workflow contract"
    if status.stdout.strip():
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Daedalus",
                "-c",
                "user.email=daedalus@local",
                "commit",
                "-m",
                commit_message,
            ],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        committed = True
        commit_sha = _git_stdout("rev-parse", "HEAD", cwd=repo_root)
    return {
        "branch": branch_name,
        "committed": committed,
        "commit_sha": commit_sha,
        "commit_message": commit_message if committed else None,
        "paths": [str(path) for path in paths],
    }


def bootstrap_workflow_root(
    *,
    repo_path: Path | None,
    workflow_name: str,
    workflow_root: Path | None,
    repo_slug: str | None,
    active_lane_label: str,
    engine_owner: str,
    force: bool,
) -> dict[str, Any]:
    repo_root = _discover_git_repo_root(repo_path)
    remote_url = None
    resolved_repo_slug = (repo_slug or "").strip()
    if not resolved_repo_slug:
        remote_url = _git_stdout("remote", "get-url", "origin", cwd=repo_root)
        resolved_repo_slug = _repo_slug_from_remote_url(remote_url)

    try:
        instance_name = derive_workflow_instance_name(
            repo_slug=resolved_repo_slug,
            workflow_name=workflow_name,
        )
    except ValueError as exc:
        raise DaedalusCommandError(f"--repo-slug {resolved_repo_slug!r} is invalid: {exc}") from exc

    resolved_workflow_root = (
        workflow_root.expanduser().resolve()
        if workflow_root is not None
        else (Path.home() / ".hermes" / "workflows" / instance_name).resolve()
    )

    result = scaffold_workflow_root(
        workflow_root=resolved_workflow_root,
        workflow_name=workflow_name,
        repo_path=repo_root,
        repo_slug=resolved_repo_slug,
        active_lane_label=active_lane_label,
        engine_owner=engine_owner,
        force=force,
    )
    pointer_path = repo_local_workflow_pointer_path(repo_root)
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(str(resolved_workflow_root) + "\n", encoding="utf-8")
    next_command = "hermes daedalus service-up"
    commit_result = _commit_bootstrap_contract(
        repo_root=repo_root,
        workflow_name=workflow_name,
        paths=[
            Path(result["contract_path"]),
            *[Path(path) for path in result.get("renamed_contract_paths") or []],
            *[Path(path) for path in result.get("renamed_contract_source_paths") or []],
        ],
    )

    result.update(
        {
            "bootstrap": True,
            "detected_repo_root": str(repo_root),
            "remote_url": remote_url,
            "repo_pointer_path": str(pointer_path),
            "next_edit_path": result["contract_path"],
            "next_command": next_command,
            "git_branch": commit_result["branch"],
            "git_committed": commit_result["committed"],
            "git_commit_sha": commit_result["commit_sha"],
            "git_commit_message": commit_result["commit_message"],
        }
    )
    return result


def scaffold_workflow_root(
    *,
    workflow_root: Path,
    workflow_name: str,
    repo_path: Path | None,
    repo_slug: str,
    active_lane_label: str,
    engine_owner: str,
    force: bool,
) -> dict[str, Any]:
    root = workflow_root.expanduser().resolve()
    repo_root = _discover_git_repo_root(repo_path)
    contract_path, rename_pairs = _prepare_repo_contract_paths(
        repo_root=repo_root,
        workflow_name=workflow_name,
        force=force,
    )
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

    resolved_repo_slug = repo_slug.strip()
    if not resolved_repo_slug:
        raise DaedalusCommandError("--repo-slug cannot be blank")
    try:
        resolved_instance_name = derive_workflow_instance_name(
            repo_slug=resolved_repo_slug,
            workflow_name=workflow_name,
        )
    except ValueError as exc:
        raise DaedalusCommandError(f"--repo-slug {resolved_repo_slug!r} is invalid: {exc}") from exc
    if root.name != resolved_instance_name:
        expected_root = root.parent / resolved_instance_name
        raise DaedalusCommandError(
            "workflow root directory name must follow <owner>-<repo>-<workflow-type>: "
            f"expected {expected_root} for repo-slug={resolved_repo_slug!r} "
            f"and workflow={workflow_name!r}"
        )

    resolved_repo_path = repo_root

    config["workflow"] = workflow_name
    instance_cfg = config.setdefault("instance", {})
    repository_cfg = config.setdefault("repository", {})

    instance_cfg["name"] = resolved_instance_name
    instance_cfg["engine-owner"] = engine_owner
    repository_cfg["local-path"] = str(resolved_repo_path)
    repository_cfg["slug"] = resolved_repo_slug
    if workflow_name == "change-delivery":
        tracker_cfg = config.setdefault("tracker", {})
        code_host_cfg = config.setdefault("code-host", {})
        repository_cfg["active-lane-label"] = active_lane_label
        tracker_cfg["kind"] = "github"
        tracker_cfg["github_slug"] = resolved_repo_slug
        code_host_cfg["kind"] = "github"
        code_host_cfg["github_slug"] = resolved_repo_slug
    triggers_cfg = config.get("triggers")
    if isinstance(triggers_cfg, dict):
        lane_selector_cfg = triggers_cfg.get("lane-selector")
        if isinstance(lane_selector_cfg, dict):
            lane_selector_cfg["label"] = active_lane_label

    created_dirs = [
        root / "config",
        root / "memory",
        root / "state" / "sessions",
        root / "runtime" / "logs",
        root / "runtime" / "memory",
        root / "runtime" / "state" / "daedalus",
        root / "workspace",
    ]
    for path in created_dirs:
        path.mkdir(parents=True, exist_ok=True)

    renamed_contract_paths: list[str] = []
    renamed_contract_source_paths: list[str] = []
    for source_path, target_path in rename_pairs:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.replace(target_path)
        renamed_contract_paths.append(str(target_path))
        renamed_contract_source_paths.append(str(source_path))

    contract_path.write_text(
        render_workflow_markdown(config=config, prompt_template=workflow_policy),
        encoding="utf-8",
    )
    write_workflow_contract_pointer(root, contract_path)
    if workflow_name == "issue-runner":
        issues_template = PLUGIN_DIR / "workflows" / "issue_runner" / "issues.template.json"
        issues_path = root / "config" / "issues.json"
        issues_path.write_text(issues_template.read_text(encoding="utf-8"), encoding="utf-8")
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
        "repo_slug": resolved_repo_slug,
        "active_lane_label": active_lane_label,
        "force": force,
        "workflow_contract_pointer_path": str(workflow_contract_pointer_path(root)),
        "renamed_contract_paths": renamed_contract_paths,
        "renamed_contract_source_paths": renamed_contract_source_paths,
    }


def cmd_scaffold_workflow(args, parser) -> str:
    result = scaffold_workflow_root(
        workflow_root=Path(args.workflow_root),
        workflow_name=args.workflow,
        repo_path=Path(args.repo_path) if args.repo_path else None,
        repo_slug=args.repo_slug,
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
        f"repo-slug: {result['repo_slug']}",
    ]
    return "\n".join(lines)


def cmd_bootstrap_workflow(args, parser) -> str:
    result = bootstrap_workflow_root(
        repo_path=Path(args.repo_path) if args.repo_path else None,
        workflow_name=args.workflow,
        workflow_root=Path(args.workflow_root) if args.workflow_root else None,
        repo_slug=args.repo_slug,
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
        f"repo-slug: {result['repo_slug']}",
        f"git branch: {result['git_branch']}",
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

    validate_cmd = sub.add_parser("validate", help="Validate the repo-owned WORKFLOW.md contract and workflow preflight rules.")
    validate_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    validate_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES))
    validate_cmd.add_argument("--json", action="store_true")
    validate_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    validate_cmd.set_defaults(func=run_cli_command)

    runs_cmd = sub.add_parser("runs", help="Inspect durable engine run history and run timelines.")
    runs_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    runs_cmd.add_argument("runs_action", nargs="?", default="list", choices=["list", "failed", "stale", "show"])
    runs_cmd.add_argument("run_id", nargs="?")
    runs_cmd.add_argument("--limit", type=int, default=20)
    runs_cmd.add_argument("--stale-seconds", type=int, default=600)
    runs_cmd.add_argument("--json", action="store_true")
    runs_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    runs_cmd.set_defaults(func=run_cli_command)

    events_cmd = sub.add_parser("events", help="Inspect and prune the durable engine event ledger.")
    events_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    events_cmd.add_argument("events_action", nargs="?", default="list", choices=["list", "stats", "prune"])
    events_cmd.add_argument("--run-id")
    events_cmd.add_argument("--work-id")
    events_cmd.add_argument("--type", dest="event_type")
    events_cmd.add_argument("--severity")
    events_cmd.add_argument("--limit", type=int, default=50)
    events_cmd.add_argument("--order", choices=["asc", "desc"], default="desc")
    events_cmd.add_argument("--max-age-days", type=float)
    events_cmd.add_argument("--max-rows", type=int)
    events_cmd.add_argument("--json", action="store_true")
    events_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    events_cmd.set_defaults(func=run_cli_command)

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

    service_loop_cmd = sub.add_parser(
        "service-loop",
        help="Run the managed long-lived workflow loop used by the supervised systemd service.",
    )
    service_loop_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    service_loop_cmd.add_argument("--project-key")
    service_loop_cmd.add_argument("--instance-id")
    service_loop_cmd.add_argument("--interval-seconds", type=int, default=30)
    service_loop_cmd.add_argument("--max-iterations", type=int)
    service_loop_cmd.add_argument("--service-mode", choices=sorted(SERVICE_PROFILES), default="active")
    service_loop_cmd.add_argument("--json", action="store_true")
    service_loop_cmd.set_defaults(func=run_cli_command)

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

    scaffold_cmd = sub.add_parser(
        "scaffold-workflow",
        help="Create a new workflow root and repo-owned workflow contract.",
    )
    scaffold_cmd.add_argument(
        "--workflow-root",
        type=Path,
        required=True,
        help="Workflow root to create. Directory name must be <owner>-<repo>-<workflow-type>.",
    )
    scaffold_cmd.add_argument("--workflow", default="issue-runner", choices=["change-delivery", "issue-runner"])
    scaffold_cmd.add_argument("--repo-path", type=Path)
    scaffold_cmd.add_argument("--repo-slug", required=True, help="Repository identity in owner/repo form for workflow instance naming.")
    scaffold_cmd.add_argument("--active-lane-label", default="active-lane")
    scaffold_cmd.add_argument("--engine-owner", default="hermes", choices=["hermes", "openclaw"])
    scaffold_cmd.add_argument("--force", action="store_true")
    scaffold_cmd.add_argument("--json", action="store_true")
    scaffold_cmd.set_defaults(handler=cmd_scaffold_workflow, func=run_cli_command)

    bootstrap_cmd = sub.add_parser(
        "bootstrap",
        help="Infer repo settings from the current git checkout and scaffold a repo-owned workflow contract.",
    )
    bootstrap_cmd.add_argument("--repo-path", type=Path, help="Git checkout to inspect (defaults to current working directory).")
    bootstrap_cmd.add_argument("--workflow-root", type=Path, help="Optional explicit workflow root override.")
    bootstrap_cmd.add_argument("--workflow", default="issue-runner", choices=["change-delivery", "issue-runner"])
    bootstrap_cmd.add_argument("--repo-slug", help="Override the inferred repository slug from git origin.")
    bootstrap_cmd.add_argument("--active-lane-label", default="active-lane")
    bootstrap_cmd.add_argument("--engine-owner", default="hermes", choices=["hermes", "openclaw"])
    bootstrap_cmd.add_argument("--force", action="store_true")
    bootstrap_cmd.add_argument("--json", action="store_true")
    bootstrap_cmd.set_defaults(handler=cmd_bootstrap_workflow, func=run_cli_command)

    codex_cmd = sub.add_parser(
        "codex-app-server",
        help="Install and control the shared Codex app-server systemd user service.",
    )
    codex_sub = codex_cmd.add_subparsers(dest="codex_app_server_command")
    codex_sub.required = True

    def _add_codex_app_server_auth_args(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--ws-token-file", help="Absolute token file for capability-token WebSocket auth.")
        cmd.add_argument("--ws-token-sha256", help="SHA-256 verifier for capability-token WebSocket auth.")
        cmd.add_argument("--ws-shared-secret-file", help="Absolute secret file for signed-bearer-token WebSocket auth.")
        cmd.add_argument("--ws-issuer")
        cmd.add_argument("--ws-audience")
        cmd.add_argument("--ws-max-clock-skew-seconds", type=int)

    codex_install_cmd = codex_sub.add_parser("install", help="Write the Codex app-server user unit.")
    codex_install_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_install_cmd.add_argument("--listen", default=DEFAULT_CODEX_APP_SERVER_LISTEN)
    codex_install_cmd.add_argument("--service-name")
    codex_install_cmd.add_argument("--codex-command", default="codex")
    _add_codex_app_server_auth_args(codex_install_cmd)
    codex_install_cmd.add_argument("--json", action="store_true")
    codex_install_cmd.set_defaults(func=run_cli_command)

    codex_up_cmd = codex_sub.add_parser("up", help="Install, enable, and start the Codex app-server user unit.")
    codex_up_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_up_cmd.add_argument("--listen", default=DEFAULT_CODEX_APP_SERVER_LISTEN)
    codex_up_cmd.add_argument("--service-name")
    codex_up_cmd.add_argument("--codex-command", default="codex")
    _add_codex_app_server_auth_args(codex_up_cmd)
    codex_up_cmd.add_argument("--json", action="store_true")
    codex_up_cmd.set_defaults(func=run_cli_command)

    codex_status_cmd = codex_sub.add_parser("status", help="Show Codex app-server user unit status.")
    codex_status_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_status_cmd.add_argument("--service-name")
    codex_status_cmd.add_argument("--endpoint", default=DEFAULT_CODEX_APP_SERVER_LISTEN)
    codex_status_cmd.add_argument("--healthcheck-path", default=DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH)
    codex_status_cmd.add_argument("--json", action="store_true")
    codex_status_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    codex_status_cmd.set_defaults(func=run_cli_command)

    codex_doctor_cmd = codex_sub.add_parser("doctor", help="Run actionable Codex app-server diagnostics.")
    codex_doctor_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_doctor_cmd.add_argument("--mode", choices=["managed", "external"], default="managed")
    codex_doctor_cmd.add_argument("--service-name")
    codex_doctor_cmd.add_argument("--endpoint")
    codex_doctor_cmd.add_argument("--healthcheck-path", default=DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH)
    _add_codex_app_server_auth_args(codex_doctor_cmd)
    codex_doctor_cmd.add_argument("--json", action="store_true")
    codex_doctor_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    codex_doctor_cmd.set_defaults(func=run_cli_command)

    codex_down_cmd = codex_sub.add_parser("down", help="Stop and disable the Codex app-server user unit.")
    codex_down_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_down_cmd.add_argument("--service-name")
    codex_down_cmd.add_argument("--json", action="store_true")
    codex_down_cmd.set_defaults(func=run_cli_command)

    codex_restart_cmd = codex_sub.add_parser("restart", help="Restart the Codex app-server user unit.")
    codex_restart_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_restart_cmd.add_argument("--service-name")
    codex_restart_cmd.add_argument("--endpoint", default=DEFAULT_CODEX_APP_SERVER_LISTEN)
    codex_restart_cmd.add_argument("--healthcheck-path", default=DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH)
    codex_restart_cmd.add_argument("--json", action="store_true")
    codex_restart_cmd.set_defaults(func=run_cli_command)

    codex_logs_cmd = codex_sub.add_parser("logs", help="Show recent logs for the Codex app-server user unit.")
    codex_logs_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_logs_cmd.add_argument("--service-name")
    codex_logs_cmd.add_argument("--lines", type=int, default=50)
    codex_logs_cmd.add_argument("--json", action="store_true")
    codex_logs_cmd.set_defaults(func=run_cli_command)

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
        # reference set by string-returning subcommands such as watch.
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
    eventless_commands = {"codex-app-server", "runs", "events", "validate"}
    if (
        workflow_root is not None
        and daedalus is not None
        and getattr(args, "daedalus_command", None)
        and args.daedalus_command not in eventless_commands
    ):
        _record_operator_command_event(workflow_root=workflow_root, args=args, daedalus=daedalus)
    command = getattr(args, "daedalus_command", None)
    project_key_commands = {"init", "start", "service-install", "service-up", "service-loop", "run-shadow", "run-active"}
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
        workflow_name = _workflow_name_for_root(workflow_root)
        if workflow_name == "issue-runner":
            return _build_issue_runner_status(workflow_root)
        return daedalus.get_runtime_status(workflow_root=workflow_root)
    if args.daedalus_command == "shadow-report":
        return build_shadow_report(
            workflow_root=workflow_root,
            recent_actions_limit=args.recent_actions_limit,
        )
    if args.daedalus_command == "doctor":
        workflow_name = _workflow_name_for_root(workflow_root)
        if workflow_name == "issue-runner":
            return _build_issue_runner_doctor(workflow_root)
        return build_doctor_report(
            workflow_root=workflow_root,
            recent_actions_limit=args.recent_actions_limit,
        )
    if args.daedalus_command == "validate":
        return build_validate_report(
            workflow_root=workflow_root,
            service_mode=getattr(args, "service_mode", None),
        )
    if args.daedalus_command == "runs":
        return build_runs_report(
            workflow_root=workflow_root,
            action=args.runs_action,
            run_id=args.run_id,
            limit=args.limit,
            stale_seconds=args.stale_seconds,
        )
    if args.daedalus_command == "events":
        return build_events_report(
            workflow_root=workflow_root,
            action=args.events_action,
            run_id=args.run_id,
            work_id=args.work_id,
            event_type=args.event_type,
            severity=args.severity,
            limit=args.limit,
            order=args.order,
            max_age_days=args.max_age_days,
            max_rows=args.max_rows,
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
    if args.daedalus_command == "service-loop":
        return service_loop(
            workflow_root=workflow_root,
            project_key=resolved_project_key,
            instance_id=args.instance_id,
            interval_seconds=args.interval_seconds,
            max_iterations=args.max_iterations,
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
    if args.daedalus_command == "codex-app-server":
        action = args.codex_app_server_command
        if action == "install":
            return codex_app_server_install(
                workflow_root=workflow_root,
                listen=args.listen,
                service_name=args.service_name,
                codex_command=args.codex_command,
                ws_token_file=args.ws_token_file,
                ws_token_sha256=args.ws_token_sha256,
                ws_shared_secret_file=args.ws_shared_secret_file,
                ws_issuer=args.ws_issuer,
                ws_audience=args.ws_audience,
                ws_max_clock_skew_seconds=args.ws_max_clock_skew_seconds,
            )
        if action == "up":
            return codex_app_server_up(
                workflow_root=workflow_root,
                listen=args.listen,
                service_name=args.service_name,
                codex_command=args.codex_command,
                ws_token_file=args.ws_token_file,
                ws_token_sha256=args.ws_token_sha256,
                ws_shared_secret_file=args.ws_shared_secret_file,
                ws_issuer=args.ws_issuer,
                ws_audience=args.ws_audience,
                ws_max_clock_skew_seconds=args.ws_max_clock_skew_seconds,
            )
        if action == "status":
            return codex_app_server_status(
                workflow_root=workflow_root,
                service_name=args.service_name,
                endpoint=args.endpoint,
                healthcheck_path=args.healthcheck_path,
            )
        if action == "doctor":
            return codex_app_server_doctor(
                workflow_root=workflow_root,
                mode=args.mode,
                service_name=args.service_name,
                endpoint=args.endpoint,
                healthcheck_path=args.healthcheck_path,
                ws_token_file=args.ws_token_file,
                ws_token_sha256=args.ws_token_sha256,
                ws_shared_secret_file=args.ws_shared_secret_file,
                ws_issuer=args.ws_issuer,
                ws_audience=args.ws_audience,
                ws_max_clock_skew_seconds=args.ws_max_clock_skew_seconds,
            )
        if action == "down":
            return codex_app_server_down(
                workflow_root=workflow_root,
                service_name=args.service_name,
            )
        if action == "restart":
            return codex_app_server_restart(
                workflow_root=workflow_root,
                service_name=args.service_name,
                endpoint=args.endpoint,
                healthcheck_path=args.healthcheck_path,
            )
        if action == "logs":
            return codex_app_server_logs(
                workflow_root=workflow_root,
                service_name=args.service_name,
                lines=args.lines,
            )
        raise DaedalusCommandError(f"unknown codex-app-server command: {action}")
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
    if command == "validate":
        checks = result.get("checks") or []
        failures = result.get("failures") or []
        warnings = result.get("warnings") or []
        lines = [
            f"workflow contract valid={result.get('ok')} workflow={result.get('workflow')}",
            f"source={result.get('source_path')}",
            f"checks={len(checks)} failures={len(failures)} warnings={len(warnings)}",
        ]
        for check in checks:
            prefix = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}.get(
                str(check.get("status")),
                str(check.get("status")).upper(),
            )
            lines.append(f"- {prefix} {check.get('name')}: {check.get('detail')}")
            for item in (check.get("items") or [])[:5]:
                path = item.get("path") if isinstance(item, dict) else None
                message = item.get("message") if isinstance(item, dict) else str(item)
                lines.append(f"  {path or '<root>'}: {message}")
        return "\n".join(lines)
    if command == "runs":
        if result.get("mode") == "show":
            run = result.get("run") or {}
            lines = [
                f"run={run.get('run_id')}",
                f"workflow={result.get('workflow')} mode={run.get('mode')} status={run.get('status')}",
                f"started_at={run.get('started_at')} completed_at={run.get('completed_at')}",
                f"selected={run.get('selected_count')} completed={run.get('completed_count')} age_seconds={run.get('age_seconds')}",
            ]
            if run.get("error"):
                lines.append(f"error={run.get('error')}")
            timeline = result.get("timeline") or []
            lines.append(f"timeline_events={len(timeline)}")
            for event in timeline[:10]:
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                kind = (
                    event.get("event")
                    or payload.get("event")
                    or event.get("action")
                    or payload.get("action")
                    or event.get("event_type")
                    or "event"
                )
                at = event.get("at") or payload.get("at") or event.get("created_at") or event.get("time") or ""
                detail = (
                    event.get("summary")
                    or payload.get("summary")
                    or event.get("error")
                    or payload.get("error")
                    or event.get("reason")
                    or payload.get("reason")
                    or ""
                )
                lines.append(f"- {at} {kind} {detail}".strip())
            return "\n".join(lines)
        runs = result.get("runs") or []
        if not runs:
            return f"workflow={result.get('workflow')} runs=0 mode={result.get('mode')}"
        lines = [f"workflow={result.get('workflow')} mode={result.get('mode')} runs={len(runs)}"]
        for run in runs:
            stale = " stale=true" if run.get("stale") else ""
            lines.append(
                f"- {run.get('run_id')} {run.get('mode')} {run.get('status')} "
                f"selected={run.get('selected_count')} completed={run.get('completed_count')} "
                f"started={run.get('started_at')}{stale}"
            )
        return "\n".join(lines)
    if command == "events":
        if result.get("mode") == "stats":
            stats = result.get("stats") or {}
            retention = stats.get("retention") or {}
            lines = [
                f"workflow={result.get('workflow')} total_events={stats.get('total_events')}",
                f"oldest_event_at={stats.get('oldest_event_at')} oldest_age_seconds={stats.get('oldest_age_seconds')}",
                f"newest_event_at={stats.get('newest_event_at')}",
                (
                    f"retention_configured={retention.get('configured')} "
                    f"overdue={retention.get('overdue')} "
                    f"max_age_seconds={retention.get('max_age_seconds')} "
                    f"max_rows={retention.get('max_rows')} "
                    f"excess_rows={retention.get('excess_rows')}"
                ),
            ]
            if stats.get("by_type"):
                lines.append(f"by_type={stats.get('by_type')}")
            if stats.get("by_severity"):
                lines.append(f"by_severity={stats.get('by_severity')}")
            return "\n".join(lines)
        if result.get("mode") == "prune":
            retention = result.get("retention") or {}
            return (
                f"workflow={result.get('workflow')} pruned_events={result.get('deleted')} "
                f"remaining={result.get('remaining')} "
                f"max_age_days={retention.get('max_age_days')} max_rows={retention.get('max_rows')}"
            )
        events = result.get("events") or []
        filters = result.get("filters") or {}
        lines = [
            f"workflow={result.get('workflow')} events={len(events)}"
            + (f" filters={filters}" if filters else "")
        ]
        for event in events[:50]:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            detail = (
                payload.get("summary")
                or payload.get("error")
                or payload.get("reason")
                or event.get("work_id")
                or event.get("run_id")
                or ""
            )
            lines.append(
                f"- {event.get('created_at')} {event.get('severity')} "
                f"{event.get('event_type')} work={event.get('work_id') or '-'} "
                f"run={event.get('run_id') or '-'} {detail}".strip()
            )
        return "\n".join(lines)
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
    if command == "service-loop":
        last_result = result.get("last_result") or {}
        return (
            f"service-loop workflow={result.get('workflow')} mode={result.get('service_mode')} "
            f"loop={result.get('loop_status')} iterations={result.get('iterations')} "
            f"last_ok={last_result.get('ok')}"
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
    if command == "codex-app-server":
        action = result.get("action")
        if action == "install":
            return (
                f"codex-app-server installed service={result.get('service_name')} "
                f"listen={result.get('listen')} ok={result.get('ok')}"
            )
        if action == "up":
            status = result.get("status") or {}
            return (
                f"codex-app-server up service={result.get('service_name')} "
                f"listen={result.get('listen')} active={status.get('active')} "
                f"enabled={status.get('enabled')} ready={(status.get('ready') or {}).get('ok')}"
            )
        if action == "down":
            status = result.get("status") or {}
            return (
                f"codex-app-server down service={result.get('service_name')} "
                f"active={status.get('active')} enabled={status.get('enabled')}"
            )
        if action == "restart":
            status = result.get("status") or {}
            return (
                f"codex-app-server restart service={result.get('service_name')} "
                f"ok={result.get('ok')} active={status.get('active')} "
                f"ready={(status.get('ready') or {}).get('ok')}"
            )
        if action == "logs":
            output = result.get("stdout") or result.get("stderr") or ""
            return output if output else f"no logs for {result.get('service_name')}"
        if action == "status":
            ready = result.get("ready") or {}
            return (
                f"codex-app-server service={result.get('service_name')} "
                f"installed={result.get('installed')} active={result.get('active')} "
                f"enabled={result.get('enabled')} ready={ready.get('ok')}"
            )
        if action == "doctor":
            failed = [check for check in result.get("checks") or [] if check.get("status") == "fail"]
            warned = [check for check in result.get("checks") or [] if check.get("status") == "warn"]
            first_problem = failed[0] if failed else (warned[0] if warned else None)
            suffix = ""
            if first_problem:
                suffix = f" first_problem={first_problem.get('name')}:{first_problem.get('detail')}"
            return (
                f"codex-app-server doctor ok={result.get('ok')} mode={result.get('mode')} "
                f"endpoint={result.get('endpoint')} failures={len(failed)} warnings={len(warned)}"
                f"{suffix}"
            )
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
        # String-returning commands bypass execute_namespace, which only knows
        # about the legacy dict-returning branches.
        if args.daedalus_command == "watch":
            return _lazy_cmd_watch(args, parser)
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
