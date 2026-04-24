"""YoYoPod Core adapter package for Hermes Relay.

This package will absorb project-specific workflow semantics from the legacy
wrapper over multiple slices. The initial slice adds central path resolution
and directory scaffolding without changing workflow policy yet.

This package satisfies the hermes-relay workflow-plugin contract:

- NAME: the canonical hyphenated workflow name ("code-review")
- SUPPORTED_SCHEMA_VERSIONS: tuple of config schema versions this module accepts
- CONFIG_SCHEMA_PATH: Path to the JSON Schema validating the workflow.yaml config
- make_workspace(*, workflow_root, config): factory returning the workspace accessor
- cli_main(workspace, argv): argparse-backed CLI dispatcher
"""
from pathlib import Path

NAME = "code-review"
SUPPORTED_SCHEMA_VERSIONS = (1,)
CONFIG_SCHEMA_PATH = Path(__file__).parent / "schema.yaml"

# Re-export the two contract callables from their implementation modules.
from workflows.code_review.workspace import (
    load_workspace_from_config as _load_workspace_from_config,
)
from workflows.code_review.cli import main as cli_main


def make_workspace(*, workflow_root: Path, config: dict):
    """Plugin-contract factory.

    In this phase (Phase 2) it ignores ``config`` and reads the workspace's
    live config file via ``load_workspace_from_config``. Phase 4 replaces
    this with a factory that consumes ``config`` directly as a YAML dict.
    """
    return _load_workspace_from_config(workspace_root=workflow_root)


__all__ = [
    "NAME",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CONFIG_SCHEMA_PATH",
    "make_workspace",
    "cli_main",
]
