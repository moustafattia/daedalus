# Harness Engineering

Daedalus uses repo-level harness checks to keep the public project clean while
the implementation continues to move quickly.

## Public Posture

The public release is tracker-neutral in shape and GitHub-first in production
coverage:

- `issue-runner` is the default managed workflow and uses the shared tracker
  boundary.
- `change-delivery` is the opinionated GitHub-first SDLC workflow, with
  `tracker` and `code-host` kept as separate config boundaries.
- GitHub is the first-class production tracker adapter.
- `local-json` exists for local development and deterministic tests.
- Linear remains an experimental adapter until the GitHub path has real
  integration coverage and stronger operator docs.

## Guardrails

The harness tests should catch these regressions before review:

- public docs must keep the workflow story tracker-neutral while documenting
  GitHub as the first-class adapter
- release readiness must keep the public-beta posture and launch gates explicit
- public examples must use generic placeholders like `your-org/your-repo`
- bundled workflow templates must match their public docs copies
- documented `/daedalus`, `/workflow`, and `hermes daedalus ...` commands must
  map to real parser surfaces
- bootstrap must safely promote `WORKFLOW.md` to `WORKFLOW-<workflow>.md`
  without overwriting existing named contracts
- `daedalus/projects/` must stay placeholder-only in the public repository
- installation docs must keep the landing-page quick start short and link to
  detailed operator docs
- Codex app-server tests must cover fake protocol behavior in CI and keep the
  real app-server smoke opt-in
- Issue-runner cleanup tests must prove `before_remove` runs before terminal
  workspaces are deleted
- live smoke entrypoints must stay discoverable through `scripts/smoke-live.sh`
- release-readiness evidence must keep passing the scheduled
  `release-scorecard.yml` check

## Next Checks

Add tests for the next hardening slice in this order:

1. Extend the `change-delivery` Codex app-server smoke from live lane dispatch
   into PR creation/update and internal-review loop evidence.
2. Add a markdown or JSON artifact mode to `scripts/smoke-live.sh` so operators
   can archive the exact live-smoke evidence from a release candidate.
3. Connect release-scorecard output to the release process instead of only
   checking that evidence paths exist.

## Harness Principles

- Keep repository knowledge discoverable in `docs/`, bundled templates, and
  `AGENTS.md` rather than relying on chat history.
- Turn repeated review comments into structural checks or docs updates.
- Prefer small, explicit guardrails over broad lint rules that hide the fix.
- Keep public examples generic and keep deployment-specific material private.
- Treat opt-in smoke tests as evidence paths, not as default CI requirements.

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

## Live Smoke Harness

`scripts/smoke-live.sh` is the single local entrypoint for opt-in live smokes:

```bash
scripts/smoke-live.sh --list
scripts/smoke-live.sh
```

It runs only configured smokes, based on required environment variables, and
skips the rest.

## Release Scorecard

`.github/workflows/release-scorecard.yml` runs `python
scripts/release_scorecard.py --check` weekly and on demand. The script keeps
`docs/release-readiness.md` anchored to concrete evidence paths instead of
letting the scorecard drift into prose.
