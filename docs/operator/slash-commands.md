# Daedalus slash command catalog

Quick reference for the two slash commands the plugin registers in Hermes:
`/daedalus` (engine + service control) and `/workflow` (per-workflow operations).

For the operator playbook ("when something looks wrong, do X"), see
`docs/operator/cheat-sheet.md`. This file is a flat catalog: every command,
grouped by purpose, with a one-line description.

Workflow-specific commands are grouped by workflow below. Do not assume every
workflow exposes the richer `change-delivery` command surface.

## `/daedalus` — engine + service control

### Inspection (read-only)

| Command | What it does |
|---|---|
| `/daedalus status` | Runtime row + lane count + paths (DB, event log) |
| `/daedalus doctor` | Full health check across all subsystems |
| `/daedalus events` | Query the durable engine event ledger |
| `/daedalus events stats` | Count durable events by type/severity and show retention posture |
| `/daedalus events prune` | Apply explicit or `WORKFLOW.md` event retention immediately |
| `/daedalus runs` | Inspect durable engine run history |
| `/daedalus shadow-report` | `change-delivery` shadow-mode action proposal vs active/runtime state |
| `/daedalus active-gate-status` | Active-execution gate state and blockers |

### Inspection output format

All inspection commands default to a structured human-readable panel.
Pass `--format json` (or the legacy `--json` alias) for machine-readable JSON.
ANSI color is auto-detected via `sys.stdout.isatty()` and respects the
`NO_COLOR` environment variable.

#### Example: `/daedalus status`

```
Daedalus runtime — <owner>-<repo>-<workflow-type>
  state    running (active mode)
  owner    daedalus-active-<owner>-<repo>-<workflow-type>
  schema   v3
  paths
    db          ~/.hermes/workflows/<owner>-<repo>-<workflow-type>/runtime/state/daedalus/daedalus.db
    events      ~/.hermes/workflows/<owner>-<repo>-<workflow-type>/runtime/memory/daedalus-events.jsonl
  heartbeat
    last        22:43:01 UTC (17s ago)
  lanes
    total       14
```

#### Example: `/daedalus active-gate-status`

```
Active execution gate
  ✓ ownership posture  primary_owner = daedalus
  ✓ active execution   enabled
  ✓ runtime mode       running in active
  ✓ previous scheduler retired (engine_owner = hermes)

→ gate is open: actions can dispatch
```

When blocked:

```
Active execution gate
  ✓ ownership posture  primary_owner = daedalus
  ✗ active execution   DISABLED  set via /daedalus set-active-execution --enabled true
  ✓ runtime mode       running in active
  ✓ previous scheduler retired (engine_owner = hermes)

→ gate is BLOCKED: no actions will dispatch
```

#### Example: `/daedalus doctor`

```
Daedalus doctor
  ✓ overall  PASS
  checks
    ✓ missing_lease       Runtime lease present
    ✓ shadow_compatible   Shadow decision matches active policy
    ✓ active_execution_failures  No active execution failures
```

#### Example: `/daedalus shadow-report`

```
Daedalus shadow-report
  runtime
    state           running (active mode)
  owner           daedalus-active-<owner>-<repo>-<workflow-type>
    heartbeat       22:43:01 UTC (17s ago)
    lease expires   22:44:00 UTC (in 42s)
  ownership
    primary owner       daedalus
    daedalus primary    yes
    ✓ active execution  yes
    ✓ gate allowed      yes
  service
    mode        active
    installed   yes
    enabled     yes
    active      yes
  active lane
    issue     #329
    lane id   lane-329
    state     under_review / pass / pending
  next action
    workflow      publish_pr   head-clean
    daedalus      publish_pr   head-clean
    ✓ compatible  yes
```

#### Example: `/daedalus service-status`

```
Daedalus service
  service  daedalus-active@<owner>-<repo>-<workflow-type>.service
  mode     active
  install state
    ✓ installed   yes
    ✓ enabled     yes
    ✓ active      yes
  runtime
    pid   12345
  paths
    unit  ~/.config/systemd/user/daedalus-active@.service
```

### Operational control

| Command | What it does |
|---|---|
| `/daedalus start` | Bootstrap runtime row + emit start event |
| `/daedalus run-active` | Supervised active service loop (use systemd; not this directly) |
| `/daedalus run-shadow` | Shadow loop (use systemd; not this directly) |
| `/daedalus iterate-active` | One tick of the active loop |
| `/daedalus iterate-shadow` | One tick of the shadow loop |
| `/daedalus set-active-execution` | Enable/disable active dispatch |

### State management

| Command | What it does |
|---|---|
| `/daedalus init` | Init/migrate the runtime DB (idempotent) |
| `/daedalus bootstrap` | Infer repo root + GitHub slug from the current checkout, create a workflow state root, write the repo-owned workflow contract, and persist a repo-local workflow pointer |
| `/daedalus scaffold-workflow` | Create a new workflow root named `<owner>-<repo>-<workflow-type>` and write the repo-owned workflow contract |
| `/daedalus ingest-live` | Pull workflow CLI status into the ledger |
| `/daedalus heartbeat` | Refresh the runtime lease |
| `/daedalus request-active-actions` | Inspect what *would* be dispatched on the next tick |
| `/daedalus execute-action` | Manually execute a queued action |
| `/daedalus analyze-failure` | Run failure analyst on a specific failure id |

