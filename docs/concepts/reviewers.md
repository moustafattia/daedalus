# Reviewers

Daedalus orchestrates a **multi-stage review pipeline**. Each stage has a different reviewer role, different gating rules, and different handoff semantics. The goal: no code reaches `main` without passing the right gate at the right time.

---

## Reviewer roles

| Role | Runtime | When active | Gate type |
|---|---|---|---|
| **Internal reviewer** | `claude-cli` | Before PR exists | Required |
| **External reviewer** | `acpx-codex` (Codex Cloud) | After PR is published | Required |
| **Advisory reviewer** | Varies (e.g., Rock Claw) | Any time | Informative only |

### Internal reviewer (Claude)

- **Runtime:** `claude-cli` (one-shot, no persistent session)
- **Model:** `claude-sonnet-4-6` (configurable)
- **Trigger:** Local unpublished branch exists and needs pre-publish gate
- **Output:** JSON verdict with `verdict`, `findings`, `targetHeadSha`
- **Gate:** Must pass before `publish_pr` is allowed

### External reviewer (Codex Cloud)

- **Runtime:** `acpx-codex` (resumable session)
- **Model:** `gpt-5` (configurable)
- **Trigger:** PR exists and is ready for review
- **Output:** GitHub review threads + PR body signal
- **Gate:** Must pass before `merge_pr` is allowed

### Advisory reviewer

- Optional additional reviewer
- Can run in parallel with required reviewers
- Findings are logged but do not block merge
- Example: Rock Claw for security-focused review

---

## Review lifecycle

```mermaid
stateDiagram-v2
    [*] --> internal_pending: local branch exists
    internal_pending --> internal_running: dispatch_claude_review
    internal_running --> internal_findings: reject
    internal_findings --> coder_repair: repair handoff
    coder_repair --> internal_pending: re-dispatch
    internal_running --> internal_passed: approve
    internal_passed --> publish_pr: create PR

    publish_pr --> external_pending: PR ready
    external_pending --> external_running: Codex Cloud review
    external_running --> external_findings: reject
    external_findings --> coder_repair2: repair handoff
    coder_repair2 --> external_pending: push update + re-review
    external_running --> external_passed: approve
    external_passed --> merge_pr: merge
```

---

## Findings format

Reviewers return structured findings that the workflow uses for repair handoff:

```json
{
  "verdict": "changes_requested",
  "targetHeadSha": "abc123def...",
  "findings": [
    {
      "file": "src/foo.py",
      "line": 42,
      "severity": 1,
      "message": "Missing error handling for network timeout"
    }
  ],
  "summary": "2 findings, both addressable"
}
```

### Severity levels

| Badge | Meaning |
|---|---|
| `P1` | Critical — blocks merge |
| `P2` | Important — should fix |
| `P3` | Minor — nice to have |

The `SEVERITY_BADGE_RE` regex (`![P(\d+) Badge`) extracts these from review output for aggregation.

---

## Repair handoff

When a reviewer returns `changes_requested`, the workflow dispatches a **repair handoff** back to the coder:

1. Findings are deduplicated against `lane-state.json` handoff metadata
2. New findings are appended to the lane memo
3. Coder session receives the repair prompt
4. Coder implements fixes and commits
5. Reviewer re-evaluates the new head

### Deduplication key

```
<target_head_sha>:<finding_file>:<finding_line>:<finding_message_hash>
```

This prevents the same finding from being re-reported after repair.

---

## Review state in SQLite

### `lane_reviews` table

| Field | Type | Meaning |
|---|---|---|
| `review_id` | string | UUID v4 |
| `lane_id` | string | FK → lanes |
| `reviewer_scope` | enum | `internal` / `external` / `advisory` |
| `status` | enum | `pending` / `running` / `completed` |
| `verdict` | enum | `pass` / `pass_with_findings` / `changes_requested` |
| `requested_head_sha` | string | Head the review was requested against |
| `reviewed_head_sha` | string \| null | Head actually reviewed (may differ if pushed mid-review) |
| `findings_count` | int | Number of findings returned |
| `requested_at` | timestamp | When review was dispatched |
| `completed_at` | timestamp \| null | When verdict was received |

---

## SQL debugging

### Show review history for a lane

```sql
select reviewer_scope, status, verdict, requested_head_sha, reviewed_head_sha, findings_count, requested_at, completed_at
from lane_reviews
where lane_id='lane:220'
order by requested_at desc;
```

### Find lanes with open findings

```sql
select l.lane_id, l.issue_number, r.reviewer_scope, r.findings_count
from lanes l
join lane_reviews r on l.lane_id = r.lane_id
where r.verdict = 'changes_requested'
  and r.completed_at is not null;
```

### Check if internal gate blocks publish

```sql
select status, verdict
from lane_reviews
where lane_id='lane:220'
  and reviewer_scope='internal'
order by requested_at desc
limit 1;
```

---

## Where this lives in code

- Review policy: `daedalus/workflows/change_delivery/reviews.py`
- Reviewer implementations: `daedalus/workflows/change_delivery/reviewers/`
- Review dispatch: `daedalus/workflows/change_delivery/dispatch.py`
- Findings parsing: `daedalus/workflows/change_delivery/reviews.py` (look for `_extract_json_object`, `SEVERITY_BADGE_RE`)
- Repair handoff: `daedalus/workflows/change_delivery/actions.py`
- Review state schema: `daedalus/workflows/change_delivery/migrations.py`
- Tests: `tests/test_workflows_change_delivery_reviews.py`, `tests/test_external_reviewer_phase_b.py`
