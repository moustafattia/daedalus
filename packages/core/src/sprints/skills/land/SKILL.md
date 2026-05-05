---
name: land
description: Support an operator or reviewer while landing a reviewed pull request.
---

# Land

Use this after a PR has passed review and the operator wants help getting it
merged. This is not part of the default implementer loop. When workflow
auto-merge is enabled, the runner owns the final merge after reviewer approval;
use this skill for manual/operator landing work.

## Rules

- Do not merge without explicit workflow or operator authority.
- Do not bypass failed checks.
- Do not ignore unresolved review comments.
- Return `blocked` when merge permission, CI, conflicts, or review state blocks
  landing.

## Steps

1. Locate the PR for the current branch with `gh pr view`.
2. Check mergeability and review state.
3. If the PR has conflicts, use the `pull` skill and push the resolved branch.
4. Inspect failing checks before making changes.
5. If checks fail and the fix is local, fix, commit, push, and wait again.
6. If checks pass and approval is present, merge only when explicitly allowed by
   the operator or workflow input.

## Blocked Output Shape

```json
{
  "status": "blocked",
  "summary": "PR cannot be landed yet",
  "blockers": [
    {
      "kind": "review_required",
      "command": "gh pr view",
      "message": "Pull request has unresolved review feedback."
    }
  ],
  "artifacts": {
    "pull_request": {}
  }
}
```
