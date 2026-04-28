---
name: yoyopod-closeout-notifier
description: Monitor recently closed YoyoPod issues and send exactly one Telegram closeout update only for a newly announced closure.
---
# YoyoPod closeout notifier

Use this skill for the Hermes-owned YoyoPod cron job that announces issue closeouts over Telegram.

## Goal
Detect the newest GitHub issue that closed after the last announced closeout, send one compact Telegram update, and advance the notifier state only after delivery succeeds.

## Inputs
- Repo: `/home/radxa/.hermes/workspaces/YoyoPod_Core`
- Operator CLI: `/home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod`
- Notifier state: `/home/radxa/.hermes/workflows/yoyopod/memory/hermes-yoyopod-closeout-state.json`
- Telegram target: `telegram:-1003651617977` (the prompt may refer to this as `telegram:YoYoPod Hermes Alerts (group)`; use the chat ID when calling `send_message`)


## Procedure
1. Read the notifier state file first.
   - If it does not exist, assume nothing has been announced yet.
   - Use `lastAnnouncedIssue` and `lastAnnouncedClosedAt` as the dedupe boundary.
2. Query recent closed issues in the repo:
   ```bash
   gh issue list --state closed --limit 20 --json number,title,closedAt,url
   ```
3. Pick the newest closed issue that is newer than the state boundary.
   - Prefer `closedAt` as the primary sort key.
   - Use issue number only as a tiebreaker.
   - Do not announce stale backfill issues if the notifier is configured to skip them.
4. If no issue is newer than the notifier state, finish with exactly:
   ```text
   NO_REPLY
   ```
5. If a new closure exists, run:
   ```bash
   python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod status --json
   ```
   and use the current `activeLane.number` and `activeLane.title` in the message.
6. Send exactly one compact Telegram message to `telegram:-1003651617977`.
   - Keep it short.
   - Include the closed issue number/title/url.
   - Include the active lane number/title.
7. Only after the Telegram send succeeds, update `hermes-yoyopod-closeout-state.json` so the same issue cannot be announced twice.

## Message shape
Keep the Telegram text compact. A good pattern is:
- closed issue number + title
- closed timestamp
- active lane number + title
- issue URL

Example:
`Closed: #251 [P16] ... | Lane: #220 [A03] ... | https://github.com/...`

## Guardrails
- Do not notify on review-progress, approval, ready-to-close, or PR-opened events.
- Do not announce anything unless GitHub shows the issue is actually closed.
- Do not advance notifier state if delivery fails.
- Do not use Telegram as a general status channel for the active lane; only send closeout notifications.

## Failure handling
If Telegram delivery fails, leave the notifier state unchanged and retry on a later run.
A failed send is not a closeout.

## Verification
Before finishing, confirm:
- the chosen issue is the newest unannounced closed issue
- exactly one Telegram message was sent
- the notifier state now points at that issue
- if nothing was new, the final output is exactly `NO_REPLY`
