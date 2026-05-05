---
name: commit
description: Create a focused commit for one Sprints lane after verification passes.
---

# Commit

Use this after the implementer has made a lane-scoped change and run focused
verification.

## Rules

- Commit only changes for the current lane.
- Do not stage unrelated files.
- Do not commit generated noise, logs, caches, or runtime state.
- If unrelated dirty files exist, leave them unstaged and report them.
- Never ask for interactive escalation. Return `blocked` if the commit cannot be
  created safely.

## Steps

1. Inspect `git status --short`.
2. Inspect `git diff` and confirm the diff matches the issue scope.
3. Run or confirm focused validation.
4. Stage only intended files.
5. Write a concise commit message:
   - imperative subject
   - short body with summary and validation
6. Run `git commit -F <message-file>`.
7. Return commit SHA, subject, files changed, and validation.

## Blocked Output Shape

```json
{
  "status": "blocked",
  "summary": "commit is blocked by unrelated dirty files",
  "blockers": [
    {
      "kind": "dirty_worktree",
      "command": "git status --short",
      "message": "Unrelated files are dirty and must not be included."
    }
  ],
  "artifacts": {
    "dirty_files": []
  }
}
```
