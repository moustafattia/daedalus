import argparse
import io
import json
import shlex
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from sprints.engine.reports import EngineReportError, build_events_report, build_runs_report
from sprints.core.bindings import (
    RuntimePresetError,
    available_runtime_presets,
    build_runtime_matrix_report,
    configure_runtime_contract,
)
from sprints.core.contract_apply import (
    WorkflowContractApplyError,
    apply_workflow_contract,
)
from sprints.core.contracts import WorkflowContractError
from sprints.core.doctor import build_doctor_report
from sprints.workflows.registry import (
    DEFAULT_WORKFLOW_NAME,
    SUPPORTED_WORKFLOW_NAMES,
)
from sprints.core.validation import validate_workflow_contract
from sprints.services.daemon import (
    DEFAULT_ACTIVE_INTERVAL_SECONDS,
    DEFAULT_ERROR_INTERVAL_SECONDS,
    DEFAULT_IDLE_INTERVAL_SECONDS,
    DEFAULT_JITTER_RATIO,
    DEFAULT_LEASE_TTL_SECONDS,
    DEFAULT_MAX_RETRY_SLEEP_SECONDS,
    WorkflowDaemonError,
    run_workflow_daemon,
    workflow_daemon_down,
    workflow_daemon_install,
    workflow_daemon_logs,
    workflow_daemon_restart,
    workflow_daemon_status,
    workflow_daemon_up,
)
from sprints.workflows.status import build_status as build_workflow_status
from sprints.services.codex_service import (
    CodexAppServerError,
    DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH,
    DEFAULT_CODEX_APP_SERVER_LISTEN,
    codex_app_server_doctor,
    codex_app_server_down,
    codex_app_server_install,
    codex_app_server_logs,
    codex_app_server_restart,
    codex_app_server_status,
    codex_app_server_up,
)
from .render import render_result
from sprints.core.bootstrap import (
    WorkflowBootstrapError,
    bootstrap_workflow_root,
    scaffold_workflow_root,
)
from sprints.core.init_wizard import WorkflowInitError, run_init_wizard
from sprints.core.paths import (
    resolve_default_workflow_root as resolve_workflow_root_default,
    workflow_cli_argv,
)

PLUGIN_DIR = Path(__file__).resolve().parents[1]


# Module Setup


def resolve_default_workflow_root() -> Path:
    return resolve_workflow_root_default(plugin_dir=PLUGIN_DIR)


class SprintsCommandError(Exception):
    pass


class SprintsArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise SprintsCommandError(f"{message}\n\n{self.format_usage().strip()}")


# Parser Shape


def build_parser() -> argparse.ArgumentParser:
    parser = SprintsArgumentParser(
        prog="sprints", description="Sprints operator control surface."
    )
    return configure_subcommands(parser)


