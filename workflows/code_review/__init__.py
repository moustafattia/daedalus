"""Code-review workflow package for Daedalus.

This package will absorb project-specific workflow semantics from the legacy
wrapper over multiple slices. The initial slice adds central path resolution
and directory scaffolding without changing workflow policy yet.

This package satisfies the daedalus workflow-plugin contract:

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

from workflows.code_review.workspace import make_workspace as _make_workspace_inner
from workflows.code_review.cli import main as cli_main


def make_workspace(*, workflow_root: Path, config: dict):
    """Plugin-contract factory.

    The plugin contract uses ``workflow_root``; the internal workspace
    factory uses the historical name ``workspace_root``. Translate at this
    boundary, then pass the YAML config dict through for the factory to
    detect-and-bridge to its legacy view if needed.
    """
    return _make_workspace_inner(workspace_root=workflow_root, config=config)


__all__ = [
    "NAME",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CONFIG_SCHEMA_PATH",
    "make_workspace",
    "cli_main",
]
