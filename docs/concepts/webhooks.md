# Webhooks

Webhooks are **pluggable outbound subscribers** for audit events. When something happens in a lane (dispatch, review, merge, operator attention), Daedalus can fan out the event to any number of configured webhooks — Slack, HTTP JSON endpoint, or a no-op disabled stub.

---

## Why webhooks

The event log (`daedalus-events.jsonl`) is great for post-hoc analysis, but real-time notifications need a push mechanism. Webhooks bridge that gap without coupling the workflow to any specific chat platform.

---

## Config schema

```yaml
webhooks:
  - name: "slack-alerts"
    kind: slack-incoming
    url: "https://hooks.slack.com/services/..."
    enabled: true
    event-globs:
      - "operator-attention-*"
      - "merge-and-promote"

  - name: "http-json"
    kind: http-json
    url: "https://my-monitoring.example.com/daedalus"
    enabled: true
    # No event-globs = match all events

  - name: "disabled-stub"
    kind: disabled
    enabled: false
```

### Fields

| Field | Required | Default | Notes |
|---|---|---|---|
| `name` | ✅ | — | Human-readable identifier. |
| `kind` | ✅ | — | `slack-incoming`, `http-json`, `disabled`. |
| `url` | ✅ (unless `disabled`) | — | Target URL. Only `http://` and `https://` schemes allowed (SSRF guard). |
| `enabled` | ❌ | `true` | `false` forces `kind=disabled`. |
| `event-globs` | ❌ | `[]` (match all) | fnmatch globs against `action` field. Empty list = firehose. |

---

## Built-in kinds

### `slack-incoming`

Posts a compact Slack message with:
- Action name
- Lane/issue context
- Summary
- Timestamp

Uses `urllib.request` (stdlib only) with a 10-second timeout.

### `http-json`

POSTs the full audit event dict as `application/json` to the configured URL. Also stdlib-only.

### `disabled`

No-op. Swallows all events silently. Useful for temporarily disabling a webhook without deleting config.

---

## Event matching

```python
event_matches(audit_event, event_globs)
```

- `action` field is matched against each glob using `fnmatch.fnmatchcase`.
- `None` or empty `event_globs` → match all (implicit `['*']`).
- If any glob matches, the webhook receives the event.

---

## Fan-out behavior

```python
compose_audit_subscribers(subscribers)
```

Returns a callable that:
1. Builds the audit event dict (`{"at": ..., "action": ..., "summary": ..., **extra}`)
2. Iterates over all subscribers
3. Catches and swallows per-subscriber exceptions
4. Never blocks workflow execution on a slow/broken webhook

This is the same contract used by `workspace._make_audit_fn`.

---

## Adding a new webhook kind

1. Implement the `Webhook` Protocol:
   - `name: str`
   - `deliver(audit_event: dict) -> None`
   - `matches(audit_event: dict) -> bool`
2. Decorate with `@register("your-kind")` from `workflows.change_delivery.webhooks`.
3. Add the kind to `schema.yaml`.
4. Lazy-import in `build_webhooks` so side-effect registration happens.

---

## Security

- **SSRF guard:** Only `http://` and `https://` URLs are allowed. `file://`, `gopher://`, `ftp://`, etc. are rejected to prevent leaking audit events to local resources.
- **Timeout:** All built-in kinds use a 10-second socket timeout.
- **Best-effort:** Exceptions are swallowed. A compromised webhook endpoint cannot crash the workflow.

---

## Where this lives in code

- Protocol + factory: `daedalus/workflows/change_delivery/webhooks/__init__.py`
- Slack implementation: `daedalus/workflows/change_delivery/webhooks/slack_incoming.py`
- HTTP JSON implementation: `daedalus/workflows/change_delivery/webhooks/http_json.py`
- Disabled stub: `daedalus/workflows/change_delivery/webhooks/disabled.py`
- Fan-out: `daedalus/workflows/change_delivery/webhooks/__init__.py::compose_audit_subscribers`
- Tests: `tests/test_webhooks_phase_c.py`, `tests/test_webhooks_schema.py`
