"""Workflow notifications for review feedback handoff."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sprints.trackers import build_code_host_client
from sprints.core.config import WorkflowConfig
from sprints.workflows.effects import (
    completed_side_effect,
    record_side_effect_failed,
    record_side_effect_skipped,
    record_side_effect_started,
    record_side_effect_succeeded,
    side_effect_key,
    with_side_effect_marker,
)
from sprints.workflows.lane_state import (
    append_engine_event,
    code_host_config,
    now_iso,
    repository_path,
    review_notification_config,
    lane_list,
)


def notify_review_changes_requested(
    *, config: WorkflowConfig, lane: dict[str, Any], output: dict[str, Any]
) -> dict[str, Any]:
    notification_cfg = review_notification_config(config)
    fingerprint = _review_changes_requested_fingerprint(lane=lane, output=output)
    existing = _existing_review_notification(lane=lane, fingerprint=fingerprint)
    if existing and existing.get("status") == "ok":
        return existing
    if not any(notification_cfg.values()):
        return _record_lane_notification(
            config=config,
            lane=lane,
            payload={
                "event": "review_changes_requested",
                "status": "skipped",
                "fingerprint": fingerprint,
                "reason": "notifications disabled",
            },
        )
    code_host_cfg = code_host_config(config)
    if not code_host_cfg:
        return _record_lane_notification(
            config=config,
            lane=lane,
            payload={
                "event": "review_changes_requested",
                "status": "skipped",
                "fingerprint": fingerprint,
                "reason": "no code-host config",
            },
        )
    body = _review_changes_requested_body(lane=lane, output=output)
    result: dict[str, Any] = {
        "event": "review_changes_requested",
        "status": "ok",
        "fingerprint": fingerprint,
        "targets": {},
        "idempotency_keys": {},
    }
    try:
        client = build_code_host_client(
            workflow_root=config.workflow_root,
            code_host_cfg=code_host_cfg,
            repo_path=repository_path(config),
        )
        if notification_cfg["pull_request_comment"]:
            pr_number = _pull_request_number(lane)
            result["targets"]["pull_request"] = (
                _run_notification_side_effect(
                    config=config,
                    lane=lane,
                    fingerprint=fingerprint,
                    operation="notification.pull_request_comment",
                    target=f"pull_request:{pr_number or 'missing'}",
                    body=body,
                    call=lambda keyed_body: (
                        client.comment_on_pull_request(pr_number, body=keyed_body)
                        if pr_number
                        else {"ok": False, "error": "pull request number missing"}
                    ),
                )
                if pr_number
                else {"ok": False, "error": "pull request number missing"}
            )
        if notification_cfg["pull_request_review"]:
            pr_number = _pull_request_number(lane)
            result["targets"]["pull_request_review"] = (
                _run_notification_side_effect(
                    config=config,
                    lane=lane,
                    fingerprint=fingerprint,
                    operation="notification.pull_request_review",
                    target=f"pull_request:{pr_number or 'missing'}",
                    body=body,
                    call=lambda keyed_body: (
                        client.request_changes_on_pull_request(
                            pr_number, body=keyed_body
                        )
                        if pr_number
                        else {"ok": False, "error": "pull request number missing"}
                    ),
                )
                if pr_number
                else {"ok": False, "error": "pull request number missing"}
            )
        if notification_cfg["issue_comment"]:
            issue_number = _issue_number(lane)
            result["targets"]["issue"] = (
                _run_notification_side_effect(
                    config=config,
                    lane=lane,
                    fingerprint=fingerprint,
                    operation="notification.issue_comment",
                    target=f"issue:{issue_number or 'missing'}",
                    body=body,
                    call=lambda keyed_body: (
                        client.comment_on_issue(issue_number, body=keyed_body)
                        if issue_number
                        else {"ok": False, "error": "issue number missing"}
                    ),
                )
                if issue_number
                else {"ok": False, "error": "issue number missing"}
            )
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
    if (
        any(
            isinstance(target, dict) and target.get("ok") is False
            for target in dict(result.get("targets") or {}).values()
        )
        and result.get("status") == "ok"
    ):
        result["status"] = "partial"
    result["idempotency_keys"] = {
        name: target.get("idempotency_key")
        for name, target in dict(result.get("targets") or {}).items()
        if isinstance(target, dict) and target.get("idempotency_key")
    }
    return _record_lane_notification(config=config, lane=lane, payload=result)


def _run_notification_side_effect(
    *,
    config: WorkflowConfig,
    lane: dict[str, Any],
    fingerprint: str,
    operation: str,
    target: str,
    body: str,
    call: Any,
) -> dict[str, Any]:
    key = side_effect_key(
        config=config,
        lane=lane,
        operation=operation,
        target=target,
        payload={"fingerprint": fingerprint},
    )
    completed = completed_side_effect(config=config, lane=lane, key=key)
    if completed:
        return {
            "ok": True,
            "skipped": True,
            "reason": "side effect already completed",
            "idempotency_key": key,
        }
    payload = {"fingerprint": fingerprint}
    record_side_effect_started(
        config=config,
        lane=lane,
        key=key,
        operation=operation,
        target=target,
        payload=payload,
    )
    keyed_body = with_side_effect_marker(body, key)
    try:
        result = call(keyed_body)
    except Exception as exc:
        error = str(exc)
        result = {"ok": False, "error": error, "idempotency_key": key}
        record_side_effect_failed(
            config=config,
            lane=lane,
            key=key,
            operation=operation,
            target=target,
            payload=payload,
            result=result,
            error=error,
        )
        return result
    if not isinstance(result, dict):
        result = {"ok": True, "result": result}
    result["idempotency_key"] = key
    if result.get("ok") is False:
        record_side_effect_failed(
            config=config,
            lane=lane,
            key=key,
            operation=operation,
            target=target,
            payload=payload,
            result=result,
            error=str(result.get("error") or "notification side effect failed"),
        )
        return result
    if result.get("skipped"):
        record_side_effect_skipped(
            config=config,
            lane=lane,
            key=key,
            operation=operation,
            target=target,
            payload=payload,
            result=result,
            reason=str(result.get("reason") or "notification skipped"),
        )
        return result
    record_side_effect_succeeded(
        config=config,
        lane=lane,
        key=key,
        operation=operation,
        target=target,
        payload=payload,
        result=result,
    )
    return result


def _existing_review_notification(
    *, lane: dict[str, Any], fingerprint: str
) -> dict[str, Any] | None:
    for record in reversed(lane_list(lane, "notifications")):
        if not isinstance(record, dict):
            continue
        if record.get("event") != "review_changes_requested":
            continue
        if record.get("fingerprint") != fingerprint:
            continue
        if record.get("status") in {"ok", "partial"}:
            return record
    return None


def _review_changes_requested_fingerprint(
    *, lane: dict[str, Any], output: dict[str, Any]
) -> str:
    payload = {
        "lane_id": lane.get("lane_id"),
        "pull_request": _pull_request_number(lane),
        "issue": _issue_number(lane),
        "status": output.get("status"),
        "summary": output.get("summary"),
        "required_fixes": output.get("required_fixes"),
        "findings": output.get("findings"),
        "verification_gaps": output.get("verification_gaps"),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_lane_notification(
    *, config: WorkflowConfig, lane: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    record = {"created_at": now_iso(), **payload}
    lane_list(lane, "notifications").append(record)
    append_engine_event(
        config=config,
        lane=lane,
        event_type=f"{config.workflow_name}.lane.notification",
        payload=record,
        severity="warning" if record.get("status") in {"error", "partial"} else "info",
    )
    return record


def _review_changes_requested_body(
    *, lane: dict[str, Any], output: dict[str, Any]
) -> str:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    lines = [
        "### Sprints review requested changes",
        "",
        f"Lane: {lane.get('lane_id')}",
    ]
    issue_label = " ".join(
        part
        for part in [
            str(issue.get("identifier") or issue.get("id") or "").strip(),
            str(issue.get("title") or "").strip(),
        ]
        if part
    )
    if issue_label:
        lines.append(f"Issue: {issue_label}")
    summary = str(output.get("summary") or "").strip()
    if summary:
        lines.extend(["", "Summary:", summary])
    _append_markdown_items(lines, "Required fixes", output.get("required_fixes"))
    _append_markdown_items(lines, "Findings", output.get("findings"))
    _append_markdown_items(lines, "Verification gaps", output.get("verification_gaps"))
    lines.extend(["", "Generated by Sprints."])
    return "\n".join(lines).strip()


def _append_markdown_items(lines: list[str], title: str, value: Any) -> None:
    if not isinstance(value, list) or not value:
        return
    lines.extend(["", f"{title}:"])
    for index, item in enumerate(value, start=1):
        lines.append(f"{index}. {_markdown_item_text(item)}")


def _markdown_item_text(item: Any) -> str:
    if isinstance(item, dict):
        parts = [
            f"{key}: {item[key]}"
            for key in sorted(item)
            if item.get(key) not in (None, "", [], {})
        ]
        return "; ".join(parts) or "{}"
    return str(item)


def _pull_request_number(lane: dict[str, Any]) -> str:
    pull_request = lane.get("pull_request")
    if not isinstance(pull_request, dict):
        return ""
    for key in ("number", "pr_number"):
        value = pull_request.get(key)
        if value not in (None, ""):
            number = _trailing_number(value)
            if number:
                return number
    url = str(pull_request.get("url") or "").strip()
    match = re.search(r"/pull/([0-9]+)(?:$|[/?#])", url)
    if match:
        return match.group(1)
    return _trailing_number(pull_request.get("id"))


def _issue_number(lane: dict[str, Any]) -> str:
    issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
    for key in ("number", "id", "identifier"):
        value = issue.get(key)
        if value not in (None, ""):
            number = _trailing_number(value)
            if number:
                return number
    return ""


def _trailing_number(value: Any) -> str:
    text = str(value or "").strip().lstrip("#")
    match = re.search(r"([0-9]+)$", text)
    return match.group(1) if match else ""
