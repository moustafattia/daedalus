"""Generic agentic workflow package."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.agentic.cli import main as cli_main
from workflows.agentic.config import AgenticConfig
from workflows.agentic.workflow_object import AgenticWorkflow

NAME = "agentic"
SUPPORTED_SCHEMA_VERSIONS = (1,)
CONFIG_SCHEMA_PATH = Path(__file__).with_name("schema.yaml")
PREFLIGHT_GATED_COMMANDS = frozenset()


def load_config(*, workflow_root: Path, raw: dict[str, Any]) -> AgenticConfig:
    return AgenticConfig.from_raw(raw=raw, workflow_root=workflow_root)


def make_workspace(*, workflow_root: Path, config: object) -> AgenticConfig:
    if isinstance(config, AgenticConfig):
        return config
    if isinstance(config, dict):
        return AgenticConfig.from_raw(raw=config, workflow_root=workflow_root)
    raise TypeError(f"unsupported agentic config object: {type(config).__name__}")


WORKFLOW = AgenticWorkflow(
    name=NAME,
    schema_versions=SUPPORTED_SCHEMA_VERSIONS,
    schema_path=CONFIG_SCHEMA_PATH,
    preflight_gated_commands=PREFLIGHT_GATED_COMMANDS,
    load_config_func=load_config,
    make_workspace_func=make_workspace,
    run_cli_func=cli_main,
)

__all__ = [
    "NAME",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CONFIG_SCHEMA_PATH",
    "PREFLIGHT_GATED_COMMANDS",
    "WORKFLOW",
    "load_config",
    "make_workspace",
    "cli_main",
]
