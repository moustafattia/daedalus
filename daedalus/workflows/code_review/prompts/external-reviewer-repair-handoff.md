{external_reviewer_agent_name} review found follow-up work for issue #{issue_number} on published head {reviewed_head_sha}.
Issue: #{issue_number} {issue_title}
PR: {pr_url}
{lane_memo_line}
{lane_state_line}
Read .lane-memo.md and .lane-state.json first; they are authoritative.
Stay on the same branch and fix the current {external_reviewer_agent_name} review findings on the published head.
After fixes, run focused validation, update the branch head, and stop so the normal review loop can re-evaluate.

{external_reviewer_agent_name} summary:
{review_summary}

Current must-fix items:
{must_fix_lines}

Current should-fix items:
{should_fix_lines}

Guardrails:
- Do not touch data/test_messages/messages.json.
- Do not publish .codex artifacts.
- Keep scope narrow to the active {external_reviewer_agent_name} repair brief.
- Report exactly what changed, what validation ran, and the new HEAD SHA.