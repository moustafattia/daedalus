from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import (
    CodeHostConfigError,
    TrackerConfigError,
    issue_priority_sort_key,
    normalize_issue,
    register,
    register_code_host,
)


_GITHUB_SLUG_RE = re.compile(
    r"^(?:(?P<host>[A-Za-z0-9.-]+(?::[0-9]+)?)/)?"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$"
)


def _github_slug_match(raw: str) -> re.Match[str] | None:
    return _GITHUB_SLUG_RE.match(raw)


def _github_slug_config_error() -> TrackerConfigError:
    return TrackerConfigError(
        "tracker.github_slug must be in owner/repo or host/owner/repo form for tracker.kind='github'"
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
            raise RuntimeError(
                f"gh auth status returned invalid {hostname} account information"
            )
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
    raise RuntimeError(
        "gh is not authenticated for any GitHub host; run `gh auth login`"
    )


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


def _subprocess_run(
    command: list[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )


def github_slug_from_config(
    tracker_cfg: dict[str, Any],
    repository_cfg: dict[str, Any] | None = None,
) -> str | None:
    raw = str(tracker_cfg.get("github_slug") or "").strip()
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
    if not slug:
        raise TrackerConfigError("tracker.kind='github' requires tracker.github_slug")
    resolved_repo_path = _resolve_repo_path(
        workflow_root=workflow_root,
        tracker_cfg=tracker_cfg,
        repo_path=repo_path,
        required=False,
    )
    if resolved_repo_path is not None and not resolved_repo_path.exists():
        raise TrackerConfigError(
            f"repository.local-path does not exist for tracker.kind='github': {resolved_repo_path}"
        )

    active_states = _configured_states(tracker_cfg, "active_states", "active-states")
    terminal_states = _configured_states(
        tracker_cfg, "terminal_states", "terminal-states"
    )
    if not active_states or set(active_states) != {"open"}:
        raise TrackerConfigError(
            "tracker.kind='github' requires tracker.active_states: [open]"
        )
    if not terminal_states or set(terminal_states) != {"closed"}:
        raise TrackerConfigError(
            "tracker.kind='github' requires tracker.terminal_states: [closed]"
        )

    for key in (
        "required_labels",
        "required-labels",
        "exclude_labels",
        "exclude-labels",
    ):
        value = tracker_cfg.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            raise TrackerConfigError(
                f"tracker.{key} must be a list for tracker.kind='github'"
            )
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
        tracker_cfg.get("repo_path") or tracker_cfg.get("repo-path") or ""
    ).strip()
    if not raw:
        if not required:
            return None
        raise TrackerConfigError(
            "tracker.kind='github' requires tracker.github_slug or repository.local-path"
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


def _coerce_number(value: int | str | None, *, field_name: str) -> str:
    if value in (None, ""):
        raise CodeHostConfigError(f"{field_name} is required")
    text = str(value).strip()
    if text.startswith("#"):
        text = text[1:].strip()
    if not text:
        raise CodeHostConfigError(f"{field_name} is required")
    return text


def code_host_github_slug_from_config(code_host_cfg: dict[str, Any]) -> str:
    slug = str(code_host_cfg.get("github_slug") or "").strip()
    if not slug:
        raise CodeHostConfigError(
            "code-host.kind='github' requires code-host.github_slug"
        )
    github_name_with_owner_from_slug(slug)
    return slug


@register("github")
class GithubTrackerClient:
    kind = "github"

    def __init__(
        self,
        *,
        workflow_root: Path,
        tracker_cfg: dict[str, Any],
        repo_path: Path | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
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
        self._run = run or _subprocess_run
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
        issues = [
            normalize_github_issue(payload)
            for payload in self.list_issue_payloads(
                state="open",
                limit=200,
                fields="number,title,url,body,labels,createdAt,updatedAt,state",
            )
        ]
        return sorted(issues, key=issue_priority_sort_key)

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

    def add_labels(self, issue_id: str | int | None, labels: list[str]) -> bool:
        issue_number = _coerce_issue_number(issue_id)
        normalized = [str(label).strip() for label in labels if str(label).strip()]
        if issue_number is None or not normalized:
            return False
        self._run(
            self._with_repo(
                [
                    "gh",
                    "issue",
                    "edit",
                    issue_number,
                    "--add-label",
                    ",".join(normalized),
                ]
            ),
            cwd=self._repo_path,
        )
        return True

    def remove_labels(self, issue_id: str | int | None, labels: list[str]) -> bool:
        issue_number = _coerce_issue_number(issue_id)
        normalized = [str(label).strip() for label in labels if str(label).strip()]
        if issue_number is None or not normalized:
            return False
        self._run(
            self._with_repo(
                [
                    "gh",
                    "issue",
                    "edit",
                    issue_number,
                    "--remove-label",
                    ",".join(normalized),
                ]
            ),
            cwd=self._repo_path,
        )
        return True


@register_code_host("github")
class GithubCodeHostClient:
    kind = "github"

    def __init__(
        self,
        *,
        code_host_cfg: dict[str, Any],
        repo_path: Path | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        run_json: Callable[..., Any] | None = None,
    ):
        self._code_host_cfg = dict(code_host_cfg or {})
        self._repo_path = repo_path
        self._repo_slug = code_host_github_slug_from_config(self._code_host_cfg)
        self._name_with_owner = github_name_with_owner_from_slug(self._repo_slug)
        self._auth_host = github_auth_host_from_slug(self._repo_slug)
        if self._name_with_owner is None:
            raise CodeHostConfigError("code-host.github_slug is required")
        self._run = run or _subprocess_run
        self._run_json = run_json or _subprocess_run_json

    @property
    def repo_path(self) -> Path | None:
        return self._repo_path

    @property
    def repo_slug(self) -> str:
        return self._repo_slug

    @property
    def name_with_owner(self) -> str:
        return self._name_with_owner

    def _with_repo(self, command: list[str]) -> list[str]:
        return [*command, "--repo", self._repo_slug]

    def _with_api_hostname(self, command: list[str]) -> list[str]:
        if self._repo_slug.count("/") < 2:
            return command
        return [*command, "--hostname", str(self._auth_host)]

    def list_open_pull_requests(
        self,
        *,
        limit: int = 50,
        fields: str | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._run_json(
            self._with_repo(
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "open",
                    "--limit",
                    str(limit),
                    "--json",
                    fields
                    or "number,title,url,headRefName,headRefOid,isDraft,updatedAt",
                ]
            ),
            cwd=self._repo_path,
        )
        if not isinstance(payload, list):
            raise RuntimeError("expected gh pr list JSON array payload")
        return [item for item in payload if isinstance(item, dict)]

    def create_pull_request(self, *, head: str, title: str, body: str) -> str:
        completed = self._run(
            self._with_repo(
                [
                    "gh",
                    "pr",
                    "create",
                    "--head",
                    head,
                    "--title",
                    title,
                    "--body",
                    body,
                ]
            ),
            cwd=self._repo_path,
        )
        return (getattr(completed, "stdout", "") or "").strip()

    def comment_on_pull_request(
        self, pr_number: int | str | None, *, body: str
    ) -> dict[str, Any]:
        number = _coerce_number(pr_number, field_name="pr_number")
        completed = self._run(
            self._with_repo(["gh", "pr", "comment", number, "--body", body]),
            cwd=self._repo_path,
        )
        return {
            "ok": True,
            "kind": "pull_request",
            "pr_number": number,
            "stdout": (getattr(completed, "stdout", "") or "").strip(),
        }

    def request_changes_on_pull_request(
        self, pr_number: int | str | None, *, body: str
    ) -> dict[str, Any]:
        number = _coerce_number(pr_number, field_name="pr_number")
        completed = self._run(
            self._with_repo(
                ["gh", "pr", "review", number, "--request-changes", "--body", body]
            ),
            cwd=self._repo_path,
        )
        return {
            "ok": True,
            "kind": "pull_request_review",
            "pr_number": number,
            "state": "changes_requested",
            "stdout": (getattr(completed, "stdout", "") or "").strip(),
        }

    def comment_on_issue(
        self, issue_number: int | str | None, *, body: str
    ) -> dict[str, Any]:
        number = _coerce_number(issue_number, field_name="issue_number")
        completed = self._run(
            self._with_repo(["gh", "issue", "comment", number, "--body", body]),
            cwd=self._repo_path,
        )
        return {
            "ok": True,
            "kind": "issue",
            "issue_number": number,
            "stdout": (getattr(completed, "stdout", "") or "").strip(),
        }

    def mark_pull_request_ready(self, pr_number: int | str | None) -> bool:
        if pr_number is None:
            return False
        try:
            self._run(
                self._with_repo(
                    [
                        "gh",
                        "pr",
                        "ready",
                        _coerce_number(pr_number, field_name="pr_number"),
                    ]
                ),
                cwd=self._repo_path,
            )
        except Exception:
            return False
        return True

    def merge_pull_request(
        self,
        pr_number: int | str | None,
        *,
        squash: bool = True,
        delete_branch: bool = True,
    ) -> dict[str, Any]:
        command = [
            "gh",
            "pr",
            "merge",
            _coerce_number(pr_number, field_name="pr_number"),
        ]
        if squash:
            command.append("--squash")
        if delete_branch:
            command.append("--delete-branch")
        completed = self._run(self._with_repo(command), cwd=self._repo_path)
        return {
            "ok": True,
            "pr_number": pr_number,
            "stdout": (getattr(completed, "stdout", "") or "").strip(),
        }

    def resolve_review_thread(self, thread_id: str) -> bool:
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return False
        try:
            result = self._run_json(
                self._with_api_hostname(
                    [
                        "gh",
                        "api",
                        "graphql",
                        "-f",
                        "query=mutation($threadId:ID!){ resolveReviewThread(input:{threadId:$threadId}) { thread { id isResolved } } }",
                        "-f",
                        f"threadId={thread_id}",
                    ]
                ),
                cwd=self._repo_path,
            )
        except Exception:
            return False
        return bool(
            (((result or {}).get("data") or {}).get("resolveReviewThread") or {})
            .get("thread", {})
            .get("isResolved")
        )

    def fetch_issue_reactions(
        self, issue_number: int | str | None
    ) -> list[dict[str, Any]]:
        number = _coerce_number(issue_number, field_name="issue_number")
        payload = self._run_json(
            self._with_api_hostname(
                [
                    "gh",
                    "api",
                    f"repos/{self._name_with_owner}/issues/{number}/reactions",
                    "-H",
                    "Accept: application/vnd.github+json",
                ]
            ),
            cwd=self._repo_path,
        )
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def fetch_pull_request_review_threads(
        self, pr_number: int | str | None
    ) -> dict[str, Any]:
        number = int(_coerce_number(pr_number, field_name="pr_number"))
        owner, name = self._name_with_owner.split("/", 1)
        data = self._run_json(
            self._with_api_hostname(
                [
                    "gh",
                    "api",
                    "graphql",
                    "-f",
                    'query=query { repository(owner:"%s", name:"%s") { pullRequest(number: %d) { state headRefOid reviewThreads(first: 100) { nodes { id isResolved isOutdated path line comments(first: 20) { nodes { author { login } body url createdAt } } } } } } }'
                    % (owner, name, number),
                ]
            ),
            cwd=self._repo_path,
        )
        return (((data or {}).get("data") or {}).get("repository") or {}).get(
            "pullRequest"
        ) or {}
