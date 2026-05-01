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

`change-delivery` has an opt-in fixture smoke skeleton for prepared workflow
roots. It does not create GitHub issues or PRs by itself; point it at a workflow
root you already prepared with a `change-delivery` `WORKFLOW.md` and at least
one `codex-app-server` runtime profile.

```bash
DAEDALUS_CHANGE_DELIVERY_CODEX_E2E=1 \
DAEDALUS_CHANGE_DELIVERY_E2E_WORKFLOW_ROOT=~/.hermes/workflows/your-org-your-repo-change-delivery \
pytest tests/test_change_delivery_codex_app_server_smoke.py -q -s
```

This is the first harness anchor for a fuller issue-to-PR-to-review-to-merge
Codex app-server E2E. Keep it opt-in until the fixture can create and clean up
its own GitHub issue, branch, PR, and review artifacts.

## Token Accounting Rule

When Codex emits both `tokenUsage.last` and cumulative `tokenUsage.total`,
Daedalus records `last` as the per-turn delta for workflow totals. If only
`total` is available, Daedalus uses that value. This avoids double-counting
cumulative thread totals across resumed turns.
