---
name: review
description: Review one Sprints pull request and return structured approval or required fixes.
---

# Review

Use this when acting as the Sprints reviewer for one lane and pull request.

## Rules

- Review exactly the lane and pull request in the prompt.
- Do not mutate unrelated state.
- Do not ask for interactive escalation.
- Return `blocked` when the PR, diff, checks, or repository access is missing.
- Return `changes_requested` only with concrete `required_fixes`.
- Return `approved` only when the implementation is reviewable, scoped, and has
  credible verification.

## Steps

1. Inspect the issue, implementation output, branch, PR URL, and verification.
2. Inspect the PR diff and relevant touched files.
3. Check for correctness, regressions, unsafe scope, missing cleanup, and weak
   validation.
4. Separate optional findings from required fixes.
5. If fixes are required, make each item directly actionable for the implementer.
6. Return JSON only.

## Output Shape

```json
{
  "status": "approved|changes_requested|blocked|failed",
  "summary": "short review result",
  "findings": [
    {
      "severity": "low|medium|high",
      "file": "path/to/file",
      "line": 123,
      "issue": "specific concern",
      "impact": "why it matters"
    }
  ],
  "required_fixes": [
    {
      "file": "path/to/file",
      "change": "specific fix required",
      "reason": "why this fix is required"
    }
  ],
  "verification_gaps": [
    {
      "command": "missing or insufficient verification",
      "reason": "what needs proof"
    }
  ],
  "blockers": [],
  "next_recommendation": "complete|retry_deliver"
}
```
