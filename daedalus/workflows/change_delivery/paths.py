"""Compatibility wrapper around the shared workflow path helpers.

This module resolves the shared implementation by local file path rather than
importing ``workflows.shared.paths`` through the package namespace. That keeps
tests and source-tree loads pinned to the repo copy even if another Daedalus
plugin install was imported earlier in the same interpreter.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_SHARED_PATH = Path(__file__).resolve().parents[1] / "shared" / "paths.py"
_SPEC = spec_from_file_location("daedalus_workflows_shared_paths_for_change_delivery", _SHARED_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"unable to load shared workflow paths from {_SHARED_PATH}")
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

_PUBLIC_NAMES = (
    "DEFAULT_WORKFLOW_ROOT_ENV_VARS",
    "REPO_LOCAL_WORKFLOW_POINTER_RELATIVE_PATH",
    "normalize_project_key",
    "normalize_workflow_instance_segment",
    "derive_workflow_instance_name",
    "workflow_markdown_path",
    "repo_local_workflow_pointer_path",
    "workflow_contract_path",
    "load_workflow_config",
    "workflow_instance_name",
    "project_key_for_workflow_root",
    "runtime_base_dir",
    "runtime_paths",
    "lane_state_path",
    "lane_memo_path",
    "tick_dispatch_dir",
    "tick_dispatch_state_path",
    "tick_dispatch_history_dir",
    "plugin_root_path",
    "plugin_entrypoint_path",
    "plugin_runtime_path",
    "workflow_cli_argv",
    "resolve_default_workflow_root",
)

for _name in _PUBLIC_NAMES:
    globals()[_name] = getattr(_MODULE, _name)

__all__ = list(_PUBLIC_NAMES)
