# Harness Engineering

Daedalus uses repo-level harness checks to keep the public project clean while
the implementation continues to move quickly.

## Public Posture

The public release is GitHub-first:

- `change-delivery` is the supported managed SDLC workflow.
- `issue-runner` supports GitHub as the first-class tracker path.
- `local-json` exists for local development and deterministic tests.
- Linear remains an experimental adapter until the GitHub path has real
  integration coverage and stronger operator docs.

## Guardrails

The harness tests should catch these regressions before review:

- public docs must describe the GitHub-first path clearly
- public examples must use generic placeholders like `your-org/your-repo`
- bundled workflow templates must match their public docs copies
- bootstrap must safely promote `WORKFLOW.md` to `WORKFLOW-<workflow>.md`
  without overwriting existing named contracts
- `daedalus/projects/` must stay placeholder-only in the public repository
- installation docs must keep the landing-page quick start short and link to
  detailed operator docs
- Codex app-server tests must cover fake protocol behavior in CI and keep the
  real app-server smoke opt-in
- Issue-runner cleanup tests must prove `before_remove` runs before terminal
  workspaces are deleted

## Next Checks

Add tests for the next hardening slice in this order:

1. CLI/docs drift checks for every command shown in the install guide.
2. End-to-end `change-delivery` Codex app-server smoke around a real active
   lane, PR update, and review loop.
3. Live GitHub recovery coverage for labels, comments, and failure replay.

## Live GitHub Smoke

The first live GitHub smoke is implemented but skipped by default:

```bash
export DAEDALUS_GITHUB_SMOKE_REPO=your-org/your-repo
pytest tests/test_github_issue_runner_smoke.py -q
```

See [operator/github-smoke.md](operator/github-smoke.md) for setup and cleanup
details.

## Codex app-server Smoke

The Codex app-server protocol harness runs in normal CI with a fake app-server.
The real app-server smoke is skipped by default:

```bash
DAEDALUS_REAL_CODEX_APP_SERVER=1 \
pytest tests/test_runtimes_codex_app_server.py \
  -k real_smoke_start_and_resume -q -s
```

See [operator/codex-app-server-smoke.md](operator/codex-app-server-smoke.md)
for the fake/real split and token accounting rule.
