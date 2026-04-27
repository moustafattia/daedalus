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
        build_webhooks([{"name": "x", "kind": "made-up", "url": "https://example.com"}], run_fn=None)


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


def test_http_json_webhook_registered():
    from workflows.code_review.webhooks import _WEBHOOK_KINDS
    from workflows.code_review.webhooks import http_json  # noqa: F401
    assert "http-json" in _WEBHOOK_KINDS


def test_http_json_webhook_posts_payload_to_url():
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{"name": "wh1", "kind": "http-json", "url": "https://example.com/hook"}]
    webhooks = build_webhooks(cfg, run_fn=None)
    assert len(webhooks) == 1

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda self: self
        mock_urlopen.return_value.__exit__ = lambda self, *a: None
        mock_urlopen.return_value.status = 200
        webhooks[0].deliver({"action": "X", "summary": "Y"})

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://example.com/hook"
    assert req.get_method() == "POST"
    body = req.data.decode("utf-8")
    import json
    parsed = json.loads(body)
    assert parsed["action"] == "X"
    assert parsed["summary"] == "Y"
    assert req.headers.get("Content-type") == "application/json"


def test_http_json_webhook_includes_custom_headers():
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{
        "name": "wh1", "kind": "http-json",
        "url": "https://example.com/hook",
        "headers": {"X-Custom": "v1", "Authorization": "Bearer xyz"},
    }]
    webhooks = build_webhooks(cfg, run_fn=None)

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda self: self
        mock_urlopen.return_value.__exit__ = lambda self, *a: None
        mock_urlopen.return_value.status = 200
        webhooks[0].deliver({"action": "X", "summary": "Y"})

    req = mock_urlopen.call_args[0][0]
    # urllib normalizes header keys via title-case
    assert req.headers.get("X-custom") == "v1"
    assert req.headers.get("Authorization") == "Bearer xyz"


def test_http_json_webhook_retries_on_failure():
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{
        "name": "wh1", "kind": "http-json",
        "url": "https://example.com/hook",
        "retry-count": 2,
    }]
    webhooks = build_webhooks(cfg, run_fn=None)

    with patch("urllib.request.urlopen", side_effect=OSError("net down")) as mock_urlopen:
        # Should not raise; retry-count: 2 means 1 initial + 2 retries = 3 calls.
        webhooks[0].deliver({"action": "X", "summary": "Y"})
        assert mock_urlopen.call_count == 3


def test_http_json_webhook_no_retry_on_success():
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{
        "name": "wh1", "kind": "http-json",
        "url": "https://example.com/hook",
        "retry-count": 5,
    }]
    webhooks = build_webhooks(cfg, run_fn=None)

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda self: self
        mock_urlopen.return_value.__exit__ = lambda self, *a: None
        mock_urlopen.return_value.status = 200
        webhooks[0].deliver({"action": "X", "summary": "Y"})
        assert mock_urlopen.call_count == 1


def test_http_json_webhook_matches_default_all_events():
    from workflows.code_review.webhooks import build_webhooks
    cfg = [{"name": "wh1", "kind": "http-json", "url": "https://x"}]
    wh = build_webhooks(cfg, run_fn=None)[0]
    assert wh.matches({"action": "anything"}) is True


def test_slack_incoming_webhook_registered():
    from workflows.code_review.webhooks import _WEBHOOK_KINDS
    from workflows.code_review.webhooks import slack_incoming  # noqa: F401
    assert "slack-incoming" in _WEBHOOK_KINDS


def test_slack_incoming_payload_shape():
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{
        "name": "slack", "kind": "slack-incoming",
        "url": "https://hooks.slack.com/services/X/Y/Z",
    }]
    webhooks = build_webhooks(cfg, run_fn=None)

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda self: self
        mock_urlopen.return_value.__exit__ = lambda self, *a: None
        mock_urlopen.return_value.status = 200
        webhooks[0].deliver({
            "action": "merge_and_promote",
            "summary": "Merged PR #42",
            "issueNumber": 42,
            "headSha": "abc123",
            "at": "2026-04-26T12:00:00Z",
        })

    req = mock_urlopen.call_args[0][0]
    assert req.full_url.startswith("https://hooks.slack.com/")
    import json
    payload = json.loads(req.data.decode("utf-8"))
    assert "text" in payload
    assert "blocks" in payload
    assert "merge_and_promote" in payload["text"]
    assert "Merged PR #42" in payload["text"]
    # Block layout: section + context
    assert any(b.get("type") == "section" for b in payload["blocks"])
    assert any(b.get("type") == "context" for b in payload["blocks"])


def test_disabled_webhook_registered():
    from workflows.code_review.webhooks import _WEBHOOK_KINDS
    from workflows.code_review.webhooks import disabled  # noqa: F401
    assert "disabled" in _WEBHOOK_KINDS


