# Daedalus Operator Cheat Sheet

> **When confused, trust GitHub + live derived status first, Daedalus DB second, stale ledger prose last.**

This doc is for the 3am debugging session. Everything here is copy-paste ready.
It is specifically written for the opinionated `change-delivery` workflow.

---

## Quick Reference

| What You Need | Command / Query |
|:---|:---|
| **Check status** | `/daedalus status` |
| **Full health check** | `/daedalus doctor` |
| **Validate config** | `/daedalus validate` |
| **Live dashboard** | `/daedalus watch` |
| **Event retention posture** | `/daedalus events stats` |
| **Service health** | `systemctl --user status daedalus-active@<profile>.service` |
| **Recent logs** | `journalctl --user -u daedalus-active@<profile>.service -n 200` |
| **Lane actions (SQL)** | `select action_id, action_type, status, retry_count from lane_actions where lane_id='lane:220' order by requested_at desc;` |
| **Lane state (SQL)** | `select lane_id, workflow_state, review_state, current_head_sha from lanes where lane_id='lane:220';` |

---

## Mental Model

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   GitHub     │───►│ Workflow pkg │───►│   Daedalus   │
│   (truth)    │    │   (policy)   │    │   (runtime)  │
└──────────────┘    └──────────────┘    └──────────────┘
       │                    │                    │
       │                    │                    │
       ▼                    ▼                    ▼
  Issue labels        nextAction          SQLite DB
  PR head             health              JSONL events
  Review threads      reviewLoopState     Leases
```

**Three layers, three commands:**

| Layer | Command | Answers |
|:---|:---|:---|
| **GitHub** | `gh issue view 220`, `gh pr view 42` | Labels, head, draft, review threads |
| **Workflow** | `/workflow change-delivery status --json` | `nextAction`, `health`, `derivedReviewLoopState` |
| **Daedalus** | `/daedalus doctor` | Runtime freshness, ownership, action compatibility, failures |

---

## Core Commands

### Slash Commands (Inside Hermes)

```text
/daedalus status              # Runtime row, lane count, paths, freshness
/daedalus doctor              # Full health check across all subsystems
/daedalus validate            # Validate WORKFLOW.md, schema, and preflight rules
/daedalus watch               # Live TUI: lanes + alerts + events
/daedalus events stats        # Event counts plus retention limit posture
/daedalus shadow-report       # Diff shadow plan vs active reality
/daedalus active-gate-status  # What's blocking promotion to active
/daedalus service-status      # systemd health snapshot
```

### Workflow CLI (Direct)

```bash
# Status
python3 ~/.hermes/plugins/daedalus/workflows/__main__.py \
  --workflow-root ~/.hermes/workflows/<owner>-<repo>-<workflow-type> \
  status --json

# Tick (manual dispatch)
python3 ~/.hermes/plugins/daedalus/workflows/__main__.py \
  --workflow-root ~/.hermes/workflows/<owner>-<repo>-<workflow-type> \
  tick --json

# Implementation turn
python3 ~/.hermes/plugins/daedalus/workflows/__main__.py \
  --workflow-root ~/.hermes/workflows/<owner>-<repo>-<workflow-type> \
  dispatch-implementation-turn --json

# internal review
python3 ~/.hermes/plugins/daedalus/workflows/__main__.py \
  --workflow-root ~/.hermes/workflows/<owner>-<repo>-<workflow-type> \
  dispatch-internal-review --json
```

### Daedalus Runtime (Direct)

```bash
# Status
python3 ~/.hermes/plugins/daedalus/runtime.py \
  status --workflow-root ~/.hermes/workflows/<owner>-<repo>-<workflow-type> \
  --json

# Doctor
python3 ~/.hermes/plugins/daedalus/runtime.py \
  doctor --workflow-root ~/.hermes/workflows/<owner>-<repo>-<workflow-type> \
  --json

# Shadow report
python3 ~/.hermes/plugins/daedalus/runtime.py \
  shadow-report --workflow-root ~/.hermes/workflows/<owner>-<repo>-<workflow-type> \
  --json

# Active actions for a lane
python3 ~/.hermes/plugins/daedalus/runtime.py \
  request-active-actions \
  --workflow-root ~/.hermes/workflows/<owner>-<repo>-<workflow-type> \
  --lane-id lane:220 --json
