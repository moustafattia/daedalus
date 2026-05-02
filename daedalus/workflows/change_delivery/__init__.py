"""Change-delivery workflow package for Daedalus.

This package will absorb project-specific workflow semantics from the legacy
wrapper over multiple slices. The initial slice adds central path resolution
and directory scaffolding without changing workflow policy yet.

This package satisfies the daedalus workflow-plugin contract:

- NAME: the canonical hyphenated workflow name ("change-delivery")
- SUPPORTED_SCHEMA_VERSIONS: tuple of config schema versions this module accepts
- CONFIG_SCHEMA_PATH: Path to the JSON Schema validating the workflow contract
- make_workspace(*, workflow_root, config): factory returning the workspace accessor
- cli_main(workspace, argv): argparse-backed CLI dispatcher
"""
from pathlib import Path
from typing import Any

from workflows.workflow import ModuleWorkflow
from workflows.change_delivery.config import ChangeDeliveryConfig

NAME = "change-delivery"
SUPPORTED_SCHEMA_VERSIONS = (1,)
CONFIG_SCHEMA_PATH = Path(__file__).parent / "schema.yaml"
SERVICE_MODES = frozenset({"shadow", "active"})

# Codex P1 on PR #21: preflight must ONLY gate commands that actually
# attempt dispatch. Diagnostic / repair / read-only commands like status,
# reconcile, doctor, preflight-* must remain available even when the
# config has a missing credential or unsupported runtime kind, because
# operators rely on them to debug exactly that situation.
#
# Commands listed here trigger the run_preflight() check in the
# generic dispatcher (workflows/__init__.py::run_cli). Anything not in
# this set runs without preflight gating.
PREFLIGHT_GATED_COMMANDS = frozenset({
    "tick",
    "dispatch-implementation-turn",
    "dispatch-internal-review",
    "dispatch-repair-handoff",
    "restart-actor-session",
    "publish-ready-pr",
    "push-pr-update",
    "merge-and-promote",
    "wake",
    "wake-job",
    "resume",
})

from workflows.change_delivery.workspace import make_workspace as _make_workspace_inner
from workflows.change_delivery.workspace import load_workspace_from_config
from workflows.change_delivery.cli import main as cli_main
from workflows.change_delivery.preflight import run_preflight

import sys as _sys

WORKFLOW = ModuleWorkflow(_sys.modules[__name__])


def load_config(*, workflow_root: Path, raw: dict[str, Any]) -> ChangeDeliveryConfig:
    return ChangeDeliveryConfig.from_raw(raw, workflow_root=workflow_root)


def make_workspace(*, workflow_root: Path, config: dict | ChangeDeliveryConfig):
    """Plugin-contract factory.

    The plugin contract uses ``workflow_root``; the internal workspace
    factory uses the historical name ``workspace_root``. Translate at this
    boundary, then pass the YAML config dict through for the factory to
    detect-and-bridge to its legacy view if needed.
    """
    raw_config = config.raw if hasattr(config, "raw") else config
    return _make_workspace_inner(workspace_root=workflow_root, config=raw_config)


def _instance_id_for(*, service_mode: str, workflow_root: Path) -> str:
    return f"daedalus-{service_mode}-{workflow_root.name}"


def _ensure_active_lane_for_service(workflow_root: Path) -> dict[str, Any]:
    try:
        workspace = load_workspace_from_config(workspace_root=workflow_root)
        return workspace.ensure_active_lane()
    except Exception as exc:
        return {
            "ok": False,
            "promoted": False,
            "reason": "active-lane-selection-failed",
            "error": f"{type(exc).__name__}: {exc}",
        }


def service_prepare(
    *,
    workflow_root: Path,
    project_key: str | None,
    service_mode: str,
) -> dict[str, Any]:
    import runtime as daedalus_runtime

    resolved_project_key = project_key or daedalus_runtime._project_key_for(workflow_root)
    init_result = daedalus_runtime.init_daedalus_db(
        workflow_root=workflow_root,
        project_key=resolved_project_key,
    )
    lane_selection = _ensure_active_lane_for_service(workflow_root) if service_mode == "active" else None
    return {
        "ok": bool(init_result.get("ok", True)) and not (
            lane_selection is not None and lane_selection.get("ok") is False
        ),
        "workflow": NAME,
        "project_key": resolved_project_key,
        "service_mode": service_mode,
        "init": init_result,
        "lane_selection": lane_selection,
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
    import runtime as daedalus_runtime

    resolved_project_key = project_key or daedalus_runtime._project_key_for(workflow_root)
    resolved_instance_id = instance_id or _instance_id_for(
        service_mode=service_mode,
        workflow_root=workflow_root,
    )
    if service_mode == "shadow":
        result = daedalus_runtime.run_shadow_loop(
            workflow_root=workflow_root,
            project_key=resolved_project_key,
            instance_id=resolved_instance_id,
            interval_seconds=interval_seconds,
            max_iterations=max_iterations,
        )
        return {"workflow": NAME, "service_mode": service_mode, **result}
    if service_mode != "active":
        raise ValueError(f"change-delivery does not support service_mode={service_mode!r}")
    lane_selection = _ensure_active_lane_for_service(workflow_root)
    result = daedalus_runtime.run_active_loop(
        workflow_root=workflow_root,
        project_key=resolved_project_key,
        instance_id=resolved_instance_id,
        interval_seconds=interval_seconds,
        max_iterations=max_iterations,
    )
    result["lane_selection"] = lane_selection
    return {"workflow": NAME, "service_mode": service_mode, **result}


__all__ = [
    "NAME",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CONFIG_SCHEMA_PATH",
    "PREFLIGHT_GATED_COMMANDS",
    "SERVICE_MODES",
    "WORKFLOW",
    "load_config",
    "make_workspace",
    "cli_main",
    "run_preflight",
    "service_prepare",
    "service_loop",
]
