---
name: pull
description: Sync the current lane branch with origin/main without losing local work.
---

# Pull

Use this when an implementer needs a current branch before editing, when a push
is rejected because the branch is stale, or when retry feedback asks for a fresh
base.

## Rules

- Work only in the current lane worktree and branch.
- Do not rebase unless the workflow input explicitly asks for it.
- Preserve operator and user changes.
- Never request interactive escalation. If auth, permissions, or conflicts block
  progress, stop and return a structured `blocked` actor output.

## Steps

1. Inspect `git status --short --branch`.
2. If the worktree has unrelated dirty changes, preserve them and report them in
   `artifacts.dirty_files`.
3. Ensure `origin` exists and run `git fetch origin`.
4. If the branch already tracks a remote branch, pull it with `git pull --ff-only`.
5. Merge `origin/main` with `git -c merge.conflictstyle=zdiff3 merge origin/main`.
6. Resolve conflicts only when the correct result is clear from local context.
7. Run `git diff --check` after conflict resolution.
8. Return the updated branch name, conflict notes, and any blockers.

## Blocked Output Shape

```json
{
  "status": "blocked",
  "summary": "branch sync is blocked",
  "blockers": [
    {
      "kind": "merge_conflict",
      "command": "git merge origin/main",
      "message": "Conflict requires operator intent."
    }
  ],
  "artifacts": {
    "dirty_files": [],
    "conflicted_files": []
  }
}
```