### Systemd supervision

| Command | What it does |
|---|---|
| `/daedalus service-up` | Validate `WORKFLOW.md`, then install, enable, and start the user unit |
| `/daedalus service-install` | Install the user unit only |
| `/daedalus service-uninstall` | Stop + remove the user unit |
| `/daedalus service-start` | Start `daedalus-active@<workspace>.service` |
| `/daedalus service-stop` | Stop the service |
| `/daedalus service-restart` | Restart the service |
| `/daedalus service-enable` | Enable on boot |
| `/daedalus service-disable` | Disable on boot |
| `/daedalus service-status` | systemd status snapshot |
| `/daedalus service-logs` | Last N journal entries |
| `/daedalus codex-app-server install` | Write the shared Codex app-server user unit |
| `/daedalus codex-app-server up` | Install, enable, and start the shared Codex app-server |
| `/daedalus codex-app-server status` | Show unit status plus `GET /readyz` readiness |
| `/daedalus codex-app-server doctor` | Diagnose managed/external listener health, auth posture, and Codex thread mappings |
| `/daedalus codex-app-server restart` | Restart the Codex app-server unit |
| `/daedalus codex-app-server logs` | Last N Codex app-server journal entries |
| `/daedalus codex-app-server down` | Stop and disable Codex app-server |

### Cutover / migration (one-shot operator commands)

| Command | What it does |
|---|---|
| `/daedalus migrate-filesystem` | Rename relay-era state files to daedalus paths |
| `/daedalus migrate-systemd` | Replace relay-era unit files with daedalus templates |

### Observability

| Command | What it does |
|---|---|
| `/daedalus watch` | Live operator TUI (lanes + alerts + recent events) |
| `/daedalus watch --once` | Render one frame and exit (works in pipes) |
| `/daedalus set-observability --workflow <name> --github-comments on\|off\|unset` | Set/clear runtime override for a workflow's GitHub-comment publishing |
| `/daedalus get-observability --workflow <name>` | Show effective observability config + which layer (default/yaml/override) won |

## `/workflow` — per-workflow operations

|| Command | What it does |
|---|---|---|
|| `/workflow` | List installed workflows |
|| `/workflow <name>` | Show that workflow's `--help` |
|| `/workflow <name> <cmd> [args]` | Route to that workflow's CLI |

### `change-delivery` workflow shortcuts (the common ones)

This is the opinionated managed SDLC workflow.

|| Command | What it does |
|---|---|---|
|| `/workflow change-delivery status` | Lane state + `nextAction` |
|| `/workflow change-delivery tick` | One workflow tick |
|| `/workflow change-delivery show-active-lane` | Current active GitHub issue |
|| `/workflow change-delivery show-lane-state` | `.lane-state.json` contents |
|| `/workflow change-delivery show-lane-memo` | `.lane-memo.md` contents |
|| `/workflow change-delivery dispatch-implementation-turn` | Force a coder turn |
|| `/workflow change-delivery dispatch-claude-review` | Force an internal Claude review |
|| `/workflow change-delivery publish-ready-pr` | Force PR publish |
|| `/workflow change-delivery merge-and-promote` | Force merge + promote next lane |
|| `/workflow change-delivery reconcile` | Repair stale ledger state |
|| `/workflow change-delivery pause` | Disable lane processing |
|| `/workflow change-delivery resume` | Re-enable |
|| `/workflow change-delivery serve` | Run the optional localhost HTTP status server |

### `issue-runner` workflow shortcuts

This is the bundled generic tracker-driven workflow.

|| Command | What it does |
|---|---|---|
|| `/workflow issue-runner status` | Selected issue + last run summary |
|| `/workflow issue-runner doctor` | Validate tracker, workspace, and runtime references |
|| `/workflow issue-runner tick` | Run one synchronous issue-runner dispatch tick |
|| `/workflow issue-runner run` | Run the supervised long-lived issue-runner polling loop |
|| `/workflow issue-runner serve` | Run the optional localhost HTTP status server |

### Webhook commands

|| Command | What it does |
|---|---|---|
|| `/workflow change-delivery webhooks status` | Show configured webhook subscribers |
|| `/workflow change-delivery webhooks test` | Fire a test event to all webhooks |

### Comments commands

|| Command | What it does |
|---|---|---|
|| `/workflow change-delivery comments status` | Show comment publisher state |
|| `/workflow change-delivery comments sync` | Force a comment sync for current lane |

## Most useful day-to-day, in order

1. `/daedalus watch` — live overview of every active lane in one frame
2. `/daedalus doctor` — overall health
3. `/workflow <name> status` — workflow-specific current state
4. `/daedalus service-logs` — last 50 journal entries from the active service
5. `/workflow change-delivery tick` or `/workflow issue-runner tick` — manually fire a tick when impatient

## Notes

- All `/daedalus` subcommands accept `--workflow-root <path>` (default: detected from the cwd or `DAEDALUS_WORKFLOW_ROOT` env var).
- A few commands accept `--json` (`status`, `ingest-live`, `request-active-actions`); per-workflow CLI commands also accept `--json` where the underlying workflow supports it.
- The output format is currently terse `key=value` strings. Improving readability is tracked in the Daedalus repo's issue tracker.
