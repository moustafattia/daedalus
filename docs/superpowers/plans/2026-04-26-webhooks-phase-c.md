# Webhooks Phase C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add outbound webhooks: operator declares N event subscribers in `workflow.yaml`; engine fans out audit events to all matching subscribers.

**Architecture:** New `workflows/code_review/webhooks/` package mirrors `runtimes/` and `reviewers/`. `Webhook` Protocol with `deliver(event)` + `matches(event)` methods. `compose_audit_subscribers(...)` fans out to N subscribers with per-subscriber exception isolation. Workspace builder composes the existing comments publisher with N webhooks into one publisher passed to `_make_audit_fn`.

**Tech Stack:** Python 3.11 stdlib (`urllib.request`, `fnmatch`), JSON Schema, pyyaml, pytest.

**Spec:** `docs/superpowers/specs/2026-04-26-webhooks-phase-c-design.md`

**Worktree:** `/home/radxa/WS/hermes-relay/.claude/worktrees/webhooks-phase-c` on branch `claude/webhooks-phase-c` from main `47ae160`. Baseline 477 tests passing. Use `/usr/bin/python3`.

---

## File Structure

**New files:**
- `workflows/code_review/webhooks/__init__.py` — Protocol, registry, `build_webhooks`, `compose_audit_subscribers`, glob filter
- `workflows/code_review/webhooks/http_json.py` — `HttpJsonWebhook`
- `workflows/code_review/webhooks/slack_incoming.py` — `SlackIncomingWebhook`
- `workflows/code_review/webhooks/disabled.py` — `DisabledWebhook`
- `tests/test_webhooks_phase_c.py`
- `tests/test_webhooks_schema.py`

**Modified files:**
- `workflows/code_review/schema.yaml` — top-level `webhooks:` array
- `workflows/code_review/workspace.py` — build webhooks, compose subscribers
- `skills/operator/SKILL.md` — document `webhooks:` config

---

## Task 1: Webhook Protocol + registry + compose

**Files:**
- Create: `workflows/code_review/webhooks/__init__.py`
- Test: `tests/test_webhooks_phase_c.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_webhooks_phase_c.py`:

```python
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
```

- [ ] **Step 2: Verify failure**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/webhooks-phase-c
/usr/bin/python3 -m pytest tests/test_webhooks_phase_c.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_review.webhooks'`.

- [ ] **Step 3: Create the package skeleton**

Create `workflows/code_review/webhooks/__init__.py`:

```python
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
    # Lazy import for side-effect registration.
    from workflows.code_review.webhooks import http_json  # noqa: F401
    from workflows.code_review.webhooks import slack_incoming  # noqa: F401
    from workflows.code_review.webhooks import disabled as _disabled  # noqa: F401

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
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_webhooks_phase_c.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 483 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(webhooks): add Webhook Protocol + registry + compose

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: http-json webhook

**Files:**
- Create: `workflows/code_review/webhooks/http_json.py`
- Test: `tests/test_webhooks_phase_c.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/test_webhooks_phase_c.py`:

```python
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
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_webhooks_phase_c.py -v
```
Expected: FAIL with `ModuleNotFoundError: workflows.code_review.webhooks.http_json`.

- [ ] **Step 3: Create the http-json webhook**

Create `workflows/code_review/webhooks/http_json.py`:

```python
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
```

- [ ] **Step 4: Run target tests**

```bash
/usr/bin/python3 -m pytest tests/test_webhooks_phase_c.py -v
```
Expected: 12 passed.

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 489 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(webhooks): add http-json webhook

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: slack-incoming webhook

**Files:**
- Create: `workflows/code_review/webhooks/slack_incoming.py`
- Test: `tests/test_webhooks_phase_c.py` (extend)

- [ ] **Step 1: Append failing tests**

```python
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
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_webhooks_phase_c.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create the slack-incoming webhook**

Create `workflows/code_review/webhooks/slack_incoming.py`:

```python
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
```

- [ ] **Step 4: Run target + full**

```bash
/usr/bin/python3 -m pytest tests/test_webhooks_phase_c.py -v
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 14 in target, 491 total.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(webhooks): add slack-incoming webhook

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: disabled webhook + filter tests

**Files:**
- Create: `workflows/code_review/webhooks/disabled.py`
- Test: `tests/test_webhooks_phase_c.py` (extend)

- [ ] **Step 1: Append failing tests**

```python
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
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_webhooks_phase_c.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create the disabled webhook**

Create `workflows/code_review/webhooks/disabled.py`:

