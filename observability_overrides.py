"""Read/write of observability override file.

Stored at ``<workflow_root>/runtime/state/daedalus/observability-overrides.json``.
Schema::

    {
        "<workflow_name>": {
            "github-comments": {
                "enabled": <bool>,
                "set-at": "<iso8601>",
                "set-by": "<operator label>"
            }
        }
    }

Override is per-workflow and overrides the workflow.yaml value at
resolution time. Used by ``/daedalus set-observability``.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OVERRIDE_FILENAME = "observability-overrides.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _override_path(state_dir: Path) -> Path:
    return Path(state_dir) / OVERRIDE_FILENAME


def _load(state_dir: Path) -> dict[str, Any]:
    p = _override_path(state_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_atomic(state_dir: Path, data: dict[str, Any]) -> None:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    p = _override_path(state_dir)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def set_override(
    state_dir: Path,
    *,
    workflow_name: str,
    github_comments_enabled: bool,
    set_by: str = "operator-cli",
) -> None:
    data = _load(state_dir)
    workflow_block = data.get(workflow_name) or {}
    workflow_block["github-comments"] = {
        "enabled": bool(github_comments_enabled),
        "set-at": _now_iso(),
        "set-by": set_by,
    }
    data[workflow_name] = workflow_block
    _save_atomic(state_dir, data)


def unset_override(state_dir: Path, *, workflow_name: str) -> None:
    data = _load(state_dir)
    if workflow_name in data:
        del data[workflow_name]
        _save_atomic(state_dir, data)


def get_override(state_dir: Path, *, workflow_name: str) -> dict[str, Any]:
    data = _load(state_dir)
    return data.get(workflow_name) or {}
