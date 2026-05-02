"""Workflow object implementation for the agentic workflow."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class AgenticWorkflow:
    name: str
    schema_versions: tuple[int, ...]
    schema_path: Path
    preflight_gated_commands: frozenset[str]
    load_config_func: Callable[..., object]
    make_workspace_func: Callable[..., object]
    run_cli_func: Callable[..., int]

    def load_config(self, *, workflow_root: Path, raw: dict[str, Any]) -> object:
        return self.load_config_func(workflow_root=workflow_root, raw=raw)

    def make_workspace(self, *, workflow_root: Path, config: object) -> object:
        return self.make_workspace_func(workflow_root=workflow_root, config=config)

    def run_cli(self, *, workspace: object, argv: list[str]) -> int:
        return self.run_cli_func(workspace, argv)

    def run_preflight(self, *, workflow_root: Path, config: object) -> object:
        return type("PreflightResult", (), {"ok": True})()
