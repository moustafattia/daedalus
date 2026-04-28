---
name: yoyopod-lane-automation
description: Operate the Hermes-owned YoyoPod issue-lane workflow through the daedalus plugin CLI instead of raw file archaeology.
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [yoyopod, workflow, automation, github, openclaw]
---

# YoyoPod Lane Automation

Use this skill when asked to run, resume, pause, reconcile, inspect, or migrate the YoyoPod issue-lane workflow.

## Purpose

The real workflow engine lives inside the daedalus plugin at
``adapters/yoyopod_core/*``. The historical ``scripts/yoyopod_workflow.py``
wrapper has been retired; the plugin's own ``__main__.py`` is now the CLI.
OpenClaw cron is retired for YoyoPod operations. Do not inspect or mutate
raw workflow files by hand unless the plugin CLI fails.

Use the plugin CLI:

`python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod <command>`

## Sources of truth

Use these in order:
1. GitHub issue label `active-lane`
2. wrapper-derived status via `status --json` and `nextAction`
3. Daedalus operator surfaces plus recurring Hermes support jobs `yoyopod-daedalus-outage-alerts` and `yoyopod-workflow-milestone-telegram`
4. `/home/radxa/.hermes/workflows/yoyopod/memory/yoyopod-workflow-status.json`
5. retired OpenClaw cron files only for history/debugging

## Supported commands

### V3 control-loop commands

Use these as the primary workflow operations now:

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod status --json
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod tick --json
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod dispatch-claude-review --json
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod dispatch-implementation-turn --json
```

Important V3 semantics:
- `build_status()` is the read model and now exposes `nextAction`
- `reconcile()` persists truthful state and lane artifacts; it is not the main forward-motion loop anymore
- Daedalus active service owns production forward motion
- `tick()` remains a manual wrapper-owned fallback/operator command and should execute at most one forward action per invocation
- there is no standalone Claude fallback runner in the intended topology
- `tick()` now executes all major forward actions directly through the wrapper: `dispatch_claude_review`, `dispatch_implementation_turn`, `publish_ready_pr`, and `merge_and_promote`
- when a published PR reaches `approved` / `reviewLoopState=clean` with no merge blockers, `nextAction` should become `merge_and_promote`; if `tick()` reports that action but `executed=null`, the wrapper is broken

### 1. Show status

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod status
```

For full machine-readable output:

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod status --json
```

This also writes or can refresh:
- `~/.openclaw/workspace/memory/yoyopod-workflow-health.json`
- `~/.openclaw/workspace/memory/yoyopod-workflow-audit.jsonl`

### 2. Reconcile workflow state

Use when the ledger looks stale or an active GitHub lane is not reflected locally.

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod reconcile
```

To also disable broken ad-hoc issue watcher jobs with invalid Telegram announce delivery:

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod reconcile --fix-watchers
```

### 3. Resume the automation

Enable the current core workflow jobs and wake them immediately:

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod resume
```

### 4. Run workflow doctor

Use this first when the workflow looks odd:

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod doctor
```

### 5. Pause the automation

Disable the current core workflow jobs:

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod pause
```

### 6. Wake the automation now

Keep jobs enabled and pull their next run time forward:

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod wake
```

### 7. Focused inspection

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod show-active-lane
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod show-core-jobs
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod show-lane-state
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod show-lane-memo
```

### 8. Cheap Claude-review preflight / event wake

```bash
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod preflight-claude-review
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod preflight-claude-review
python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod tick --json
```

## Migration / engine-ownership rules

When migrating YoyoPod workflow ownership from OpenClaw to Hermes:
- commit the workflow-related OpenClaw workspace artifacts first
- create a backup tarball of the related artifacts before changing scheduler ownership
- canonical Hermes-side locations are:
  - workflow artifacts: `~/.hermes/workflows/yoyopod`
  - repo clone: `~/.hermes/workspaces/YoyoPod_Core`
- move workflow docs/config/scripts/tests/memory/state near the engine instead of leaving them in the old OpenClaw workspace
- create Hermes cron jobs for the watchdog and Telegram closure notifier
- disable the old OpenClaw YoyoPod jobs instead of deleting them; archaeology matters and split-brain schedulers are stupid
- keep workflow policy in the plugin's adapter code/config (`adapters/yoyopod_core/*`, `config/yoyopod-workflow.json`), not in cron prompt prose
- if you clone the repo from the old local OpenClaw checkout, immediately reset `origin` back to the real GitHub URL; otherwise `gh` commands fail because the remote points at a local path instead of GitHub
- recreate the active `/tmp/yoyopod-issue-*` worktree against the new Hermes-side repo clone so the workflow does not keep a split-brain binding to the retired OpenClaw clone
- if the active lane was migrated mid-flight, expect the stored `resumeSessionId` for the persistent Codex session to be stale; verify `acpx ... sessions ensure --resume-session ...` still works, and if it fails with `Resource not found`, fall back to ensuring the session again without `--resume-session`
- stale-lane detection for no-PR lanes should key off recent lane-state progress (`lastMeaningfulProgressAt` / recent session activity), not the coarse `implementation.updatedAt` field alone; otherwise an actively moving local repair loop can be mislabeled stale and suppressed with `nextAction=noop`
- if post-publish Codex Cloud findings exist but `nextAction` falls back to `noop`, check whether the implementation session became `restart-session`; the repair-handoff path only routes through a live/routable session, so stale session resume failures can silently suppress the handoff until session health is recovered
- previous wrapper gap (now fixed in `scripts/yoyopod_workflow.py`): after a post-publish repair handoff, if Codex produced a new local commit while the PR already existed, `_derive_next_action()` had no branch for "open PR + local worktree ahead of PR head". The watchdog could loop on `nextAction=noop` forever even though there was unpublished repair work ready to push.
- current fixed behavior: `_derive_next_action()` now tolerates `health=stale-ledger`, derives `push_pr_update` when the local worktree head is ahead of the existing PR head, and `tick()` executes that path through `push_pr_update()`.
- current fixed behavior: actionable post-publish Codex Cloud findings no longer require a routable `continue-session`/`poke-session` only; if the session recommendation is `restart-session`, `_derive_next_action()` can still return `dispatch_codex_turn` in `postpublish_repair` mode.
- fallback if the wrapper ever regresses again: if the lane worktree is ahead of the PR branch while `nextAction=noop`, inspect `git status --branch`, run the narrow lane tests, push `HEAD` to the lane branch manually, then run wrapper `reconcile`. This recovers the lane back into truthful post-publish review without waiting for the dead session to publish its own fix.
- if a migrated mid-flight lane uses `acpx ... sessions ensure --resume-session ...` and that resume id is dead, patch or expect fallback behavior: retry `ensure` without `--resume-session` when acpx returns `Resource not found`; otherwise the lane can get wedged with a stale session and never re-enter the repair loop
- when investigating whether Codex has started on Claude findings, do not trust only the current `reviews.claudeCode` block; also inspect `.lane-state.json -> sessionControl.lastClaudeRepairHandoff`, compare its `headSha` to the current `implementation.localHeadSha`, and check git history. If the current head moved forward after the handoff, Codex already started even if the wrapper has since reset Claude status to pending for the newer head
- after moving paths, run `reconcile` before judging watchdog behavior; stale ledger drift can make `tick` look idle for the wrong reason until reconcile normalizes truth
- after manually pushing a post-publish repair head, an immediate `reconcile` can still leave the ledger saying `approved/clean` while derived state is `awaiting_reviews` if Codex Cloud has already dropped a fresh PR-body `eyes` reaction on the new head; treat that as `stale-ledger`, trust the derived state/review signal, and wait for the next reconcile/tick instead of treating the lane as actually approved
- verify migration with `status --json`, a Hermes cron job list, and at least one manual watchdog run
- commit the migration artifacts separately from unrelated workspace dirt


## Core jobs managed by the wrapper

- `yoyopod-workflow-milestone-telegram`

Current recurring Hermes support jobs:
- `yoyopod-daedalus-outage-alerts`
- `yoyopod-workflow-milestone-telegram`

Recommended resource-optimized cadence:
- Daedalus outage alerts: every 5 minutes
- Telegram closure notifier: every 60 minutes

V3 topology:
- Daedalus active service is the primary orchestrator
- old job `yoyopod-workflow-watchdog` is retired from primary ownership
- `tick --json` remains available as a manual fallback/operator command
- old jobs `yoyopod-workflow-checker` and `yoyopod-claude-review-runner` are retired/removed in the final topology
- Claude review is wrapper-owned via `dispatch-claude-review` when operators invoke the wrapper path directly

Phase 1 session-preservation policy:
- write `.lane-state.json` and `.lane-memo.md` into the active lane worktree
- prefer continuing a healthy active Codex session rather than restarting it
- if the wrapper recommends `poke-session`, treat that as a one-cycle grace state for a still-open but recently idle session; do not spawn a fresh restart yet
- record that explicit grace-state poke in `sessionNudge` / `.lane-state.json -> sessionControl.lastNudge`
- Implementation lanes now use wrapper-managed persistent `acpx codex` sessions keyed per lane worktree, not raw one-shot ACP turns
- the wrapper command `python3 /home/radxa/.hermes/plugins/daedalus/workflows/__main__.py --workflow-root /home/radxa/.hermes/workflows/yoyopod dispatch-implementation-turn --json` owns session ensure/restart/prompt delivery for the active lane
- Codex model routing for wrapper-owned implementation sessions should live in `~/.hermes/workflows/yoyopod/config/yoyopod-workflow.json` under `sessionPolicy`: use `codexModel` as the default (for example `gpt-5.3-codex-spark/high`) and `codexModelLargeEffort` for `effort:large` lanes (fallback also accepts `effort:high` label)
- Current fixed behavior: top-level ledger field `ledger.codexModel` should stay synchronized with the live implementation model instead of leaking stale historical values from an older lane session.
- Current fixed behavior: wrapper-owned Claude pre-publish review should use structured bounded CLI invocation (`--output-format json`, `--json-schema`, configurable `claudeReviewMaxTurns`) rather than free-form print output.
- Real runtime finding: low Claude turn budgets were too tight on real YoyoPod lane reviews and can fail with Claude `error_max_turns`; the workflow now reads `reviewPolicy.claudeReviewMaxTurns` (currently 12). If local review starts crashing in `dispatch-claude-review`/`tick`, check the exact Claude subprocess result first instead of treating it as generic workflow flakiness.
- Current fixed behavior: model escalation should route to `gpt-5.4` when the lane has clearly entered a costly repair loop, such as repeated restart cycles, repeated local Claude review cycles, or large post-publish finding counts.
- Current fixed behavior: `.lane-state.json` should track lightweight failure and budget metadata so the wrapper can reason about repeated failure classes and stalled/no-progress loops without reconstructing everything from scratch each tick. The current fields are `failure.lastClass/detail/retryCount/lastAt` and `budget.noProgressTicks/lastEvaluatedAt`.
- current fixed behavior: `_derive_next_action()` should actively use those budgets. When `budget.noProgressTicks >= laneNoProgressTickBudget` on a local implementation lane, it should re-dispatch an implementation turn with reason `no-progress-budget-reached` instead of idling forever. When `failure.retryCount >= laneFailureRetryBudget` on an actionable lane, it should force another implementation/repair turn with reason `failure-retry-budget-reached`.
- Current fixed behavior: after a higher threshold (`laneOperatorAttentionRetryThreshold` / `laneOperatorAttentionNoProgressThreshold`), the wrapper should stop pretending autonomy is still working. `build_status()` should surface `stale-lane` with `operator-attention-required:*` reasons, `_derive_next_action()` should usually return `noop` with reason `operator-attention-required`, and `reconcile()` should persist `workflowState=operator_attention_required` / `reviewState=operator_attention_required` with `blockedReason=operator-attention-required`.
- Critical hardening learned on lane 203: operator-attention is not allowed to block closure of a clean published PR forever. If a PR is open, `reviewLoopState == clean`, and merge blockers are empty, `_derive_next_action()` must prefer `merge_and_promote` ahead of the operator-attention noop path. Otherwise a stale Codex session can wedge an already-approved lane for hours.
- Critical hardening learned on lane 203: `latest_progress` cannot be derived only from `implementation.updatedAt` / implementation status. A clean post-publish review signal (for example Codex Cloud `PASS_CLEAN` / PR-body `+1`) must count as meaningful progress so `noProgressTicks` resets after review success instead of continuing to climb against a stale implementation timestamp.
- Critical hardening learned on lane 203: repeated `reconcile()` calls must not inflate failure/no-progress counters by themselves. The wrapper now uses a minimum increment window (`laneCounterIncrementMinSeconds`, default 240s) and should treat approved/merged progress kinds as resetting `noProgressTicks` instead of accumulating stale-lane pressure every pass.
- Critical hardening learned on lane 203: `failure.retryCount` and `restart.count` should come from real implementation dispatch attempts, not inferred stale state. The wrapper now persists attempt markers in implementation/lane-state (`lastDispatchAttemptId/At`, `lastRestartAttemptId/At`) and `write_lane_state()` should only increment those counters when the attempt markers actually advance.
- Critical hardening learned on lane 203: once post-publish Codex Cloud truth is already `PASS_CLEAN` with zero open findings, stale session state should no longer classify the lane as actively failing. `session_stale` / `stale-open-session` are debugging facts at that point, not retry-budget evidence.

- Important runtime finding from lane #203 stale-lane investigation: once `operator_attention_required` is latched, the current wrapper can keep returning `nextAction=noop` even after the published PR later becomes `reviewLoopState=clean` with no merge blockers. In that state the lane is effectively approved-but-frozen. If status shows `health=stale-lane`, `workflowState=operator_attention_required`, `derivedReviewLoopState=clean`, and `derivedMergeBlocked=false`, treat that as a wrapper bug/regression, not a real review block.
- Important runtime finding from lane #203 stale-lane investigation: the persisted lane `lastMeaningfulProgressAt` currently keys off implementation timestamps/status too narrowly. Review-side success signals like Codex Cloud `+1`, auto-resolved threads, or transition to clean approval may fail to reset `budget.noProgressTicks`, so the lane can look stale long after it actually succeeded.
- Hardened operator rule: a clean, mergeable published PR should not stay blocked behind stale-session bookkeeping. If you see the approved-but-frozen pattern above, capture `status --json`, `show-lane-state`, `acpx codex sessions show <lane-session>`, and the relevant audit-log window first; then patch the wrapper so merge eligibility outranks stale operator-attention counters, and so those counters reset on real progress.
- Current fixed behavior: actor identity should be standardized separately from concrete model names. `config/yoyopod-workflow.json` now supports `agentLabels` for generic names like `Internal_Coder_Agent`, `Escalation_Coder_Agent`, `Internal_Reviewer_Agent`, `External_Reviewer_Agent`, and `Advisory_Reviewer_Agent`.
- Current fixed behavior: persisted workflow truth should expose a top-level `ledger.workflowActors` map, and status should mirror it under `status.ledger.workflowActors`, while implementation/review entries should also carry `agentName` / `agentRole`. This lets operators swap backing models later without rewriting workflow semantics around vendor names.
- Current fixed behavior: workflow truth surfaces should expose configurable generic actor identities instead of forcing vendor names into operator-facing fields. Use config `agentLabels` for names like `Internal_Coder_Agent`, `Escalation_Coder_Agent`, `Internal_Reviewer_Agent`, `External_Reviewer_Agent`, and `Advisory_Reviewer_Agent`. Persist/show these through canonical top-level `ledger.workflowActors`, plus `implementation.agentName/agentRole` and `reviews.*.agentName/agentRole`, while keeping the existing internal review-map keys (`claudeCode`, `codexCloud`, `rockClaw`) stable for compatibility.
- the wrapper must thread the selected per-lane Codex model into both `acpx codex sessions ensure` and `acpx codex prompt`
- the selected Codex model should also be visible in workflow truth surfaces: `status --json -> implementation.codexModel`, `status --json -> ledger.codexModel`, and persisted ledger field `ledger.implementation.codexModel`
- use the lane memo/state files as the primary restart handoff instead of giant restart prompts
- when the active lane changes after a merge/promotion, the wrapper must rebind implementation state to the new lane instead of carrying over the prior lane session/worktree/branch
- the expected fresh-lane targets are deterministic: worktree `/tmp/yoyopod-issue-{N}` and branch `codex/issue-{N}-{slug}`
- before any implementation turn, the wrapper must ensure `/tmp/yoyopod-issue-{N}` is a real git worktree, not just a metadata directory containing `.lane-*` files
- for a fresh lane with no PR, the wrapper should fetch `origin/main` and materialize/reset the lane worktree from `origin/main`
- for an existing lane PR, the wrapper should fetch `origin/<branch>` and materialize the lane worktree from that branch tip instead of letting the agent drift into some unrelated repo copy
- preserve `.lane-state.json` and `.lane-memo.md` when rebuilding the lane worktree so session handoff survives re-materialization
- when there is no open PR on the new lane, required review state should reset to pending rather than inheriting stale findings from the previous lane
- current hardened review flow is phase-based: local unpublished branch heads are gated by Claude Code first, and Codex Cloud does not become required until the PR is non-draft / ready for review
- the pre-publish Claude policy is now single-pass per lane head family: run one Claude local review, feed any findings back to Codex once, then publish after Codex's follow-up head without requiring a second or third Claude pass on subsequent local fix heads
- current hardened review flow is phase-based: local unpublished branch heads are gated by Claude Code first, and Codex Cloud does not become required until the PR is non-draft / ready for review
- current hardened review flow is phase-based: local unpublished branch heads are gated by Claude Code first, and Codex Cloud does not become required until the PR is non-draft / ready for review
- draft PRs must be represented honestly as `codexCloud.status=not_started`, not pending
- Claude pre-publish review policy is configurable via `reviewPolicy.claudePassWithFindingsReviews` in `config/yoyopod-workflow.json`
- Claude review model selection should also live in `config/yoyopod-workflow.json` under `reviewPolicy.claudeModel` (currently pinned to `claude-sonnet-4-6`), and wrapper-owned local review execution should pass it explicitly via `claude --model <id> --permission-mode bypassPermissions --print ...`
- the configured Claude model should be visible in workflow truth surfaces: `status --json -> reviews.claudeCode.model`, `status --json -> ledger.internalReviewerModel`, and persisted review field `ledger.reviews.internalReview.model` (the top-level `ledger.claudeModel` field has been dropped in Phase D-3)
- current intended semantics:
  - `REWORK` always forces Codex repair plus another Claude review on the new local head
  - `PASS_WITH_FINDINGS` allows publish after the configured number of local Claude review passes has been consumed; with `claudePassWithFindingsReviews = 1`, Codex fixes once and the lane publishes without another Claude pass
  - `PASS_CLEAN` always satisfies the local Claude gate immediately
- after the local Claude pre-publish gate is satisfied under that policy, publish the branch and make the PR ready for review immediately; do not leave it sitting as a draft waiting for Codex Cloud
- the wrapper states for this flow should be interpreted as: `implementing_local` -> `awaiting_claude_prepublish` / `claude_prepublish_findings` -> `ready_to_publish` -> published ready-for-review PR with Codex Cloud gate -> `approved`
- once the PR is ready for review, stale pre-publish state labels are wrong; normalize the lane to post-publish review or approved state based on Codex Cloud/checks truth
- if GitHub CLI label filtering is flaky, do not trust `gh issue list --label active-lane` alone; list open issues and filter the `labels[].name` client-side to resolve the real active lane deterministically

- active-lane detection should not rely on `gh issue list --label active-lane`; list open issues and filter labels client-side, because GitHub CLI label filtering can intermittently miss the active-lane issue even when `gh issue view` shows the label correctly
- stale-lane detection for no-PR lanes should key off lane-state progress (`lastMeaningfulProgressAt`, recent session activity, current local head) rather than `implementation.updatedAt` alone, or the wrapper can suppress a live local repair loop with a false `stale-lane`
- important Daedalus active-runtime finding from lane 220: a no-PR lane sitting in `claude_prepublish_findings` or `rework_required` with a completed internal Claude review can still need an implementation restart/repair turn. Daedalus action derivation must not fall through to `[]` there just because the session is stale or the PR does not exist yet; for stale local repair loops with completed internal review and no active PR, derive `dispatch_implementation_turn` instead of idling.
- important Hermes-engine finding: when `engineOwner=hermes`, cron job entries may legitimately store `state` as a string like `"scheduled"` instead of a dict. Workflow status/Daedalus compatibility code must tolerate that shape and fall back to top-level `next_run_at`, `last_run_at`, and `last_status` fields rather than assuming `job["state"].get(...)` exists. If Daedalus/status crashes with `AttributeError: 'str' object has no attribute 'get'`, inspect `~/.hermes/cron/jobs.json` first before "fixing" the wrong layer.
- critical lane 220 follow-up fix: if wrapper-owned `dispatch-claude-review` fails before Claude returns a structured result, do not leave `reviews.claudeCode.status=running` with a fresh `requestedAt/requestedHeadSha`. Reset it to a retryable pending state and clear the request markers; otherwise `_claude_review_preflight()` can suppress the lane for the full cooldown window with `claude-review-request-recent` even though nothing is actually running.
- critical lane 220 debugging finding: when Claude review appears broken, reproduce with the exact wrapper-generated `claude -p ... --output-format json --json-schema ...` command in the same lane worktree before blaming the CLI globally. Minimal demo prompts can succeed while the workflow looks wedged; that usually means wrapper recovery/state handling is wrong or the earlier failure was transient. Also note that `claude doctor` is not a reliable automation probe here because it can fail under non-real-TTY stdin/raw-mode conditions even while `claude -p` works.
- critical Daedalus retry fix from lane 220: failed active `request_internal_review` actions must not permanently consume the active idempotency key for that head. When Daedalus needs the same internal review again after a failed active request, it should enqueue a fresh retry action with incremented `retry_count`, use a retry-suffixed idempotency key, and link the failed predecessor via `superseded_by_action_id`.
- critical lane-220/Claude-review failure mode: `dispatch_claude_review()` currently writes `reviews.claudeCode.status="running"` and `requestedAt/requestedHeadSha` before invoking the Claude CLI. If `_run_claude_code_review()` then exits non-zero, the wrapper leaves that stale `running` state behind. `_claude_review_preflight()` will treat it as a recent in-flight request and suppress rerun for `CLAUDE_REVIEW_REQUEST_COOLDOWN_SECONDS`, so `status --json` can show `workflowState=awaiting_claude_prepublish`, `reviewStatus=running`, `shouldRun=false`, and `nextAction=noop` even though no review is actually running.
- corresponding wrapper hardening: after a `dispatch_claude_review()` subprocess failure, reset the Claude review back to a retryable pending state and clear `requestedAt`, `requestedHeadSha`, and `reviewScope`; also make `_claude_review_preflight()` apply `claude-review-request-recent` only when the review status is actually `running`, not merely because stale request markers exist.
- corresponding Daedalus runtime failure mode: after an active `request_internal_review` action fails, Daedalus still keeps the same active-action idempotency key (`active:request_internal_review:<lane>:<head>`). `request_active_actions_for_lane()` inserts with `ON CONFLICT(idempotency_key) DO NOTHING`, only returns already-`requested` rows or newly inserted rows, and ignores prior `failed` rows. Net effect: once a `request_internal_review` action fails for a head, Daedalus can return `[]` forever for that same head even after wrapper preflight later recovers and says `nextAction=run_claude_review`. When debugging a stuck no-PR Claude gate, inspect `state/daedalus/daedalus.db` tables `lane_actions`, `lane_reviews`, and `lanes` before blaming derivation.
- operator rule from that failure mode: if wrapper status says `nextAction=run_claude_review` but Daedalus `request-active-actions` returns `[]`, check for a failed active `request_internal_review` row with the same target head in `state/daedalus/daedalus.db`. That means the lane is wedged by active-action idempotency, not by current wrapper derivation.
- postpublish repair parity finding from lane 220: Daedalus shadow/action derivation for published findings must mirror wrapper session-routability semantics. If Codex Cloud findings are open on the current PR head and the coder session is stale / `restart-session`, Daedalus must derive `dispatch_implementation_turn` (restart the coder lane) instead of `dispatch_repair_handoff`; the handoff action only works for routable `continue-session` / `poke-session` lanes. Otherwise Daedalus can execute an active repair-handoff action that returns `not-dispatched` forever while wrapper status correctly says the lane needs a postpublish repair restart.
- important runtime finding from reproducing the later Claude CLI scare: do not assume the Claude CLI is globally broken just because the workflow lane saw a transient `CalledProcessError`. Reproduce with the exact wrapper-generated `claude -p ... --output-format json --json-schema ...` command inside the same lane worktree. Minimal demo prompts and even the exact structured review prompt can succeed later while the lane still looks wedged due to stale wrapper/Daedalus state. Also, `claude doctor` is a lousy health probe in some automation terminals here because Ink raw-mode stdin can fail even while `claude -p` works normally.
- critical lane 221 Codex finding: ACP prompt failures that print only `Internal error` can hide a structured usage-limit root cause in `~/.acpx/sessions/<record>.stream.ndjson`. For lane 221, the real JSON-RPC error was `codex_error_info=usage_limit_exceeded` for `gpt-5.3-codex-spark/high` even though the wrapper traceback only showed `CalledProcessError`.
- operator/debug rule from that failure: when `acpx ... codex prompt -s ...` returns non-zero with vague output, inspect the latest `~/.acpx/sessions/*.stream.ndjson` for the active lane session before blaming workflow logic. The stream contains the real JSON-RPC error payload from `codex-acp`.
- recovery rule from that failure: if the selected Codex model hits `usage_limit_exceeded`, falling back requires a fresh session on the fallback model. Reusing the same session and only changing the `acpx codex prompt --model ...` CLI flag is not enough; the prompt can still run against the session's existing exhausted model. Close the lane session, ensure a new one on the fallback model (currently `gpt-5.4`), then resend the prompt.

Current notification behavior:
- the Telegram milestone job is closure-only
- it should send one update only when an issue is actually closed
- it should not send intermediate review/progress/ready-to-close milestones
- do not mark a closeout as announced unless message delivery actually succeeded; notifier state must only advance after confirmed send
- if Telegram delivery fails because the gateway is unconfigured, the target chat is invalid for the bot, or the API returns `chat not found`, leave notifier state unchanged and report the failure instead of pretending it worked
- for Telegram channel troubleshooting, first try the real channel target; if it fails with `Chat not found`, immediately send a DM test to a known-good Telegram contact to distinguish broken Telegram integration from a broken channel target. DM success + channel failure means the bot/channel configuration is wrong, not the Telegram client.
- when manually testing closeout delivery outside the normal Hermes tool runtime, verify the real gateway runtime has Telegram credentials and dependencies loaded before trusting the result; otherwise `send_message_tool` can falsely look unconfigured or fail on missing Telegram client deps even though the deployed gateway environment is fine
- Hermes `send_message` has cron duplicate-target suppression: if the current cron run is already configured to auto-deliver its final response to the exact same Telegram target, an explicit `send_message` to that target may return `success=true` with `skipped=true` and reason `cron_auto_delivery_duplicate_target`; treat that as not sent for closeout-notifier state advancement unless the final response itself is intentionally the user-facing notification

## What counts as healthy

Healthy means:
- exactly one GitHub issue has `active-lane`
- the current core jobs exist and are enabled
- core jobs are not stale relative to their schedules
- no enabled broken issue-specific watcher jobs are erroring on invalid delivery
- the workflow ledger matches the live active lane closely enough to trust operations

Common non-healthy states reported by the wrapper:
- `degraded`
- `stale-ledger`
- `stale-core-jobs`
- `stale-lane`
- `disabled-core-jobs`
- `missing-core-jobs`
- `multi-active-lane`

## Review loop model

Required reviewers now depend on phase:
- pre-publish / no ready-for-review PR yet: Claude Code only, reviewing the local lane worktree HEAD
- post-publish / PR ready for review: Codex Cloud only, using PR review threads and PR-body reactions

Merge rule:
- do not expect Codex Cloud to review draft PRs
- a PR is not mergeable while the required reviewer for the current phase has open major/blocking findings on the active head

Important ledger fields:
- `workflowState`
- `reviewLoopState`
- `repairBrief`
- `pr.mergeBlocked`
- `pr.mergeBlockers`
- `reviews.codexCloud`

Important verdict semantics:
- `PASS_CLEAN` — no meaningful remaining findings on the current head
- `PASS_WITH_FINDINGS` — nontrivial findings remain and must enter the repair loop
- `REWORK` — blocking issues must be fixed before merge

Current wrapper behavior:
- harvests Codex Cloud unresolved review threads from the live PR
- classifies severity from review badges (`P1`/`P2` => major blockers by default)
- interprets Codex Cloud PR-body bot reactions as a state machine: `eyes` means review is still pending on the current head, while `+1` means clean pass on the current head
- treats lingering open Codex Cloud threads as superseded only when a newer clean `+1` bot reaction lands on the PR body after those thread comments
- auto-resolves those superseded lingering Codex Cloud threads on GitHub when the clean `+1` signal is present and the review snapshot matches the same current `pr.headSha`
- records resolved Codex Cloud thread IDs in the ledger under `codexCloudAutoResolved` and in the audit log
- synthesizes a `repairBrief` when major/blocking findings exist
- derives `reviewLoopState` such as `awaiting_reviews`, `findings_open`, or `clean`
- sets `pr.mergeBlocked=true` with explicit `pr.mergeBlockers`

Operator rule:
- if a reviewer leaves actionable findings, do not treat the lane as approved even if the review text looks mostly positive
- re-run review on the next `pr.headSha` and keep iterating until the repair brief closes

## Operator guidance

### If asked to “run the YoyoPod automation”
Do this:
1. `status`
2. if degraded, `reconcile --fix-watchers`
3. `resume`
4. `status` again and report current active lane + health

### If asked to “what is the workflow doing?”
Run:
1. `status --json`
2. summarize:
   - health
   - active lane
   - open PR if any
   - ledger state
   - any drift or blocked condition

### If asked to “stop it for now”
Run:
1. `pause`
2. `status`

## Fallback only if wrapper fails

Only if the wrapper script is broken, inspect these directly:
- `~/.openclaw/cron/jobs.json`
- `~/.openclaw/workspace/memory/yoyopod-workflow-status.json`
- `~/.openclaw/workspace/memory/yoyopod-workflow-health.json`
- `~/.openclaw/agents/main/sessions/sessions.json`
- `~/.openclaw/subagents/runs.json`

## ACP session-control findings

Useful implementation-lane facts discovered during optimization work:
- OpenClaw exposes real ACP persistent-session support; ACP spawn modes are `run` (oneshot) and `session` (persistent).
- OpenClaw also exposes `sessions_send(sessionKey, message)` for sending a follow-up prompt into an existing visible session.
- ACP spawn supports `resumeSessionId` for reattaching to an existing ACP agent session via session/load instead of starting fresh.
- `acpx` supports model selection for Codex sessions via the global `--model <id>` flag, and the flag works on both `codex sessions ensure` and `codex prompt`.
- Verified available Codex ACP models currently include `gpt-5.4/*`, `gpt-5.3-codex/*`, and `gpt-5.3-codex-spark/*` reasoning tiers; use lowercase ids such as `gpt-5.3-codex-spark/high`.
- For an already-created session, `acpx codex set -s <session> model gpt-5.3-codex-spark` and `acpx codex set -s <session> reasoning_effort high` also work, but threading `--model` through session creation/prompting is the cleaner workflow-level control.
- Default observed Codex ACP session model on this host was `gpt-5.4/xhigh`, so the workflow will silently stay on that unless the wrapper passes an explicit model.
- For future YoyoPod workflow optimization, the clean target design is: spawn implementation lanes as persistent ACP sessions, store the child session key, and use `sessions_send` for nudges/follow-ups instead of repeatedly restarting oneshot Codex runs.
- Until that migration is implemented, treat current lane memo/state + `poke-session` as control-plane intent only, not true same-session prompt delivery.

Operator guidance:
- when asked what happens next, prefer `status --json` and read `nextAction`
- when asked to move the workflow forward, prefer `tick --json`
- current fixed behavior: `tick --json` now plans in-adapter and backgrounds long-running review/implementation dispatches instead of waiting in the foreground for the full child action to finish
- when `tick --json` starts a long-running action, expect a quick JSON response with an `executed.background=true` payload plus a `logPath`; then re-run `status --json` to confirm the lane moved into the expected running state
- current fixed behavior: `status --json` may now also expose `tickDispatch` with the last persisted background tick-dispatch metadata (`active`, `command`, `pid`, `logPath`, `startedAt`, `statePath`) when a backgrounded tick action is in play
- current fixed behavior: stale/dead `memory/tick-dispatch/active.json` state is auto-archived into `memory/tick-dispatch/history/` on the next status read instead of lingering forever as fake active state
- important shim pitfall: the live wrapper’s adapter loaders use `importlib.util.spec_from_file_location(...)`; if `import importlib.util` is missing at the top of `scripts/yoyopod_workflow.py`, adapter loading silently fails and the wrapper falls back to raw behavior. If newly installed adapter logic appears to be ignored, check that import first before debugging the wrong layer.
- `restart-actor-session --json` can still be long-running and silent; if it times out in the foreground, rerun it in the background and poll until it exits, then immediately re-run `status --json` to confirm the lane rebounded to a fresh session and the ledger is truthful again
- after `tick --json` merges/promotes a lane, always re-run `status --json` before touching the old worktree; the active lane can advance to a new issue number and the previous `/tmp/yoyopod-issue-<N>` path may no longer be the current truth
- if `restart-actor-session` re-materializes a new lane, expect the wrapper to spawn a fresh `acpx codex prompt -s lane-<N>` child and may take several minutes before any output appears; treat the absence of stdout as normal until the child process itself dies
- if a backgrounded `tick` is still silent, inspect the wrapper PID and its child processes with `ps --forest` or similar before calling it hung; a live child `acpx codex prompt -s <lane>` usually means the wrapper is actively dispatching the implementation turn and just not printing progress
- if that child `acpx codex prompt` eventually exits non-zero, grab the background process log/traceback and then re-read `yoyopod-workflow-status.json`, `yoyopod-workflow-health.json`, and the active lane `.lane-state.json`; the failed tick can still advance persisted state enough to show the real lane, current head, and unchanged `nextAction`
- if Claude pre-publish review is needed, let `tick` or wrapper-owned `dispatch-claude-review` handle it; do not rely on a separate Claude cron worker
- if you see stale references to `yoyopod-workflow-checker` or `yoyopod-claude-review-runner`, treat them as outdated documentation/history unless the user is explicitly asking about old runs

## Pitfalls

- Do not treat Hermes `cronjob.list` as the primary lane-state source. Use the wrapper first; use cron listing only to verify scheduler ownership/job presence.
- Do not trust the workflow ledger blindly when GitHub says otherwise.
- Do not reimplement the workflow in Hermes cron unless explicitly doing migration work.
- Do not hand-edit cron JSON when the wrapper can do the job.
- Do not assume a `/daedalus` executable exists in every Hermes environment; when it is missing, verify Daedalus control paths by importing the local plugin/module directly or by running the wrapper’s Python entrypoint.
- Daedalus `execute-action` is not a freeform "run this action type" escape hatch. It only executes an existing `lane_actions` row where `action_mode='active'` and `status='requested'`. If you guess or handcraft an action id, expect `missing-action` or `not-active-action`. First request/inspect the real active action row, or use the wrapper fallback command when you need to move the lane immediately.
- Treat legacy watchdog mode as an explicit status field (`legacyWatchdogMode`) and distinguish `primary_dispatcher` from `fallback_reconciler` in operator/reporting logic.
- The wrapper command `preflight-claude-review` already prints JSON by default. Prefer `python3 ... preflight-claude-review` with no extra flags. The wrapper now accepts `--json` as a compatibility no-op, but that flag is baggage, not the preferred invocation.
- When wrapper-owned Claude review looks stuck, inspect the actual OpenClaw main-session logs under `~/.openclaw/agents/main/sessions/*.jsonl` for the watchdog/tick turn that invoked `dispatch-claude-review`. Session logs are the ground truth for in-flight behavior.
- Reconcile now emits `claude-review-requested` and `claude-review-completed` audit events when the Claude review fields transition. If those events are absent, either nothing changed yet or you are looking before reconcile wrote the transition.
- When the workflow enters `claude_prepublish_findings` for a local unpublished head and the active Codex lane session is still routable (`continue-session` / `poke-session`), reconcile now auto-sends the current Claude repair brief back into that same Codex session and records it under `.lane-state.json -> sessionControl.lastClaudeRepairHandoff` plus audit event `claude-repair-handoff-dispatched`.
- That Claude-to-Codex repair handoff is same-review deduped by session name + reviewed head SHA + Claude review updatedAt, so repeated reconciles do not spam the same repair brief.
- When the workflow is post-publish and Codex Cloud leaves open findings on the current PR head, the wrapper now auto-sends a post-publish repair brief back into the same active Codex lane session and records it under `.lane-state.json -> sessionControl.lastCodexCloudRepairHandoff` plus audit event `codex-cloud-repair-handoff-dispatched`.
- That Codex-Cloud-to-Codex repair handoff is deduped by session name + reviewed head SHA + Codex Cloud review updatedAt, so repeated reconciles do not keep re-sending the same repair brief.
- `tick()` now executes publish and merge closeout itself. For `ready_to_publish`, it pushes/opens the PR ready-for-review through `publish_ready_pr()`. For an approved published PR, it runs `merge_and_promote()`, removes `active-lane` from the merged issue, closes it with a merge comment, and promotes the next prioritized open issue by adding `active-lane`.
- if the active lane was accidentally assigned to a tracker/container issue rather than a real implementation issue, do not let the workflow keep chewing on that nonsense. Manually remove `active-lane` from the tracker issue, add `active-lane` to the real next issue, comment on both issues explaining the handoff, run wrapper `reconcile --fix-watchers`, and verify with `status --json`. If the abandoned tracker lane left a stale `/tmp/yoyopod-issue-{N}` worktree behind, remove it with `git worktree remove --force /tmp/yoyopod-issue-{N}` once the lane has been reassigned.
- important current wrapper limitation: `_pick_next_lane_issue()` only parses `[P<number>]` from titles; anything else like `[S01]` or `[A01]` is treated as priority `999` and then sorted by issue number. So for manual tracker-lane cleanup, do not blindly trust the wrapper's idea of "next prioritized issue" unless the backlog is actually using `[P...]` titles.
- v3 topology: Daedalus active service owns recurring production orchestration; `tick --json` is a manual fallback/operator command rather than a scheduled heartbeat.
- The old jobs `yoyopod-workflow-checker` and `yoyopod-claude-review-runner` are retired/removed in the final v3 topology. If they appear, treat that as stale history or stale state, not current design intent.
- There is no standalone Claude fallback runner in the intended v3 topology; wrapper-owned `dispatch-claude-review` / `tick` should own that transition.
- `build_status()` should explain what happens next via `nextAction`; `reconcile()` should persist truthful state; `tick()` should execute at most one forward action per invocation.
- Current implementation-side Codex ACP behavior is restart-heavy: the same issue worktree is reused, but many implementation runs are fresh `oneshot` ACP sessions with new prompts like `Why you are being restarted`. That preserves filesystem state, not conversational token context.
- Because of that, prefer minimizing unnecessary Codex restarts, and when restart is unavoidable, rely on compact durable handoff artifacts in the worktree/ledger instead of ever-longer restart prompts.
- Current fixed behavior: while an implementation lane remains in `implementing` / `implementing_local` with `sessionActionRecommendation=continue-session` and the active Codex session is still healthy/fresh, `_derive_next_action()` should return `noop` with reason `fresh-session-still-working` instead of re-dispatching another full Codex turn every watchdog tick.
- Current fixed behavior: `_render_implementation_dispatch_prompt()` should keep `continue-session` / `poke-session` prompts compact and avoid re-inlining the raw GitHub issue body; reserve the full `Issue summary` block for `restart-session` turns where a new owner actually needs the full brief.


## Hermes engine note

- Hermes cron now owns the recurring outage-alert and closure-notifier jobs.
- OpenClaw cron jobs for YoyoPod should be considered archived/disabled, not active engine components.
- If `engineOwner=hermes`, the wrapper must read and mutate the live Hermes cron store at `~/.hermes/cron/jobs.json` for scheduler truth; `archive/openclaw-cron-jobs.json` is history only.
- If `engineOwner=hermes` and the wrapper is reading archived cron job snapshots for history, disabled archived jobs must not be allowed to degrade wrapper health into `disabled-core-jobs`; archived scheduler state is archaeology, not live engine truth.
- If redesigning YoyoPod away from the watchdog-centric topology, do not do an in-place rip-and-replace first. Safest migration strategy is: take an immediate timestamped snapshot backup of `~/.hermes/workflows/yoyopod` and related active lane artifacts, build the async orchestrator as a side-by-side V2 engine, run it in shadow mode against live state without mutating GitHub, then cut over with an explicit single-owner lease/feature flag and retire the watchdog only after V2 proves itself on real lanes.
- For that async redesign, the target runtime should be a dedicated long-lived orchestrator service/process with one stable Hermes-visible session projection, not cron-resumed fresh runs and not ordinary chat sessions pretending to be daemons.
- Use SQLite as canonical current workflow state and queue/lease storage; keep append-only JSONL event/action/error logs for audit/replay/debugging. Do not make JSONL the canonical store.
- Keep persistent lane ACP sessions as the primary execution unit for the active coder lane. Do not replace the main implementation lane with stateless `delegate_task` workers; reserve stateless subagents for bounded analysis/review/error-triage only.
- In the async design, preserve the wrapper as the policy brain: orchestrator decides, actors execute, analysts diagnose, state store remembers. LLM-driven replanning should be bounded to ambiguous recovery/failure analysis, with fixed legal outputs, not allowed to freestyle workflow policy.
- Current architecture direction: the YoYoPod wrapper should move into the Daedalus plugin tree as a first-class project adapter, not remain a sidecar script outside the plugin. Preferred layout is Daedalus core at the plugin root with project-specific code under something like `.hermes/plugins/daedalus/projects/yoyopod/`.
- Keep the boundary clean: Daedalus core owns generic orchestration/runtime concerns (leases, DB state, queues, retries, alerts, supervision, operator surfaces), while the YoYoPod adapter owns project semantics (`build_status`, `reconcile`, `tick`, review/publish/merge policy, lane/worktree/session conventions, project prompts, and GitHub-specific logic).
- Do not solve this by merging YoYoPod workflow semantics directly into `runtime.py` or by dropping `yoyopod_workflow.py` into plugin root unchanged. That only relocates the mess. The useful split is: one plugin package containing Daedalus engine plus a YoYoPod adapter module tree.
- Keep project config/data outside the plugin code and near the workflow root (for example `~/.hermes/workflows/yoyopod/config/yoyopod-workflow.json`). Code moves into the plugin; mutable project state/config stays with the project.
- Lowest-risk migration path: first relocate the wrapper into the plugin tree with a thin compatibility shim at the old script path if needed, then refactor internal module boundaries (`status`, `actions`, `reviews`, `sessions`, `prompts`, `config`) after behavior parity is preserved.