def configure_subcommands(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    sub = parser.add_subparsers(dest="sprints_command")
    sub.required = True
    default_workflow_root_str = str(resolve_default_workflow_root())
    default_workflow_root_path = resolve_default_workflow_root()

    status_cmd = sub.add_parser("status", help="Show workflow status.")
    status_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    status_cmd.set_defaults(func=run_cli_command)

    doctor_cmd = sub.add_parser("doctor", help="Run workflow diagnostics.")
    doctor_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    doctor_cmd.add_argument(
        "--fix",
        action="store_true",
        help=(
            "Conservatively repair missing local workflow paths, pointers, "
            "services, and engine projections."
        ),
    )
    doctor_cmd.add_argument("--json", action="store_true")
    doctor_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    doctor_cmd.set_defaults(func=run_cli_command)

    validate_cmd = sub.add_parser(
        "validate",
        help="Validate the active workflow contract and runtime bindings.",
    )
    validate_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    validate_cmd.add_argument("--json", action="store_true")
    validate_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    validate_cmd.set_defaults(func=run_cli_command)

    runs_cmd = sub.add_parser(
        "runs", help="Inspect durable engine run history and run timelines."
    )
    runs_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    runs_cmd.add_argument(
        "runs_action",
        nargs="?",
        default="list",
        choices=["list", "failed", "stale", "show"],
    )
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

    events_cmd = sub.add_parser(
        "events", help="Inspect and prune the durable engine event ledger."
    )
    events_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    events_cmd.add_argument(
        "events_action", nargs="?", default="list", choices=["list", "stats", "prune"]
    )
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

    watch_cmd = sub.add_parser(
        "watch",
        help="Live operator TUI: lanes, alerts, recent events.",
    )
    watch_cmd.add_argument(
        "--workflow-root", type=Path, default=default_workflow_root_path
    )
    watch_cmd.add_argument(
        "--once",
        action="store_true",
        help="Render one frame and exit (default when stdout is not a TTY).",
    )
    watch_cmd.add_argument(
        "--interval", type=float, default=2.0, help="Poll interval in live mode."
    )
    watch_cmd.set_defaults(handler=_lazy_cmd_watch, func=run_cli_command)

    init_cmd = sub.add_parser(
        "init",
        help="Run the first-time setup wizard and write a repo-owned WORKFLOW.md.",
    )
    init_cmd.add_argument(
        "--repo-path",
        type=Path,
        help="Target git checkout. Defaults to the current repository.",
    )
    init_cmd.add_argument(
        "--workflow-root",
        type=Path,
        help="Workflow root to create. Defaults to ~/.hermes/workflows/<owner>-<repo>-<workflow>.",
    )
    init_cmd.add_argument(
        "--workflow",
        default=DEFAULT_WORKFLOW_NAME,
        choices=SUPPORTED_WORKFLOW_NAMES,
    )
    init_cmd.add_argument("--repo-slug", help="Repository slug in owner/repo form.")
    init_cmd.add_argument("--tracker", choices=["github"], default="github")
    init_cmd.add_argument(
        "--runtime", default="codex-app-server", choices=available_runtime_presets()
    )
    init_cmd.add_argument(
        "--runtime-name",
        help="Runtime profile name to write under runtimes:. Defaults to --runtime.",
    )
    init_cmd.add_argument(
        "--model",
        help="Optional actor model override written to orchestrator, implementer, and reviewer.",
    )
    init_cmd.add_argument("--active-label", default="active")
    init_cmd.add_argument("--done-label", default="done")
    init_cmd.add_argument(
        "--exclude-labels",
        help="Comma-separated labels that make issues ineligible.",
    )
    init_cmd.add_argument("--max-lanes", type=int, default=1)
    init_cmd.add_argument("--force", action="store_true")
    init_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Use defaults and provided flags without prompting.",
    )
    init_cmd.add_argument("--json", action="store_true")
    init_cmd.set_defaults(handler=cmd_init_workflow, func=run_cli_command)

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
    scaffold_cmd.add_argument(
        "--workflow",
        default=DEFAULT_WORKFLOW_NAME,
        choices=SUPPORTED_WORKFLOW_NAMES,
    )
    scaffold_cmd.add_argument("--repo-path", type=Path)
    scaffold_cmd.add_argument(
        "--repo-slug",
        required=True,
        help="Repository identity in owner/repo form for workflow instance naming.",
    )
    scaffold_cmd.add_argument(
        "--engine-owner", default="hermes", choices=["hermes", "openclaw"]
    )
    scaffold_cmd.add_argument("--force", action="store_true")
    scaffold_cmd.add_argument("--json", action="store_true")
    scaffold_cmd.set_defaults(handler=cmd_scaffold_workflow, func=run_cli_command)

    bootstrap_cmd = sub.add_parser(
        "bootstrap",
        help="Infer repo settings from the current git checkout and scaffold a repo-owned workflow contract.",
    )
    bootstrap_cmd.add_argument(
        "--repo-path",
        type=Path,
        help="Git checkout to inspect (defaults to current working directory).",
    )
    bootstrap_cmd.add_argument(
        "--workflow-root", type=Path, help="Optional explicit workflow root override."
    )
    bootstrap_cmd.add_argument(
        "--workflow",
        default=DEFAULT_WORKFLOW_NAME,
        choices=SUPPORTED_WORKFLOW_NAMES,
    )
    bootstrap_cmd.add_argument(
        "--repo-slug", help="Override the inferred repository slug from git origin."
    )
    bootstrap_cmd.add_argument(
        "--engine-owner", default="hermes", choices=["hermes", "openclaw"]
    )
    bootstrap_cmd.add_argument("--force", action="store_true")
    bootstrap_cmd.add_argument("--json", action="store_true")
    bootstrap_cmd.set_defaults(handler=cmd_bootstrap_workflow, func=run_cli_command)

    apply_contract_cmd = sub.add_parser(
        "apply-contract",
        help="Validate and promote the repo-owned WORKFLOW.md from origin/main into the active workflow snapshot.",
    )
    apply_contract_cmd.add_argument(
        "--workflow-root", default=default_workflow_root_str
    )
    apply_contract_cmd.add_argument("--source-ref", default="origin/main")
    apply_contract_cmd.add_argument("--force", action="store_true")
    apply_contract_cmd.add_argument("--json", action="store_true")
    apply_contract_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    apply_contract_cmd.set_defaults(func=run_cli_command)

    configure_runtime_cmd = sub.add_parser(
        "configure-runtime",
        help="Bind a workflow role to a built-in runtime preset in the active workflow contract.",
    )
    configure_runtime_cmd.add_argument(
        "--workflow-root", default=default_workflow_root_str
    )
    configure_runtime_cmd.add_argument(
        "--runtime", required=True, choices=available_runtime_presets()
    )
    configure_runtime_cmd.add_argument(
        "--role",
        required=True,
        help="Role to bind, such as orchestrator, implementer, reviewer, or all.",
    )
    configure_runtime_cmd.add_argument(
        "--runtime-name",
        help="Optional profile name to write under runtimes: (defaults to the preset name).",
    )
    configure_runtime_cmd.add_argument("--dry-run", action="store_true")
    configure_runtime_cmd.add_argument("--json", action="store_true")
    configure_runtime_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    configure_runtime_cmd.set_defaults(func=run_cli_command)

    runtime_matrix_cmd = sub.add_parser(
        "runtime-matrix",
        help="Show workflow role-to-runtime bindings and optionally execute a tiny runtime-stage smoke.",
    )
    runtime_matrix_cmd.add_argument(
        "--workflow-root", default=default_workflow_root_str
    )
    runtime_matrix_cmd.add_argument(
        "--role", action="append", help="Limit to a workflow role. Can be repeated."
    )
    runtime_matrix_cmd.add_argument(
        "--runtime",
        action="append",
        help="Limit to a runtime profile. Can be repeated.",
    )
    runtime_matrix_cmd.add_argument(
        "--execute",
        action="store_true",
        help="Run a tiny prompt through each selected role runtime.",
    )
    runtime_matrix_cmd.add_argument("--json", action="store_true")
    runtime_matrix_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    runtime_matrix_cmd.set_defaults(func=run_cli_command)

    codex_cmd = sub.add_parser(
        "codex-app-server",
        help="Install and control the shared Codex app-server systemd user service.",
    )
    codex_sub = codex_cmd.add_subparsers(dest="codex_app_server_command")
    codex_sub.required = True

    def _add_codex_app_server_auth_args(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument(
            "--ws-token-file",
            help="Absolute token file for capability-token WebSocket auth.",
        )
        cmd.add_argument(
            "--ws-token-sha256",
            help="SHA-256 verifier for capability-token WebSocket auth.",
        )
        cmd.add_argument(
            "--ws-shared-secret-file",
            help="Absolute secret file for signed-bearer-token WebSocket auth.",
        )
        cmd.add_argument("--ws-issuer")
        cmd.add_argument("--ws-audience")
        cmd.add_argument("--ws-max-clock-skew-seconds", type=int)

    def _add_workflow_daemon_runtime_args(
        cmd: argparse.ArgumentParser, workflow_root_default: str
    ) -> None:
        cmd.add_argument("--workflow-root", default=workflow_root_default)
        cmd.add_argument(
            "--active-interval",
            type=float,
            default=DEFAULT_ACTIVE_INTERVAL_SECONDS,
            help="Seconds between ticks while lanes are active.",
        )
        cmd.add_argument(
            "--idle-interval",
            type=float,
            default=DEFAULT_IDLE_INTERVAL_SECONDS,
            help="Seconds between ticks while no lanes are active.",
        )
        cmd.add_argument(
            "--max-retry-sleep",
            type=float,
            default=DEFAULT_MAX_RETRY_SLEEP_SECONDS,
            help="Maximum sleep when a retry is due sooner than the idle interval.",
        )
        cmd.add_argument(
            "--error-interval",
            type=float,
            default=DEFAULT_ERROR_INTERVAL_SECONDS,
            help="Seconds to sleep after a failed tick.",
        )
        cmd.add_argument(
            "--lease-ttl",
            type=int,
            default=DEFAULT_LEASE_TTL_SECONDS,
            help="Workflow daemon lease TTL in seconds.",
        )
        cmd.add_argument(
            "--jitter",
            type=float,
            default=DEFAULT_JITTER_RATIO,
            help="Positive jitter ratio added to sleep intervals.",
        )

    def _add_workflow_daemon_service_args(
        cmd: argparse.ArgumentParser, workflow_root_default: str
    ) -> None:
        _add_workflow_daemon_runtime_args(cmd, workflow_root_default)
        cmd.add_argument("--service-name")
        cmd.add_argument(
            "--python-command",
            default="python3",
            help="Python command used by the generated systemd unit.",
        )

    codex_install_cmd = codex_sub.add_parser(
        "install", help="Write the Codex app-server user unit."
    )
    codex_install_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_install_cmd.add_argument("--listen", default=DEFAULT_CODEX_APP_SERVER_LISTEN)
    codex_install_cmd.add_argument("--service-name")
    codex_install_cmd.add_argument("--codex-command", default="codex")
    _add_codex_app_server_auth_args(codex_install_cmd)
    codex_install_cmd.add_argument("--json", action="store_true")
    codex_install_cmd.set_defaults(func=run_cli_command)

    codex_up_cmd = codex_sub.add_parser(
        "up", help="Install, enable, and start the Codex app-server user unit."
    )
    codex_up_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_up_cmd.add_argument("--listen", default=DEFAULT_CODEX_APP_SERVER_LISTEN)
    codex_up_cmd.add_argument("--service-name")
    codex_up_cmd.add_argument("--codex-command", default="codex")
    _add_codex_app_server_auth_args(codex_up_cmd)
    codex_up_cmd.add_argument("--json", action="store_true")
    codex_up_cmd.set_defaults(func=run_cli_command)

    codex_status_cmd = codex_sub.add_parser(
        "status", help="Show Codex app-server user unit status."
    )
    codex_status_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_status_cmd.add_argument("--service-name")
    codex_status_cmd.add_argument("--endpoint", default=DEFAULT_CODEX_APP_SERVER_LISTEN)
    codex_status_cmd.add_argument(
        "--healthcheck-path", default=DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH
    )
    codex_status_cmd.add_argument("--json", action="store_true")
    codex_status_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    codex_status_cmd.set_defaults(func=run_cli_command)

    codex_doctor_cmd = codex_sub.add_parser(
        "doctor", help="Run actionable Codex app-server diagnostics."
    )
    codex_doctor_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_doctor_cmd.add_argument(
        "--mode", choices=["managed", "external"], default="managed"
    )
    codex_doctor_cmd.add_argument("--service-name")
    codex_doctor_cmd.add_argument("--endpoint")
    codex_doctor_cmd.add_argument(
        "--healthcheck-path", default=DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH
    )
    _add_codex_app_server_auth_args(codex_doctor_cmd)
    codex_doctor_cmd.add_argument("--json", action="store_true")
    codex_doctor_cmd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text|json). --json flag is a back-compat alias for --format json.",
    )
    codex_doctor_cmd.set_defaults(func=run_cli_command)

    codex_down_cmd = codex_sub.add_parser(
        "down", help="Stop and disable the Codex app-server user unit."
    )
    codex_down_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_down_cmd.add_argument("--service-name")
    codex_down_cmd.add_argument("--json", action="store_true")
    codex_down_cmd.set_defaults(func=run_cli_command)

    codex_restart_cmd = codex_sub.add_parser(
        "restart", help="Restart the Codex app-server user unit."
    )
    codex_restart_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_restart_cmd.add_argument("--service-name")
    codex_restart_cmd.add_argument(
        "--endpoint", default=DEFAULT_CODEX_APP_SERVER_LISTEN
    )
    codex_restart_cmd.add_argument(
        "--healthcheck-path", default=DEFAULT_CODEX_APP_SERVER_HEALTHCHECK_PATH
    )
    codex_restart_cmd.add_argument("--json", action="store_true")
    codex_restart_cmd.set_defaults(func=run_cli_command)

    codex_logs_cmd = codex_sub.add_parser(
        "logs", help="Show recent logs for the Codex app-server user unit."
    )
    codex_logs_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    codex_logs_cmd.add_argument("--service-name")
    codex_logs_cmd.add_argument("--lines", type=int, default=50)
    codex_logs_cmd.add_argument("--json", action="store_true")
    codex_logs_cmd.set_defaults(func=run_cli_command)

    daemon_cmd = sub.add_parser(
        "daemon",
        help="Run and manage the workflow orchestrator daemon.",
    )
    daemon_sub = daemon_cmd.add_subparsers(dest="daemon_command")
    daemon_sub.required = True

    daemon_run_cmd = daemon_sub.add_parser(
        "run", help="Run the workflow tick loop in the foreground."
    )
    _add_workflow_daemon_runtime_args(daemon_run_cmd, default_workflow_root_str)
    daemon_run_cmd.add_argument("--once", action="store_true")
    daemon_run_cmd.add_argument("--json", action="store_true")
    daemon_run_cmd.set_defaults(func=run_cli_command)

    daemon_install_cmd = daemon_sub.add_parser(
        "install", help="Write the workflow daemon user unit."
    )
    _add_workflow_daemon_service_args(daemon_install_cmd, default_workflow_root_str)
    daemon_install_cmd.add_argument("--json", action="store_true")
    daemon_install_cmd.set_defaults(func=run_cli_command)

    daemon_up_cmd = daemon_sub.add_parser(
        "up", help="Install, enable, and start the workflow daemon."
    )
    _add_workflow_daemon_service_args(daemon_up_cmd, default_workflow_root_str)
    daemon_up_cmd.add_argument("--json", action="store_true")
    daemon_up_cmd.set_defaults(func=run_cli_command)

    daemon_status_cmd = daemon_sub.add_parser(
        "status", help="Show workflow daemon service and lease status."
    )
    daemon_status_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    daemon_status_cmd.add_argument("--service-name")
    daemon_status_cmd.add_argument("--json", action="store_true")
    daemon_status_cmd.set_defaults(func=run_cli_command)

    daemon_down_cmd = daemon_sub.add_parser(
        "down", help="Stop and disable the workflow daemon."
    )
    daemon_down_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    daemon_down_cmd.add_argument("--service-name")
    daemon_down_cmd.add_argument("--json", action="store_true")
    daemon_down_cmd.set_defaults(func=run_cli_command)

    daemon_restart_cmd = daemon_sub.add_parser(
        "restart", help="Restart the workflow daemon service."
    )
    daemon_restart_cmd.add_argument(
        "--workflow-root", default=default_workflow_root_str
    )
    daemon_restart_cmd.add_argument("--service-name")
    daemon_restart_cmd.add_argument("--json", action="store_true")
    daemon_restart_cmd.set_defaults(func=run_cli_command)

    daemon_logs_cmd = daemon_sub.add_parser(
        "logs", help="Read workflow daemon journal logs."
    )
    daemon_logs_cmd.add_argument("--workflow-root", default=default_workflow_root_str)
    daemon_logs_cmd.add_argument("--service-name")
    daemon_logs_cmd.add_argument("--lines", type=int, default=50)
    daemon_logs_cmd.add_argument("--json", action="store_true")
    daemon_logs_cmd.set_defaults(func=run_cli_command)

    return parser


