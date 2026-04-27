---
name: operator
description: Operate the YoYoPod Daedalus project plugin control surface for status checks and shadow-runtime commands.
version: 0.1.0
author: Hermes Agent
license: MIT
---

# Daedalus Operator

Use this when the YoYoPod workflow repo-local `daedalus` plugin is enabled.

## Enable project plugin discovery

Run Hermes from the YoYoPod workflow root with:

```bash
export HERMES_ENABLE_PROJECT_PLUGINS=true
cd ~/.hermes/workflows/yoyopod
hermes
```

## Available slash command

Inside Hermes sessions:

```text
/daedalus status
/daedalus shadow-report
/daedalus doctor
/daedalus active-gate-status
/daedalus set-active-execution --enabled true
/daedalus set-active-execution --enabled false
/daedalus service-install
/daedalus service-install --service-mode active
/daedalus service-status
/daedalus service-status --service-mode active
/daedalus service-start
/daedalus service-start --service-mode active
/daedalus service-stop
/daedalus service-stop --service-mode active
/daedalus service-restart
/daedalus service-logs --lines 50
/daedalus service-logs --service-mode active --lines 50
/daedalus start --instance-id relay-operator-1
/daedalus heartbeat --instance-id relay-operator-1
/daedalus iterate-shadow --instance-id relay-operator-1
/daedalus run-shadow --instance-id relay-operator-1 --max-iterations 1 --json
/daedalus iterate-active --instance-id relay-operator-1 --json
/daedalus run-active --instance-id relay-operator-1 --max-iterations 1 --json
```

## Notes

- Default workflow root is the current YoYoPod workflow repo.
- Use `--workflow-root` to point at a different test root.
- Service commands default to the shadow observer profile. Add `--service-mode active` to manage the guarded executor profile (`daedalus-active@yoyopod.service`).
- `service-install` resolves profile defaults automatically:
  - shadow: `daedalus-shadow@yoyopod.service` + `relay-shadow-service-1` + `run-shadow`
  - active: `daedalus-active@yoyopod.service` + `relay-active-service-1` + `run-active`
- `run-shadow` remains shadow-only: it derives and records actions but does not execute active side effects.
- `iterate-active` / `run-active` are guarded: they will only execute actions when Daedalus active execution is enabled, the runtime is in `active` mode, and current Daedalus-vs-wrapper parity is still compatible.
- `set-active-execution --enabled true|false` toggles the guarded executor directly. Pair it with the supervised active service when you want a real executor instead of manual active runs.
- The plugin also registers a CLI command tree for future compatibility, but the reliable operator surface in the current Hermes build is the slash command.

## Configurable Lane Selection

Daedalus picks "the next issue to promote to active lane" via `pick_next_lane_issue`.
Default behavior: any open issue not yet labeled `active-lane`, sorted by `[P1]/[P2]`
title priority, then issue number ASC. To customize, add a `lane-selection:` block
to `workflow.yaml`:

```yaml
# Severity-priority routing example
lane-selection:
  require-labels:
    - needs-review              # only promote issues marked ready
  exclude-labels:
    - blocked                   # operator escape-hatch
    - do-not-touch
  priority:
    - severity:critical         # higher in list = higher priority
    - severity:high
    - severity:medium
  tiebreak: oldest              # within bucket: oldest createdAt wins
```

All five fields are optional. The `active-lane` label is auto-injected into
`exclude-labels` so the picker can never select an already-promoted lane.

`tiebreak` options: `oldest` (default), `newest`, `random`.

When `priority:` is configured, label priority becomes primary and the legacy
`[P1]`/`[P2]` title priority is demoted to a tertiary tiebreak. When `priority:`
is empty, title priority remains primary (full back-compat).

## Runtime + agent config (Phase A — runtime-agnostic)

Each agent role chooses a runtime, optionally a `command:` array, and optionally a `prompt:` template path.

**Runtime profile** declares a default invocation:

```yaml
runtimes:
  codex-acpx:
    kind: acpx-codex
    command: ["acpx", "--model", "{model}", "--cwd", "{worktree}",
              "codex", "prompt", "-s", "{session_name}", "{prompt_path}"]
    session-idle-freshness-seconds: 900
    session-idle-grace-seconds: 1800
    session-nudge-cooldown-seconds: 600
```

**Agent role** picks a runtime and optionally overrides `command:` (full replacement) and/or `prompt:` (template path):

```yaml
agents:
  coder:
    default:
      runtime: codex-acpx
      model: gpt-5
      # prompt: implied as <workspace>/config/prompts/coder.md,
      #         falls back to bundled prompts/coder.md
    high:
      runtime: codex-acpx
      model: gpt-5
      command: ["acpx", "--model", "{model}", "--cwd", "{worktree}",
                "codex", "prompt", "-s", "{session_name}",
                "--reasoning", "high", "{prompt_path}"]
```

**Placeholders** filled by the dispatcher:
- `{model}` — agent's `model:` value
- `{prompt_path}` — absolute path to the rendered prompt file
- `{worktree}` — lane worktree directory
- `{session_name}` — lane session identifier

**Prompt resolution order** (highest priority first):
1. `prompt:` on the agent role (absolute or relative to `<workspace>/config/`)
2. `<workspace>/config/prompts/<role>.md`
3. Bundled `workflows/code_review/prompts/<role>.md`

**Runtime kinds:**
- `acpx-codex` — persistent Codex sessions via `acpx`
- `claude-cli` — one-shot Claude CLI invocations
- `hermes-agent` — operator-supplied hermes-agent CLI; requires `command:` (no built-in invocation)

To swap a coder from Codex to Claude, change one line:

