{internal_reviewer_agent_name} pre-publish review found follow-up work for issue #{issue_number} on local head {reviewed_head_sha}.
Issue: #{issue_number} {issue_title}
{lane_memo_line}
{lane_state_line}
Read .lane-memo.md and .lane-state.json first; they are authoritative.
Do not publish yet.
Stay in the same lane and fix the current Claude pre-publish findings on the local branch.
After fixes, run focused validation, update the local branch head, and stop for Claude re-review.

Claude summary:
{review_summary}

Current must-fix items:
{must_fix_lines}

Current should-fix items:
{should_fix_lines}

Guardrails:
- Do not touch data/test_messages/messages.json.
- Do not publish .codex artifacts.
- Keep scope narrow to the current repair brief.
- Report exactly what changed, what validation ran, and the new local HEAD SHA.