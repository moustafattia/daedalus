from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from trackers.github import github_auth_host_from_slug, github_name_with_owner_from_slug

from . import CodeHostConfigError, register


def _subprocess_run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )


def _subprocess_run_json(command: list[str], *, cwd: Path | None = None) -> Any:
    completed = _subprocess_run(command, cwd=cwd)
    return json.loads(completed.stdout or "null")


def github_slug_from_config(code_host_cfg: dict[str, Any]) -> str:
    slug = str(code_host_cfg.get("github_slug") or "").strip()
    if not slug:
        raise CodeHostConfigError("code-host.kind='github' requires code-host.github_slug")
    github_name_with_owner_from_slug(slug)
    return slug


def _coerce_number(value: int | str | None, *, field_name: str) -> str:
    if value in (None, ""):
        raise CodeHostConfigError(f"{field_name} is required")
    text = str(value).strip()
    if text.startswith("#"):
        text = text[1:].strip()
    if not text:
        raise CodeHostConfigError(f"{field_name} is required")
    return text


@register("github")
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
        self._repo_slug = github_slug_from_config(self._code_host_cfg)
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
            self._with_repo([
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                fields or "number,title,url,headRefName,headRefOid,isDraft,updatedAt",
            ]),
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

    def mark_pull_request_ready(self, pr_number: int | str | None) -> bool:
        if pr_number is None:
            return False
        try:
            self._run(
                self._with_repo(["gh", "pr", "ready", _coerce_number(pr_number, field_name="pr_number")]),
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
        command = ["gh", "pr", "merge", _coerce_number(pr_number, field_name="pr_number")]
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
                self._with_api_hostname([
                    "gh",
                    "api",
                    "graphql",
                    "-f",
                    "query=mutation($threadId:ID!){ resolveReviewThread(input:{threadId:$threadId}) { thread { id isResolved } } }",
                    "-f",
                    f"threadId={thread_id}",
                ]),
                cwd=self._repo_path,
            )
        except Exception:
            return False
        return bool((((result or {}).get("data") or {}).get("resolveReviewThread") or {}).get("thread", {}).get("isResolved"))

    def fetch_issue_reactions(self, issue_number: int | str | None) -> list[dict[str, Any]]:
        number = _coerce_number(issue_number, field_name="issue_number")
        payload = self._run_json(
            self._with_api_hostname([
                "gh",
                "api",
                f"repos/{self._name_with_owner}/issues/{number}/reactions",
                "-H",
                "Accept: application/vnd.github+json",
            ]),
            cwd=self._repo_path,
        )
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def fetch_pull_request_review_threads(self, pr_number: int | str | None) -> dict[str, Any]:
        number = int(_coerce_number(pr_number, field_name="pr_number"))
        owner, name = self._name_with_owner.split("/", 1)
        data = self._run_json(
            self._with_api_hostname([
                "gh",
                "api",
                "graphql",
                "-f",
                "query=query { repository(owner:\"%s\", name:\"%s\") { pullRequest(number: %d) { state headRefOid commits(last: 1) { nodes { commit { oid committedDate } } } reviewThreads(first: 100) { nodes { id isResolved isOutdated path line comments(first: 20) { nodes { author { login } body url createdAt } } } } } } }"
                % (owner, name, number),
            ]),
            cwd=self._repo_path,
        )
        return (((data or {}).get("data") or {}).get("repository") or {}).get("pullRequest") or {}