# Entrypoints


def execute_raw_args(raw_args: str) -> str:
    parser = build_parser()
    argv = shlex.split(raw_args) if raw_args.strip() else ["status"]
    stderr_buffer = io.StringIO()
    try:
        with redirect_stderr(stderr_buffer):
            args = parser.parse_args(argv)
        args._command_source = "plugin-command"
        # String-returning commands bypass execute_namespace.
        if args.sprints_command == "watch":
            return _lazy_cmd_watch(args, parser)
        if args.sprints_command == "init":
            return cmd_init_workflow(args, parser)
        if args.sprints_command == "scaffold-workflow":
            return cmd_scaffold_workflow(args, parser)
        if args.sprints_command == "bootstrap":
            return cmd_bootstrap_workflow(args, parser)
        result = execute_namespace(args)
        fmt = _resolve_format(
            getattr(args, "format", None), getattr(args, "json", False)
        )
        return render_result(args.sprints_command, result, output_format=fmt)
    except SprintsCommandError as exc:
        return f"sprints error: {exc}"
    except SystemExit:
        detail = stderr_buffer.getvalue().strip()
        return f"sprints error: {detail or parser.format_usage().strip()}"
    except Exception as exc:
        return f"sprints error: unexpected {type(exc).__name__}: {exc}"


