---
name: yoyopod-workflow-watchdog-tick
description: Run exactly one Hermes-owned YoyoPod workflow watchdog tick via the daedalus plugin CLI, handle timeouts, and produce the required final response shape.
---
# YoyoPod workflow watchdog tick

Use this skill when executing a scheduled Hermes-owned YoyoPod watchdog tick
against the daedalus plugin CLI (``workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod``).

## Goal
Run one tick, then report only the mandated outcome.

## Procedure
1. Run:
   ```bash
   python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod tick --json
   ```
2. If the command returns JSON, inspect `action.type`.
3. If `action.type == "noop"`, the final response must be exactly:
   ```text
   NO_REPLY
   ```
4. Otherwise, return a compact summary with exactly these fields:
   - Current issue
   - Action
   - Reason
   - Head
   - After state

## Timeout / failure fallback
If the CLI command times out or exits ambiguously:
1. Read the workflow state files directly:
   - `/home/radxa/.hermes/workflows/yoyopod/memory/yoyopod-workflow-status.json`
   - `/home/radxa/.hermes/workflows/yoyopod/memory/yoyopod-workflow-health.json`
   - `/home/radxa/.hermes/workflows/yoyopod/memory/yoyopod-workflow-audit.jsonl`
2. Use those files to infer the actual tick result. Prefer `health.nextAction` / `status.nextAction` and the latest audit entry over guesswork.
3. Do not invent policy; the state files are the source of truth.
4. If the active lane is stale-open and `sessionActionRecommendation.action` is `poke-session`, the practical recovery is a named-session nudge from the lane worktree, e.g.:
   ```bash
   acpx codex prompt -s lane-<N> --no-wait "continue from lane memo/state"
   ```
   Run it from `/tmp/yoyopod-issue-<N>` so the named session resolves correctly.
5. If `reconcile` is needed for watcher drift, call it without `--json` unless the command explicitly supports that flag; the CLI currently prints JSON on stdout by default and rejects `--json` on reconcile.

## Guardrails
- Do not spawn raw Codex or Claude sessions yourself.
- Do not create or manage OpenClaw cron jobs.
- Do not use `send_message`; cron delivery is automatic.
- Do not report extra commentary outside the required output shape.

## Verification
Confirm the final answer matches the CLI contract exactly:
- `NO_REPLY` for noop
- otherwise a compact summary only

## Notes
In this workflow, the state files can already show the reconciled next action even when the CLI call itself hangs. That is the correct fallback for a timed-out tick.