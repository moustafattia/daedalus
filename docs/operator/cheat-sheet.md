# Daedalus Operator Cheat Sheet

## 1. 10-second mental model

- **Workflow CLI** = the policy brain, exposed via
  `~/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root ~/.hermes/workflows/yoyopod`
  (historically: "the wrapper" at `scripts/yoyopod_workflow.py`, now retired)
- **Daedalus runtime** = durable orchestrator around that brain
- **systemd active service** = keeps Daedalus alive 24/7
- **Codex** = internal coder
- **Claude** = internal unpublished-branch gate
- **Codex Cloud** = external PR reviewer
- **SQLite** = canonical Daedalus runtime truth now
- **JSONL** = append-only event/audit history
- **lane-state + lane-memo** = lane-local handoff artifacts

If status looks weird, always ask:
1. What does the workflow CLI's `status --json` think?
2. What does Daedalus think?
3. Is the active service alive?
4. Is GitHub truth drifting away from persisted ledger truth?

---

## 2. Core surfaces

### Workflow CLI (plugin-owned; replaces retired `scripts/yoyopod_workflow.py`)
```bash
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root ~/.hermes/workflows/yoyopod status --json
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root ~/.hermes/workflows/yoyopod tick --json
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root ~/.hermes/workflows/yoyopod dispatch-implementation-turn --json
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root ~/.hermes/workflows/yoyopod dispatch-claude-review --json
```

### Daedalus runtime direct
```bash
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/runtime.py status --workflow-root ~/\.hermes/workflows/yoyopod --json
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/runtime.py shadow-report --workflow-root ~/\.hermes/workflows/yoyopod --json
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/runtime.py doctor --workflow-root ~/\.hermes/workflows/yoyopod --json
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/runtime.py request-active-actions --workflow-root ~/\.hermes/workflows/yoyopod --lane-id lane:220 --json
```

### Daedalus slash command inside Hermes
```text
/daedalus status
/daedalus shadow-report
/daedalus doctor
/daedalus active-gate-status
```

### Active service
```bash
systemctl --user status daedalus-active@yoyopod.service --no-pager
journalctl --user -u daedalus-active@yoyopod.service -n 200 --no-pager
```

---

## 3. Source of truth order

Use this order when debugging:
1. **GitHub truth**
   - active issue label
   - PR existence / head / draft state
   - Codex Cloud review threads/signals
2. **Wrapper read model**
   - `status --json`
   - especially `nextAction`, `health`, `derivedReviewLoopState`
3. **Daedalus runtime state**
   - `daedalus.db`
   - `shadow-report`
   - `doctor`
4. **Lane handoff files**
   - `.lane-state.json`
   - `.lane-memo.md`
5. **Legacy/archive cron files**
   - history only, not scheduler truth

---

## 4. Key files

### Workflow root
- `~/.hermes/workflows/yoyopod`

### Main repo clone
- `~/.hermes/workspaces/YoyoPod_Core`

### Workflow CLI (plugin-owned; replaces retired `scripts/yoyopod_workflow.py`)
- `~/.hermes/workflows/yoyopod/.hermes/plugins/daedalus/workflows/__main__.py`
  (always pass `--workflow-root ~/.hermes/workflows/yoyopod`)

### Daedalus plugin
- `~/.hermes/workflows/yoyopod/.hermes/plugins/daedalus/__init__.py`
- `~/.hermes/workflows/yoyopod/.hermes/plugins/daedalus/tools.py`
- `~/.hermes/workflows/yoyopod/.hermes/plugins/daedalus/runtime.py`
- `~/.hermes/workflows/yoyopod/.hermes/plugins/daedalus/alerts.py`

### Daedalus canonical state
- `~/.hermes/workflows/yoyopod/state/daedalus/daedalus.db`
- `~/.hermes/workflows/yoyopod/memory/daedalus-events.jsonl`

### Wrapper projections
- `~/.hermes/workflows/yoyopod/memory/yoyopod-workflow-status.json`
- `~/.hermes/workflows/yoyopod/memory/yoyopod-workflow-health.json`
- `~/.hermes/workflows/yoyopod/memory/yoyopod-workflow-audit.jsonl`

### Lane-local handoff artifacts
- `/tmp/yoyopod-issue-<N>/.lane-state.json`
- `/tmp/yoyopod-issue-<N>/.lane-memo.md`

### Service unit
- `~/.config/systemd/user/daedalus-active@yoyopod.service`

---

## 5. State machine at a glance

### Local lane phase
- `implementing`
- `implementing_local`
- `awaiting_claude_prepublish`
- `claude_prepublish_findings`
- `ready_to_publish`

### Published PR phase
- `under_review`
- `findings_open`
- `approved`

### Operational health overlays
- `healthy`
- `stale-ledger`
- `stale-lane`
- `disabled-core-jobs`
- `missing-core-jobs`
- `operator_attention_required`

---

## 6. Reviewer policy

### Before PR exists
Required reviewer:
- **Claude only**

Meaning:
- local unpublished branch must clear Claude gate before publish

### After PR is ready for review
Required reviewer:
- **Codex Cloud only**

Meaning:
- merge blocked until PR head is clean

### Advisory reviewer
- **Rock Claw** is informative, not always the primary gate

---

## 7. Actor model

Configured actor labels:
- `Internal_Coder_Agent`
- `Escalation_Coder_Agent`
- `Internal_Reviewer_Agent`
- `External_Reviewer_Agent`
- `Advisory_Reviewer_Agent`

