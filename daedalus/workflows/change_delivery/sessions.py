from __future__ import annotations

import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from workflows.change_delivery.migrations import get_lane_state_review_field, get_review


"""Workflow session and worktree helpers.

This slice extracts the session-action recommendation logic from the legacy
wrapper so the adapter can own a meaningful part of the read model.
"""


ISSUE_PREFIX_RE = re.compile(r"^\[[^\]]+\]\s*")
ISSUE_BRANCH_RE = re.compile(r"issue-(\d+)")
ISSUE_WORKTREE_RE = re.compile(r"issue-(\d+)")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
MULTI_DASH_RE = re.compile(r"-+")


def _iso_to_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        return None


def slugify_issue_title(title: str | None) -> str:
    text = ISSUE_PREFIX_RE.sub("", title or "").strip().lower()
    text = NON_ALNUM_RE.sub("-", text)
    text = MULTI_DASH_RE.sub("-", text).strip("-")
    return text[:48] or "lane"



def expected_lane_worktree(issue_number: int | None) -> Path | None:
    if issue_number is None:
        return None
    return Path(f"/tmp/issue-{issue_number}")



def expected_lane_branch(issue: dict[str, Any] | None) -> str | None:
    if not issue:
        return None
    number = issue.get("number")
    if number is None:
        return None
    return f"codex/issue-{number}-{slugify_issue_title(issue.get('title'))}"



def lane_acpx_session_name(issue_number: int | None) -> str | None:
    if issue_number is None:
        return None
    return f"lane-{issue_number}"



def issue_number_from_branch(branch: str | None) -> int | None:
    if not branch:
        return None
    match = ISSUE_BRANCH_RE.search(branch)
    return int(match.group(1)) if match else None



def issue_number_from_worktree(worktree: str | Path | None) -> int | None:
    if not worktree:
        return None
    match = ISSUE_WORKTREE_RE.search(str(worktree))
    return int(match.group(1)) if match else None



def implementation_lane_matches(implementation: dict[str, Any] | None, lane_number: int | None) -> bool:
    if lane_number is None:
        return False
    impl = implementation or {}
    candidates = {
        issue_number_from_branch(impl.get("branch")),
        issue_number_from_worktree(impl.get("worktree")),
        (((impl.get("laneState") or {}).get("issue") or {}).get("number")),
    }
    return lane_number in {int(candidate) for candidate in candidates if candidate is not None}



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



def should_escalate_codex_model(
    *,
    lane_state: dict[str, Any] | None = None,
    workflow_state: str | None = None,
    reviews: dict[str, Any] | None = None,
    escalate_restart_count: int = 3,
    escalate_local_review_count: int = 2,
    escalate_postpublish_finding_count: int = 5,
) -> bool:
    lane_state = lane_state or {}
    review_state = lane_state.get("review") or {}
    restart_state = lane_state.get("restart") or {}
    restart_count = int(restart_state.get("count") or 0)
    local_review_count = int(get_lane_state_review_field(review_state, "localInternalReviewCount") or 0)
    external_review = get_review(reviews, "externalReview")
    codex_open_findings = int(external_review.get("openFindingCount") or 0)
    if restart_count >= escalate_restart_count:
        return True
    if local_review_count >= escalate_local_review_count:
        return True
    if workflow_state in {"findings_open", "rework_required"} and codex_open_findings >= escalate_postpublish_finding_count:
        return True
    return False



def codex_model_for_issue(
    issue: dict[str, Any] | None,
    *,
    lane_state: dict[str, Any] | None = None,
    workflow_state: str | None = None,
    reviews: dict[str, Any] | None = None,
    default_model: str,
    high_effort_model: str,
    escalated_model: str,
    escalate_restart_count: int = 3,
    escalate_local_review_count: int = 2,
    escalate_postpublish_finding_count: int = 5,
) -> str:
    if should_escalate_codex_model(
        lane_state=lane_state,
        workflow_state=workflow_state,
        reviews=reviews,
        escalate_restart_count=escalate_restart_count,
        escalate_local_review_count=escalate_local_review_count,
        escalate_postpublish_finding_count=escalate_postpublish_finding_count,
    ):
        return escalated_model
    labels = issue_label_names(issue)
    if "effort:large" in labels or "effort:high" in labels:
        return high_effort_model
    return default_model



