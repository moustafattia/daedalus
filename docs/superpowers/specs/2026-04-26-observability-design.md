# Operator Observability — Design Spec

**Issue:** [moustafattia/daedalus #1](https://github.com/moustafattia/daedalus/issues/1)
**Date:** 2026-04-26
**Status:** Approved (auto-mode)

## 1. Problem

While running an end-to-end test of three lanes today the question "what is Daedalus doing right now?" required reading five different sources (systemd journal, `daedalus-events.jsonl`, `workflow-audit.jsonl`, `.lane-state.json`, acpx codex sessions). There is no single human-readable surface for either:
- **retrospective** — "what happened on this lane?" (audit trail)
- **live** — "what is happening on this lane right now?" (live ops view)

## 2. Goals (in scope)

1. **GitHub ticket comments** — workflow CLI publishes lifecycle events as a single editable bot-comment on the active lane's GitHub issue. Off by default. Per-workflow config + per-workflow formatter.
2. **Operator override** — slash-command kill switch (`/daedalus set-observability`) that overrides workflow.yaml without an edit-and-redeploy cycle.
3. **Live TUI** — `/daedalus watch` renders a live operator view sourced from existing event streams (no new persistence).

## 3. Non-goals (out of scope)

- Webhook-driven push (Phase 3 in the issue — defer until polling latency proves insufficient).
- Comment thread per event (we use single editable bot-comment to keep ticket noise low).
- Alerting via external channels (Telegram/email) — `daedalus-alert-state.json` already covers that; we only surface its state in the TUI banner.
- Cross-workflow comment formatter (each workflow owns its own).

## 4. Architecture decisions (responding to the issue's open questions)

### 4.1 Where comments come from: workflow CLI, not Daedalus core

**Daedalus core stays out of the GitHub-comment business.** Core only sees generic events (`lane_action_dispatched`, `lane_action_completed`, `failure_recorded`, `runtime_lease_renewed`) — those are right primitives for an *engine* but they don't carry workflow-specific meaning.

The workflow CLI already has the `audit(action, summary, **extra)` primitive emitting at every lifecycle insertion point in `workflows/code_review/actions.py`:
- `actions.py:66` — `publish-ready-pr`
- `actions.py:99` — `push-pr-update`
- `actions.py:147` — `merge-and-promote`
- `actions.py:310` — `dispatch-implementation-turn`
- `actions.py:487` — `request-internal-review`

Plus `orchestrator.py:334` (`reconcile`), `orchestrator.py:540` (review transitions), and `workspace.py:412` (`audit` definition). These are the natural emit sites — they already know issue number, PR number, SHAs, review verdict.

**Implication:** comments emitted from `workflows/code_review/comments.py` (new). Daedalus core gains no GitHub knowledge.

### 4.2 Per-workflow customization: formatter + config in workflow package

- **Formatter**: `workflows/<name>/comments.py`. A future testing workflow has its own formatter keyed off its own audit-event vocabulary.
- **Config**: top-level `observability:` block in `workflow.yaml`, schema-validated by `workflows/<name>/schema.yaml`.

### 4.3 Failure handling: emit only on operator-attention transition

The signal already exists — `health.py:126` (`lane_operator_attention_reasons`) and `status.py:693` (`workflowState = "operator_attention_required"`). Routine retries inside the `lane-failure-retry-budget` window stay silent. The comment fires only when the lane state transitions *into* `operator_attention_required` (with a one-line reason from `lane_operator_attention_reasons`), and once when it transitions *out* (`✅ Recovered — resuming`).

### 4.4 Live alerts via `/daedalus watch`

Single live-ops surface, not a separate `daedalus dashboard` subcommand. Reads existing sources only:
- `runtime/memory/daedalus-events.jsonl`
- SQLite `runtime/state/daedalus/daedalus.db` (lanes + lane_actors)
- `runtime/memory/workflow-audit.jsonl`
- `acpx codex sessions show <session>`
- `runtime/memory/daedalus-alert-state.json` (surfaced as banner)

`rich.live` polling at ~2s. No new persistence.

## 5. Configuration schema

New top-level `observability:` block in workflow.yaml:

```yaml
observability:
  github-comments:
    enabled: false                       # opt-in; default off
    mode: edit-in-place                  # only mode supported in Phase 1
    include-events:                      # whitelist of audit actions to render
      - dispatch-implementation-turn
      - codex-committed                  # synthesized from dispatch-implementation-turn 'committed' branch
      - internal-review-completed
      - publish-ready-pr
      - push-pr-update
      - merge-and-promote
      - operator-attention-transition
    suppress-transient-failures: true    # only emit failure on operator-attention transition
```

Schema entry (added to `workflows/code_review/schema.yaml`):

```yaml
observability:
  type: object
  properties:
    github-comments:
      type: object
      required: [enabled]
      properties:
        enabled: {type: boolean}
        mode: {type: string, enum: [edit-in-place]}
        include-events:
          type: array
          items: {type: string}
        suppress-transient-failures: {type: boolean}
```

Defaults when block absent: `enabled=false` (back-compat).

## 6. Operator override (kill switch)

State file: `<workflow_root>/runtime/state/daedalus/observability-overrides.json`:

```json
{
  "code-review": {
    "github-comments": {
      "enabled": false,
      "set-at": "2026-04-26T20:30:00Z",
      "set-by": "operator-cli"
    }
  }
}
```

Slash commands:

```
/daedalus set-observability --workflow code-review --github-comments off
/daedalus set-observability --workflow code-review --github-comments on
/daedalus set-observability --workflow code-review --github-comments unset    # remove override
/daedalus get-observability --workflow code-review                             # show effective state
```

Resolution order (highest precedence first):
1. Override file (`enabled: true|false`)
2. workflow.yaml `observability.github-comments.enabled`
3. Default (`false`)

## 7. Comment publisher mechanics (Phase 1)

### 7.1 Lifecycle

For each tick:
1. Workflow CLI calls `audit("dispatch-implementation-turn", ...)` (or other action)
2. Audit hook reads effective observability config (override > yaml > default)
3. If `enabled` and action ∈ `include-events`:
   - Resolve active lane issue number from current status
   - Look up bot-comment id from `runtime/state/lane-comments/<issue>.json`
   - If absent: `gh issue comment <issue> --body <rendered>` and capture `comment_id` from response URL
   - If present: `gh api -X PATCH /repos/<slug>/issues/comments/<id> -f body=<rendered>` (edit in place)
   - Write back `{comment_id, last_rendered_text, last_updated, last_action}` to the per-issue state file

### 7.2 Render format

Single comment, edit-in-place. Top section is a 1-line header, body is a chronological event log (most recent first):

```markdown
🤖 **Daedalus lane status** — lane #329 · `under_review`

| Time (UTC) | Event | Detail |
|---|---|---|
| 22:30:34 | 🔁 Repair turn dispatched | gpt-5.3-codex-spark (lane-329) |
| 22:25:43 | 🔍 Internal review | PASS_WITH_FINDINGS — 6 findings |
| 22:11:11 | 🔍 Internal review started | claude-sonnet-4-6 |
| 21:58:28 | ✅ Codex committed `cea697f4` | 1 file +N/-M |
| 21:50:02 | 🔄 Codex coder dispatched | gpt-5.3-codex-spark/high |

_Last update: 2026-04-26 22:30:34 UTC · auto-generated by Daedalus_
```

No per-event headers — the top emoji-line of each row is the per-event marker.

Maximum row count: 50 (oldest rows truncate). Keeps the comment under GitHub's 65535-byte limit safely.

### 7.3 Idempotency

The state file `runtime/state/lane-comments/<issue>.json` carries `last_rendered_text`. Before issuing the API call we compare `rendered == last_rendered_text` and skip if identical (prevents API spam from no-op ticks).

### 7.4 Failure mode

If `gh` returns nonzero or the API call fails:
1. Log to `runtime/memory/daedalus-events.jsonl` as `observability_publish_failed`
2. Do NOT raise — the comment is observability, not correctness
3. Retry on next audit hook fire

Failures never block the workflow tick.

### 7.5 Operator-attention failure rendering

When `workflowState` transitions `→ operator_attention_required`:
- Append a row: `⚠️ **Operator attention required** — <reason from lane_operator_attention_reasons>`
- Set sticky header: `🤖 **Daedalus lane status** — lane #N · ⚠️ operator-attention`

When transitioning out (back to `running` / similar):
- Append: `✅ Recovered — resuming`
- Restore normal header

## 8. `/daedalus watch` TUI (Phase 2)

### 8.1 Layout

```
┌─ Daedalus active lanes ─────────────────────────────────── [paused] ┐
│ #329 [Arch] Cross-screen UI overlays              effort:large       │
│   ├ State          under_review                                       │
│   ├ Codex          lane-329  healthy  fresh:42s  6 history entries    │
│   ├ Internal       PASS_WITH_FINDINGS  6 findings  (1 cycle used)     │
│   ├ Local head     a3e8c23 (2 commits ahead)                          │
│   └ Next action    publish_ready_pr  reason=local-head-cleared        │
│                                                                       │
│ ⚠️  Active alerts (1)                                                  │
│   – outage: dispatch-implementation-turn last success 12m ago         │
│                                                                       │
│ Recent events                                                         │
│ 22:30:34  workflow  dispatch_implementation_turn  → committed         │
│ 22:25:43  workflow  internal_review_completed  PASS_WITH_FINDINGS=6   │
│ 22:11:11  workflow  internal_review_started                           │
│ 21:58:28  workflow  codex_committed  cea697f4                         │
│ 21:50:02  daedalus  lane_action_dispatched  dispatch_impl_turn        │
└── q quit · p pause · j/k filter lane · J dump frame ────────────────┘
```

### 8.2 Data sources (read-only, no new persistence)

| Field | Source |
|---|---|
| Active lanes | `daedalus.db` `lanes` table |
| State / next action | `workflow-audit.jsonl` last 50 rows + DB lane row |
| Codex session health | `acpx codex sessions show <session> --json` |
| Local head | `git rev-parse HEAD` in lane worktree |
| Active alerts banner | `daedalus-alert-state.json` |
| Recent events stream | `daedalus-events.jsonl` + `workflow-audit.jsonl` (interleaved by timestamp) |

### 8.3 Polling

`rich.live` at 2-second tick. Each source poll is independent; if one source is slow or fails its panel shows `[stale]` instead of blocking the whole frame.

### 8.4 Hot keys

- `q` quit
- `p` pause autoscroll
- `j` / `k` filter to specific lane (cycle through active lanes)
- `J` dump current frame state as JSON to stdout (for piping into a debug log)

### 8.5 No-TTY fallback

If stdout is not a TTY (e.g. piped, captured by systemd journal), render once as plain text and exit 0. This makes `daedalus watch | grep something` work and prevents weird behavior in non-interactive shells.

## 9. Module layout

```
workflows/code_review/
  comments.py              # NEW — render audit events to comment text
  observability.py         # NEW — config resolution (override > yaml > default)
  schema.yaml              # MODIFIED — add observability block

workflows/code_review/workspace.py    # MODIFIED — wrap audit_fn to call comments publisher

# Daedalus core
observability_overrides.py # NEW — read/write override file
schemas.py                 # MODIFIED — add /daedalus set-observability + get-observability + watch
tools.py                   # MODIFIED — entry points for the new subcommands
watch.py                   # NEW — TUI renderer

tests/
  test_workflow_comments.py             # NEW — formatter unit tests
  test_workflow_comment_publisher.py    # NEW — gh-mocked integration tests
  test_observability_overrides.py       # NEW — override resolution + persistence
  test_daedalus_watch.py                # NEW — TUI snapshot tests (no live render)
```

## 10. Test strategy

### 10.1 Unit tests
- `comments.py` formatter: input audit-event dict → rendered table row. Pure-function, no I/O.
- `observability.py` resolver: (yaml, override) → effective config. Pure-function.
- `observability_overrides.py`: file read/write round-trip + atomic write.

### 10.2 Integration tests (mocked subprocess)
- Comment publisher: monkeypatch `subprocess.run` to capture `gh` calls; verify create-then-edit lifecycle.
- `set-observability` slash command: invoke handler, read override file, verify schema.

### 10.3 TUI tests
- Snapshot test of rendered frame against fixture event sources. No live render — invoke the renderer with frozen clock.
- No-TTY fallback: redirect stdout to non-TTY, verify single-shot render + exit 0.

## 11. Backward compatibility

- `observability:` block absent in workflow.yaml → resolves to `enabled: false`. No behavioral change.
- Override file absent → no override, falls through to yaml.
- Live YoYoPod workspace (`/home/radxa/.hermes/workflows/yoyopod/config/workflow.yaml`) is not modified. Operator flips `enabled: true` manually after Phase 1 ships.
- No DB schema migration. No new state files until first comment fires.

## 12. Rollout

1. Land Phase 1 (comment publisher + opt-in flag + override commands) — closes core of issue
2. Operator flips `enabled: true` on YoYoPod live workspace via `/daedalus set-observability`
3. Run one lane end-to-end with comments active; iterate on formatter if needed
4. Phase 2 (`watch` TUI) ships in same PR if it fits, else fast-follow

## 13. Acceptance criteria

- [ ] `observability:` schema validates in workflow.yaml; tests cover present + absent + invalid
- [ ] `enabled: false` (default) → no `gh` calls, no state file writes
- [ ] `enabled: true` → bot-comment created on first audit, edited on subsequent audits, single comment per issue
- [ ] Operator-attention transition fires comment update with sticky `⚠️` header; recovery clears it
- [ ] `/daedalus set-observability --github-comments off` mutes a workflow that's `enabled: true` in yaml
- [ ] `/daedalus get-observability` shows effective config + source (override / yaml / default)
- [ ] `/daedalus watch` renders live frame; gracefully degrades on missing data sources
- [ ] No-TTY fallback: `/daedalus watch | head` produces plain text, exits 0
- [ ] All existing tests pass; new tests added per §10
- [ ] No behavioral change for YoYoPod live workspace until operator flips override
