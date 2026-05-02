"""Generic tracker-driven issue runner workflow."""
from __future__ import annotations

from pathlib import Path

NAME = "issue-runner"
SUPPORTED_SCHEMA_VERSIONS = (1,)
CONFIG_SCHEMA_PATH = Path(__file__).parent / "schema.yaml"
PREFLIGHT_GATED_COMMANDS = frozenset({"tick", "run"})

from workflows.issue_runner.cli import main as cli_main
from workflows.issue_runner.preflight import run_preflight
from workflows.issue_runner.workspace import make_workspace

__all__ = [
    "NAME",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CONFIG_SCHEMA_PATH",
    "PREFLIGHT_GATED_COMMANDS",
    "make_workspace",
    "cli_main",
    "run_preflight",
]