def coder_agent_name_for_model(model: str | None, *, escalated_model: str, internal_coder_agent_name: str, escalation_coder_agent_name: str) -> str:
    if model == escalated_model:
        return escalation_coder_agent_name
    return internal_coder_agent_name



def actor_labels_payload(
    *,
    current_coder_model: str | None,
    default_model: str,
    escalated_model: str,
    internal_coder_agent_name: str,
    escalation_coder_agent_name: str,
    internal_reviewer_agent_name: str,
    internal_reviewer_model: str,
    external_reviewer_agent_name: str,
    advisory_reviewer_agent_name: str,
) -> dict[str, Any]:
    return {
        "internalCoderAgent": {"name": internal_coder_agent_name, "model": default_model},
        "escalationCoderAgent": {"name": escalation_coder_agent_name, "model": escalated_model},
        "internalReviewerAgent": {"name": internal_reviewer_agent_name, "model": internal_reviewer_model},
        "externalReviewerAgent": {"name": external_reviewer_agent_name, "model": None},
        "advisoryReviewerAgent": {"name": advisory_reviewer_agent_name, "model": None},
        "currentCoderAgent": {
            "name": coder_agent_name_for_model(
                current_coder_model,
                escalated_model=escalated_model,
                internal_coder_agent_name=internal_coder_agent_name,
                escalation_coder_agent_name=escalation_coder_agent_name,
            ),
            "model": current_coder_model,
        },
    }



def normalize_acpx_session_meta(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "name": payload.get("name"),
        "closed": bool(payload.get("closed")),
        "cwd": payload.get("cwd"),
        "last_used_at": payload.get("lastUsedAt") or payload.get("last_used_at"),
        "session_id": payload.get("acpSessionId") or payload.get("acpxSessionId"),
        "record_id": payload.get("acpxRecordId") or payload.get("acpx_record_id"),
    }