```python
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
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_webhooks_phase_c.py -v
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: ~23 in target, 500 total.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(webhooks): add disabled webhook + event filter coverage

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Schema extensions

**Files:**
- Modify: `workflows/code_review/schema.yaml`
- Test: `tests/test_webhooks_schema.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_webhooks_schema.py`:

```python
"""Phase C schema validation."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator, ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "workflows/code_review/schema.yaml"


def _schema():
    return yaml.safe_load(SCHEMA_PATH.read_text())


def _base_config():
    return {
        "workflow": "code-review",
        "schema-version": 1,
        "instance": {"name": "test", "engine-owner": "hermes"},
        "repository": {
            "local-path": "/tmp/x",
            "github-slug": "x/y",
            "active-lane-label": "active",
        },
        "runtimes": {
            "codex-acpx": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": 900,
                "session-idle-grace-seconds": 1800,
                "session-nudge-cooldown-seconds": 600,
            },
        },
        "agents": {
            "coder": {"default": {"name": "c", "model": "m", "runtime": "codex-acpx"}},
            "internal-reviewer": {"name": "ir", "model": "m", "runtime": "codex-acpx"},
            "external-reviewer": {"enabled": True, "name": "er"},
        },
        "gates": {"internal-review": {}, "external-review": {}, "merge": {}},
        "triggers": {"lane-selector": {"type": "label", "label": "active"}},
        "storage": {"ledger": "x", "health": "x", "audit-log": "x"},
    }


def test_schema_accepts_no_webhooks_block():
    Draft7Validator(_schema()).validate(_base_config())


def test_schema_accepts_empty_webhooks_array():
    cfg = _base_config()
    cfg["webhooks"] = []
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_http_json_webhook():
    cfg = _base_config()
    cfg["webhooks"] = [{"name": "wh", "kind": "http-json", "url": "https://x"}]
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_slack_incoming_webhook():
    cfg = _base_config()
    cfg["webhooks"] = [{"name": "slack", "kind": "slack-incoming", "url": "https://hooks.slack.com/X"}]
    Draft7Validator(_schema()).validate(cfg)


def test_schema_accepts_full_subscription():
    cfg = _base_config()
    cfg["webhooks"] = [{
        "name": "wh", "kind": "http-json", "url": "https://x",
        "enabled": True,
        "events": ["merge_*", "run_*"],
        "headers": {"X-Custom": "v"},
        "timeout-seconds": 10,
        "retry-count": 3,
    }]
    Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_unknown_kind():
    cfg = _base_config()
    cfg["webhooks"] = [{"name": "wh", "kind": "made-up"}]
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)


def test_schema_rejects_extra_property_on_subscription():
    cfg = _base_config()
    cfg["webhooks"] = [{"name": "wh", "kind": "http-json", "urls": "https://x"}]  # typo
    with pytest.raises(ValidationError):
        Draft7Validator(_schema()).validate(cfg)


def test_existing_yoyopod_workflow_yaml_still_validates():
    yoyopod = Path(os.path.expanduser("~/.hermes/workflows/yoyopod/config/workflow.yaml"))
    if not yoyopod.exists():
        pytest.skip("yoyopod workspace not present on this host")
    cfg = yaml.safe_load(yoyopod.read_text())
    Draft7Validator(_schema()).validate(cfg)
```

- [ ] **Step 2: Verify failure**

```bash
/usr/bin/python3 -m pytest tests/test_webhooks_schema.py -v
```
Expected: FAIL on `webhooks` kind enum tests.

- [ ] **Step 3: Edit schema.yaml**

In `workflows/code_review/schema.yaml`, add a new top-level property after `observability:`:

```yaml
  webhooks:
    type: array
    items:
      type: object
      required: [name, kind]
      additionalProperties: false
      properties:
        name: {type: string}
        kind:
          type: string
          enum: [http-json, slack-incoming, disabled]
        enabled: {type: boolean}
        url: {type: string}
        events:
          type: array
          items: {type: string}
        headers:
          type: object
          additionalProperties: {type: string}
        timeout-seconds: {type: integer, minimum: 1}
        retry-count: {type: integer, minimum: 0}
```

- [ ] **Step 4: Run target + full**

```bash
/usr/bin/python3 -m pytest tests/test_webhooks_schema.py -v
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 8 in target, 508 total.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(schema): add webhooks block with kind enum + per-subscription fields

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Workspace integration

**Files:**
- Modify: `workflows/code_review/workspace.py`

- [ ] **Step 1: Wire webhooks into the publisher chain**

Locate the existing publisher creation around `workspace.py:560`:
```python
_publisher = _make_comment_publisher(
    workflow_root=workspace_root,
    repo_slug=_repo_slug,
    workflow_yaml=yaml_cfg or {},
    get_active_issue_number=lambda: ...,
    get_workflow_state=lambda: ...,
    get_is_operator_attention=lambda: ...,
)
audit = _make_audit_fn(audit_log_path=audit_log_path, publisher=_publisher)
```

Replace with a fan-out chain. After the `_publisher = _make_comment_publisher(...)` call:

```python
from workflows.code_review.webhooks import build_webhooks, compose_audit_subscribers

_webhooks = build_webhooks((yaml_cfg or {}).get("webhooks") or [], run_fn=_run)

def _adapt_legacy_publisher(legacy_pub):
    """The legacy comments publisher takes (action=, summary=, extra=).
    Compose-style subscribers receive a single audit_event dict. Adapt."""
    if legacy_pub is None:
        return None
    def _sub(audit_event):
        legacy_pub(
            action=audit_event.get("action") or "",
            summary=audit_event.get("summary") or "",
            extra={k: v for k, v in audit_event.items() if k not in ("action", "summary", "at")},
        )
    return _sub

def _adapt_webhook(wh):
    """Wrap a Webhook into a (audit_event)->None subscriber that respects matches()."""
    def _sub(audit_event):
        if not wh.matches(audit_event):
            return
        wh.deliver(audit_event)
    return _sub

_subscribers = []
_legacy = _adapt_legacy_publisher(_publisher)
if _legacy is not None:
    _subscribers.append(_legacy)
for _wh in _webhooks:
    _subscribers.append(_adapt_webhook(_wh))

_fanout_publisher = compose_audit_subscribers(_subscribers) if _subscribers else None
audit = _make_audit_fn(audit_log_path=audit_log_path, publisher=_fanout_publisher)
```

Note: `_run` is the workspace-scoped subprocess primitive — verify it's defined before this block. Search for `def _run` or `_run =` in workspace.py and confirm. If `_run` is not yet defined at this point in the file, pass `None` for `run_fn`.

- [ ] **Step 2: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: 508 passed. If any existing test fails because the publisher signature changed, investigate — `_make_audit_fn` itself didn't change; only what's passed in changed. Existing mocks of `_make_comment_publisher` should still work.

- [ ] **Step 3: Verify yoyopod still validates and behaves**

```bash
/usr/bin/python3 -c "
import yaml
from pathlib import Path
from jsonschema import Draft7Validator
schema = yaml.safe_load(Path('workflows/code_review/schema.yaml').read_text())
cfg = yaml.safe_load(Path('/home/radxa/.hermes/workflows/yoyopod/config/workflow.yaml').read_text())
Draft7Validator(schema).validate(cfg)
print('yoyopod config valid')
"
```
Expected: `yoyopod config valid`. Yoyopod has no `webhooks:` block ⇒ `build_webhooks([])` → empty list → only the legacy comments publisher runs (behavior preserved).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(workspace): fan out audit events to comments publisher + webhooks

Workspace builds N webhook subscribers from yaml_cfg.webhooks and
composes them with the existing comments publisher into one fan-out
publisher passed to _make_audit_fn. Subscribers run inline; per-
subscriber exceptions are isolated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Operator docs

**Files:**
- Modify: `skills/operator/SKILL.md`

- [ ] **Step 1: Append section**

Append to `skills/operator/SKILL.md`:

````markdown
## Webhooks (Phase C — outbound event subscribers)

Declare N webhook subscriptions under top-level `webhooks:`. Each subscription receives audit events that match its `events:` filter.

```yaml
webhooks:
  - name: notify-slack
    kind: slack-incoming
    url: https://hooks.slack.com/services/T.../B.../...
    events: ["merge_and_promote", "operator_attention_required"]

  - name: ci-mirror
    kind: http-json
    url: https://ci.example.com/hooks/code-review
    headers:
      Authorization: Bearer xyz
    events: ["run_*", "merge_*"]
    timeout-seconds: 5
    retry-count: 2

  - name: temporarily-off
    kind: http-json
    url: https://example.com/hook
    enabled: false   # short-circuit without removing the entry
```

**Kinds:**
- `http-json` — POST raw audit-event JSON to `url` with optional `headers:`.
- `slack-incoming` — POST Slack-formatted blocks to a Slack Incoming Webhook URL.
- `disabled` — explicit no-op (equivalent to `enabled: false`).

**Event filter (`events:`):** list of fnmatch globs against the audit event's `action` field. Examples:
- `["*"]` or omitted ⇒ all events
- `["run_*"]` ⇒ everything starting with `run_`
- `["merge_and_promote"]` ⇒ exact match
- `["*_review"]` ⇒ suffix match
- Multiple globs are OR'd

**Delivery semantics:** fire-and-forget, inline retry (default `retry-count: 1` ⇒ initial + 1 retry). Per-subscriber exceptions are swallowed — webhooks cannot break workflow execution. No persistent queue: if the engine crashes mid-delivery the event lives in `audit-log` JSONL but is not redelivered.

**Audit-event payload (what `http-json` POSTs):**
```json
{
  "at": "2026-04-26T12:34:56Z",
  "action": "merge_and_promote",
  "summary": "Merged PR #42",
  "issueNumber": 42,
  "headSha": "abc123"
}
```

(Extra fields beyond `at`/`action`/`summary` come from the action's audit context — they vary by action.)
````

- [ ] **Step 2: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```
Expected: 508 passed.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "docs(operator): document webhooks config surface

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Run full suite**

```bash
cd /home/radxa/WS/hermes-relay/.claude/worktrees/webhooks-phase-c
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -10
```
Expected: 508 passed.

- [ ] **Sanity-check live yoyopod config still validates**

```bash
/usr/bin/python3 -c "
import yaml
from pathlib import Path
from jsonschema import Draft7Validator
schema = yaml.safe_load(Path('workflows/code_review/schema.yaml').read_text())
cfg = yaml.safe_load(Path('/home/radxa/.hermes/workflows/yoyopod/config/workflow.yaml').read_text())
Draft7Validator(schema).validate(cfg)
print('yoyopod config valid')
"
```
Expected: `yoyopod config valid`.

- [ ] **Use superpowers:finishing-a-development-branch** to wrap up.
