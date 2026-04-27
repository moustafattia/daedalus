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
