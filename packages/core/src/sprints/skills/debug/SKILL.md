---
name: debug
description: Diagnose a blocked or failing Sprints lane using local state, logs, and runtime metadata.
---

# Debug

Use this when implementation, validation, runtime dispatch, push, or PR creation
fails.

## Inputs To Inspect

- The lane JSON in the actor prompt.
- `lane.runtime_session.thread_id` and `lane.runtime_session.turn_id`.
- `lane.branch`, `lane.pull_request`, `lane.operator_attention`, and
  `lane.last_actor_output`.
- Local git state in the repository worktree.
- Sprints state under `.sprints/` and, when present,
  `runtime/state/sprints/sprints.db`.

## Steps

1. Identify the failing step: pull, edit, validation, commit, push, or PR.
2. Capture the exact command and error text.
3. Check whether the failure is local and fixable without operator input.
4. If fixable, make the smallest correction and rerun the focused validation.
5. If blocked by auth, permissions, sandbox, missing tools, unclear product
   intent, or unsafe changes, stop and return `status: blocked`.

## Evidence To Return

- failing command
- stderr/stdout excerpt
- branch
- dirty files
- validation output
- PR URL, if one exists
- runtime thread/turn IDs, if available

## Blocked Output Shape

```json
{
  "status": "blocked",
  "summary": "debug found an external blocker",
  "blockers": [
    {
      "kind": "permission_required",
      "command": "git push",
      "message": "Cannot push with current credentials."
    }
  ],
  "artifacts": {
    "branch": "codex/issue-20-short-name",
    "dirty_files": [],
    "validation": []
  }
}
```
