"""Shared tracker integrations reused across workflows."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Protocol


_SLUG_RE = re.compile(r"[^a-z0-9]+")

DEFAULT_ACTIVE_STATES = ("Todo", "In Progress")
DEFAULT_TERMINAL_STATES = ("Closed", "Cancelled", "Canceled", "Duplicate", "Done")


class TrackerConfigError(RuntimeError):
    """Raised when the tracker section is missing or invalid."""


class TrackerClient(Protocol):
    kind: str

    def list_all(self) -> list[dict[str, Any]]: ...

    def list_candidates(self) -> list[dict[str, Any]]: ...

    def refresh(self, issue_ids: list[str]) -> dict[str, dict[str, Any]]: ...

    def list_terminal(self) -> list[dict[str, Any]]: ...


_TRACKER_KINDS: dict[str, type] = {}


def register(kind: str):
    def _register(cls):
        _TRACKER_KINDS[kind] = cls
        return cls

    return _register


def _ensure_builtin_tracker_kinds() -> None:
    """Register built-ins even if submodules were imported before this package reloaded.

    Hermes plugin tests load repo and installed-plugin copies in the same Python
    process. In that situation ``trackers`` can be re-execed while
    ``trackers.local_json`` remains cached, so decorators do not run again.
    Explicitly binding the built-in classes keeps the registry deterministic.
    """
    from .github import GithubTrackerClient
    from .linear import LinearTrackerClient
    from .local_json import LocalJsonTrackerClient

    _TRACKER_KINDS.setdefault("github", GithubTrackerClient)
    _TRACKER_KINDS.setdefault("linear", LinearTrackerClient)
    _TRACKER_KINDS.setdefault("local-json", LocalJsonTrackerClient)


def resolve_tracker_path(*, workflow_root: Path, tracker_cfg: dict[str, Any]) -> Path:
    path_value = str(tracker_cfg.get("path") or "").strip()
    if not path_value:
        raise TrackerConfigError("tracker.path is required")
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = (workflow_root / path).resolve()
    return path


def describe_tracker_source(*, workflow_root: Path, tracker_cfg: dict[str, Any]) -> str:
    kind = tracker_kind(tracker_cfg)
    if kind == "local-json":
        return str(resolve_tracker_path(workflow_root=workflow_root, tracker_cfg=tracker_cfg))
    if kind == "github":
        slug = tracker_cfg.get("github_slug") or tracker_cfg.get("github-slug")
        if slug:
            return f"github:{slug}"
        repo_path = tracker_cfg.get("repo_path") or tracker_cfg.get("repo-path")
        if repo_path:
            path = Path(str(repo_path)).expanduser()
            if not path.is_absolute():
                path = (workflow_root / path).resolve()
            return str(path)
        return "github"
    endpoint = linear_endpoint(tracker_cfg)
    project_slug = linear_project_slug(tracker_cfg)
    return f"{endpoint}#project={project_slug}"


def build_tracker_client(
    *,
    workflow_root: Path,
    tracker_cfg: dict[str, Any],
    post_json: Callable[..., dict[str, Any]] | None = None,
    repo_path: Path | None = None,
    run_json: Callable[..., Any] | None = None,
) -> TrackerClient:
    kind = tracker_kind(tracker_cfg)
    _ensure_builtin_tracker_kinds()

    if kind not in _TRACKER_KINDS:
        raise TrackerConfigError(
            f"unsupported tracker.kind={kind!r}; supported kinds: {sorted(_TRACKER_KINDS)}"
        )
    cls = _TRACKER_KINDS[kind]
    kwargs = {
        "workflow_root": workflow_root,
        "tracker_cfg": tracker_cfg,
    }
    if kind == "linear":
        kwargs["post_json"] = post_json or http_post_json
    if kind == "github":
        kwargs["repo_path"] = repo_path
        kwargs["run_json"] = run_json
    return cls(**kwargs)


def load_issues(
    *,
    workflow_root: Path,
    tracker_cfg: dict[str, Any],
    repo_path: Path | None = None,
    run_json: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    return build_tracker_client(
        workflow_root=workflow_root,
        tracker_cfg=tracker_cfg,
        repo_path=repo_path,
        run_json=run_json,
    ).list_all()


def tracker_kind(tracker_cfg: dict[str, Any]) -> str:
    kind = str(tracker_cfg.get("kind") or "").strip()
    if not kind:
        raise TrackerConfigError("tracker.kind is required")
    return kind


def linear_endpoint(tracker_cfg: dict[str, Any]) -> str:
    endpoint = str(tracker_cfg.get("endpoint") or "https://api.linear.app/graphql").strip()
    if not endpoint:
        raise TrackerConfigError("tracker.endpoint cannot be blank for tracker.kind='linear'")
    return endpoint


def linear_project_slug(tracker_cfg: dict[str, Any]) -> str:
    project_slug = str(tracker_cfg.get("project_slug") or "").strip()
    if not project_slug:
        raise TrackerConfigError("tracker.project_slug is required for tracker.kind='linear'")
    return project_slug


def linear_api_key(tracker_cfg: dict[str, Any]) -> str:
    raw = str(tracker_cfg.get("api_key") or "$LINEAR_API_KEY").strip()
    value = resolve_env_indirection(raw)
    if not value:
        raise TrackerConfigError("tracker.api_key is required for tracker.kind='linear' (supports $VARNAME indirection)")
    return value


def resolve_env_indirection(value: str) -> str:
    if value.startswith("$") and len(value) > 1:
        return os.environ.get(value[1:], "").strip()
    return value.strip()


def cfg_list(config: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = config.get(key)
        if isinstance(value, list):
            return value
    return []


def coerce_priority(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_blocked_by(*, issue_id: str, payload: Any) -> list[dict[str, Any]]:
    if payload is None or payload == "":
        return []
    if not isinstance(payload, list):
        raise TrackerConfigError(f"issue {issue_id!r} blocked_by must be a list")
    blockers: list[dict[str, Any]] = []
    for index, blocker in enumerate(payload):
        if not isinstance(blocker, dict):
            raise TrackerConfigError(
                f"issue {issue_id!r} blocked_by[{index}] must be an object"
            )
        blockers.append(
            {
                "id": str(blocker.get("id") or "").strip() or None,
                "identifier": str(blocker.get("identifier") or "").strip() or None,
                "state": str(blocker.get("state") or "").strip() or None,
                "created_at": str(blocker.get("created_at") or blocker.get("createdAt") or "").strip() or None,
                "updated_at": str(blocker.get("updated_at") or blocker.get("updatedAt") or "").strip() or None,
            }
        )
    return blockers


def normalize_issue(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TrackerConfigError(f"issue entries must be objects, got {type(payload).__name__}")
    issue_id = str(payload.get("id") or "").strip()
    if not issue_id:
        raise TrackerConfigError("each issue entry must define a non-empty id")
    identifier = str(payload.get("identifier") or issue_id).strip()
    title = str(payload.get("title") or identifier or issue_id).strip()
    description = str(payload.get("description") or "").strip() or None
    state = str(payload.get("state") or "").strip()
    priority = coerce_priority(payload.get("priority"))
    branch_name = str(payload.get("branch_name") or payload.get("branchName") or "").strip() or None
    url = str(payload.get("url") or "").strip() or None
    labels_raw = payload.get("labels") or []
    if not isinstance(labels_raw, list):
        raise TrackerConfigError(f"issue {issue_id!r} labels must be a list")
    labels = [str(label).strip().lower() for label in labels_raw if str(label).strip()]
    blocked_by = normalize_blocked_by(issue_id=issue_id, payload=payload.get("blocked_by") or payload.get("blockedBy"))
    created_at = str(payload.get("created_at") or payload.get("createdAt") or "").strip() or None
    updated_at = str(payload.get("updated_at") or payload.get("updatedAt") or "").strip() or None
    return {
        "id": issue_id,
        "identifier": identifier,
        "title": title,
        "description": description,
        "priority": priority,
        "state": state,
        "branch_name": branch_name,
        "url": url,
        "labels": labels,
        "blocked_by": blocked_by,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def normalize_linear_issue(payload: dict[str, Any]) -> dict[str, Any]:
    labels_connection = payload.get("labels") or {}
    if isinstance(labels_connection, dict):
        labels_nodes = labels_connection.get("nodes") or []
    else:
        labels_nodes = labels_connection if isinstance(labels_connection, list) else []
    labels = [
        str((node or {}).get("name") or "").strip()
        for node in labels_nodes
        if isinstance(node, dict) and str((node or {}).get("name") or "").strip()
    ]

    raw_state = payload.get("state")
    state = ""
    if isinstance(raw_state, dict):
        state = str(raw_state.get("name") or "").strip()
    elif raw_state is not None:
        state = str(raw_state).strip()

    raw_issue = {
        "id": payload.get("id"),
        "identifier": payload.get("identifier"),
        "title": payload.get("title"),
        "description": payload.get("description"),
        "priority": payload.get("priority"),
        "branch_name": payload.get("branchName") or payload.get("branch_name"),
        "url": payload.get("url"),
        "state": state,
        "labels": labels,
        "blocked_by": extract_linear_blockers(payload),
        "created_at": payload.get("createdAt") or payload.get("created_at"),
        "updated_at": payload.get("updatedAt") or payload.get("updated_at"),
    }
    return normalize_issue(raw_issue)


def extract_linear_blockers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    relations = payload.get("relations") or {}
    if isinstance(relations, dict):
        nodes = relations.get("nodes") or []
    else:
        nodes = relations if isinstance(relations, list) else []
    blockers: list[dict[str, Any]] = []
    for relation in nodes:
        if not isinstance(relation, dict):
            continue
        relation_type = str(relation.get("type") or relation.get("relationType") or "").strip().lower()
        if "block" not in relation_type:
            continue
        related = relation.get("relatedIssue") or relation.get("issue") or {}
        if not isinstance(related, dict):
            continue
        state_value = related.get("state")
        if isinstance(state_value, dict):
            state_name = str(state_value.get("name") or "").strip() or None
        else:
            state_name = str(state_value or "").strip() or None
        blockers.append(
            {
                "id": str(related.get("id") or "").strip() or None,
                "identifier": str(related.get("identifier") or "").strip() or None,
                "state": state_name,
                "created_at": str(related.get("createdAt") or related.get("created_at") or "").strip() or None,
                "updated_at": str(related.get("updatedAt") or related.get("updated_at") or "").strip() or None,
            }
        )
    return blockers


def issue_priority_sort_key(issue: dict[str, Any]) -> tuple[int, str, str]:
    priority = issue.get("priority")
    priority_key = int(priority) if isinstance(priority, int) else 999999
    created_key = str(issue.get("created_at") or "")
    identifier = str(issue.get("identifier") or issue.get("id") or "")
    return (priority_key, created_key, identifier)


def chunk(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def http_post_json(endpoint: str, *, query: str, variables: dict[str, Any], api_key: str) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TrackerConfigError(f"Linear API request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise TrackerConfigError(f"Linear API request failed: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise TrackerConfigError("Linear API response was not a JSON object")
    errors = payload.get("errors") or []
    if errors:
        raise TrackerConfigError(f"Linear API returned errors: {errors}")
    return payload
