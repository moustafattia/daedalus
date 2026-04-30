"""GitHub bot-comment publisher for the change-delivery workflow.

This is the only module that actually shells out to ``gh``. It composes:

  - ``observability.event_is_included`` (gate)
  - ``comments.render_row`` / ``render_comment`` (markdown body)
  - ``comments.load_state`` / ``save_state`` (per-issue persistence)
  - ``gh issue comment`` (create) / ``gh api PATCH`` (edit-in-place)

Failures NEVER raise. The workflow tick must continue even if observability
is broken — this is read-the-tea-leaves scaffolding, not a correctness layer.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

# Sibling-import boilerplate: dual-import (package vs script).
try:
    from . import comments as _comments_module
    from . import observability as _observability_module
except ImportError:
    _here = Path(__file__).resolve().parent

    def _load(name: str):
        spec = importlib.util.spec_from_file_location(
            f"daedalus_workflow_change_delivery_{name}", _here / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    _comments_module = _load("comments")
    _observability_module = _load("observability")


_COMMENT_URL_RE = re.compile(r"#issuecomment-(\d+)")


def _parse_comment_id_from_gh_output(stdout: str) -> str | None:
    """``gh issue comment`` prints the URL of the created comment on success."""
    if not stdout:
        return None
    m = _COMMENT_URL_RE.search(stdout)
    return m.group(1) if m else None


def publish_event(
    *,
    repo_slug: str,
    issue_number: int,
    workflow_state: str,
    is_operator_attention: bool,
    audit_event: Mapping[str, Any],
    effective_config: Mapping[str, Any],
    state_dir: Path,
    run_fn: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Publish (or skip) a comment update for one audit event.

    Returns a result dict. Never raises.
    """
    gh_cfg = (effective_config or {}).get("github-comments") or {}
    if not gh_cfg.get("enabled"):
        return {"published": False, "reason": "github-comments-disabled"}

    action = audit_event.get("action") or ""
    if not _observability_module.event_is_included(effective_config, action):
        return {"published": False, "reason": "event-not-in-include-events"}

    state = _comments_module.load_state(state_dir, issue_number)
    new_row = _comments_module.render_row(audit_event)
    existing_rows = state.get("rows") or []

    # Row-based dedupe: re-firing an event that produces the same row as
    # the current top row is a no-op tick — skip the API call. This is
    # more reliable than rendered-text dedupe because the rendered body
    # includes a `Last update: now()` timestamp that always changes.
    if existing_rows and new_row == existing_rows[0]:
        return {"published": False, "reason": "rendered-unchanged"}

    new_rows = _comments_module.append_row(existing_rows, new_row)
    rendered = _comments_module.render_comment(
        issue_number=issue_number,
        workflow_state=workflow_state,
        rows=new_rows,
        is_operator_attention=is_operator_attention,
    )

    comment_id = state.get("comment_id")

    try:
        if comment_id is None:
            # Create the bot-comment.
            argv = [
                "gh", "issue", "comment", str(issue_number),
                "--repo", repo_slug,
                "--body", rendered,
            ]
            result = run_fn(argv, check=True, capture_output=True, text=True)
            stdout = getattr(result, "stdout", "") or ""
            new_comment_id = _parse_comment_id_from_gh_output(stdout)
            if new_comment_id is None:
                return {"published": False, "error": f"could-not-parse-comment-id-from: {stdout!r}"}
            comment_id = new_comment_id
        else:
            # Edit-in-place via the comments API.
            api_path = f"/repos/{repo_slug}/issues/comments/{comment_id}"
            argv = [
                "gh", "api", "-X", "PATCH", api_path,
                "-f", f"body={rendered}",
            ]
            run_fn(argv, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (getattr(exc, "stderr", "") or "").strip()
        stdout = (getattr(exc, "stdout", "") or "").strip()
        return {
            "published": False,
            "error": stderr or stdout or str(exc),
        }
    except (FileNotFoundError, OSError) as exc:
        return {"published": False, "error": f"gh-cli-unavailable: {exc}"}

    new_state = {
        "comment_id": comment_id,
        "last_rendered_text": rendered,
        "rows": new_rows,
        "last_action": action,
    }
    try:
        _comments_module.save_state(state_dir, issue_number, new_state)
    except OSError as exc:
        return {"published": True, "comment_id": comment_id, "warning": f"state-save-failed: {exc}"}

    return {"published": True, "comment_id": comment_id}