```yaml
agents:
  coder:
    default:
      runtime: claude-oneshot   # was: codex-acpx
      model: claude-sonnet-4
```

No code changes required.

## External reviewer config (Phase B — pluggable)

Pick a reviewer kind via `agents.external-reviewer.kind`:

```yaml
agents:
  external-reviewer:
    enabled: true
    name: ChatGPT_Codex_Cloud
    kind: github-comments         # default; reads PR review threads
    repo-slug: owner/repo         # optional; falls back to legacy hardcode
    cache-seconds: 300
    logins:
      - chatgpt-codex-connector[bot]
    clean-reactions: ["+1", "rocket", "heart", "hooray"]
    pending-reactions: ["eyes"]
```

**Kinds:**
- `github-comments` — reads PR review threads via `gh api graphql`. Configurable bot logins, clean/pending reactions, repo slug, cache TTL.
- `disabled` — no external review; placeholder review with `status: skipped`.

**`enabled: false`** is equivalent to `kind: disabled` regardless of any other field.

**Deprecated:** the top-level `codex-bot:` block (`logins`/`clean-reactions`/`pending-reactions`) is still honored as a fallback for one release. Move those keys inside `agents.external-reviewer:` to silence the deprecation path.

**Prompt overrides:** the repair-handoff prompt now lives at `workflows/code_review/prompts/external-reviewer-repair-handoff.md`. Drop a file at `<workspace>/config/prompts/external-reviewer-repair-handoff.md` to override it (Phase A resolution chain).

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

**Security:** webhook URLs MUST use `http://` or `https://`. Other schemes (file, gopher, ftp) are rejected at workspace setup. Audit events contain issue numbers, head SHAs, and branch names; choose webhook destinations carefully. `timeout-seconds` is capped at 30; `retry-count` at 5 — webhook delivery runs inline in the audit hook.

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

## Persisted-state migration (Phase D-1)

The workflow ledger renames two `reviews.*` keys for provider neutrality:
- `reviews.claudeCode` → `reviews.internalReview`
- `reviews.codexCloud` → `reviews.externalReview`

**Migration is automatic.** On workspace bootstrap, the engine rewrites the persisted ledger in place (atomic temp-file + rename). Idempotent: subsequent boots are no-ops.

**Back-compat reads.** For one release, code paths use a `get_review(reviews, new_key)` helper that falls back to the legacy key if the migration hasn't run yet (e.g., a stale process wrote an old key after migration).

**Action-type literal.** The transient action `run_claude_review` is renamed to `run_internal_review`. The dispatcher accepts both for one release.

**What this means for you:** nothing — the rename is transparent. If you write external tooling that reads the ledger directly (e.g., a dashboard parsing `yoyopod-workflow-status.json`), update it to use `reviews.internalReview` / `reviews.externalReview`.

## Deprecation cleanup (Phase D-2)

The one-release back-compat aliases introduced in Phases B / D-1 have been removed:
- `render_codex_cloud_repair_handoff_prompt` no longer importable — use `render_external_reviewer_repair_handoff_prompt`
- Top-level `codex-bot:` block in `workflow.yaml` is no longer honored — move `logins` / `clean-reactions` / `pending-reactions` into `agents.external-reviewer:`
- The `run_claude_review` action-type literal is no longer dispatched — only `run_internal_review`
- `get_review(reviews, key)` no longer falls back to legacy ledger keys — `migrate_persisted_ledger` already ran on D-1 boot
- 8 functions in `workflows/code_review/reviews.py` were renamed (`fetch_codex_cloud_review` → `fetch_external_review`, etc.); old names retained as one-release aliases

## Persisted-state migration round 2 (Phase D-3)

Five additional top-level ledger fields renamed for provider neutrality:
- `claudeRepairHandoff` → `internalReviewRepairHandoff`
- `codexCloudRepairHandoff` → `externalReviewRepairHandoff`
- `codexCloudAutoResolved` → `externalReviewAutoResolved`
- `interReviewAgentModel` → `internalReviewerModel`
- `lastClaudeVerdict` → `lastInternalVerdict`

Plus `claudeModel` is dropped entirely — its value lives in `internalReviewerModel` after the rename.

**Migration is automatic** on workspace bootstrap (atomic temp+rename, idempotent), same mechanism as Phase D-1.

**Read-both / write-new** for one release via the new `get_ledger_field(ledger, new_key)` helper.

**Status output keys also renamed** — external tooling that parsed `claudeModel` / `interReviewAgentModel` / `codexCloudAutoResolved` / `lastClaudeVerdict` from `yoyopod-workflow-status.json` should switch to the new names.

**Workspace internals.** Four `_codex_cloud_repair_handoff_*` shims in `workflows/code_review/workspace.py` renamed to `_external_review_repair_handoff_*`. Workspace-internal API; affects subagent test fixtures only.

## Deprecation cleanup round 2 (Phase D-4)

The Phase D-2 / D-3 one-release back-compat aliases have been removed:
- 8 D-2 module-level function aliases in `workflows/code_review/reviews.py` (`fetch_codex_cloud_review`, etc.) — gone. Use the `external_review` names.
- D-3 read-time legacy-key fallbacks in `get_ledger_field`, `reviews.py:308`, `workspace.py:504` — gone. Live ledgers were migrated by the D-3 bootstrap; restored backups still get migrated automatically before any read.
- Per-thread `"source": "codexCloud"` review-thread label is now `"externalReview"`. Threads are rebuilt from GitHub data each tick, so old labels self-heal.

Migration helpers (`migrate_review_keys`, `migrate_top_level_keys`, `migrate_persisted_ledger`) remain — they run idempotently on bootstrap and protect against stale state from backups.