def run_cli_command(args: argparse.Namespace) -> None:
    args._command_source = "cli"
    # Some subcommands have handlers that return strings directly, not dicts.
    # ``execute_namespace`` only knows about the legacy dict-returning commands,
    # so without this branch the new (string-returning) commands would fall
    # through to ``unknown sprints command``. This mirrors the special-cases
    # in ``execute_raw_args`` for the slash-command path.
    string_returning = {
        "watch",
        "init",
        "scaffold-workflow",
        "bootstrap",
    }
    if getattr(args, "sprints_command", None) in string_returning:
        handler = getattr(args, "handler", None)
        if handler is not None:
            print(handler(args, parser=None))
            return
    fmt = _resolve_format(getattr(args, "format", None), getattr(args, "json", False))
    print(
        render_result(args.sprints_command, execute_namespace(args), output_format=fmt)
    )


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

    from sprints.workflows import list_workflows, run_cli

    if not parts:
        names = list_workflows()
        return (
            ("available workflows: " + ", ".join(names))
            if names
            else "no workflows installed"
        )

    name, *cmd_args = parts

    try:
        if not cmd_args:
            cmd_args = ["--help"]
        stdout_buffer = io.StringIO()
        with redirect_stdout(stdout_buffer):
            rc = run_cli(workflow_root, cmd_args, require_workflow=name)
        output = stdout_buffer.getvalue().strip()
        if rc != 0:
            suffix = f"\n{output}" if output else ""
            return f"workflow '{name}' exited with status {rc}{suffix}"
        return output or "ok"
    except Exception as exc:
        return f"sprints error: {exc}"


