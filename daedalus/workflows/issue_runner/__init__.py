"""Generic tracker-driven issue runner workflow."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.workflow import ModuleWorkflow
from workflows.issue_runner.config import IssueRunnerConfig

NAME = "issue-runner"
SUPPORTED_SCHEMA_VERSIONS = (1,)
CONFIG_SCHEMA_PATH = Path(__file__).parent / "schema.yaml"
PREFLIGHT_GATED_COMMANDS = frozenset({"tick", "run"})
SERVICE_MODES = frozenset({"active"})

from workflows.issue_runner.cli import main as cli_main
from workflows.issue_runner.preflight import run_preflight
from workflows.issue_runner.workspace import make_workspace as _make_workspace_inner
from workflows.issue_runner.workspace import load_workspace_from_config

import sys as _sys

WORKFLOW = ModuleWorkflow(_sys.modules[__name__])


def load_config(*, workflow_root: Path, raw: dict[str, Any]) -> IssueRunnerConfig:
    return IssueRunnerConfig.from_raw(raw, workflow_root=workflow_root)


def make_workspace(*, workflow_root: Path, config: dict | IssueRunnerConfig):
    raw_config = config.raw if hasattr(config, "raw") else config
    return _make_workspace_inner(workflow_root=workflow_root, config=raw_config)


def service_prepare(
    *,
    workflow_root: Path,
    project_key: str | None,
    service_mode: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "workflow": NAME,
        "project_key": project_key,
        "service_mode": service_mode,
        "skipped": True,
        "reason": "issue-runner initializes engine state through EngineStore on first service tick",
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
    workspace = load_workspace_from_config(workspace_root=workflow_root)
    payload = workspace.run_loop(
        interval_seconds=interval_seconds,
        max_iterations=max_iterations,
    )
    return {
        "workflow": NAME,
        "project_key": project_key,
        "instance_id": instance_id,
        "service_mode": service_mode,
        **payload,
    }

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
