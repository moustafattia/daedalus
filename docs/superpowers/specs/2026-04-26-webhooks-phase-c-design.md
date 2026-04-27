# Webhooks (Outbound Event Subscribers) — Phase C Design

**Status:** Approved
**Date:** 2026-04-26
**Branch:** `claude/webhooks-phase-c` (worktree at `.claude/worktrees/webhooks-phase-c`)
**Baseline:** main `47ae160`, 477 tests passing

## Problem

The code-review workflow already emits structured audit events (`_make_audit_fn` in `workspace.py:311`) on every action transition. Today only one consumer plugs in: the GitHub-comments publisher (`_make_comment_publisher`). Operators who want to mirror events to Slack, a custom dashboard, a CI hook, or any external system have no path other than reading the audit JSONL file.

Phase C adds **outbound webhooks**: operator declares N event subscribers in `workflow.yaml`, each with a kind (`http-json`, `slack-incoming`, `disabled`), a URL, and an event filter. The engine fans out each audit event to all matching subscribers, fire-and-forget with one best-effort retry. Subscribers run inline in the audit hook — same exception-swallowing semantics as the existing comments publisher (observability must never break workflow execution).

## Scope

### In scope (this PR)
1. **`Webhook` Protocol + registry** — new package `workflows/code_review/webhooks/` mirroring `runtimes/` and `reviewers/`. Protocol has one method: `deliver(audit_event: dict) → None`. `@register("<kind>")` decorator + `_WEBHOOK_KINDS` registry + `build_webhooks(webhooks_cfg, *, run_fn) → list[Webhook]` factory.
2. **`http-json` webhook** — POST raw audit-event JSON to a URL. Configurable headers, timeout, retry count (default 1). Uses `urllib.request` (stdlib only — no new dependencies).
3. **`slack-incoming` webhook** — POSTs Slack-formatted blocks (`{"text": "...", "blocks": [...]}`) to an Incoming Webhook URL. Operator supplies the URL; payload built from the audit event's `action` + `summary`.
4. **`disabled` webhook** — explicit kind for `enabled: false`. No-op `deliver`.
5. **Composing publisher** — generalize the `publisher=` slot in `_make_audit_fn` from a single callable to a list of subscribers. New `compose_audit_subscribers(subscribers: list[Callable]) → Callable` that fans out and swallows per-subscriber exceptions independently. The existing comments publisher is one entry; webhooks become additional entries.
6. **Schema** — new top-level `webhooks:` block (array of subscriptions). Each subscription has `name`, `kind`, optional `url`, optional `enabled`, optional `events:` filter (list of action-name globs), optional `headers:`, `timeout-seconds:`, `retry-count:`.
7. **Event filtering** — `events:` filter is a list of action-name globs (`*` for all, `merge_*` for prefix matches, `run_claude_review` for exact). When omitted ⇒ all events.
8. **Tests** — protocol + registry, http-json delivery (mocked urllib), slack-incoming payload shape, disabled provider, event-filter glob matching, fan-out exception isolation, schema validation.
9. **Operator docs** — `skills/operator/SKILL.md` documents the new `webhooks:` config surface.

### Out of scope (deferred)
- **Inbound webhooks** (e.g., GitHub webhook → trigger a workflow tick). Different shape entirely; would need an HTTP listener.
- **Persistent retry queue.** Fire-and-forget with N retries inline. If the engine crashes mid-delivery the event is logged in the audit JSONL but not redelivered.
- **HMAC signing on outbound** (`X-Hub-Signature` style). Operators who need this can put a reverse proxy in front.
- **Templated payloads.** `slack-incoming` builds a fixed block layout; if operators want custom formatting, Phase D problem.
- **Phase D rename pass** — still pending.

## Architecture