# Dispatch


def execute_namespace(args: argparse.Namespace) -> dict[str, Any]:
    workflow_root = (
        Path(args.workflow_root).resolve() if hasattr(args, "workflow_root") else None
    )
    if args.sprints_command == "status":
        return _build_project_status(workflow_root)
    if args.sprints_command == "doctor":
        return build_doctor_report(workflow_root=workflow_root, fix=bool(args.fix))
    if args.sprints_command == "validate":
        return validate_workflow_contract(workflow_root)
    if args.sprints_command == "apply-contract":
        try:
            return apply_workflow_contract(
                workflow_root=workflow_root,
                source_ref=args.source_ref,
                force=args.force,
            )
        except (WorkflowContractApplyError, WorkflowContractError, OSError) as exc:
            raise SprintsCommandError(str(exc)) from exc
    if args.sprints_command == "configure-runtime":
        return configure_runtime_preset(
            workflow_root=workflow_root,
            runtime_preset=args.runtime,
            role=args.role,
            runtime_name=args.runtime_name,
            dry_run=args.dry_run,
        )
    if args.sprints_command == "runtime-matrix":
        return build_runtime_matrix_report(
            workflow_root=workflow_root,
            execute=args.execute,
            roles=args.role,
            runtimes=args.runtime,
        )
    if args.sprints_command == "runs":
        try:
            return build_runs_report(
                workflow_root=workflow_root,
                action=args.runs_action,
                run_id=args.run_id,
                limit=args.limit,
                stale_seconds=args.stale_seconds,
            )
        except EngineReportError as exc:
            raise SprintsCommandError(str(exc)) from exc
    if args.sprints_command == "events":
        try:
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
        except EngineReportError as exc:
            raise SprintsCommandError(str(exc)) from exc
    if args.sprints_command == "codex-app-server":
        try:
            return _execute_codex_app_server_namespace(args, workflow_root)
        except CodexAppServerError as exc:
            raise SprintsCommandError(str(exc)) from exc
    if args.sprints_command == "daemon":
        try:
            return _execute_workflow_daemon_namespace(args, workflow_root)
        except WorkflowDaemonError as exc:
            raise SprintsCommandError(str(exc)) from exc
    raise SprintsCommandError(f"unknown sprints command: {args.sprints_command}")


