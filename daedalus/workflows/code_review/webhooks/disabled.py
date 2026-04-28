"""Disabled webhook — no-op delivery, never matches."""
from __future__ import annotations

from typing import Any

from workflows.code_review.webhooks import (
    Webhook,
    WebhookContext,
    register,
)


@register("disabled")
class DisabledWebhook:
    def __init__(self, cfg: dict, *, ws_context: WebhookContext):
        self._cfg = cfg
        self._ctx = ws_context
        self.name = str(cfg.get("name") or "unnamed-disabled")

    def matches(self, audit_event: dict[str, Any]) -> bool:
        return False

    def deliver(self, audit_event: dict[str, Any]) -> None:
        return None
