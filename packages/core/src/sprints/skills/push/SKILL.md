---
name: push
description: Push a lane branch and create or update its pull request.
---

# Push

Use this after a lane commit exists and the implementer must publish the change
for review. This skill owns pull request creation or update.

## Rules

- Use the existing `origin` remote.
- Do not change remotes as a workaround for auth failures.
- Use `--force-with-lease` only after an intentional local history rewrite.
- Never request interactive escalation. Return `blocked` for auth, permission,
  protected branch, missing `gh`, or network failures.

## Steps

1. Identify the branch with `git branch --show-current`.
2. Confirm the branch is not `main` or another protected base branch.
3. Confirm there is at least one commit to push.
4. Push with `git push -u origin HEAD`.
5. If rejected because the branch is stale, use the `pull` skill, rerun focused
   validation, then retry push.
6. Create or update the PR with `gh`:
   - if no PR exists, run `gh pr create`
   - if an open PR exists, run `gh pr edit`
7. Make the PR title and body describe the full lane result.
8. Return PR URL, number, branch, commits, and validation.

## Done Output Fields

Include these fields in the implementer JSON:

```json
{
  "branch": "codex/issue-20-short-name",
  "commits": ["<sha>"],
  "pull_request": {
    "url": "https://github.com/owner/repo/pull/123",
    "number": 123,
    "state": "open"
  }
}
```

## Blocked Output Shape

```json
{
  "status": "blocked",
  "summary": "push or PR creation is blocked",
  "blockers": [
    {
      "kind": "permission_required",
      "command": "git push -u origin HEAD",
      "message": "Remote rejected push with current credentials."
    }
  ],
  "artifacts": {
    "branch": "codex/issue-20-short-name",
    "commits": [],
    "pull_request": null
  }
}
```
