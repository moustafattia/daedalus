"""Shared tracker feedback helpers.

Workflows emit stage updates through this module; tracker adapters decide how
those updates become comments, state transitions, labels, or no-ops.
"""
from __future__ import annotations

from typing import Any


DEFAULT_INCLUDE = (
    "issue.selected",
    "issue.dispatched",
    "issue.running",
    "issue.completed",
    "issue.failed",
    "issue.canceled",
    "issue.retry_scheduled",
)


def feedback_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("tracker-feedback") or config.get("tracker_feedback") or {}
    return raw if isinstance(raw, dict) else {}


def feedback_enabled(config: dict[str, Any]) -> bool:
    cfg = feedback_config(config)
    return bool(cfg.get("enabled", False))


def comment_mode(config: dict[str, Any]) -> str:
    cfg = feedback_config(config)
    mode = str(cfg.get("comment-mode") or cfg.get("comment_mode") or "append").strip().lower()
    if mode in {"append", "upsert"}:
        return mode
    return "append"


def event_included(config: dict[str, Any], event: str) -> bool:
    cfg = feedback_config(config)
    include = cfg.get("include")
    if include is None:
        return event in DEFAULT_INCLUDE
    if not isinstance(include, list):
        return False
    return event in {str(item).strip() for item in include if str(item).strip()}


def state_for_event(config: dict[str, Any], event: str) -> str | None:
    cfg = feedback_config(config)
    state_cfg = cfg.get("state-updates") or cfg.get("state_updates") or {}
    if not isinstance(state_cfg, dict) or not state_cfg.get("enabled", False):
        return None
    event_key = str(event).strip()
    short_key = event_key.split(".")[-1].replace("_", "-")
    for key in (
        f"on-{event_key}",
        f"on-{event_key.replace('.', '-')}",
        f"on-{short_key}",
        event_key,
    ):
        value = state_cfg.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def format_feedback_body(*, event: str, summary: str, metadata: dict[str, Any] | None = None) -> str:
    lines = [
        f"### Daedalus update: {event}",
        "",
        summary.strip() or "Daedalus recorded a workflow update.",
    ]
    metadata = metadata or {}
    visible_metadata = {
        key: value
        for key, value in metadata.items()
        if value not in (None, "", [], {})
    }
    if visible_metadata:
        lines.append("")
        for key, value in sorted(visible_metadata.items()):
            lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines).strip() + "\n"


def publish_tracker_feedback(
    *,
    tracker_client: Any,
    workflow_config: dict[str, Any],
    issue: dict[str, Any],
    event: str,
    summary: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not feedback_enabled(workflow_config):
        return {"ok": True, "skipped": True, "reason": "disabled", "event": event}
    if not event_included(workflow_config, event):
        return {"ok": True, "skipped": True, "reason": "not-included", "event": event}
    issue_id = str(issue.get("id") or "").strip()
    if not issue_id:
        return {"ok": False, "skipped": True, "reason": "missing-issue-id", "event": event}
    publisher = getattr(tracker_client, "post_feedback", None)
    if not callable(publisher):
        return {"ok": True, "skipped": True, "reason": "tracker-does-not-support-feedback", "event": event}
    target_state = state_for_event(workflow_config, event)
    body = format_feedback_body(event=event, summary=summary, metadata=metadata)
    return publisher(
        issue_id=issue_id,
        event=event,
        body=body,
        summary=summary,
        state=target_state,
        metadata=metadata or {},
        comment_mode=comment_mode(workflow_config),
    )