### Backing models today
- internal coder default: `gpt-5.3-codex-spark/high`
- internal coder escalation: `gpt-5.4`
- internal reviewer: `claude-sonnet-4-6`

---

## 8. What the wrapper owns vs what Daedalus owns

### Wrapper owns
- semantic workflow policy
- status/read model
- `nextAction`
- implementation dispatch
- Claude review dispatch
- publish / merge / promote logic
- repair-handoff gating logic

### Daedalus owns
- canonical runtime DB
- leases / heartbeats
- action queue rows
- event log
- active vs shadow execution
- failure tracking
- retry bookkeeping
- service supervision surface

Short version:
- **Wrapper decides what should happen**
- **Daedalus decides how to orchestrate it durably**

---

## 9. Handoff map

### 1. Orchestrator -> coder
- wrapper: `dispatch-implementation-turn`
- Daedalus action: `dispatch_implementation_turn`

### 2. Coder -> Claude local gate
- wrapper semantic action: `run_claude_review`
- Daedalus action: `request_internal_review`

### 3. Claude -> coder repair handoff
- local unpublished findings go back into the Codex lane session
- deduped by lane-state handoff metadata

### 4. Claude -> publish
- once local gate satisfied, wrapper derives publish path

### 5. Published PR -> Codex Cloud
- external reviewer becomes required

### 6. Codex Cloud -> coder repair handoff
- post-publish findings route back to coder session

### 7. Clean PR -> merge/promote
- merge PR
- close issue
- remove `active-lane`
- promote next issue

---

## 10. Daedalus action types

### Coder actions
- `dispatch_implementation_turn`
- `dispatch_repair_handoff`
- `restart_actor_session`

### Review action
- `request_internal_review`

### PR lifecycle actions
- `publish_pr`
- `push_pr_update`
- `merge_pr`

### Why naming differs
Wrapper semantic names:
- `run_claude_review`
- `publish_ready_pr`
- `merge_and_promote`

Daedalus execution names:
- `request_internal_review`
- `publish_pr`
- `merge_pr`

That’s expected. Daedalus speaks execution language.

---

## 11. Day-2 operating patterns

### What is happening right now?
```bash
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root ~/.hermes/workflows/yoyopod status --json
```
Check:
- `health`
- `activeLane`
- `openPr`
- `nextAction`
- `derivedReviewLoopState`
- `derivedMergeBlocked`

### Is Daedalus healthy?
```bash
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/runtime.py doctor --workflow-root ~/\.hermes/workflows/yoyopod --json
```
Check:
- runtime freshness
- ownership posture
- action compatibility
- unresolved active failures
- split-brain hints

### Is the service actually alive?
```bash
systemctl --user status daedalus-active@yoyopod.service --no-pager
journalctl --user -u daedalus-active@yoyopod.service -n 200 --no-pager
```

### What active actions does Daedalus think exist?
```bash
python3 ~/\.hermes/workflows/yoyopod/.hermes/plugins/daedalus/runtime.py request-active-actions \
  --workflow-root ~/\.hermes/workflows/yoyopod \
  --lane-id lane:220 --json
```

---

## 12. Common failure signatures

### A. Wrapper says `run_claude_review`, Daedalus returns `[]`
Likely cause:
- failed active `request_internal_review` row for same head wedged the old idempotency key

Check:
- `daedalus.db` -> `lane_actions`

Current fix already in place:
- failed internal-review actions can now requeue with incremented `retry_count`

### B. Wrapper says review is `running` but nothing is actually running
Likely cause:
- `dispatch_claude_review()` failed after marking review running

Current fix already in place:
- failure now resets Claude review back to retryable pending state

### C. `health=stale-ledger`
Meaning:
- persisted ledger truth and live derived truth differ

Typical causes:
- PR was published or updated
- Codex Cloud review changed faster than ledger reconciliation
- live GitHub truth outran persisted state

Operator move:
- trust derived live state more than stale ledger prose

### D. `nextAction=noop` on a lane that obviously has open findings
Ask:
- is the lane actually local/no-PR or published/PR-backed?
- is the coder session stale?
- did a repair handoff already go out?
- is the local head ahead of PR head?
- are you looking at wrapper truth or Daedalus truth?

---

## 13. SQL debugging cheats

### Show recent lane actions
```sql
select action_id, action_type, status, retry_count, requested_at, failed_at, completed_at
from lane_actions
where lane_id='lane:220'
order by requested_at desc;
```

### Show lane review rows
```sql
select reviewer_scope, status, verdict, requested_head_sha, reviewed_head_sha, review_scope, requested_at, completed_at
from lane_reviews
where lane_id='lane:220';
```

### Show actor row
```sql
select actor_id, backend_identity, runtime_status, session_action_recommendation, last_used_at, can_continue, can_nudge
from lane_actors
where lane_id='lane:220';
```

### Show lane row
```sql
select lane_id, issue_number, workflow_state, review_state, current_head_sha, active_pr_number, merge_state, merge_blocked
from lanes
where lane_id='lane:220';
```

---

## 14. Current important policy knobs

From `config/yoyopod-workflow.json`:
- coder default model: `gpt-5.3-codex-spark/high`
- coder large-effort model: `gpt-5.3-codex`
- coder escalation model: `gpt-5.4`
- Claude model: `claude-sonnet-4-6`
- Claude pass-with-findings reviews: `1`
- Claude max turns: `12`
- lane failure retry budget: `3`
- lane no-progress tick budget: `3`
- operator-attention thresholds: `5 / 5`

---

## 15. The one-sentence operator rulebook

**When confused, trust GitHub + live derived status first, Daedalus DB second, stale ledger prose last.**
