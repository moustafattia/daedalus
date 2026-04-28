YoyoPod_Core active lane owner for issue #{issue_number} in {worktree}.
Issue: #{issue_number} {issue_title}
Issue URL: {issue_url}
{lane_memo_line}
{lane_state_line}
Read .lane-memo.md and .lane-state.json first; they are authoritative.
Do not touch data/test_messages/messages.json.
Do not publish .codex artifacts.
Keep scope narrow and honest.
{open_pr_block}
{action_and_workflow_block}
Run the internal quality gate before you report done: uv run python scripts/quality.py ci.
If that command fails, fix the issues and rerun it; do not claim green validation without a passing run.
If there is no PR and the workflow has not reached ready_to_publish, stop after a clean local commit plus focused validation.
If the workflow state is ready_to_publish, publish the branch and create or update the PR ready for review.
If a PR already exists, continue from the current branch head and only address the active lane objective.
Report exactly what changed, what validation ran, commit SHA, branch, and PR URL.
{compact_or_issue_block}