```

### Service Control

```bash
# Check service
systemctl --user status \
  daedalus-active@<owner>-<repo>-<workflow-type>.service --no-pager

# View logs
journalctl --user -u \
  daedalus-active@<owner>-<repo>-<workflow-type>.service \
  -n 200 --no-pager

# Restart
systemctl --user restart \
  daedalus-active@<owner>-<repo>-<workflow-type>.service
```

---

## Key Files

| File | Purpose |
|:---|:---|
| `~/.hermes/workflows/<profile>/runtime/state/daedalus/daedalus.db` | `change-delivery` runtime state (SQLite) |
| `~/.hermes/workflows/<profile>/runtime/memory/daedalus-events.jsonl` | Daedalus runtime event history |
| `~/.hermes/workflows/<profile>/memory/workflow-status.json` | Workflow status projection |
| `~/.hermes/workflows/<profile>/memory/workflow-health.json` | Workflow health projection |
| `~/.hermes/workflows/<profile>/memory/workflow-scheduler.json` | Generated scheduler snapshot; SQLite remains the source of truth |
| `/tmp/issue-<N>/.lane-state.json` | Lane-local handoff state |
| `/tmp/issue-<N>/.lane-memo.md` | Lane-local handoff notes |
| `~/.config/systemd/user/daedalus-active@<profile>.service` | Service unit file |

---

## Lane States

### Local Phase (No PR yet)

```
implementing → awaiting_pre_publish_review → ready_to_publish
     ↑                    │
     └──── findings ──────┘
```

### Published Phase (PR exists)

```
under_review → findings_open → approved → merged
     ↑              │
     └── findings ──┘
```

### Health Overlays

| State | Meaning |
|:---|:---|
| `healthy` | All systems nominal |
| `stale-ledger` | Persisted truth differs from live derived state |
| `stale-lane` | Lane hasn't progressed in N ticks |
| `operator_attention_required` | Human judgment needed |

---

## Reviewer Policy

| Phase | Required Reviewer | Gate |
|:---|:---|:---|
| **Before PR** | Internal reviewer | Must pass before publish |
| **After PR** | external review (external) | Must pass before merge |
| **Advisory** | Rock Claw | Informative only |

---

## Actor Model

| Role | Model | Purpose |
|:---|:---|:---|
| Implementer | `gpt-5.4` | Default implementation |
| High-effort implementer | `gpt-5.4` | Large-effort / complex tasks |
| Reviewer | `gpt-5.4` | Local unpublished branch gate |
| External Reviewer | external review | Published PR review |
| Advisory Reviewer | Rock Claw | Optional additional eyes |

---

## Handoff Map

```
Orchestrator ──► Implementer ──► Reviewer ──► Publish ──► External Review ──► Merge
     │              │                    │                                    │                          │
     │              │                    └─► repair ──────────────────────────┘                          │
     │              │                                                                                    │
     │              └─► repair ◄─────────────────────────────────────────────────────────────────────────┘
     │
     └─► restart session (if stale)
