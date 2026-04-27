"""HTTP-JSON outbound webhook (POST raw audit-event JSON to a URL)."""
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


@register("http-json")
class HttpJsonWebhook:
    """POSTs each audit event verbatim as JSON to a configured URL.

    Config shape (YAML):
        - name: my-hook
          kind: http-json
          url: https://example.com/hook
          headers: {X-Custom: v}
          events: ["merge_*"]
          timeout-seconds: 5
          retry-count: 1
    """

    def __init__(self, cfg: dict, *, ws_context: WebhookContext):
        self._cfg = cfg
        self._ctx = ws_context
        self.name = str(cfg.get("name") or "unnamed")
        self._url = cfg.get("url") or ""
        self._headers = dict(cfg.get("headers") or {})
        self._events = list(cfg.get("events") or [])
        self._timeout = int(cfg.get("timeout-seconds") or _DEFAULT_TIMEOUT)
        self._retry_count = int(cfg.get("retry-count") if cfg.get("retry-count") is not None else _DEFAULT_RETRY_COUNT)

    def matches(self, audit_event: dict[str, Any]) -> bool:
        return event_matches(audit_event, self._events)

    def deliver(self, audit_event: dict[str, Any]) -> None:
        if not self._url:
            return
        body = json.dumps(audit_event).encode("utf-8")
        attempts = self._retry_count + 1
        last_err: Exception | None = None
        for _ in range(attempts):
            try:
                req = urllib.request.Request(
                    self._url,
                    data=body,
                    method="POST",
                    headers={"Content-type": "application/json", **self._headers},
                )
                with urllib.request.urlopen(req, timeout=self._timeout):
                    return
            except (urllib.error.URLError, OSError) as e:
                last_err = e
                continue
        # All retries exhausted; swallow (compose_audit_subscribers also catches,
        # but be explicit: webhook delivery is best-effort).
        return
