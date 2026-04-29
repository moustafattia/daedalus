# Daedalus slash command catalog

Quick reference for the two slash commands the plugin registers in Hermes:
`/daedalus` (engine + service control) and `/workflow` (per-workflow operations).

For the operator playbook ("when something looks wrong, do X"), see
`docs/operator-cheat-sheet.md`. This file is a flat catalog: every command,
grouped by purpose, with a one-line description.

## `/daedalus` — engine + service control

### Inspection (read-only)

| Command | What it does |
|---|---|
| `/daedalus status` | Runtime row + lane count + paths (DB, event log) |
| `/daedalus doctor` | Full health check across all subsystems |
| `/daedalus shadow-report` | Shadow-mode action proposal vs legacy comparison |
| `/daedalus active-gate-status` | Active-execution gate state and blockers |

### Inspection output format

All inspection commands default to a structured human-readable panel.
Pass `--format json` (or the legacy `--json` alias) for machine-readable JSON.
ANSI color is auto-detected via `sys.stdout.isatty()` and respects the
`NO_COLOR` environment variable.

#### Example: `/daedalus status`

```
Daedalus runtime — yoyopod
  state    running (active mode)
  owner    daedalus-active-yoyopod
  schema   v3
  paths
    db          ~/.hermes/workflows/yoyopod/runtime/state/daedalus/daedalus.db
    events      ~/.hermes/workflows/yoyopod/runtime/memory/daedalus-events.jsonl
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
  ✓ legacy watchdog    retired (engine_owner = hermes)

→ gate is open: actions can dispatch
```

When blocked:

```
Active execution gate
  ✓ ownership posture  primary_owner = daedalus
  ✗ active execution   DISABLED  set via /daedalus set-active-execution --enabled true
  ✓ runtime mode       running in active
  ✓ legacy watchdog    retired (engine_owner = hermes)

→ gate is BLOCKED: no actions will dispatch
```

#### Example: `/daedalus doctor`

```
Daedalus doctor
  ✓ overall  PASS
  checks
    ✓ missing_lease       Runtime lease present
    ✓ shadow_compatible   Shadow decision matches legacy
    ✓ active_execution_failures  No active execution failures
```

#### Example: `/daedalus shadow-report`

```
Daedalus shadow-report
  runtime
    state           running (active mode)
    owner           daedalus-active-yoyopod
    heartbeat       22:43:01 UTC (17s ago)
    lease expires   22:44:00 UTC (in 42s)
  ownership
    primary owner       daedalus
    relay primary       yes
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
    legacy        publish_pr   head-clean
    relay         publish_pr   head-clean
    ✓ compatible  yes
```

#### Example: `/daedalus service-status`

```
Daedalus service
  service  daedalus-active@yoyopod.service
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
| `/daedalus run-active` | Active loop (use systemd; not this directly) |
| `/daedalus run-shadow` | Shadow loop (use systemd; not this directly) |
| `/daedalus iterate-active` | One tick of the active loop |
| `/daedalus iterate-shadow` | One tick of the shadow loop |
| `/daedalus set-active-execution` | Enable/disable active dispatch |

### State management

| Command | What it does |
|---|---|
| `/daedalus init` | Init/migrate the runtime DB (idempotent) |
| `/daedalus ingest-live` | Pull workflow CLI status into the ledger |
| `/daedalus heartbeat` | Refresh the runtime lease |
| `/daedalus request-active-actions` | Inspect what *would* be dispatched on the next tick |
| `/daedalus execute-action` | Manually execute a queued action |
| `/daedalus analyze-failure` | Run failure analyst on a specific failure id |

### Systemd supervision

| Command | What it does |
|---|---|
| `/daedalus service-install` | Install + enable the user unit |
| `/daedalus service-uninstall` | Stop + remove the user unit |
| `/daedalus service-start` | Start `daedalus-active@<workspace>.service` |
| `/daedalus service-stop` | Stop the service |
| `/daedalus service-restart` | Restart the service |
| `/daedalus service-enable` | Enable on boot |
| `/daedalus service-disable` | Disable on boot |
| `/daedalus service-status` | systemd status snapshot |
| `/daedalus service-logs` | Last N journal entries |

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

### `code-review` workflow shortcuts (the common ones)

|| Command | What it does |
|---|---|---|
|| `/workflow code-review status` | Lane state + `nextAction` |
|| `/workflow code-review tick` | One workflow tick |
|| `/workflow code-review show-active-lane` | Current active GitHub issue |
|| `/workflow code-review show-lane-state` | `.lane-state.json` contents |
|| `/workflow code-review show-lane-memo` | `.lane-memo.md` contents |
|| `/workflow code-review dispatch-implementation-turn` | Force a coder turn |
|| `/workflow code-review dispatch-claude-review` | Force an internal Claude review |
|| `/workflow code-review publish-ready-pr` | Force PR publish |
|| `/workflow code-review merge-and-promote` | Force merge + promote next lane |
|| `/workflow code-review reconcile` | Repair stale ledger state |
|| `/workflow code-review pause` | Disable lane processing |
|| `/workflow code-review resume` | Re-enable |

### Webhook commands

|| Command | What it does |
|---|---|---|
|| `/workflow code-review webhooks status` | Show configured webhook subscribers |
|| `/workflow code-review webhooks test` | Fire a test event to all webhooks |

### Comments commands

|| Command | What it does |
|---|---|---|
|| `/workflow code-review comments status` | Show comment publisher state |
|| `/workflow code-review comments sync` | Force a comment sync for current lane |

## Most useful day-to-day, in order

1. `/daedalus watch` — live overview of every active lane in one frame
2. `/workflow code-review status` — current lane + next action
3. `/daedalus doctor` — overall health
4. `/workflow code-review show-active-lane` — what GitHub thinks
5. `/daedalus service-logs` — last 50 journal entries from the active service
5. `/workflow code-review tick` — manually fire a tick when impatient

## Notes

- All `/daedalus` subcommands accept `--workflow-root <path>` (default: detected from the cwd or `DAEDALUS_WORKFLOW_ROOT` env var).
- A few commands accept `--json` (`status`, `ingest-live`, `request-active-actions`); per-workflow CLI commands also accept `--json` where the underlying workflow supports it.
- The output format is currently terse `key=value` strings. Improving readability is tracked in the Daedalus repo's issue tracker.
