from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

PROJECT_SLUG = "yoyopod_core"
PROJECT_DISPLAY_NAME = "yoyopod-core"
WORKSPACE_REPO_NAME = "yoyopod-core"
DEFAULT_WORKFLOW_ROOT_ENV_VARS = ("YOYOPOD_RELAY_WORKFLOW_ROOT", "HERMES_RELAY_WORKFLOW_ROOT")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def project_data_root(*, plugin_dir: Path | None = None) -> Path:
    base_dir = (plugin_dir or repo_root()).resolve()
    return base_dir / "projects" / PROJECT_SLUG


def _has_project_runtime_layout(workflow_root: Path) -> bool:
    return any((workflow_root / name).exists() for name in ("runtime", "config", "workspace", "docs"))


def runtime_base_dir(workflow_root: Path) -> Path:
    root = workflow_root.resolve()
    return root / "runtime" if _has_project_runtime_layout(root) else root


def runtime_paths(workflow_root: Path) -> dict[str, Path]:
    base_dir = runtime_base_dir(workflow_root)
    return {
        "db_path": base_dir / "state" / "relay" / "relay.db",
        "event_log_path": base_dir / "memory" / "relay-events.jsonl",
        "alert_state_path": base_dir / "memory" / "hermes-relay-alert-state.json",
    }


def lane_state_path(worktree: Path | None) -> Path | None:
    if worktree is None:
        return None
    return worktree / ".lane-state.json"


def lane_memo_path(worktree: Path | None) -> Path | None:
    if worktree is None:
        return None
    return worktree / ".lane-memo.md"


def tick_dispatch_dir(workflow_root: Path) -> Path:
    return runtime_base_dir(workflow_root) / "memory" / "tick-dispatch"


def tick_dispatch_state_path(workflow_root: Path) -> Path:
    return tick_dispatch_dir(workflow_root) / "active.json"


def tick_dispatch_history_dir(workflow_root: Path) -> Path:
    return tick_dispatch_dir(workflow_root) / "history"


def plugin_entrypoint_path(workflow_root: Path) -> Path:
    """Path to the installed plugin's CLI entrypoint.

    Lives at ``<workflow_root>/.hermes/plugins/hermes-relay/adapters/yoyopod_core/__main__.py``
    after ``./scripts/install.sh``. This is the canonical — and only — YoYoPod
    workflow CLI surface; the historical ``scripts/yoyopod_workflow.py``
    wrapper has been retired.
    """
    root = workflow_root.resolve()
    return (
        root
        / ".hermes"
        / "plugins"
        / "hermes-relay"
        / "adapters"
        / "yoyopod_core"
        / "__main__.py"
    )


def yoyopod_cli_argv(workflow_root: Path, *command_args: str) -> list[str]:
    """Build the argv list to invoke the YoYoPod workflow CLI.

    Always targets the plugin-side entrypoint. If the plugin is not installed
    under ``workflow_root``, the returned path still points at the expected
    install location — callers get a clear ``FileNotFoundError`` at subprocess
    spawn time, which reliably directs operators to run ``./scripts/install.sh``.
    """
    plugin_path = plugin_entrypoint_path(workflow_root)
    return ["python3", str(plugin_path), *command_args]


def _has_installed_plugin(workflow_root: Path) -> bool:
    return plugin_entrypoint_path(workflow_root).exists()


def resolve_default_workflow_root(
    *,
    plugin_dir: Path,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    env_map = env if env is not None else os.environ
    for env_var in DEFAULT_WORKFLOW_ROOT_ENV_VARS:
        value = env_map.get(env_var)
        if value:
            return Path(value).expanduser().resolve()

    installed_candidate = plugin_dir.parent.parent.parent.resolve()
    if _has_installed_plugin(installed_candidate) or _has_project_runtime_layout(installed_candidate):
        return installed_candidate

    legacy_project_candidate = ((home or Path.home()) / ".hermes" / "workflows" / "yoyopod").resolve()
    if _has_installed_plugin(legacy_project_candidate) or _has_project_runtime_layout(legacy_project_candidate):
        return legacy_project_candidate

    repo_project_candidate = project_data_root(plugin_dir=plugin_dir)
    if _has_project_runtime_layout(repo_project_candidate):
        return repo_project_candidate.resolve()

    return repo_project_candidate.resolve()
