"""Slack Incoming Webhook delivery.

Reformats audit events into Slack block payloads. Operator supplies
the Incoming Webhook URL; the engine never authenticates separately.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from workflows.code_review.webhooks import (
    Webhook,
    WebhookContext,
    event_matches,
    register,
)


_DEFAULT_TIMEOUT = 5
_DEFAULT_RETRY_COUNT = 1


@register("slack-incoming")
class SlackIncomingWebhook:
    """Posts audit events to a Slack Incoming Webhook URL with block layout.

    Config shape (YAML):
        - name: notify-slack
          kind: slack-incoming
          url: https://hooks.slack.com/services/T.../B.../...
          events: ["merge_and_promote"]
    """

    def __init__(self, cfg: dict, *, ws_context: WebhookContext):
        self._cfg = cfg
        self._ctx = ws_context
        self.name = str(cfg.get("name") or "unnamed")
        self._url = cfg.get("url") or ""
        self._events = list(cfg.get("events") or [])
        self._timeout = int(cfg.get("timeout-seconds") or _DEFAULT_TIMEOUT)
        self._retry_count = int(cfg.get("retry-count") if cfg.get("retry-count") is not None else _DEFAULT_RETRY_COUNT)

    def matches(self, audit_event: dict[str, Any]) -> bool:
        return event_matches(audit_event, self._events)

    def _build_payload(self, audit_event: dict[str, Any]) -> dict[str, Any]:
        action = str(audit_event.get("action") or "")
        summary = str(audit_event.get("summary") or "")
        issue_number = audit_event.get("issueNumber")
        head_sha = audit_event.get("headSha")
        at = audit_event.get("at")

        context_bits = []
        if issue_number is not None:
            context_bits.append(f"issue #{issue_number}")
        if head_sha:
            context_bits.append(f"`{head_sha}`")
        if at:
            context_bits.append(str(at))
        context_text = " · ".join(context_bits) or "code-review event"

        return {
            "text": f"[code-review] {action} — {summary}",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{action}*\n{summary}"}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": context_text}]},
            ],
        }

    def deliver(self, audit_event: dict[str, Any]) -> None:
        if not self._url:
            return
        body = json.dumps(self._build_payload(audit_event)).encode("utf-8")
        attempts = self._retry_count + 1
        for _ in range(attempts):
            try:
                req = urllib.request.Request(
                    self._url,
                    data=body,
                    method="POST",
                    headers={"Content-type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=self._timeout):
                    return
            except (urllib.error.URLError, OSError):
                continue
        return
