"""Presentation-neutral application command APIs for Sprints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sprints.core.doctor import build_doctor_report
from sprints.core.init_wizard import run_init_wizard
from sprints.core.validation import validate_workflow_contract
from sprints.engine.reports import build_events_report, build_runs_report
from sprints.services.codex_service import (
    codex_app_server_doctor,
    codex_app_server_status,
)
from sprints.services.daemon import workflow_daemon_status
from sprints.workflows.status import build_status


def get_status(workflow_root: Path) -> dict[str, Any]:
    return build_status(Path(workflow_root))


def run_doctor(workflow_root: Path, *, fix: bool = False) -> dict[str, Any]:
    return build_doctor_report(workflow_root=Path(workflow_root), fix=fix)


def init_workflow(**options: Any) -> dict[str, Any]:
    return run_init_wizard(**options)


def validate_workflow(workflow_root: Path) -> dict[str, Any]:
    return validate_workflow_contract(Path(workflow_root))


def list_runs(workflow_root: Path, **filters: Any) -> dict[str, Any]:
    return build_runs_report(workflow_root=Path(workflow_root), **filters)


def list_events(workflow_root: Path, **filters: Any) -> dict[str, Any]:
    return build_events_report(workflow_root=Path(workflow_root), **filters)


def get_daemon_status(workflow_root: Path, **options: Any) -> dict[str, Any]:
    return workflow_daemon_status(workflow_root=Path(workflow_root), **options)


def get_codex_app_server_status(workflow_root: Path, **options: Any) -> dict[str, Any]:
    return codex_app_server_status(workflow_root=Path(workflow_root), **options)


def run_codex_app_server_doctor(workflow_root: Path, **options: Any) -> dict[str, Any]:
    return codex_app_server_doctor(workflow_root=Path(workflow_root), **options)