def _execute_codex_app_server_namespace(
    args: argparse.Namespace, workflow_root: Path
) -> dict[str, Any]:
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
    raise SprintsCommandError(f"unknown codex-app-server command: {action}")


def _execute_workflow_daemon_namespace(
    args: argparse.Namespace, workflow_root: Path
) -> dict[str, Any]:
    action = args.daemon_command
    if action == "run":
        return run_workflow_daemon(
            workflow_root=workflow_root,
            active_interval=args.active_interval,
            idle_interval=args.idle_interval,
            max_retry_sleep=args.max_retry_sleep,
            error_interval=args.error_interval,
            lease_ttl_seconds=args.lease_ttl,
            jitter_ratio=args.jitter,
            once=args.once,
        )
    if action == "install":
        return workflow_daemon_install(
            workflow_root=workflow_root,
            service_name=args.service_name,
            active_interval=args.active_interval,
            idle_interval=args.idle_interval,
            max_retry_sleep=args.max_retry_sleep,
            error_interval=args.error_interval,
            lease_ttl_seconds=args.lease_ttl,
            jitter_ratio=args.jitter,
            python_command=args.python_command,
        )
    if action == "up":
        return workflow_daemon_up(
            workflow_root=workflow_root,
            service_name=args.service_name,
            active_interval=args.active_interval,
            idle_interval=args.idle_interval,
            max_retry_sleep=args.max_retry_sleep,
            error_interval=args.error_interval,
            lease_ttl_seconds=args.lease_ttl,
            jitter_ratio=args.jitter,
            python_command=args.python_command,
        )
    if action == "status":
        return workflow_daemon_status(
            workflow_root=workflow_root,
            service_name=args.service_name,
        )
    if action == "down":
        return workflow_daemon_down(
            workflow_root=workflow_root,
            service_name=args.service_name,
        )
    if action == "restart":
        return workflow_daemon_restart(
            workflow_root=workflow_root,
            service_name=args.service_name,
        )
    if action == "logs":
        return workflow_daemon_logs(
            workflow_root=workflow_root,
            service_name=args.service_name,
            lines=args.lines,
        )
    raise SprintsCommandError(f"unknown daemon command: {action}")


