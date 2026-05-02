from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


"""Change-delivery workflow command-line interface.

This module owns the argparse surface and the ``main`` dispatcher that the
workflow workspace accessor delegates into directly.

Each subcommand is dispatched against the workspace accessor so the adapter
CLI does not take on a direct dependency on module-level globals; instead it
reads them through the accessor. The accessor contract is simply "exposes the
workflow's public entrypoints": ``build_status``,
``reconcile``, ``doctor``, ``dispatch_*``, ``publish_ready_pr``,
``push_pr_update``, ``merge_and_promote``, ``tick``, ``wake_named_jobs``,
``wake_core_jobs``, ``set_core_jobs_enabled``, ``write_lane_state``,
``write_lane_memo``, ``load_ledger``, ``_summarize_validation``,
``_load_optional_json``, plus the ``HEALTH_PATH`` / ``AUDIT_LOG_PATH`` /
``WORKFLOW_WATCHDOG_JOB_NAME`` constants.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the change-delivery workflow automation.")
    sub = parser.add_subparsers(dest="command", required=True)

    status_parser = sub.add_parser("status", help="Show workflow status and health.")
    status_parser.add_argument("--json", action="store_true", help="Print full JSON status.")
    status_parser.add_argument("--write-health", action="store_true", help="Refresh the derived health file.")

    rec = sub.add_parser("reconcile", help="Reconcile stale ledger state against GitHub/jobs.")
    rec.add_argument("--fix-watchers", action="store_true", help="Disable broken issue watch jobs with invalid announce delivery.")

    doctor_parser = sub.add_parser("doctor", help="Check and attempt safe workflow repair.")
    doctor_parser.add_argument("--no-fix-watchers", action="store_true", help="Do not disable broken issue watchers during doctor.")

    preflight_internal = sub.add_parser("preflight-internal-review", help="Run cheap deterministic preflight for the internal review stage.")
    preflight_internal.add_argument("--json", action="store_true", help="Accepted for scripting; preflight output is always JSON.")
    preflight_internal.add_argument("--wake-if-needed", action="store_true", help="Wake the internal review runner when preflight says it should run soon.")

    wake_job = sub.add_parser("wake-job", help="Wake one named job now by pulling its next run forward.")
    wake_job.add_argument("name", help="Exact cron job name to wake.")

    sub.add_parser("pause", help="Disable the core workflow jobs.")
    sub.add_parser("resume", help="Enable the core workflow jobs and wake them now.")
    sub.add_parser("wake", help="Wake the core workflow jobs now without changing enablement intent.")
    sub.add_parser("show-active-lane", help="Print the active-lane issue only.")
    sub.add_parser("show-core-jobs", help="Print only the core workflow job summaries.")
    sub.add_parser("show-lane-state", help="Print the current active lane state artifact.")
    sub.add_parser("show-lane-memo", help="Print the current active lane memo artifact.")
    dispatch_parser = sub.add_parser("dispatch-implementation-turn", help="Run the configured implementation actor for the active lane.")
    dispatch_parser.add_argument("--json", action="store_true", help="Print machine-readable dispatch output.")
    publish_ready_pr_parser = sub.add_parser("publish-ready-pr", help="Publish the ready local branch as a PR for review.")
    publish_ready_pr_parser.add_argument("--json", action="store_true", help="Print machine-readable publish output.")
    push_pr_update_parser = sub.add_parser("push-pr-update", help="Push the current local repair head to the existing PR branch.")
    push_pr_update_parser.add_argument("--json", action="store_true", help="Print machine-readable push output.")
    merge_and_promote_parser = sub.add_parser("merge-and-promote", help="Merge the approved PR and promote the next lane.")
    merge_and_promote_parser.add_argument("--json", action="store_true", help="Print machine-readable merge output.")
    internal_dispatch = sub.add_parser("dispatch-internal-review", help="Run the local pre-publish internal review directly from the wrapper.")
    internal_dispatch.add_argument("--json", action="store_true", help="Print machine-readable dispatch output.")
    restart_actor = sub.add_parser("restart-actor-session", help="Force-close and recreate the active implementation actor session before dispatching the next lane turn.")
    restart_actor.add_argument("--json", action="store_true", help="Print machine-readable restart output.")
    repair_dispatch = sub.add_parser("dispatch-repair-handoff", help="Send the current repair brief back into the active implementation actor session.")
    repair_dispatch.add_argument("--json", action="store_true", help="Print machine-readable repair handoff output.")
    tick_parser = sub.add_parser("tick", help="Run one workflow-watchdog control-loop tick.")
    tick_parser.add_argument("--json", action="store_true", help="Print machine-readable tick output.")

    serve_parser = sub.add_parser(
        "serve",
        help="Run the optional HTTP status surface (Symphony §13.7).",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="TCP port to bind. Overrides config.server.port. 0 = ephemeral (tests).",
    )

    return parser


def print_status(status: dict[str, Any], *, health_path: str, audit_log_path: str) -> None:
    active = status.get("activeLane")
    print(f"health: {status['health']}")
    if status.get("activeLaneError"):
        print(f"active-lane error: {status['activeLaneError']}")
    elif active:
        print(f"active lane: #{active['number']} {active['title']}")
    else:
        print("active lane: none")
    pr = status.get("openPr")
    print(f"open pr: {pr['url']}" if pr else "open pr: none")
    print(f"ledger state: {status['ledger']['workflowState']} review-loop={status['derivedReviewLoopState']} idle={status['ledger']['workflowIdle']}")
    if status.get("legacyWatchdogMode"):
        print(f"legacy watchdog mode: {status['legacyWatchdogMode']}")
    merge_blockers = status.get("derivedMergeBlockers") or []
    if merge_blockers:
        print("merge blockers: " + ", ".join(merge_blockers))
    if status["missingCoreJobs"]:
        print("missing core jobs: " + ", ".join(status["missingCoreJobs"]))
    if status["disabledCoreJobs"]:
        print("disabled core jobs: " + ", ".join(status["disabledCoreJobs"]))
    if status["staleCoreJobs"]:
        print("stale core jobs: " + ", ".join(status["staleCoreJobs"]))
    if status["brokenIssueWatchers"]:
        print("broken issue watchers: " + ", ".join(item["name"] for item in status["brokenIssueWatchers"]))
    if status["drift"]:
        print("drift:")
        for item in status["drift"]:
            print(f"- {item}")
    if status["staleLaneReasons"]:
        print("stale lane signals:")
        for item in status["staleLaneReasons"]:
            print(f"- {item}")
    print(f"health file: {health_path}")
    print(f"audit log: {audit_log_path}")


def main(workspace: Any, argv: list[str] | None = None) -> int:
    """Dispatch a workflow CLI command against the provided ``workspace`` module.

    ``workspace`` is expected to expose the workflow's public entrypoints and
    config constants (``build_status``, ``reconcile``, ``HEALTH_PATH``, etc.).
    Passing a workspace accessor directly is the intended usage.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        status = workspace.build_status()
        if args.write_health:
            workspace._write_json(workspace.HEALTH_PATH, status)
            impl = status.get("implementation") or {}
            worktree = Path(impl["worktree"]) if impl.get("worktree") else None
            workspace.write_lane_state(
                worktree=worktree,
                issue=status.get("activeLane"),
                open_pr=status.get("openPr"),
                implementation=impl,
                reviews=status.get("reviews") or {},
                repair_brief=((workspace.load_ledger().get("repairBrief")) if workspace.load_ledger() else None),
                now_iso=status["updatedAt"],
                latest_progress={"kind": impl.get("status") or status.get("ledger", {}).get("workflowState"), "at": impl.get("updatedAt") or status["updatedAt"]},
                preflight=status.get("preflight") or {},
            )
            workspace.write_lane_memo(
                worktree=worktree,
                issue=status.get("activeLane"),
                branch=impl.get("branch"),
                open_pr=status.get("openPr"),
                repair_brief=(workspace.load_ledger().get("repairBrief") if workspace.load_ledger() else None),
                latest_progress={"kind": impl.get("status") or status.get("ledger", {}).get("workflowState"), "at": impl.get("updatedAt") or status["updatedAt"]},
                validation_summary=workspace._summarize_validation(workspace.load_ledger()),
                acp_strategy=impl.get("acpSessionStrategy"),
            )
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print_status(status, health_path=str(workspace.HEALTH_PATH), audit_log_path=str(workspace.AUDIT_LOG_PATH))
        return 0

    if args.command == "reconcile":
        status = workspace.reconcile(fix_watchers=args.fix_watchers)
        print(json.dumps(status, indent=2))
        return 0

    if args.command == "doctor":
        result = workspace.doctor(fix_watchers=not args.no_fix_watchers)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "preflight-internal-review":
        status = workspace.build_status()
        preflight = ((status.get("preflight") or {}).get("prePublishReview") or {})
        if args.wake_if_needed and preflight.get("wakeSuggested"):
            workspace.wake_named_jobs([workspace.WORKFLOW_WATCHDOG_JOB_NAME])
            preflight = {**preflight, "woken": True, "wokenJob": workspace.WORKFLOW_WATCHDOG_JOB_NAME}
        print(json.dumps(preflight, indent=2))
        return 0

    if args.command == "wake-job":
        result = workspace.wake_named_jobs([args.name])
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "pause":
        status = workspace.set_core_jobs_enabled(False, wake_now=False)
        print(json.dumps({"result": "paused", "health": status["health"]}, indent=2))
        return 0

    if args.command == "resume":
        status = workspace.set_core_jobs_enabled(True, wake_now=True)
        print(json.dumps({"result": "resumed", "health": status["health"]}, indent=2))
        return 0

    if args.command == "wake":
        status = workspace.wake_core_jobs()
        print(json.dumps({"result": "woken", "health": status["health"]}, indent=2))
        return 0

    if args.command == "show-active-lane":
        status = workspace.build_status()
        print(json.dumps(status.get("activeLane"), indent=2))
        return 0

    if args.command == "show-core-jobs":
        status = workspace.build_status()
        print(json.dumps(status.get("coreJobs"), indent=2))
        return 0

    if args.command == "show-lane-state":
        status = workspace.build_status()
        path = (status.get("implementation") or {}).get("laneStatePath")
        payload = workspace._load_optional_json(Path(path)) if path else None
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "show-lane-memo":
        status = workspace.build_status()
        path = (status.get("implementation") or {}).get("laneMemoPath")
        print(Path(path).read_text(encoding="utf-8") if path and Path(path).exists() else "")
        return 0

    if args.command == "dispatch-implementation-turn":
        result = workspace.dispatch_implementation_turn()
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "publish-ready-pr":
        result = workspace.publish_ready_pr()
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "push-pr-update":
        result = workspace.push_pr_update()
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "merge-and-promote":
        result = workspace.merge_and_promote()
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "dispatch-internal-review":
        result = workspace.dispatch_inter_review_agent_review()
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "restart-actor-session":
        result = workspace.restart_actor_session()
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "dispatch-repair-handoff":
        result = workspace.dispatch_repair_handoff()
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "tick":
        result = workspace.tick()
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "serve":
        # Symphony §13.7 — long-running HTTP status surface. Read config
        # from the workspace; CLI --port overrides config.server.port.
        cfg = getattr(workspace, "CONFIG", None) or {}
        server_cfg = cfg.get("server") if isinstance(cfg, dict) else None
        server_cfg = server_cfg or {}
        port = args.port if args.port is not None else server_cfg.get("port", 8080)
        bind = server_cfg.get("bind", "127.0.0.1")
        # Imported lazily so workspaces that never call ``serve`` do not
        # take the server import cost.
        from workflows.change_delivery.server import start_server
        handle = start_server(workspace.WORKSPACE, port=port, bind=bind)
        print(f"daedalus serve listening on http://{bind}:{handle.port}/")
        try:
            handle.thread.join()
        except KeyboardInterrupt:
            handle.shutdown()
        return 0

    parser.error("unknown command")
    return 2
