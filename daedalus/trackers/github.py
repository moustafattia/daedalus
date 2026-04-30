from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import TrackerConfigError, issue_priority_sort_key, normalize_issue, register


_GITHUB_SLUG_RE = re.compile(
    r"^(?:(?P<host>[A-Za-z0-9.-]+(?::[0-9]+)?)/)?"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$"
)


def _github_slug_match(raw: str) -> re.Match[str] | None:
    return _GITHUB_SLUG_RE.match(raw)


def _github_slug_config_error() -> TrackerConfigError:
    return TrackerConfigError(
        "tracker.github_slug or repository.github-slug must be in owner/repo or host/owner/repo form for tracker.kind='github'"
    )


def github_auth_host_from_slug(slug: str | None) -> str | None:
    raw = str(slug or "").strip()
    if not raw:
        return None
    match = _github_slug_match(raw)
    if not match:
        raise _github_slug_config_error()
    return match.group("host") or "github.com"


def github_name_with_owner_from_slug(slug: str | None) -> str | None:
    raw = str(slug or "").strip()
    if not raw:
        return None
    match = _github_slug_match(raw)
    if not match:
        raise _github_slug_config_error()
    return f"{match.group('owner')}/{match.group('repo')}"


def github_auth_success_accounts(
    payload: dict[str, Any],
    *,
    hostname: str | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    hosts = payload.get("hosts") if isinstance(payload, dict) else None
    if not isinstance(hosts, dict):
        raise RuntimeError("gh auth status did not return host information")

    if hostname:
        accounts = hosts.get(hostname) or []
        if not isinstance(accounts, list):
            raise RuntimeError(f"gh auth status returned invalid {hostname} account information")
        valid_accounts = [account for account in accounts if isinstance(account, dict)]
        success_accounts = [
            account for account in valid_accounts if account.get("state") == "success"
        ]
        if not success_accounts:
            raise RuntimeError(
                f"gh is not authenticated for {hostname}; run `gh auth login --hostname {hostname}`"
            )
        return hostname, success_accounts

    for host, accounts in hosts.items():
        if not isinstance(accounts, list):
            continue
        valid_accounts = [account for account in accounts if isinstance(account, dict)]
        success_accounts = [
            account for account in valid_accounts if account.get("state") == "success"
        ]
        if success_accounts:
            return str(host), success_accounts
    raise RuntimeError("gh is not authenticated for any GitHub host; run `gh auth login`")


def issue_label_names(issue: dict[str, Any] | None) -> set[str]:
    labels = (issue or {}).get("labels") or []
    names: set[str] = set()
    for label in labels:
        if isinstance(label, dict):
            name = str(label.get("name") or "").strip().lower()
            if name:
                names.add(name)
        elif isinstance(label, str):
            name = label.strip().lower()
            if name:
                names.add(name)
    return names


def normalize_github_issue(payload: dict[str, Any]) -> dict[str, Any]:
    issue_number = payload.get("number")
    issue_id = str(issue_number or payload.get("id") or "").strip()
    if not issue_id:
        raise TrackerConfigError("GitHub issue payload is missing number/id")
    raw = {
        "id": issue_id,
        "identifier": f"#{issue_id}",
        "title": payload.get("title"),
        "description": payload.get("body"),
        "priority": None,
        "branch_name": None,
        "url": payload.get("url"),
        "state": str(payload.get("state") or "open").strip().lower(),
        "labels": sorted(issue_label_names(payload)),
        "blocked_by": [],
        "created_at": payload.get("createdAt") or payload.get("created_at"),
        "updated_at": payload.get("updatedAt") or payload.get("updated_at"),
    }
    return normalize_issue(raw)


def _subprocess_run_json(command: list[str], *, cwd: Path | None = None) -> Any:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout or "null")
    if not isinstance(payload, (dict, list)):
        raise RuntimeError("expected JSON object or list payload")
    return payload


