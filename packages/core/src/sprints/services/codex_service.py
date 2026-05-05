import http.client
import ipaddress
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sprints.engine.state import read_engine_scheduler_state
from sprints.core.contracts import WorkflowContractError, load_workflow_contract
from sprints.core.paths import runtime_paths

CODEX_APP_SERVER_SERVICE_PREFIX = "sprints-codex-app-server"
DEFAULT_CODEX_APP_SERVER_LISTEN = "ws://127.0.0.1:4500"
DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH = "/readyz"


class CodexAppServerError(Exception):
    pass


def _systemd_user_dir() -> Path:
    override = os.environ.get("SPRINTS_SYSTEMD_USER_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".config" / "systemd" / "user").resolve()


def _codex_app_server_service_name(
    *, workflow_root: Path, service_name: str | None = None
) -> str:
    if service_name:
        return (
            service_name
            if service_name.endswith(".service")
            else f"{service_name}.service"
        )
    return f"{CODEX_APP_SERVER_SERVICE_PREFIX}@{workflow_root.name}.service"


def _codex_app_server_unit_path(service_name: str) -> Path:
    return _systemd_user_dir() / service_name


def _absolute_secret_path(value: str, *, flag_name: str) -> str:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        raise CodexAppServerError(f"{flag_name} must be an absolute path")
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
        raise CodexAppServerError(
            "use either --ws-token-file or --ws-token-sha256, not both"
        )
    if (token_file or token_sha256) and shared_secret_file:
        raise CodexAppServerError(
            "capability-token and signed-bearer-token auth modes are mutually exclusive"
        )
    if (
        issuer or audience or ws_max_clock_skew_seconds is not None
    ) and not shared_secret_file:
        raise CodexAppServerError(
            "--ws-issuer, --ws-audience, and --ws-max-clock-skew-seconds require --ws-shared-secret-file"
        )
    if ws_max_clock_skew_seconds is not None and ws_max_clock_skew_seconds < 0:
        raise CodexAppServerError("--ws-max-clock-skew-seconds must be non-negative")

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
        path = _absolute_secret_path(
            shared_secret_file, flag_name="--ws-shared-secret-file"
        )
        args = ["--ws-auth", "signed-bearer-token", "--ws-shared-secret-file", path]
        summary: dict[str, Any] = {
            "mode": "signed-bearer-token",
            "shared_secret_file": path,
        }
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
    exec_start = shlex.join(
        ["/usr/bin/env", *command_parts, "app-server", "--listen", listen, *auth_args]
    )
    return "\n".join(
        [
            "[Unit]",
            "Description=Sprints Codex app-server (workspace=%i)",
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
        ]
    )


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
        raise CodexAppServerError(
            "unable to install codex-app-server service: "
            f"{daemon_reload.get('stderr') or daemon_reload.get('stdout') or 'daemon-reload failed'}"
        )
    resolved_service_name = str(install_result["service_name"])
    enable_result = _run_systemctl("enable", resolved_service_name)
    if not enable_result.get("ok"):
        raise CodexAppServerError(
            "unable to enable codex-app-server service: "
            f"{enable_result.get('stderr') or enable_result.get('stdout') or enable_result.get('returncode')}"
        )
    start_result = _run_systemctl("start", resolved_service_name)
    if not start_result.get("ok"):
        raise CodexAppServerError(
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
        "status": codex_app_server_status(
            workflow_root=workflow_root, service_name=resolved_service_name
        ),
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
        "enabled": enabled.get("stdout")
        or ("enabled" if enabled.get("ok") else "unknown"),
        "ready": _codex_app_server_readyz(
            endpoint=endpoint, healthcheck_path=healthcheck_path
        ),
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
    shared_secret_file = _codex_app_server_token_value(
        tokens, "--ws-shared-secret-file"
    )
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
    try:
        contract = load_workflow_contract(workflow_root)
    except (
        FileNotFoundError,
        WorkflowContractError,
        OSError,
        UnicodeDecodeError,
    ) as exc:
        return {
            "ok": False,
            "path": str(db_path),
            "exists": db_path.exists(),
            "threads": [],
            "totals": {},
            "invalid_thread_count": 0,
            "error": str(exc),
        }
    workflow_name = str(contract.config.get("workflow") or "").strip()
    if not workflow_name:
        return {
            "ok": False,
            "path": str(db_path),
            "exists": db_path.exists(),
            "threads": [],
            "totals": {},
            "invalid_thread_count": 0,
            "error": f"{contract.source_path} is missing top-level `workflow:` field",
        }

    scheduler = read_engine_scheduler_state(
        db_path,
        workflow=workflow_name,
        now_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        now_epoch=time.time(),
    )

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
    raw_threads = scheduler.get("runtime_sessions") or {}
    threads: list[dict[str, Any]] = []
    invalid_thread_count = 0
    if isinstance(raw_threads, dict):
        for issue_id, raw_entry in sorted(
            raw_threads.items(), key=lambda item: str(item[0])
        ):
            if not isinstance(raw_entry, dict):
                invalid_thread_count += 1
                continue
            thread_id = raw_entry.get("thread_id")
            if not str(thread_id or "").strip():
                invalid_thread_count += 1
            issue_number = raw_entry.get("issue_number")
            threads.append(
                {
                    "issue_id": raw_entry.get("issue_id") or issue_id,
                    "issue_number": issue_number,
                    "identifier": raw_entry.get("identifier")
                    or (f"#{issue_number}" if issue_number else issue_id),
                    "session_name": raw_entry.get("session_name"),
                    "runtime_name": raw_entry.get("runtime_name"),
                    "runtime_kind": raw_entry.get("runtime_kind"),
                    "thread_id": thread_id,
                    "turn_id": raw_entry.get("turn_id"),
                    "status": raw_entry.get("status"),
                    "cancel_requested": bool(
                        raw_entry.get("cancel_requested") or False
                    ),
                    "cancel_reason": raw_entry.get("cancel_reason"),
                    "updated_at": raw_entry.get("updated_at"),
                }
            )
    totals = scheduler.get("runtime_totals") or {}
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
        raise CodexAppServerError("--mode must be managed or external")

    resolved_service_name = _codex_app_server_service_name(
        workflow_root=workflow_root,
        service_name=service_name,
    )
    unit_path = _codex_app_server_unit_path(resolved_service_name)
    effective_endpoint = str(endpoint or "").strip()
    if not effective_endpoint and mode == "managed":
        effective_endpoint = (
            _codex_app_server_listen_from_unit(unit_path)
            or DEFAULT_CODEX_APP_SERVER_LISTEN
        )
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
    unit_auth_summary = (
        _codex_app_server_auth_summary_from_unit(unit_path)
        if mode == "managed"
        else None
    )
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
                remedy="run `hermes sprints codex-app-server install` or `up`",
            )
        )
        active = str(status_result.get("active") or "unknown")
        checks.append(
            _codex_app_server_doctor_check(
                "managed-service-active",
                "pass" if active == "active" else "fail",
                active,
                remedy="run `hermes sprints codex-app-server up` or inspect `logs`",
            )
        )
        enabled = str(status_result.get("enabled") or "unknown")
        checks.append(
            _codex_app_server_doctor_check(
                "managed-service-enabled",
                "pass" if enabled == "enabled" else "warn",
                enabled,
                severity="warning",
                remedy="run `hermes sprints codex-app-server up` if the listener should start on login",
            )
        )
        ready = status_result.get("ready") or {}
    else:
        ready = _codex_app_server_readyz(
            endpoint=effective_endpoint, healthcheck_path=healthcheck_path
        )
        checks.append(
            _codex_app_server_doctor_check(
                "managed-unit-file",
                "skip",
                "external mode uses a listener started outside Sprints",
                severity="info",
            )
        )

    parsed_endpoint = urlparse(effective_endpoint)
    endpoint_shape_ok = (
        parsed_endpoint.scheme == "ws"
        and bool(parsed_endpoint.hostname)
        and bool(parsed_endpoint.port)
    )
    checks.append(
        _codex_app_server_doctor_check(
            "endpoint-shape",
            "pass" if endpoint_shape_ok else "fail",
            effective_endpoint,
            remedy="use a ws://host:port endpoint such as ws://127.0.0.1:4500",
        )
    )

    if ready.get("ok") is True:
        checks.append(
            _codex_app_server_doctor_check(
                "readyz", "pass", f"{effective_endpoint}{healthcheck_path}"
            )
        )
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
                remedy="start the listener or inspect `hermes sprints codex-app-server logs`",
            )
        )

    missing_secret_paths = [
        path
        for path in _codex_app_server_secret_paths(auth_summary)
        if not Path(path).exists()
    ]
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