def test_disabled_webhook_does_not_call_urlopen():
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{"name": "wh", "kind": "disabled"}]
    webhooks = build_webhooks(cfg, run_fn=None)

    with patch("urllib.request.urlopen") as mock_urlopen:
        webhooks[0].deliver({"action": "X", "summary": "Y"})
        mock_urlopen.assert_not_called()


def test_disabled_via_enabled_false():
    """enabled: false overrides any kind."""
    from workflows.code_review.webhooks import build_webhooks
    from workflows.code_review.webhooks.disabled import DisabledWebhook

    cfg = [{"name": "wh", "kind": "http-json", "url": "https://x", "enabled": False}]
    webhooks = build_webhooks(cfg, run_fn=None)
    assert isinstance(webhooks[0], DisabledWebhook)


def test_event_filter_glob_matches_exact():
    from workflows.code_review.webhooks import event_matches
    assert event_matches({"action": "run_claude_review"}, ["run_claude_review"]) is True
    assert event_matches({"action": "merge_and_promote"}, ["run_claude_review"]) is False


def test_event_filter_glob_matches_prefix():
    from workflows.code_review.webhooks import event_matches
    assert event_matches({"action": "run_claude_review"}, ["run_*"]) is True
    assert event_matches({"action": "run_internal_review"}, ["run_*"]) is True
    assert event_matches({"action": "merge_and_promote"}, ["run_*"]) is False


def test_event_filter_glob_suffix():
    from workflows.code_review.webhooks import event_matches
    assert event_matches({"action": "internal_review"}, ["*_review"]) is True
    assert event_matches({"action": "external_review"}, ["*_review"]) is True
    assert event_matches({"action": "merge_and_promote"}, ["*_review"]) is False


def test_event_filter_omitted_defaults_to_all():
    from workflows.code_review.webhooks import event_matches
    assert event_matches({"action": "any"}, None) is True
    assert event_matches({"action": "any"}, []) is True


def test_event_filter_multiple_globs_or():
    from workflows.code_review.webhooks import event_matches
    globs = ["merge_*", "operator_*"]
    assert event_matches({"action": "merge_and_promote"}, globs) is True
    assert event_matches({"action": "operator_attention_required"}, globs) is True
    assert event_matches({"action": "run_claude_review"}, globs) is False


def test_filtered_subscriber_does_not_deliver_unmatched_events():
    """When wrapping a webhook into a subscriber, non-matching events are skipped."""
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{
        "name": "only-merges", "kind": "http-json",
        "url": "https://x", "events": ["merge_*"],
    }]
    wh = build_webhooks(cfg, run_fn=None)[0]
    assert wh.matches({"action": "merge_and_promote"}) is True
    assert wh.matches({"action": "run_claude_review"}) is False


def test_build_webhooks_rejects_file_url():
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{"name": "wh", "kind": "http-json", "url": "file:///etc/passwd"}]
    with pytest.raises(ValueError, match="unsupported URL scheme"):
        build_webhooks(cfg, run_fn=None)


def test_build_webhooks_rejects_gopher_url():
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{"name": "wh", "kind": "slack-incoming", "url": "gopher://internal/"}]
    with pytest.raises(ValueError, match="unsupported URL scheme"):
        build_webhooks(cfg, run_fn=None)


def test_build_webhooks_accepts_http_and_https():
    from workflows.code_review.webhooks import build_webhooks

    for url in ("https://example.com/hook", "http://example.com/hook"):
        cfg = [{"name": "wh", "kind": "http-json", "url": url}]
        assert len(build_webhooks(cfg, run_fn=None)) == 1


def test_build_webhooks_disabled_kind_skips_scheme_check():
    """Disabled webhooks don't deliver — scheme check shouldn't apply."""
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{"name": "wh", "kind": "disabled", "url": "file:///irrelevant"}]
    # Should not raise
    assert len(build_webhooks(cfg, run_fn=None)) == 1


def test_build_webhooks_rejects_http_json_without_url():
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{"name": "wh", "kind": "http-json"}]
    with pytest.raises(ValueError, match="requires a 'url'"):
        build_webhooks(cfg, run_fn=None)


def test_build_webhooks_rejects_slack_incoming_without_url():
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{"name": "wh", "kind": "slack-incoming"}]
    with pytest.raises(ValueError, match="requires a 'url'"):
        build_webhooks(cfg, run_fn=None)


def test_build_webhooks_disabled_kind_allows_missing_url():
    """Disabled is the explicit no-op kind; url isn't required."""
    from workflows.code_review.webhooks import build_webhooks

    cfg = [{"name": "wh", "kind": "disabled"}]
    assert len(build_webhooks(cfg, run_fn=None)) == 1
