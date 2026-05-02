from __future__ import annotations

from pathlib import Path
from typing import Any

from trackers import (
    DEFAULT_ACTIVE_STATES,
    DEFAULT_TERMINAL_STATES,
    TrackerClient,
    TrackerConfigError,
    build_tracker_client,
    cfg_list,
    describe_tracker_source,
    issue_priority_sort_key,
    load_issues,
    resolve_tracker_path,
)


_WORKSPACE_KEY_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def eligible_issues(*, tracker_cfg: dict[str, Any], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_states = {
        str(value).strip().lower()
        for value in (cfg_list(tracker_cfg, "active_states", "active-states") or DEFAULT_ACTIVE_STATES)
        if str(value).strip()
    }
    terminal_states = {
        str(value).strip().lower()
        for value in (cfg_list(tracker_cfg, "terminal_states", "terminal-states") or DEFAULT_TERMINAL_STATES)
        if str(value).strip()
    }
    required_labels = {
        str(value).strip().lower()
        for value in (cfg_list(tracker_cfg, "required_labels", "required-labels") or [])
        if str(value).strip()
    }
    exclude_labels = {
        str(value).strip().lower()
        for value in (cfg_list(tracker_cfg, "exclude_labels", "exclude-labels") or [])
        if str(value).strip()
    }

    out: list[dict[str, Any]] = []
    for issue in issues:
        state = str(issue.get("state") or "").strip().lower()
        labels = {
            str(label).strip().lower()
            for label in (issue.get("labels") or [])
            if str(label).strip()
        }
        if state and state in terminal_states:
            continue
        if active_states and state not in active_states:
            continue
        if state == "todo":
            blockers = issue.get("blocked_by") or []
            if any(_blocker_is_active(blocker, terminal_states=terminal_states) for blocker in blockers):
                continue
        if required_labels and not required_labels.issubset(labels):
            continue
        if exclude_labels and labels.intersection(exclude_labels):
            continue
        out.append(issue)
    out.sort(key=issue_priority_sort_key)
    return out


def select_issue(*, tracker_cfg: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = eligible_issues(tracker_cfg=tracker_cfg, issues=issues)
    return matches[0] if matches else None


def issue_workspace_slug(issue: dict[str, Any]) -> str:
    identifier = str(issue.get("identifier") or issue.get("id") or "issue").strip()
    raw = identifier or "issue"
    sanitized = "".join(char if char in _WORKSPACE_KEY_ALLOWED else "_" for char in raw)
    return sanitized or "issue"


def issue_session_name(issue: dict[str, Any]) -> str:
    return issue_workspace_slug(issue)[:64]


def _blocker_is_active(blocker: dict[str, Any], *, terminal_states: set[str]) -> bool:
    state = str(blocker.get("state") or "").strip().lower()
    if not state:
        return True
    return state not in terminal_states


__all__ = [
    "DEFAULT_ACTIVE_STATES",
    "DEFAULT_TERMINAL_STATES",
    "TrackerClient",
    "TrackerConfigError",
    "build_tracker_client",
    "describe_tracker_source",
    "eligible_issues",
    "issue_session_name",
    "issue_workspace_slug",
    "load_issues",
    "resolve_tracker_path",
    "select_issue",
]
