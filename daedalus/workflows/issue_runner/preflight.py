from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workflows.issue_runner.tracker import TrackerConfigError, build_tracker_client, resolve_tracker_path
from trackers.github import (
    github_auth_host_from_slug,
    github_auth_success_accounts,
    github_name_with_owner_from_slug,
    github_slug_from_config,
    validate_github_tracker_config,
)


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    error_code: str | None = None
    error_detail: str | None = None


def run_preflight(config: dict[str, Any], *, workflow_root: Path | None = None) -> PreflightResult:
    try:
        _validate_config(config, workflow_root=workflow_root or Path("."))
    except RuntimeError as exc:
        return PreflightResult(ok=False, error_code="invalid-config", error_detail=str(exc))
    return PreflightResult(ok=True)


def _validate_config(config: dict[str, Any], *, workflow_root: Path) -> None:
    daedalus_cfg = config.get("daedalus") or {}
    runtimes = config.get("runtimes") or (daedalus_cfg.get("runtimes") if isinstance(daedalus_cfg, dict) else {}) or {}
    agent = config.get("agent") or {}
    codex_cfg = config.get("codex") or {}
    runtime_name = str(agent.get("runtime") or "").strip()
    if runtime_name:
        if runtime_name not in runtimes:
            raise RuntimeError(f"agent.runtime={runtime_name!r} does not reference a declared runtime profile")
        runtime_cfg = runtimes.get(runtime_name) or {}
        runtime_kind = str(runtime_cfg.get("kind") or "").strip()
        if runtime_kind == "hermes-agent":
            if not (agent.get("command") or runtime_cfg.get("command")):
                raise RuntimeError(
                    "hermes-agent runtime requires command on the runtime profile or agent block"
                )
        if runtime_kind == "codex-app-server":
            if not (runtime_cfg.get("command") or codex_cfg.get("command")):
                raise RuntimeError(
                    "codex-app-server runtime requires command on the runtime profile or codex block"
                )
    elif not (agent.get("command") or codex_cfg.get("command")):
        raise RuntimeError("issue-runner requires agent.runtime, agent.command, or codex.command")

    tracker_cfg = config.get("tracker") or {}
    repository_cfg = config.get("repository") or {}
    repo_raw = str(
        repository_cfg.get("local-path")
        or repository_cfg.get("local_path")
        or ""
    ).strip()
    repo_path = None
    if repo_raw:
        repo_path = Path(repo_raw).expanduser()
        if not repo_path.is_absolute():
            repo_path = (workflow_root / repo_path).resolve()
    try:
        tracker_kind = str(tracker_cfg.get("kind") or "").strip()
        tracker_client_cfg = dict(tracker_cfg)
        if tracker_kind == "github":
            if tracker_client_cfg.get("github-slug"):
                raise TrackerConfigError(
                    "issue-runner GitHub config uses tracker.github_slug; remove tracker.github-slug"
                )
            if repository_cfg.get("github_slug") or repository_cfg.get("github-slug"):
                raise TrackerConfigError(
                    "issue-runner GitHub config uses tracker.github_slug; remove repository.github-slug"
                )
            github_slug_from_config(tracker_client_cfg)
            validate_github_tracker_config(
                workflow_root=workflow_root,
                tracker_cfg=tracker_client_cfg,
                repository_cfg=repository_cfg,
                repo_path=repo_path,
            )
        if str(tracker_cfg.get("kind") or "").strip() == "local-json":
            path = resolve_tracker_path(workflow_root=workflow_root, tracker_cfg=tracker_cfg)
            if not path.exists():
                raise TrackerConfigError(f"tracker.path does not exist: {path}")
        client = build_tracker_client(
            workflow_root=workflow_root,
            tracker_cfg=tracker_client_cfg,
            repo_path=repo_path,
        )
        if tracker_kind == "github":
            expected_slug = github_slug_from_config(tracker_client_cfg)
            auth_host = github_auth_host_from_slug(expected_slug)
            auth_status = getattr(client, "auth_status_payload")(hostname=auth_host)
            _assert_github_auth_ok(auth_status, hostname=auth_host)
            repo_view = getattr(client, "repo_view_payload")()
            expected_name_with_owner = github_name_with_owner_from_slug(expected_slug)
            actual_slug = str(repo_view.get("nameWithOwner") or "").strip()
            if (
                expected_name_with_owner
                and actual_slug
                and actual_slug.lower() != expected_name_with_owner.lower()
            ):
                raise RuntimeError(
                    f"gh resolved repository {actual_slug!r}, expected {expected_name_with_owner!r}"
                )
    except TrackerConfigError as exc:
        raise RuntimeError(str(exc)) from exc


def _assert_github_auth_ok(payload: dict[str, Any], *, hostname: str | None) -> None:
    github_auth_success_accounts(payload, hostname=hostname)
