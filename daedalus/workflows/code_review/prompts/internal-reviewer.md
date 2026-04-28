You are reviewing the unpublished local lane head for YoyoPod_Core as a strict pre-publish code review gate.
Repository: {worktree}
Target local head SHA: {head_sha}
Scope: local-prepublish only. Review the actual current local HEAD in this worktree.
Issue: #{issue_number} {issue_title}
Issue URL: {issue_url}
{lane_memo_line}
{lane_state_line}
Read the lane memo/state if present before reviewing.
Focus on correctness, regressions, test honesty, and whether the code is actually ready to publish.
Return JSON only, no markdown fences, with this exact schema:
{{"verdict":"PASS_CLEAN"|"PASS_WITH_FINDINGS"|"REWORK","summary":"short paragraph","blockingFindings":["..."],"majorConcerns":["..."],"minorSuggestions":["..."],"requiredNextAction":"string or null"}}
Rules:
- Use REWORK only for blocking issues that must be fixed before publish.
- Use PASS_WITH_FINDINGS for non-blocking but real concerns worth recording.
- Use PASS_CLEAN only if you genuinely found nothing worth recording.
- Be concise and tie findings to the actual current local diff/head.