# Command Handlers


def cmd_init_workflow(args, parser) -> str:
    del parser
    try:
        result = run_init_wizard(
            repo_path=Path(args.repo_path) if args.repo_path else None,
            workflow_name=args.workflow,
            workflow_root=Path(args.workflow_root) if args.workflow_root else None,
            repo_slug=args.repo_slug,
            tracker=args.tracker,
            runtime_preset=args.runtime,
            runtime_name=args.runtime_name,
            model=args.model,
            active_label=args.active_label,
            done_label=args.done_label,
            exclude_labels=_split_arg_csv(args.exclude_labels),
            max_lanes=args.max_lanes,
            force=args.force,
            yes=args.yes,
        )
    except (WorkflowInitError, WorkflowBootstrapError) as exc:
        raise SprintsCommandError(str(exc)) from exc
    if getattr(args, "json", False):
        return json.dumps(result, indent=2, sort_keys=True)
    validation = result.get("validation") or {}
    lines = [
        f"initialized workflow root: {result['workflow_root']}",
        f"contract: {result['contract_path']}",
        f"repo pointer: {result['repo_pointer_path']}",
        f"workflow: {result['workflow']}",
        f"repo: {(result.get('answers') or {}).get('repo_slug')}",
        f"validation ok: {validation.get('ok')}",
        "next steps:",
    ]
    lines.extend(f"- {step}" for step in result.get("next_steps") or [])
    recommendations = validation.get("recommendations") or []
    if recommendations:
        lines.append("recommendations:")
        lines.extend(f"- {item}" for item in recommendations)
    return "\n".join(lines)


