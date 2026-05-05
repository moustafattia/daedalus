from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

from sprints.core.contracts import (
    DEFAULT_WORKFLOW_MARKDOWN_FILENAME,
    find_workflow_contract_path,
    load_workflow_contract,
    workflow_markdown_path as _workflow_markdown_path,
)

DEFAULT_WORKFLOW_ROOT_ENV_VARS = ("SPRINTS_WORKFLOW_ROOT",)
REPO_LOCAL_WORKFLOW_POINTER_RELATIVE_PATH = (
    Path(".hermes") / "sprints" / "workflow-root"
)

_PROJECT_KEY_CHARS_RE = re.compile(r"[^a-z0-9._-]+")
_PROJECT_KEY_SEPARATORS_RE = re.compile(r"[-._]{2,}")
_WORKFLOW_INSTANCE_SEGMENT_RE = re.compile(r"[^a-z0-9]+")


def normalize_project_key(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = _PROJECT_KEY_CHARS_RE.sub("-", text)
    text = _PROJECT_KEY_SEPARATORS_RE.sub("-", text)
    text = text.strip("-.")
    return text or "workflow"


def normalize_workflow_instance_segment(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = _WORKFLOW_INSTANCE_SEGMENT_RE.sub("-", text)
    return text.strip("-")


def derive_workflow_instance_name(*, repo_slug: str, workflow_name: str) -> str:
    slug = str(repo_slug or "").strip()
    if slug.count("/") != 1:
        raise ValueError("repo slug must use owner/repo format")
    owner_raw, repo_raw = slug.split("/", 1)
    owner = normalize_workflow_instance_segment(owner_raw)
    repo = normalize_workflow_instance_segment(repo_raw)
    workflow = normalize_workflow_instance_segment(workflow_name)
    if not owner or not repo or not workflow:
        raise ValueError(
            "workflow instance name requires non-empty owner, repo, and workflow segments"
        )
    return f"{owner}-{repo}-{workflow}"


def workflow_markdown_path(workflow_root: Path) -> Path:
    return _workflow_markdown_path(workflow_root)


def repo_local_workflow_pointer_path(repo_root: Path) -> Path:
    return repo_root.resolve() / REPO_LOCAL_WORKFLOW_POINTER_RELATIVE_PATH


def workflow_contract_path(workflow_root: Path) -> Path:
    path = find_workflow_contract_path(workflow_root)
    if path is None:
        raise FileNotFoundError(
            f"workflow contract not found under {Path(workflow_root).resolve()} "
            f"(looked for {DEFAULT_WORKFLOW_MARKDOWN_FILENAME} / WORKFLOW-<name>.md)"
        )
    return path


def load_workflow_config(workflow_root: Path) -> dict:
    return load_workflow_contract(workflow_root).config


def workflow_instance_name(workflow_root: Path) -> str:
    config = load_workflow_config(workflow_root)
    instance = config.get("instance")
    if not isinstance(instance, dict):
        raise ValueError(
            f"{workflow_contract_path(workflow_root)} is missing required instance config"
        )
    name = str(instance.get("name") or "").strip()
    if not name:
        raise ValueError(
            f"{workflow_contract_path(workflow_root)} is missing instance.name"
        )
    return name


def project_key_for_workflow_root(workflow_root: Path) -> str:
    return normalize_project_key(workflow_instance_name(workflow_root))


def _has_project_runtime_layout(workflow_root: Path) -> bool:
    return any(
        (workflow_root / name).exists()
        for name in ("runtime", "config", "workspace", "docs")
    )


def _is_discoverable_markdown_workflow_root(workflow_root: Path) -> bool:
    return any(
        (workflow_root / name).exists() for name in ("runtime", "memory", "state")
    )


def runtime_base_dir(workflow_root: Path) -> Path:
    root = workflow_root.resolve()
    return root / "runtime" if _has_project_runtime_layout(root) else root


def runtime_paths(workflow_root: Path) -> dict[str, Path]:
    base_dir = runtime_base_dir(workflow_root)
    return {
        "db_path": base_dir / "state" / "sprints" / "sprints.db",
        "event_log_path": base_dir / "memory" / "sprints-events.jsonl",
        "alert_state_path": base_dir / "memory" / "sprints-alert-state.json",
    }


def plugin_root_path(*, plugin_dir: Path | None = None) -> Path:
    if plugin_dir is not None:
        candidate = Path(plugin_dir).expanduser().resolve()
        if candidate.name == "workflows":
            return candidate.parent
        return candidate
    return Path(__file__).resolve().parents[1]


def plugin_entrypoint_path(
    workflow_root: Path | None = None, *, plugin_dir: Path | None = None
) -> Path:
    del workflow_root
    return plugin_root_path(plugin_dir=plugin_dir) / "workflows" / "__main__.py"


def workflow_cli_argv(workflow_root: Path, *command_args: str) -> list[str]:
    import sys

    plugin_path = plugin_entrypoint_path(workflow_root)
    return [sys.executable, str(plugin_path), *command_args]


def _find_workflow_root(start: Path) -> Path | None:
    path = start.expanduser().resolve()
    for candidate in (path, *path.parents):
        if workflow_markdown_path(
            candidate
        ).exists() and _is_discoverable_markdown_workflow_root(candidate):
            return candidate
        pointer_path = repo_local_workflow_pointer_path(candidate)
        if pointer_path.exists():
            try:
                pointer_value = pointer_path.read_text(encoding="utf-8").strip()
            except OSError:
                pointer_value = ""
            if pointer_value:
                target = Path(pointer_value).expanduser()
                if not target.is_absolute():
                    target = (candidate / target).resolve()
                else:
                    target = target.resolve()
                if find_workflow_contract_path(target) is not None:
                    return target
    return None


def resolve_default_workflow_root(
    *,
    plugin_dir: Path,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    del home
    env_map = env if env is not None else os.environ
    for env_var in DEFAULT_WORKFLOW_ROOT_ENV_VARS:
        value = env_map.get(env_var)
        if value:
            return Path(value).expanduser().resolve()

    cwd_path = (cwd or Path.cwd()).expanduser().resolve()
    detected = _find_workflow_root(cwd_path)
    if detected is not None:
        return detected

    plugin_dir = plugin_root_path(plugin_dir=plugin_dir)
    repo_parent = plugin_dir.parent.resolve()
    if workflow_markdown_path(
        repo_parent
    ).exists() and _is_discoverable_markdown_workflow_root(repo_parent):
        return repo_parent
    return cwd_path
