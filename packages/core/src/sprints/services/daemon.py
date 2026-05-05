"""Workflow daemon loop and systemd service controls."""

from __future__ import annotations

import os
import random
import shlex
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from sprints.engine import EngineStore
from sprints.core.config import WorkflowConfig
from sprints.core.contracts import load_workflow_contract
from sprints.core.paths import runtime_paths
from sprints.workflows.registry import run_cli
from sprints.workflows.status import build_status

WORKFLOW_DAEMON_SERVICE_PREFIX = "sprints-workflow"
DEFAULT_ACTIVE_INTERVAL_SECONDS = 15.0
DEFAULT_IDLE_INTERVAL_SECONDS = 60.0
DEFAULT_ERROR_INTERVAL_SECONDS = 60.0
DEFAULT_MAX_RETRY_SLEEP_SECONDS = 30.0
DEFAULT_LEASE_TTL_SECONDS = 90
DEFAULT_JITTER_RATIO = 0.15


class WorkflowDaemonError(Exception):
    pass


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _systemd_user_dir() -> Path:
    override = os.environ.get("SPRINTS_SYSTEMD_USER_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".config" / "systemd" / "user").resolve()


def _service_name(*, workflow_root: Path, service_name: str | None = None) -> str:
    if service_name:
        return (
            service_name
            if service_name.endswith(".service")
            else f"{service_name}.service"
        )
    return f"{WORKFLOW_DAEMON_SERVICE_PREFIX}@{workflow_root.name}.service"


def _unit_path(service_name: str) -> Path:
    return _systemd_user_dir() / service_name


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


def _plugin_cli_path() -> Path:
    return Path(__file__).resolve().parents[1] / "sprints_cli.py"


def _load_config(workflow_root: Path) -> WorkflowConfig:
    root = Path(workflow_root).expanduser().resolve()
    contract = load_workflow_contract(root)
    return WorkflowConfig.from_raw(raw=contract.config, workflow_root=root)


def _engine_store(config: WorkflowConfig) -> EngineStore:
    return EngineStore(
        db_path=runtime_paths(config.workflow_root)["db_path"],
        workflow=config.workflow_name,
    )


def _lease_key(workflow_root: Path) -> str:
    return str(Path(workflow_root).expanduser().resolve())


def _acquire_daemon_lease(
    *,
    config: WorkflowConfig,
    owner_instance_id: str,
    lease_ttl_seconds: int,
) -> dict[str, Any]:
    return _engine_store(config).acquire_lease(
        lease_scope="workflow-daemon",
        lease_key=_lease_key(config.workflow_root),
        owner_instance_id=owner_instance_id,
        owner_role="orchestrator-loop",
        ttl_seconds=lease_ttl_seconds,
        metadata={
            "workflow": config.workflow_name,
            "workflow_root": str(config.workflow_root),
            "heartbeat_at": _now_iso(),
        },
    )


def _release_daemon_lease(*, config: WorkflowConfig, owner_instance_id: str) -> None:
    _engine_store(config).release_lease(
        lease_scope="workflow-daemon",
        lease_key=_lease_key(config.workflow_root),
        owner_instance_id=owner_instance_id,
        release_reason="daemon stopped",
    )


def _daemon_lease_status(config: WorkflowConfig) -> dict[str, Any]:
    return _engine_store(config).lease_status(
        lease_scope="workflow-daemon",
        lease_key=_lease_key(config.workflow_root),
        stale_after_seconds=DEFAULT_LEASE_TTL_SECONDS * 2,
    )


def _render_unit(
    *,
    workflow_root: Path,
    active_interval: float,
    idle_interval: float,
    max_retry_sleep: float,
    error_interval: float,
    lease_ttl_seconds: int,
    jitter_ratio: float,
    python_command: str,
) -> str:
    service_path = os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    command_parts = shlex.split(str(python_command).strip() or "python3")
    exec_start = shlex.join(
        [
            "/usr/bin/env",
            *command_parts,
            str(_plugin_cli_path()),
            "daemon",
            "run",
            "--workflow-root",
            str(workflow_root),
            "--active-interval",
            str(active_interval),
            "--idle-interval",
            str(idle_interval),
            "--max-retry-sleep",
            str(max_retry_sleep),
            "--error-interval",
            str(error_interval),
            "--lease-ttl",
            str(lease_ttl_seconds),
            "--jitter",
            str(jitter_ratio),
        ]
    )
    return "\n".join(
        [
            "[Unit]",
            "Description=Sprints workflow daemon (workflow=%i)",
            "After=default.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={workflow_root}",
            f"Environment=PATH={service_path}",
            "Environment=PYTHONUNBUFFERED=1",
            f"ExecStart={exec_start}",
            "Restart=always",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def workflow_daemon_install(
    *,
    workflow_root: Path,
    service_name: str | None = None,
    active_interval: float = DEFAULT_ACTIVE_INTERVAL_SECONDS,
    idle_interval: float = DEFAULT_IDLE_INTERVAL_SECONDS,
    max_retry_sleep: float = DEFAULT_MAX_RETRY_SLEEP_SECONDS,
    error_interval: float = DEFAULT_ERROR_INTERVAL_SECONDS,
    lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
    python_command: str = "python3",
) -> dict[str, Any]:
    config = _load_config(workflow_root)
    resolved_service_name = _service_name(
        workflow_root=config.workflow_root,
        service_name=service_name,
    )
    unit_path = _unit_path(resolved_service_name)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(
        _render_unit(
            workflow_root=config.workflow_root,
            active_interval=active_interval,
            idle_interval=idle_interval,
            max_retry_sleep=max_retry_sleep,
            error_interval=error_interval,
            lease_ttl_seconds=lease_ttl_seconds,
            jitter_ratio=jitter_ratio,
            python_command=python_command,
        ),
        encoding="utf-8",
    )
    reload_result = _run_systemctl("daemon-reload")
    return {
        "ok": reload_result.get("ok", False),
        "action": "install",
        "workflow": config.workflow_name,
        "workflow_root": str(config.workflow_root),
        "service_name": resolved_service_name,
        "unit_path": str(unit_path),
        "daemon_reload": reload_result,
        "intervals": _interval_payload(
            active_interval=active_interval,
            idle_interval=idle_interval,
            max_retry_sleep=max_retry_sleep,
            error_interval=error_interval,
            lease_ttl_seconds=lease_ttl_seconds,
            jitter_ratio=jitter_ratio,
        ),
    }


def workflow_daemon_up(
    *,
    workflow_root: Path,
    service_name: str | None = None,
    active_interval: float = DEFAULT_ACTIVE_INTERVAL_SECONDS,
    idle_interval: float = DEFAULT_IDLE_INTERVAL_SECONDS,
    max_retry_sleep: float = DEFAULT_MAX_RETRY_SLEEP_SECONDS,
    error_interval: float = DEFAULT_ERROR_INTERVAL_SECONDS,
    lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
    python_command: str = "python3",
) -> dict[str, Any]:
    install_result = workflow_daemon_install(
        workflow_root=workflow_root,
        service_name=service_name,
        active_interval=active_interval,
        idle_interval=idle_interval,
        max_retry_sleep=max_retry_sleep,
        error_interval=error_interval,
        lease_ttl_seconds=lease_ttl_seconds,
        jitter_ratio=jitter_ratio,
        python_command=python_command,
    )
    if not install_result.get("ok"):
        daemon_reload = install_result.get("daemon_reload") or {}
        raise WorkflowDaemonError(
            "unable to install workflow daemon service: "
            f"{daemon_reload.get('stderr') or daemon_reload.get('stdout') or 'daemon-reload failed'}"
        )
    resolved_service_name = str(install_result["service_name"])
    enable_result = _run_systemctl("enable", resolved_service_name)
    if not enable_result.get("ok"):
        raise WorkflowDaemonError(
            "unable to enable workflow daemon service: "
            f"{enable_result.get('stderr') or enable_result.get('stdout') or enable_result.get('returncode')}"
        )
    start_result = _run_systemctl("start", resolved_service_name)
    if not start_result.get("ok"):
        raise WorkflowDaemonError(
            "unable to start workflow daemon service: "
            f"{start_result.get('stderr') or start_result.get('stdout') or start_result.get('returncode')}"
        )
    return {
        "ok": True,
        "action": "up",
        "workflow_root": install_result["workflow_root"],
        "workflow": install_result["workflow"],
        "service_name": resolved_service_name,
        "install": install_result,
        "enable": enable_result,
        "start": start_result,
        "status": workflow_daemon_status(
            workflow_root=Path(str(install_result["workflow_root"])),
            service_name=resolved_service_name,
        ),
    }


def workflow_daemon_down(
    *, workflow_root: Path, service_name: str | None = None
) -> dict[str, Any]:
    config = _load_config(workflow_root)
    resolved_service_name = _service_name(
        workflow_root=config.workflow_root,
        service_name=service_name,
    )
    stop_result = _run_systemctl("stop", resolved_service_name)
    disable_result = _run_systemctl("disable", resolved_service_name)
    return {
        "ok": stop_result.get("ok", False) or disable_result.get("ok", False),
        "action": "down",
        "workflow": config.workflow_name,
        "workflow_root": str(config.workflow_root),
        "service_name": resolved_service_name,
        "stop": stop_result,
        "disable": disable_result,
        "status": workflow_daemon_status(
            workflow_root=config.workflow_root,
            service_name=resolved_service_name,
        ),
    }


def workflow_daemon_restart(
    *, workflow_root: Path, service_name: str | None = None
) -> dict[str, Any]:
    config = _load_config(workflow_root)
    resolved_service_name = _service_name(
        workflow_root=config.workflow_root,
        service_name=service_name,
    )
    restart_result = _run_systemctl("restart", resolved_service_name)
    return {
        "ok": restart_result.get("ok", False),
        "action": "restart",
        "workflow": config.workflow_name,
        "workflow_root": str(config.workflow_root),
        "service_name": resolved_service_name,
        "restart": restart_result,
        "status": workflow_daemon_status(
            workflow_root=config.workflow_root,
            service_name=resolved_service_name,
        ),
    }


def workflow_daemon_logs(
    *, workflow_root: Path, service_name: str | None = None, lines: int = 50
) -> dict[str, Any]:
    config = _load_config(workflow_root)
    resolved_service_name = _service_name(
        workflow_root=config.workflow_root,
        service_name=service_name,
    )
    completed = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            resolved_service_name,
            "-n",
            str(lines),
            "--no-pager",
            "-o",
            "cat",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "action": "logs",
        "workflow": config.workflow_name,
        "workflow_root": str(config.workflow_root),
        "service_name": resolved_service_name,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "lines": lines,
    }


def workflow_daemon_status(
    *, workflow_root: Path, service_name: str | None = None
) -> dict[str, Any]:
    config = _load_config(workflow_root)
    resolved_service_name = _service_name(
        workflow_root=config.workflow_root,
        service_name=service_name,
    )
    unit_path = _unit_path(resolved_service_name)
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
        "workflow": config.workflow_name,
        "workflow_root": str(config.workflow_root),
        "service_name": resolved_service_name,
        "unit_path": str(unit_path),
        "installed": unit_path.exists(),
        "active": active.get("stdout") or ("active" if active.get("ok") else "unknown"),
        "enabled": enabled.get("stdout")
        or ("enabled" if enabled.get("ok") else "unknown"),
        "lease": _daemon_lease_status(config),
        "properties": props,
        "active_check": active,
        "enabled_check": enabled,
        "show": show,
    }


def run_workflow_daemon(
    *,
    workflow_root: Path,
    active_interval: float = DEFAULT_ACTIVE_INTERVAL_SECONDS,
    idle_interval: float = DEFAULT_IDLE_INTERVAL_SECONDS,
    max_retry_sleep: float = DEFAULT_MAX_RETRY_SLEEP_SECONDS,
    error_interval: float = DEFAULT_ERROR_INTERVAL_SECONDS,
    lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
    once: bool = False,
    owner_instance_id: str | None = None,
) -> dict[str, Any]:
    config = _load_config(workflow_root)
    owner = owner_instance_id or f"{os.getpid()}:{uuid.uuid4().hex[:12]}"
    stop_requested = False
    tick_count = 0
    last_error: str | None = None

    def _request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    old_term = None
    old_int = None
    try:
        old_term = signal.signal(signal.SIGTERM, _request_stop)
        old_int = signal.signal(signal.SIGINT, _request_stop)
    except ValueError:
        # Some plugin hosts run commands outside the main interpreter thread.
        # In that case foreground stop handling belongs to the host process.
        pass
    try:
        while not stop_requested:
            lease = _acquire_daemon_lease(
                config=config,
                owner_instance_id=owner,
                lease_ttl_seconds=lease_ttl_seconds,
            )
            if not lease.get("acquired"):
                sleep_for = _with_jitter(idle_interval, jitter_ratio)
                _log(
                    "lease held by another daemon; sleeping",
                    workflow=config.workflow_name,
                    owner=lease.get("owner_instance_id"),
                    sleep_seconds=sleep_for,
                )
                if once:
                    return _run_payload(
                        config=config,
                        owner=owner,
                        status="skipped",
                        tick_count=tick_count,
                        last_error=None,
                        lease=lease,
                    )
                _sleep_or_stop(sleep_for, lambda: stop_requested)
                continue

            try:
                tick_count += 1
                _log("tick start", workflow=config.workflow_name, tick=tick_count)
                rc = run_cli(
                    config.workflow_root,
                    ["tick"],
                    require_workflow=config.workflow_name,
                )
                if rc != 0:
                    raise WorkflowDaemonError(f"workflow tick exited with status {rc}")
                last_error = None
                status = build_status(config.workflow_root)
                sleep_for = _next_sleep_seconds(
                    status=status,
                    active_interval=active_interval,
                    idle_interval=idle_interval,
                    max_retry_sleep=max_retry_sleep,
                    jitter_ratio=jitter_ratio,
                )
                _log(
                    "tick complete",
                    workflow=config.workflow_name,
                    tick=tick_count,
                    active_lanes=status.get("active_lane_count"),
                    running=status.get("running_count"),
                    retry=status.get("retry_count"),
                    due_retries=_retry_wakeup_due_count(status),
                    next_retry_wakeup=_seconds_until_next_retry(status),
                    operator_attention=status.get("operator_attention_count"),
                    sleep_seconds=sleep_for,
                )
            except Exception as exc:
                last_error = str(exc)
                sleep_for = _with_jitter(error_interval, jitter_ratio)
                _log(
                    "tick failed",
                    workflow=config.workflow_name,
                    tick=tick_count,
                    error=last_error,
                    sleep_seconds=sleep_for,
                )

            if once:
                return _run_payload(
                    config=config,
                    owner=owner,
                    status="completed" if last_error is None else "failed",
                    tick_count=tick_count,
                    last_error=last_error,
                    lease=lease,
                )
            _sleep_or_stop(sleep_for, lambda: stop_requested)
    finally:
        try:
            _release_daemon_lease(config=config, owner_instance_id=owner)
        finally:
            if old_term is not None:
                signal.signal(signal.SIGTERM, old_term)
            if old_int is not None:
                signal.signal(signal.SIGINT, old_int)
    return _run_payload(
        config=config,
        owner=owner,
        status="stopped",
        tick_count=tick_count,
        last_error=last_error,
        lease=None,
    )


def _next_sleep_seconds(
    *,
    status: dict[str, Any],
    active_interval: float,
    idle_interval: float,
    max_retry_sleep: float,
    jitter_ratio: float,
) -> float:
    active_lanes = int(status.get("active_lane_count") or 0)
    base = active_interval if active_lanes > 0 else idle_interval
    retry_wait = _seconds_until_next_retry(status)
    if retry_wait is not None:
        base = min(base, max_retry_sleep, max(retry_wait, 0.0))
        if base <= 0:
            base = 1.0
    return _with_jitter(base, jitter_ratio)


def _seconds_until_next_retry(status: dict[str, Any]) -> float | None:
    retry_wakeup = (
        status.get("retry_wakeup")
        if isinstance(status.get("retry_wakeup"), dict)
        else {}
    )
    if int(retry_wakeup.get("queued_count") or 0) <= 0:
        return None
    value = retry_wakeup.get("next_due_in_seconds")
    if value in (None, ""):
        return None
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return None


def _retry_wakeup_due_count(status: dict[str, Any]) -> int:
    retry_wakeup = (
        status.get("retry_wakeup")
        if isinstance(status.get("retry_wakeup"), dict)
        else {}
    )
    return int(retry_wakeup.get("due_count") or 0)


def _with_jitter(seconds: float, jitter_ratio: float) -> float:
    base = max(float(seconds or 0), 0.0)
    ratio = max(float(jitter_ratio or 0), 0.0)
    if base <= 0 or ratio <= 0:
        return base
    return base + random.uniform(0.0, base * ratio)


def _sleep_or_stop(seconds: float, stop_requested: Any) -> None:
    deadline = time.monotonic() + max(float(seconds or 0), 0.0)
    while not stop_requested():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))


def _log(message: str, **fields: Any) -> None:
    suffix = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"{_now_iso()} {message}" + (f" {suffix}" if suffix else ""), flush=True)


def _interval_payload(
    *,
    active_interval: float,
    idle_interval: float,
    max_retry_sleep: float,
    error_interval: float,
    lease_ttl_seconds: int,
    jitter_ratio: float,
) -> dict[str, Any]:
    return {
        "active_interval": active_interval,
        "idle_interval": idle_interval,
        "max_retry_sleep": max_retry_sleep,
        "error_interval": error_interval,
        "lease_ttl_seconds": lease_ttl_seconds,
        "jitter_ratio": jitter_ratio,
    }


def _run_payload(
    *,
    config: WorkflowConfig,
    owner: str,
    status: str,
    tick_count: int,
    last_error: str | None,
    lease: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ok": status not in {"failed"},
        "action": "run",
        "status": status,
        "workflow": config.workflow_name,
        "workflow_root": str(config.workflow_root),
        "owner_instance_id": owner,
        "tick_count": tick_count,
        "last_error": last_error,
        "lease": lease,
    }