def cmd_scaffold_workflow(args, parser) -> str:
    try:
        result = scaffold_workflow_root(
            workflow_root=Path(args.workflow_root),
            workflow_name=args.workflow,
            repo_path=Path(args.repo_path) if args.repo_path else None,
            repo_slug=args.repo_slug,
            engine_owner=args.engine_owner,
            force=args.force,
        )
    except WorkflowBootstrapError as exc:
        raise SprintsCommandError(str(exc)) from exc
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
    try:
        result = bootstrap_workflow_root(
            repo_path=Path(args.repo_path) if args.repo_path else None,
            workflow_name=args.workflow,
            workflow_root=Path(args.workflow_root) if args.workflow_root else None,
            repo_slug=args.repo_slug,
            engine_owner=args.engine_owner,
            force=args.force,
        )
    except WorkflowBootstrapError as exc:
        raise SprintsCommandError(str(exc)) from exc
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


def configure_runtime_preset(
    *,
    workflow_root: Path,
    runtime_preset: str,
    role: str,
    runtime_name: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    try:
        return configure_runtime_contract(
            workflow_root=workflow_root,
            preset_name=runtime_preset,
            role=role,
            runtime_name=runtime_name,
            dry_run=dry_run,
        )
    except (
        RuntimePresetError,
        WorkflowContractError,
        FileNotFoundError,
        OSError,
    ) as exc:
        raise SprintsCommandError(str(exc)) from exc


def _lazy_cmd_watch(args, parser):
    """Lazy import so importing the CLI doesn't pull rich into every invocation."""
    from sprints.observe.watch import cmd_watch

    return cmd_watch(args, parser)


def _build_project_status(workflow_root: Path) -> dict[str, Any]:
    return build_workflow_status(workflow_root)


# Utilities


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
        raise SprintsCommandError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"wrapper command failed: {command}"
        )
    return json.loads(completed.stdout)


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


def _split_arg_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    import sys

    result = execute_raw_args(" ".join(sys.argv[1:]))
    print(result)
    sys.exit(0 if not result.startswith("sprints error:") else 1)