```

| Step | Workflow Action | Daedalus Action |
|:---|:---|:---|
| 1. Orchestrator → Implementer | `dispatch-implementation-turn` | `dispatch_implementation_turn` |
| 2. Implementer → Reviewer | `run_internal_review` | `request_internal_review` |
| 3. Reviewer → Implementer repair | local findings → lane session | `dispatch_repair_handoff` |
| 4. Reviewer → Publish | workflow derives publish | `publish_pr` |
| 5. Publish → external review | external review triggered | — |
| 6. external review → Implementer repair | post-publish findings | `dispatch_repair_handoff` |
| 7. Clean → Merge | `merge_and_promote` | `merge_pr` |

---

## Action Types

### Coder Actions
- `dispatch_implementation_turn`
- `dispatch_repair_handoff`
- `restart_actor_session`

### Review Actions
- `request_internal_review`

### PR Lifecycle Actions
- `publish_pr`
- `push_pr_update`
- `merge_pr`

---

## Common Failure Signatures

### A. Workflow says `run_internal_review`, Daedalus returns `[]`

**Likely cause:** Failed active `request_internal_review` for the same head wedged the idempotency key.

**Check:**
```sql
select action_id, action_type, status, retry_count
from lane_actions
where lane_id='lane:220' and action_type='request_internal_review'
order by requested_at desc;
```

**Fix:** Already in place — failed internal-review actions can requeue with incremented `retry_count`.

---

### B. Workflow says review is `running` but nothing is actually running

**Likely cause:** `dispatch_internal_review()` failed after marking review as running.

**Fix:** Already in place — failure now resets internal review back to retryable pending state.

---

### C. `health = stale-ledger`

**Meaning:** Persisted ledger truth and live derived truth differ.

**Typical causes:**
- PR was published or updated
- External review changed faster than ledger reconciliation
- Live GitHub truth outran persisted state

**Operator move:** Trust derived live state more than stale ledger prose.

---

### D. `nextAction = noop` on a lane with obvious open findings

**Ask:**
- Is the lane local/no-PR or published/PR-backed?
- Is the implementation actor session stale?
- Did a repair handoff already go out?
- Is the local head ahead of PR head?
- Are you looking at workflow-derived truth or Daedalus runtime truth?

---

## SQL Debugging

### Show recent lane actions
```sql
select action_id, action_type, status, retry_count,
       requested_at, failed_at, completed_at
from lane_actions
where lane_id='lane:220'
order by requested_at desc;
```

### Show lane review rows
```sql
select reviewer_scope, status, verdict,
       requested_head_sha, reviewed_head_sha,
       review_scope, requested_at, completed_at
from lane_reviews
where lane_id='lane:220';
```

### Show actor row
```sql
select actor_id, backend_identity, runtime_status,
       session_action_recommendation, last_used_at,
       can_continue, can_nudge
from lane_actors
where lane_id='lane:220';
```

### Show lane row
```sql
select lane_id, issue_number, workflow_state, review_state,
       current_head_sha, active_pr_number, merge_state, merge_blocked
from lanes
where lane_id='lane:220';
```

### Show recent events
```bash
# Query the durable SQLite engine event ledger
hermes daedalus events \
  --workflow-root ~/.hermes/workflows/<profile> \
  --limit 50 \
  --json

# Filter by run or work item
hermes daedalus events --workflow-root ~/.hermes/workflows/<profile> --run-id <run_id>
hermes daedalus events --workflow-root ~/.hermes/workflows/<profile> --work-id ISSUE-123
```

---

## Webhook Debugging

### Show configured webhooks
```bash
python3 ~/.hermes/plugins/daedalus/workflows/__main__.py \
  --workflow-root ~/.hermes/workflows/<profile> \
  status --json | jq '.webhooks'
```

### Test a webhook manually
```bash
python3 ~/.hermes/plugins/daedalus/workflows/__main__.py \
  --workflow-root ~/.hermes/workflows/<profile> \
  dispatch-test-webhook --event action=test
```

---

## Config Hot-Reload

### Check if a bad WORKFLOW.md edit is being ignored
```bash
/daedalus doctor
```
Look for `config_reload_failed` in the event tail or doctor output.

### Force a config re-read
```bash
# Touch the repo-owned contract file; the next tick will pick it up.
touch /path/to/repo/WORKFLOW.md
```

Tracker feedback is configured in `WORKFLOW.md` under `tracker-feedback`.

---

## Policy Knobs

| Knob | Value |
|:---|:---|
| Implementer default model | `gpt-5.4` |
| Implementer high-effort model | `gpt-5.4` |
| Reviewer model | `gpt-5.4` |
| Internal review pass-with-findings reviews | `1` |
| Internal review max turns | `12` |
| Lane failure retry budget | `3` |
| Lane no-progress tick budget | `3` |
| Operator-attention thresholds | `5 / 5` |

---

## See Also

| Doc | What It Covers |
|:---|:---|
| [Operator Guide](./README.md) | Landing page for all operator docs |
| [Slash Commands](./slash-commands.md) | Complete catalog of `/daedalus` commands |
| [HTTP Status Surface](./http-status.md) | JSON health snapshots for dashboards |
| [Installation](./installation.md) | First-time setup |
| [Architecture Overview](../architecture.md) | How Daedalus works internally |
| [Concepts](../concepts/README.md) | Leases, lanes, actions, failures, etc. |
