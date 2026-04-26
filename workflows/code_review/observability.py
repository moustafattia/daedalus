"""Effective observability config resolution.

Resolution precedence (highest first):

1. Override file at ``<override_dir>/observability-overrides.json`` (per-workflow,
   set by the operator via the ``/daedalus set-observability`` slash command).
2. ``observability:`` block in ``workflow.yaml``.
3. Hardcoded defaults (everything off).

The override file is canonical for "right now this workflow's observability is X"
without forcing an edit-and-redeploy cycle on workflow.yaml.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


OVERRIDE_FILENAME = "observability-overrides.json"

# The default include-events whitelist matches design spec §5 — these are the
# six lifecycle transitions that are interesting to a human reader of the
# ticket. An empty list (explicitly set in workflow.yaml or override) means
# "firehose, render every audit action" — useful for debugging only.
_DEFAULT_INCLUDE_EVENTS = [
    "dispatch-implementation-turn",
    "internal-review-completed",
    "publish-ready-pr",
    "push-pr-update",
    "merge-and-promote",
    "operator-attention-transition",
    "operator-attention-recovered",
]

_DEFAULT_GITHUB_COMMENTS = {
    "enabled": False,
    "mode": "edit-in-place",
    "include-events": list(_DEFAULT_INCLUDE_EVENTS),
}


def _read_override_file(override_dir: Path) -> dict[str, Any]:
    path = override_dir / OVERRIDE_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable override file — pretend it does not exist.
        # The operator override surface is best-effort observability config;
        # never block real workflow execution on a malformed override.
        return {}


def resolve_effective_config(
    *,
    workflow_yaml: Mapping[str, Any],
    override_dir: Path,
    workflow_name: str,
) -> dict[str, Any]:
    """Return the effective observability config for ``workflow_name``.

    The result has the shape::

        {
            "github-comments": {"enabled": bool, "mode": str, ...},
            "source": {"github-comments": "default" | "yaml" | "override"},
        }

    ``source`` is informational — used by ``/daedalus get-observability`` to
    explain *why* the current value is what it is.
    """
    yaml_block = (workflow_yaml or {}).get("observability") or {}
    yaml_gh = yaml_block.get("github-comments")

    overrides = _read_override_file(override_dir)
    override_for_wf = (overrides.get(workflow_name) or {}).get("github-comments")

    if override_for_wf is not None:
        merged = {**_DEFAULT_GITHUB_COMMENTS, **(yaml_gh or {}), **override_for_wf}
        # Strip the bookkeeping fields that the override file may carry.
        merged.pop("set-at", None)
        merged.pop("set-by", None)
        source = "override"
    elif yaml_gh is not None:
        merged = {**_DEFAULT_GITHUB_COMMENTS, **yaml_gh}
        source = "yaml"
    else:
        merged = dict(_DEFAULT_GITHUB_COMMENTS)
        source = "default"

    return {
        "github-comments": merged,
        "source": {"github-comments": source},
    }


def event_is_included(effective_config: Mapping[str, Any], audit_action: str) -> bool:
    """Whether ``audit_action`` should produce a comment update under ``effective_config``."""
    gh = (effective_config or {}).get("github-comments") or {}
    if not gh.get("enabled"):
        return False
    include = gh.get("include-events") or []
    if not include:
        # Empty list = include every event (caller's whitelist is "everything").
        return True
    return audit_action in include
