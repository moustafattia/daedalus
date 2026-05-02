# Codex app-server Smoke Tests

Daedalus has two Codex app-server confidence layers.

## CI Fake Harness

The default tests use a deterministic fake app-server. They do not require a
Codex install, model quota, or network access, and they can force protocol
events that are hard to reproduce with a real model.

```bash
pytest tests/test_runtimes_codex_app_server.py \
  tests/test_workflows_issue_runner_workspace.py \
  -k codex
```

This verifies JSON-RPC start/resume, WebSocket reuse, cancellation,
read/stall timeout behavior, token/rate-limit mapping, external WebSocket auth,
malformed protocol failures, and workflow scheduler thread persistence.

## Real Local Smoke

Run the real smoke only on a machine with a working `codex` CLI and app-server
auth. It starts a real `codex app-server` subprocess, sends a tiny prompt,
persists the returned thread id, then resumes the same thread for a second
tiny prompt.

```bash
DAEDALUS_REAL_CODEX_APP_SERVER=1 \
pytest tests/test_runtimes_codex_app_server.py \
  -k real_smoke_start_and_resume -q -s
```

Optional model override:

```bash
DAEDALUS_REAL_CODEX_MODEL=gpt-5.4-mini \
DAEDALUS_REAL_CODEX_APP_SERVER=1 \
pytest tests/test_runtimes_codex_app_server.py \
  -k real_smoke_start_and_resume -q -s
```

Keep this test opt-in. It depends on local Codex installation, account state,
quota, model availability, and live runtime timing. Use it before production
changes to Codex runtime/service behavior, not as a required CI gate.

## Change-Delivery Fixture Smoke

`change-delivery` has an opt-in self-contained smoke for the first live lane
dispatch. It creates a temporary GitHub issue with a unique active-lane label,
builds a temporary `change-delivery` workflow root, dispatches one real Codex
app-server lane turn, verifies durable thread state and tracker feedback, then
cleans up the issue, label, branch, PR, and `/tmp/issue-<n>` worktree if they
were created.

```bash
DAEDALUS_CHANGE_DELIVERY_CODEX_E2E=1 \
DAEDALUS_CHANGE_DELIVERY_E2E_REPO=your-org/your-repo \
pytest tests/test_change_delivery_codex_app_server_smoke.py -q -s
```

Optional controls:

```bash
export DAEDALUS_CHANGE_DELIVERY_E2E_REPO_PATH=/path/to/local/checkout
export DAEDALUS_CHANGE_DELIVERY_E2E_ACTIVE_LABEL=daedalus-active-smoke
export DAEDALUS_CHANGE_DELIVERY_CODEX_MODEL=gpt-5.4-mini
```

If `DAEDALUS_CHANGE_DELIVERY_E2E_REPO_PATH` is not set, the test clones the
repository into a temporary directory. The repository must have an `origin/main`
branch because `change-delivery` currently prepares lane worktrees from
`origin/main`.

This proves live GitHub issue selection, repo-owned workflow construction,
Codex app-server dispatch, thread persistence, and tracker feedback for the
flagship workflow. It is still not the full issue-to-PR-to-review-to-merge E2E.

## One-Command Live Harness

Use the local harness to run every live smoke whose environment is configured:

```bash
scripts/smoke-live.sh --list
scripts/smoke-live.sh
```

The harness skips unconfigured smokes and runs only the ones with their required
environment variables present.

## Token Accounting Rule

When Codex emits both `tokenUsage.last` and cumulative `tokenUsage.total`,
Daedalus records `last` as the per-turn delta for workflow totals. If only
`total` is available, Daedalus uses that value. This avoids double-counting
cumulative thread totals across resumed turns.