def show_acpx_session(
    *,
    worktree: Path | None,
    session_name: str | None,
    run_json: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    if worktree is None or not session_name:
        return None
    try:
        payload = run_json([
            "acpx",
            "--format",
            "json",
            "--json-strict",
            "--cwd",
            str(worktree),
            "codex",
            "sessions",
            "show",
            session_name,
        ])
    except Exception:
        return None
    return normalize_acpx_session_meta(payload)



def close_acpx_session(
    *,
    worktree: Path | None,
    session_name: str | None,
    run: Callable[..., Any],
) -> bool:
    if worktree is None or not session_name:
        return False
    try:
        run([
            "acpx",
            "--cwd",
            str(worktree),
            "codex",
            "sessions",
            "close",
            session_name,
        ])
        return True
    except Exception:
        return False



def ensure_acpx_session(
    *,
    worktree: Path,
    session_name: str,
    codex_model: str,
    run_json: Callable[..., dict[str, Any]],
    resume_session_id: str | None = None,
) -> dict[str, Any]:
    command = [
        "acpx",
        "--model",
        codex_model,
        "--format",
        "json",
        "--json-strict",
        "--cwd",
        str(worktree),
        "codex",
        "sessions",
        "ensure",
        "--name",
        session_name,
    ]
    if resume_session_id:
        command.extend(["--resume-session", resume_session_id])
    try:
        return run_json(command)
    except subprocess.CalledProcessError as exc:
        output = ((exc.stdout or "") + "\n" + (exc.stderr or "")).strip()
        if resume_session_id and "Resource not found" in output:
            return run_json(command[:-2])
        raise



def run_acpx_prompt(
    *,
    worktree: Path,
    session_name: str,
    prompt: str,
    codex_model: str,
    run: Callable[..., Any],
) -> str:
    completed = run([
        "acpx",
        "--model",
        codex_model,
        "--approve-all",
        "--format",
        "quiet",
        "--cwd",
        str(worktree),
        "codex",
        "prompt",
        "-s",
        session_name,
        prompt,
    ])
    return completed.stdout.strip()



def snapshot_lane_artifacts(worktree: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for artifact in (".lane-state.json", ".lane-memo.md"):
        path = worktree / artifact
        if path.exists() and path.is_file():
            artifacts[artifact] = path.read_text(encoding="utf-8")
    return artifacts



def is_git_repo(path: Path | None, *, run: Callable[..., Any]) -> bool:
    if path is None or not path.exists():
        return False
    try:
        run(["git", "rev-parse", "--git-dir"], cwd=path)
        return True
    except Exception:
        return False



def restore_lane_artifacts(worktree: Path, artifacts: dict[str, str], *, write_text: Callable[[Path, str], Any]) -> None:
    for name, content in artifacts.items():
        write_text(worktree / name, content)



def prepare_lane_worktree(
    *,
    worktree: Path,
    branch: str,
    open_pr: dict[str, Any] | None,
    repo_path: Path,
    run: Callable[..., Any],
    is_git_repo: Callable[[Path], bool],
    snapshot_lane_artifacts_fn: Callable[[Path], dict[str, str]] = snapshot_lane_artifacts,
    restore_lane_artifacts_fn: Callable[[Path, dict[str, str]], Any] = lambda _path, _artifacts: None,
    rmtree: Callable[[Path], Any] = shutil.rmtree,
) -> dict[str, Any]:
    artifacts = snapshot_lane_artifacts_fn(worktree) if worktree.exists() else {}
    source_ref = f"origin/{branch}" if open_pr else "origin/main"
    fetch_args = ["git", "fetch", "origin", "main"]
    if open_pr:
        fetch_args.append(branch)
    run(fetch_args, cwd=repo_path)
    if is_git_repo(worktree):
        run(["git", "checkout", branch], cwd=worktree)
        restore_lane_artifacts_fn(worktree, artifacts)
        return {"created": False, "sourceRef": source_ref, "branch": branch, "path": str(worktree)}
    if worktree.exists():
        rmtree(worktree)
    run(["git", "worktree", "add", "-B", branch, str(worktree), source_ref], cwd=repo_path)
    restore_lane_artifacts_fn(worktree, artifacts)
    return {"created": True, "sourceRef": source_ref, "branch": branch, "path": str(worktree)}



def latest_lane_progress_epoch(implementation: dict[str, Any] | None, lane_state: dict[str, Any] | None) -> int | None:
    impl = implementation or {}
    state = lane_state or {}
    state_impl = state.get("implementation") or {}
    active_health = impl.get("activeSessionHealth") or {}
    candidates = [
        state_impl.get("lastMeaningfulProgressAt"),
        state_impl.get("activeSessionLastUsedAt"),
        active_health.get("lastUsedAt"),
        impl.get("updatedAt"),
    ]
    epochs = [epoch for epoch in (_iso_to_epoch(value) for value in candidates if value) if epoch is not None]
    return max(epochs) if epochs else None



def assess_codex_session_health(
    session_meta: dict[str, Any] | None,
    worktree: Path | None,
    *,
    now_epoch: int | None = None,
    freshness_seconds: int = 900,
    poke_grace_seconds: int = 1800,
) -> dict[str, Any]:
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    if not session_meta:
        return {"healthy": False, "reason": "missing-session-meta", "sessionName": None, "lastUsedAt": None, "canPoke": False}
    session_name = session_meta.get("name")
    if session_meta.get("closed"):
        return {"healthy": False, "reason": "closed-session", "sessionName": session_name, "lastUsedAt": session_meta.get("last_used_at"), "canPoke": False}
    session_cwd = session_meta.get("cwd")
    if worktree is not None and session_cwd and Path(session_cwd) != worktree:
        return {"healthy": False, "reason": "wrong-worktree", "sessionName": session_name, "lastUsedAt": session_meta.get("last_used_at"), "canPoke": False}
    last_used = session_meta.get("last_used_at")
    last_used_epoch = _iso_to_epoch(last_used)
    if last_used_epoch is None:
        return {"healthy": False, "reason": "missing-last-used", "sessionName": session_name, "lastUsedAt": last_used, "canPoke": False}
    freshness = now_epoch - last_used_epoch
    if freshness <= freshness_seconds:
        return {"healthy": True, "reason": None, "sessionName": session_name, "lastUsedAt": last_used, "freshnessSeconds": freshness, "canPoke": False}
    if freshness <= poke_grace_seconds:
        return {"healthy": False, "reason": "stale-open-session", "sessionName": session_name, "lastUsedAt": last_used, "freshnessSeconds": freshness, "canPoke": True}
    return {"healthy": False, "reason": "stale-session", "sessionName": session_name, "lastUsedAt": last_used, "freshnessSeconds": freshness, "canPoke": False}



def build_acp_session_strategy(
    *,
    implementation_session_key: str | None,
    session_action: dict[str, Any] | None,
    lane_state: dict[str, Any] | None,
    session_runtime: str | None = None,
    session_name: str | None = None,
    resume_session_id: str | None = None,
) -> dict[str, Any]:
    session_control = (lane_state or {}).get("sessionControl") or {}
    if session_runtime == "acpx-codex":
        return {
            "runtime": "acpx-codex",
            "spawnMode": "session",
            "nudgeTool": "acpx codex prompt -s",
            "targetSessionKey": session_name or session_control.get("targetSessionKey"),
            "resumeSessionId": resume_session_id or session_control.get("resumeSessionId"),
            "preferredAction": (session_action or {}).get("action"),
        }
    if session_runtime == "codex-app-server":
        return {
            "runtime": "codex-app-server",
            "spawnMode": "thread",
            "nudgeTool": "codex app-server turn/start",
            "targetSessionKey": session_name or session_control.get("targetSessionKey"),
            "resumeSessionId": resume_session_id or session_control.get("resumeSessionId"),
            "preferredAction": (session_action or {}).get("action"),
        }
    return {
        "runtime": "acp",
        "spawnMode": "session",
        "nudgeTool": "sessions_send",
        "targetSessionKey": implementation_session_key or session_control.get("targetSessionKey"),
        "resumeSessionId": resume_session_id or session_control.get("resumeSessionId"),
        "preferredAction": (session_action or {}).get("action"),
    }



def build_session_nudge_payload(
    *,
    session_action: dict[str, Any],
    issue: dict[str, Any] | None,
    open_pr: dict[str, Any] | None,
    lane_memo_path: str | None,
    now_iso: str,
) -> dict[str, Any]:
    return {
        "action": session_action.get("action"),
        "reason": session_action.get("reason"),
        "sessionName": session_action.get("sessionName"),
        "issueNumber": (issue or {}).get("number"),
        "issueTitle": (issue or {}).get("title"),
        "prNumber": (open_pr or {}).get("number"),
        "prUrl": (open_pr or {}).get("url"),
        "headSha": (open_pr or {}).get("headRefOid"),
        "laneMemoPath": lane_memo_path,
        "at": now_iso,
    }



def record_session_nudge(
    *,
    worktree: Path | None,
    payload: dict[str, Any],
    lane_state_path_fn: Callable[[Path | None], Path | None],
    load_optional_json_fn: Callable[[Path | None], dict[str, Any] | None],
    write_json_fn: Callable[[Path, dict[str, Any]], Any],
) -> dict[str, Any] | None:
    path = lane_state_path_fn(worktree)
    if path is None:
        return None
    state = load_optional_json_fn(path) or {"schemaVersion": 1}
    state.setdefault("sessionControl", {})["lastNudge"] = payload
    write_json_fn(path, state)
    return state



def should_nudge_session(
    *,
    lane_state: dict[str, Any] | None,
    session_action: dict[str, Any],
    current_head_sha: str | None,
    now_epoch: int | None = None,
    cooldown_seconds: int = 600,
) -> dict[str, Any]:
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    if session_action.get("action") != "poke-session":
        return {"shouldNudge": False, "reason": "not-poke-session"}
    last_nudge = ((lane_state or {}).get("sessionControl") or {}).get("lastNudge") or {}
    last_at = _iso_to_epoch(last_nudge.get("at"))
    if (
        last_nudge.get("sessionName") == session_action.get("sessionName")
        and last_nudge.get("headSha") == current_head_sha
        and last_at is not None
        and (now_epoch - last_at) < cooldown_seconds
    ):
        return {"shouldNudge": False, "reason": "recent-nudge-same-head"}
    return {"shouldNudge": True, "reason": None}



def decide_session_action(
    *,
    active_session_health: dict[str, Any] | None,
    implementation_status: str | None,
    has_open_pr: bool,
) -> dict[str, Any]:
    health = active_session_health or {}
    if health.get("healthy"):
        return {"action": "continue-session", "reason": None, "sessionName": health.get("sessionName")}
    if health.get("canPoke") and health.get("reason") == "stale-open-session":
        return {"action": "poke-session", "reason": health.get("reason"), "sessionName": health.get("sessionName")}
    if implementation_status in {"implementing", "implementing_local", "revalidating", "findings_open", "rework_required", "self_checked", "awaiting_pre_publish_review", "pre_publish_review_findings", "ready_to_publish"} or has_open_pr:
        return {"action": "restart-session", "reason": health.get("reason") or "missing-session", "sessionName": health.get("sessionName")}
    return {"action": "no-action", "reason": "lane-not-active", "sessionName": health.get("sessionName")}


def ensure_session_via_runtime(
    *,
    workspace,
    runtime_name: str,
    worktree,
    session_name: str,
    model: str,
    resume_session_id: str | None = None,
):
    """Runtime-aware version of ensure_acpx_session.

    Resolves the runtime via ``workspace.runtime(runtime_name)`` and calls
    its ``ensure_session`` method. New callers should use this form; the
    free ``ensure_acpx_session`` remains for callers that haven't been
    rewired yet.
    """
    runtime = workspace.runtime(runtime_name)
    return runtime.ensure_session(
        worktree=worktree,
        session_name=session_name,
        model=model,
        resume_session_id=resume_session_id,
    )


def run_prompt_via_runtime(
    *,
    workspace,
    runtime_name: str,
    worktree,
    session_name: str,
    prompt: str,
    model: str,
    cancel_event: Any | None = None,
    progress_callback: Callable[[Any], None] | None = None,
) -> Any:
    """Runtime-aware version of run_acpx_prompt / the inline claude invocation."""
    runtime = workspace.runtime(runtime_name)
    set_cancel_event = getattr(runtime, "set_cancel_event", None)
    set_progress_callback = getattr(runtime, "set_progress_callback", None)
    if callable(set_cancel_event):
        set_cancel_event(cancel_event)
    if callable(set_progress_callback):
        set_progress_callback(progress_callback)
    runner = getattr(runtime, "run_prompt_result", None)
    try:
        if callable(runner):
            return runner(
                worktree=worktree,
                session_name=session_name,
                prompt=prompt,
                model=model,
            )
        return runtime.run_prompt(
            worktree=worktree,
            session_name=session_name,
            prompt=prompt,
            model=model,
        )
    finally:
        if callable(set_progress_callback):
            set_progress_callback(None)
        if callable(set_cancel_event):
            set_cancel_event(None)


def close_session_via_runtime(*, workspace, runtime_name: str, worktree, session_name: str) -> None:
    """Runtime-aware version of close_acpx_session."""
    return workspace.runtime(runtime_name).close_session(
        worktree=worktree,
        session_name=session_name,
    )
