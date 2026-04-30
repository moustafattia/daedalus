# GitHub Comments

Daedalus can publish audit events as **comments on the active PR** (or issue, if no PR exists). This creates a visible audit trail in GitHub itself, so anyone browsing the issue/PR can see what the workflow did and when.

---

## Why comments

- **Transparency:** Humans see workflow activity without running CLI commands.
- **Debugging:** When something goes wrong, the comment history shows exactly which actions ran.
- **Low friction:** No separate dashboard needed — the audit trail lives where the work happens.

---

## Enable it

```yaml
observability:
  github-comments:
    enabled: true
    mode: edit-in-place
    include-events:
      - dispatch-implementation-turn
      - internal-review-completed
      - publish-ready-pr
      - push-pr-update
      - merge-and-promote
      - operator-attention-transition
      - operator-attention-recovered
```

### Resolution precedence

1. **Override file** (`observability-overrides.json`) — set via `/daedalus set-observability`
2. **`WORKFLOW.md`** `observability:` block
3. **Hardcoded defaults** (everything off)

See [observability.md](observability.md) for the full override surface.

---

## Modes

| Mode | Behavior | Best for |
|---|---|---|
| `edit-in-place` | One comment per lane; edits it as events arrive. | Clean, compact history. |
| `append` | New comment for every event. | Full audit trail (noisy). |

### `edit-in-place` detail

The publisher:
1. Checks if a Daedalus comment already exists on the PR/issue.
2. If yes: edits the existing comment, appending the new event.
3. If no: creates a new comment with a header like `<!-- daedalus-comment -->`.

The HTML comment marker makes it easy to find the comment later without relying on exact text matching.

---

## Event filtering

Only events in `include-events` are published. The default whitelist matches design spec §5:

```python
_DEFAULT_INCLUDE_EVENTS = [
    "dispatch-implementation-turn",
    "internal-review-completed",
    "publish-ready-pr",
    "push-pr-update",
    "merge-and-promote",
    "operator-attention-transition",
    "operator-attention-recovered",
]
```

An **empty list** (`[]`) means firehose — every audit action is rendered. This is useful for debugging only.

---

## Comment format

```markdown
<!-- daedalus-comment -->
**Daedalus audit trail**

| Time | Action | Detail |
|---|---|---|
| 14:03 | dispatch-implementation-turn | coder-claude-1, 1342→506 tokens |
| 14:15 | internal-review-completed | pass with 2 findings |
| 14:20 | publish-ready-pr | PR #123 created |
```

The table is truncated at ~50 rows to avoid GitHub's comment length limits. Older events are archived to the JSONL log, not lost.

---

## Operator commands

```text
/daedalus set-observability --workflow change-delivery --github-comments on
/daedalus set-observability --workflow change-delivery --github-comments off
/daedalus get-observability --workflow change-delivery
```

`get-observability` shows:
- Effective config
- Which layer won (default / yaml / override)
- Current mode and event whitelist

---

## Failure handling

- **Comment API failure:** Logged, not retried. The next event will attempt to edit/create again.
- **PR doesn't exist yet:** Comment is queued in memory and published once the PR exists.
- **Rate limited:** GitHub API rate limits are respected; the publisher backs off and retries on the next tick.

---

## Where this lives in code

- Comment formatting: `daedalus/workflows/change_delivery/comments.py`
- Comment publishing: `daedalus/workflows/change_delivery/comments_publisher.py`
- Observability config: `daedalus/workflows/change_delivery/observability.py`
- Override surface: `daedalus/observability_overrides.py`
- Tests: `tests/test_workflow_change_delivery_comments_*.py`
