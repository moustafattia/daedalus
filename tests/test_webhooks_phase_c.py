"""Phase C tests: webhook event subscribers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_webhook_module_exposes_protocol_registry_and_compose():
    from workflows.code_review.webhooks import (
        Webhook, WebhookContext, register, build_webhooks,
        compose_audit_subscribers, _WEBHOOK_KINDS,
    )
    assert callable(register)
    assert callable(build_webhooks)
    assert callable(compose_audit_subscribers)
    assert isinstance(_WEBHOOK_KINDS, dict)


def test_build_webhooks_empty_list_returns_empty():
    from workflows.code_review.webhooks import build_webhooks
    assert build_webhooks([], run_fn=None) == []


def test_build_webhooks_unknown_kind_raises():
    from workflows.code_review.webhooks import build_webhooks
    with pytest.raises(ValueError, match="unknown"):
        build_webhooks([{"name": "x", "kind": "made-up"}], run_fn=None)


def test_compose_audit_subscribers_fans_out():
    from workflows.code_review.webhooks import compose_audit_subscribers

    sub1 = MagicMock()
    sub2 = MagicMock()
    sub3 = MagicMock()
    pub = compose_audit_subscribers([sub1, sub2, sub3])
    pub(action="X", summary="Y", extra={"k": "v"})
    for s in (sub1, sub2, sub3):
        s.assert_called_once()
        evt = s.call_args[0][0]
        assert evt["action"] == "X"
        assert evt["summary"] == "Y"
        assert evt["k"] == "v"


def test_compose_audit_subscribers_isolates_exceptions():
    from workflows.code_review.webhooks import compose_audit_subscribers

    sub1 = MagicMock(side_effect=RuntimeError("boom"))
    sub2 = MagicMock()
    sub3 = MagicMock()
    pub = compose_audit_subscribers([sub1, sub2, sub3])
    # Should not raise
    pub(action="X", summary="Y", extra={})
    sub2.assert_called_once()
    sub3.assert_called_once()


def test_compose_audit_subscribers_empty_list_is_noop():
    from workflows.code_review.webhooks import compose_audit_subscribers
    pub = compose_audit_subscribers([])
    pub(action="X", summary="Y", extra={})  # no-op, no error