### Webhook layering
```
        ┌────────────────────────────────────────┐
        │     workspace.py: _make_audit_fn       │
        │     (publisher=fan_out)                │
        └─────────────────┬──────────────────────┘
                          │ audit_event dict
                ┌─────────▼──────────┐
                │   fan_out(event)   │  ← compose_audit_subscribers
                │   - per-subscriber │
                │   - swallow errors │
                └────┬───────────┬──┘
                     │           │
       ┌─────────────▼┐         ┌▼──────────────┐
       │ comments_pub │         │ webhooks list │
       │  (existing)  │         │  (Phase C)    │
       └──────────────┘         └───────┬───────┘
                                        │
                            ┌───────────┼───────────┐
                            ▼           ▼           ▼
                     ┌────────┐  ┌─────────┐  ┌──────────┐
                     │ http-  │  │ slack-  │  │ disabled │
                     │ json   │  │ incoming│  └──────────┘
                     └────────┘  └─────────┘
```

### Webhook Protocol contract
```python
# workflows/code_review/webhooks/__init__.py

@dataclass(frozen=True)
class WebhookContext:
    """Workspace-scoped primitives a webhook needs at delivery time."""
    run_fn: Callable[..., Any] | None    # for shelling out (e.g. curl) if needed
    now_iso: Callable[[], str]


@runtime_checkable
class Webhook(Protocol):
    name: str

    def deliver(self, audit_event: dict[str, Any]) -> None: ...

    def matches(self, audit_event: dict[str, Any]) -> bool: ...
```

### Schema changes
```yaml
# New top-level block (optional)
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
      enabled: {type: boolean}     # default true
      url: {type: string}
      events:
        type: array
        items: {type: string}      # glob: "*", "run_*", "merge_and_promote"
      headers:
        type: object
        additionalProperties: {type: string}
      timeout-seconds: {type: integer, minimum: 1}
      retry-count: {type: integer, minimum: 0}
```

### Audit-event payload shape (already defined by `_make_audit_fn`)
```json
{
  "at": "2026-04-26T12:34:56Z",
  "action": "run_claude_review",
  "summary": "Internal review queued for issue #42",
  "issueNumber": 42,
  "headSha": "abc123"
}
```

`http-json` webhook POSTs this dict verbatim. `slack-incoming` reformats:
```json
{
  "text": "[code-review] run_claude_review — Internal review queued for issue #42",
  "blocks": [
    {"type": "section", "text": {"type": "mrkdwn", "text": "*run_claude_review*\nInternal review queued for issue #42"}},
    {"type": "context", "elements": [{"type": "mrkdwn", "text": "issue #42 · `abc123` · 2026-04-26T12:34:56Z"}]}
  ]
}
```

### Event-filter glob semantics
Match an audit event's `action` field against each glob in `events:`:
- `*` → match all
- `run_*` → prefix match (`run_claude_review`, `run_internal_review`, etc.)
- `merge_and_promote` → exact match
- `*_review` → suffix match
- Multiple globs OR'd

When `events:` is omitted from config ⇒ implicit `["*"]`.

### Composing subscribers
```python
# workflows/code_review/webhooks/__init__.py

def compose_audit_subscribers(
    subscribers: list[Callable[[dict], None]],
) -> Callable[[Any], None]:
    """Fan-out callable matching the publisher contract used by
    _make_audit_fn: publisher(action, summary, extra=...).

    Each subscriber receives a fully-built audit_event dict. Per-subscriber
    exceptions are caught and swallowed so one bad subscriber cannot break
    others or affect workflow execution.
    """
```

The existing `comments_publisher` (which currently takes `action=, summary=, extra=`) is wrapped to consume the `audit_event` dict instead. Workspace builder composes `[comments_publisher_wrapper, *webhook_subscribers]` and passes the result to `_make_audit_fn(publisher=...)`.