def github_slug_from_config(
    tracker_cfg: dict[str, Any],
    repository_cfg: dict[str, Any] | None = None,
) -> str | None:
    repository_cfg = repository_cfg or {}
    raw = str(
        tracker_cfg.get("github_slug")
        or tracker_cfg.get("github-slug")
        or repository_cfg.get("github_slug")
        or repository_cfg.get("github-slug")
        or ""
    ).strip()
    if not raw:
        return None
    if not _github_slug_match(raw):
        raise _github_slug_config_error()
    return raw


def _configured_states(tracker_cfg: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = tracker_cfg.get(key)
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
    return []


def validate_github_tracker_config(
    *,
    workflow_root: Path,
    tracker_cfg: dict[str, Any],
    repository_cfg: dict[str, Any] | None = None,
    repo_path: Path | None = None,
) -> None:
    repository_cfg = repository_cfg or {}
    slug = github_slug_from_config(tracker_cfg, repository_cfg)
    resolved_repo_path = _resolve_repo_path(
        workflow_root=workflow_root,
        tracker_cfg=tracker_cfg,
        repo_path=repo_path,
        required=slug is None,
    )
    if resolved_repo_path is not None and not resolved_repo_path.exists():
        raise TrackerConfigError(
            f"repository.local-path does not exist for tracker.kind='github': {resolved_repo_path}"
        )

    active_states = _configured_states(tracker_cfg, "active_states", "active-states")
    terminal_states = _configured_states(tracker_cfg, "terminal_states", "terminal-states")
    if not active_states or set(active_states) != {"open"}:
        raise TrackerConfigError(
            "tracker.kind='github' requires tracker.active_states: [open]"
        )
    if not terminal_states or set(terminal_states) != {"closed"}:
        raise TrackerConfigError(
            "tracker.kind='github' requires tracker.terminal_states: [closed]"
        )

    for key in ("required_labels", "required-labels", "exclude_labels", "exclude-labels"):
        value = tracker_cfg.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            raise TrackerConfigError(f"tracker.{key} must be a list for tracker.kind='github'")
        if any(not str(item).strip() for item in value):
            raise TrackerConfigError(f"tracker.{key} must not contain blank labels")


def _resolve_repo_path(
    *,
    workflow_root: Path,
    tracker_cfg: dict[str, Any],
    repo_path: Path | None,
    required: bool = True,
) -> Path | None:
    if repo_path is not None:
        return repo_path.expanduser().resolve()

    raw = str(
        tracker_cfg.get("repo_path")
        or tracker_cfg.get("repo-path")
        or ""
    ).strip()
    if not raw:
        if not required:
            return None
        raise TrackerConfigError(
            "tracker.kind='github' requires tracker.github_slug, repository.github-slug, or repository.local-path"
        )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (workflow_root / path).resolve()
    return path


def _coerce_issue_number(issue_id: str | int | None) -> str | None:
    if issue_id in (None, ""):
        return None
    text = str(issue_id).strip()
    if text.startswith("#"):
        text = text[1:].strip()
    return text or None


@register("github")
class GithubTrackerClient:
    kind = "github"

    def __init__(
        self,
        *,
        workflow_root: Path,
        tracker_cfg: dict[str, Any],
        repo_path: Path | None = None,
        run_json: Callable[..., Any] | None = None,
    ):
        self._workflow_root = workflow_root
        self._tracker_cfg = tracker_cfg
        self._repo_path = _resolve_repo_path(
            workflow_root=workflow_root,
            tracker_cfg=tracker_cfg,
            repo_path=repo_path,
            required=github_slug_from_config(tracker_cfg) is None,
        )
        self._repo_slug = github_slug_from_config(tracker_cfg)
        self._run_json = run_json or _subprocess_run_json

    @property
    def repo_path(self) -> Path | None:
        return self._repo_path

    @property
    def repo_slug(self) -> str | None:
        return self._repo_slug

    def _with_repo(self, command: list[str]) -> list[str]:
        if not self._repo_slug:
            return command
        return [*command, "--repo", self._repo_slug]

    def list_issue_payloads(
        self,
        *,
        state: str,
        limit: int,
        fields: str,
    ) -> list[dict[str, Any]]:
        payload = self._run_json(
            self._with_repo(
                [
                    "gh",
                    "issue",
                    "list",
                    "--state",
                    state,
                    "--limit",
                    str(limit),
                    "--json",
                    fields,
                ]
            ),
            cwd=self._repo_path,
        )
        if not isinstance(payload, list):
            raise RuntimeError("expected gh issue list JSON array payload")
        return [item for item in payload if isinstance(item, dict)]

    def repo_view_payload(self) -> dict[str, Any]:
        command = ["gh", "repo", "view", "--json", "nameWithOwner"]
        if self._repo_slug:
            command = ["gh", "repo", "view", self._repo_slug, "--json", "nameWithOwner"]
        payload = self._run_json(
            command,
            cwd=self._repo_path,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("expected gh repo view JSON object payload")
        return payload

    def auth_status_payload(self, hostname: str | None = None) -> dict[str, Any]:
        command = [
            "gh",
            "auth",
            "status",
            "--json",
            "hosts",
        ]
        if hostname:
            command = [
                "gh",
                "auth",
                "status",
                "--hostname",
                hostname,
                "--json",
                "hosts",
            ]
        payload = self._run_json(
            command,
            cwd=self._repo_path,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("expected gh auth status JSON object payload")
        return payload

    def list_open_issue_payloads(
        self,
        *,
        limit: int = 100,
        fields: str = "number,title,url,labels,createdAt",
    ) -> list[dict[str, Any]]:
        return self.list_issue_payloads(state="open", limit=limit, fields=fields)

    def view_issue_payload(
        self,
        issue_id: str | int | None,
        *,
        fields: str = "number,title,url,body",
    ) -> dict[str, Any] | None:
        issue_number = _coerce_issue_number(issue_id)
        if issue_number is None:
            return None
        payload = self._run_json(
            self._with_repo(["gh", "issue", "view", issue_number, "--json", fields]),
            cwd=self._repo_path,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("expected gh issue view JSON object payload")
        return payload

    def list_all(self) -> list[dict[str, Any]]:
        issues = {}
        for payload in self.list_issue_payloads(
            state="all",
            limit=200,
            fields="number,title,url,body,labels,createdAt,updatedAt,state",
        ):
            issue = normalize_github_issue(payload)
            issues[issue["id"]] = issue
        return sorted(issues.values(), key=issue_priority_sort_key)

    def list_candidates(self) -> list[dict[str, Any]]:
        from workflows.issue_runner.tracker import eligible_issues

        issues = [
            normalize_github_issue(payload)
            for payload in self.list_issue_payloads(
                state="open",
                limit=200,
                fields="number,title,url,body,labels,createdAt,updatedAt,state",
            )
        ]
        return eligible_issues(tracker_cfg=self._tracker_cfg, issues=issues)

    def refresh(self, issue_ids: list[str]) -> dict[str, dict[str, Any]]:
        refreshed: dict[str, dict[str, Any]] = {}
        for issue_id in issue_ids:
            issue_number = _coerce_issue_number(issue_id)
            if issue_number is None:
                continue
            try:
                payload = self.view_issue_payload(
                    issue_number,
                    fields="number,title,url,body,labels,createdAt,updatedAt,state",
                )
            except Exception:
                continue
            if payload is None:
                continue
            issue = normalize_github_issue(payload)
            refreshed[issue["id"]] = issue
        return refreshed

    def list_terminal(self) -> list[dict[str, Any]]:
        issues = [
            normalize_github_issue(payload)
            for payload in self.list_issue_payloads(
                state="closed",
                limit=200,
                fields="number,title,url,body,labels,createdAt,updatedAt,state",
            )
        ]
        return sorted(issues, key=issue_priority_sort_key)
