"""Pluggable outbound webhook subscribers for audit events.

Mirrors the runtime/reviewer layers: Protocol + @register decorator +
factory. ``compose_audit_subscribers`` fans out an audit event to N
subscribers with per-subscriber exception isolation, matching the
publisher contract used by ``workspace._make_audit_fn``.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class WebhookContext:
    """Workspace-scoped primitives a webhook needs at delivery time."""

    run_fn: Callable[..., Any] | None
    now_iso: Callable[[], str]


@runtime_checkable
class Webhook(Protocol):
    """Protocol every webhook kind implements."""

    name: str

    def deliver(self, audit_event: dict[str, Any]) -> None: ...

    def matches(self, audit_event: dict[str, Any]) -> bool: ...


_WEBHOOK_KINDS: dict[str, type] = {}


def register(kind: str):
    """Decorator: registers a class as the implementation for a webhook kind."""

    def _register(cls):
        _WEBHOOK_KINDS[kind] = cls
        return cls

    return _register


def event_matches(audit_event: dict[str, Any], event_globs: list[str] | None) -> bool:
    """Match an audit event's `action` against a list of fnmatch globs.

    None / empty list => match all (implicit ['*']).
    """
    action = str(audit_event.get("action") or "")
    if not event_globs:
        return True
    return any(fnmatch.fnmatchcase(action, g) for g in event_globs)


def build_webhooks(
    webhooks_cfg: list[dict] | None,
    *,
    run_fn: Callable[..., Any] | None = None,
) -> list[Webhook]:
    """Instantiate one Webhook per subscription. Empty/None config -> []."""
    if not webhooks_cfg:
        return []
    # Lazy import for side-effect registration. Wrapped in try/except so this
    # module stands alone before Tasks 2-4 land. Once http_json/slack_incoming/
    # disabled exist, remove the wrappers.
    try:
        from workflows.code_review.webhooks import http_json  # noqa: F401
    except ImportError:
        pass
    try:
        from workflows.code_review.webhooks import slack_incoming  # noqa: F401
    except ImportError:
        pass
    try:
        from workflows.code_review.webhooks import disabled as _disabled  # noqa: F401
    except ImportError:
        pass

    import time as _time
    ctx = WebhookContext(run_fn=run_fn, now_iso=lambda: _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()))

    out: list[Webhook] = []
    for sub_cfg in webhooks_cfg:
        if sub_cfg.get("enabled") is False:
            kind = "disabled"
        else:
            kind = sub_cfg.get("kind") or ""
        if kind not in _WEBHOOK_KINDS:
            raise ValueError(
                f"unknown webhook kind={kind!r}; "
                f"registered kinds: {sorted(_WEBHOOK_KINDS)}"
            )
        cls = _WEBHOOK_KINDS[kind]
        out.append(cls(sub_cfg, ws_context=ctx))
    return out


def compose_audit_subscribers(
    subscribers: list[Callable[[dict], None]],
) -> Callable[..., None]:
    """Fan-out callable matching the publisher contract used by
    ``_make_audit_fn``: ``publisher(action, summary, extra=...)``.

    Each subscriber receives a fully-built audit_event dict
    ``{"at": ..., "action": ..., "summary": ..., **extra}``.
    Per-subscriber exceptions are caught and swallowed.
    """
    import time as _time

    def _now_iso():
        return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())

    def publisher(*, action, summary, extra=None):
        event = {"at": _now_iso(), "action": action, "summary": summary, **(extra or {})}
        for sub in subscribers:
            try:
                sub(event)
            except Exception:
                # Best-effort: never break workflow execution.
                pass

    return publisher