### Workspace integration
```python
# workspace.py around line 560
_comments_pub = _make_comment_publisher(...)  # existing
_webhooks = build_webhooks(yaml_cfg.get("webhooks") or [], run_fn=_run)
_subscribers = []
if _comments_pub:
    _subscribers.append(_wrap_legacy_publisher(_comments_pub))
for wh in _webhooks:
    _subscribers.append(_wrap_webhook(wh))   # filters by wh.matches() inside
_publisher = compose_audit_subscribers(_subscribers)
audit = _make_audit_fn(audit_log_path=audit_log_path, publisher=_publisher)
```

## Migration path for live `yoyopod` workspace

Live `~/.hermes/workflows/yoyopod/config/workflow.yaml` does NOT have a `webhooks:` block. After this PR:
- Schema treats `webhooks:` as optional ⇒ existing config still validates.
- `build_webhooks([])` returns an empty list ⇒ no behavior change.
- `compose_audit_subscribers([_comments_pub])` is a single-element fan-out ⇒ behavior preserved.

To opt in, operator adds:
```yaml
webhooks:
  - name: notify-slack
    kind: slack-incoming
    url: https://hooks.slack.com/services/T.../B.../...
    events: ["merge_and_promote", "operator_attention_required"]
```

## Tests

New file `tests/test_webhooks_phase_c.py`:
- `test_webhook_protocol_kinds_registered` — `http-json`, `slack-incoming`, `disabled` all in `_WEBHOOK_KINDS`.
- `test_build_webhooks_empty_list_returns_empty` — `[]` ⇒ `[]`, no errors.
- `test_build_webhooks_unknown_kind_raises` — `ValueError` with kind list.
- `test_http_json_webhook_posts_payload_to_url` — mocked `urllib.request.urlopen`, asserts URL + JSON body + Content-Type header.
- `test_http_json_webhook_includes_custom_headers` — `headers:` config appears in the request.
- `test_http_json_webhook_retries_on_failure` — `retry-count: 2` ⇒ urlopen called 3 times total when all fail.
- `test_http_json_webhook_no_retry_on_success` — succeeds on first try ⇒ no retries.
- `test_slack_incoming_payload_shape` — POSTed JSON has `text`, `blocks` keys; blocks include action + summary.
- `test_disabled_webhook_does_not_call_urlopen` — `kind: disabled` ⇒ urlopen never called.
- `test_disabled_webhook_via_enabled_false` — `enabled: false` overrides any kind.
- `test_event_filter_glob_matches_exact` — `events: ["run_claude_review"]` matches that action only.
- `test_event_filter_glob_matches_prefix` — `events: ["run_*"]` matches all `run_*` actions.
- `test_event_filter_omitted_defaults_to_all` — no `events:` key ⇒ all events match.
- `test_event_filter_no_match_skips_delivery` — non-matching action ⇒ urlopen not called.
- `test_compose_audit_subscribers_fans_out` — 3 subscribers each receive the same event.
- `test_compose_audit_subscribers_isolates_exceptions` — subscriber 1 raises ⇒ subscribers 2 & 3 still called.
- `test_compose_audit_subscribers_signature_matches_publisher` — accepts `(action=, summary=, extra=)`.

New file `tests/test_webhooks_schema.py`:
- `test_schema_accepts_empty_webhooks_array`
- `test_schema_accepts_http_json_webhook`
- `test_schema_accepts_slack_incoming_webhook`
- `test_schema_rejects_unknown_kind`
- `test_schema_rejects_extra_properties_on_subscription`
- `test_existing_yoyopod_workflow_yaml_still_validates`

Existing 477 stay green. Target: ~477 + 23 new = ~500 passing. (Coincidental match with Phase B target — Phase C branches from main, not from Phase B.)

## Open questions

None. Locked in:
- Outbound only; inbound deferred.
- Fire-and-forget with inline retry; no persistent queue.
- Three built-in kinds: `http-json`, `slack-incoming`, `disabled`.
- Stdlib-only delivery (`urllib.request`).
- Schema is `additionalProperties: false` per subscription (catch operator typos like `urls:` vs `url:`).
