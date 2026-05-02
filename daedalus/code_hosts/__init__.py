"""Shared code-host integrations reused across workflows."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Protocol


class CodeHostConfigError(RuntimeError):
    """Raised when the code-host section is missing or invalid."""


class CodeHostClient(Protocol):
    kind: str

    def list_open_pull_requests(self, *, limit: int = 50, fields: str | None = None) -> list[dict[str, Any]]: ...

    def create_pull_request(self, *, head: str, title: str, body: str) -> str: ...

    def mark_pull_request_ready(self, pr_number: int | str | None) -> bool: ...

    def merge_pull_request(self, pr_number: int | str | None, *, squash: bool = True, delete_branch: bool = True) -> dict[str, Any]: ...

    def resolve_review_thread(self, thread_id: str) -> bool: ...

    def fetch_issue_reactions(self, issue_number: int | str | None) -> list[dict[str, Any]]: ...

    def fetch_pull_request_review_threads(self, pr_number: int | str | None) -> dict[str, Any]: ...


_CODE_HOST_KINDS: dict[str, type] = {}


def register(kind: str):
    def _register(cls):
        _CODE_HOST_KINDS[kind] = cls
        return cls

    return _register


def _ensure_builtin_code_host_kinds() -> None:
    from .github import GithubCodeHostClient

    _CODE_HOST_KINDS.setdefault("github", GithubCodeHostClient)


def code_host_kind(code_host_cfg: dict[str, Any]) -> str:
    kind = str(code_host_cfg.get("kind") or "").strip()
    if not kind:
        raise CodeHostConfigError("code-host.kind is required")
    return kind


def build_code_host_client(
    *,
    workflow_root: Path,
    code_host_cfg: dict[str, Any],
    repo_path: Path | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    run_json: Callable[..., Any] | None = None,
) -> CodeHostClient:
    del workflow_root
    kind = code_host_kind(code_host_cfg)
    _ensure_builtin_code_host_kinds()
    if kind not in _CODE_HOST_KINDS:
        raise CodeHostConfigError(
            f"unsupported code-host.kind={kind!r}; supported kinds: {sorted(_CODE_HOST_KINDS)}"
        )
    cls = _CODE_HOST_KINDS[kind]
    if kind == "github":
        return cls(code_host_cfg=code_host_cfg, repo_path=repo_path, run=run, run_json=run_json)
    return cls(code_host_cfg=code_host_cfg, repo_path=repo_path, run=run, run_json=run_json)
