# GitHub Smoke Test

Use this only against a repository where temporary issues and comments are
acceptable. The test creates one labeled issue, lets `issue-runner` select and
dispatch it, forces one runtime failure, verifies retry recovery, writes
upserted tracker feedback comments, closes the issue through
`tracker-feedback`, and verifies terminal cleanup.

## Prerequisites

- `gh` installed and authenticated with issue read/write access
- a repository you can create and close issues in
- normal Python test dependencies installed

## Run

```bash
export DAEDALUS_GITHUB_SMOKE_REPO=your-org/your-repo
pytest tests/test_github_issue_runner_smoke.py -q
```

Or run it through the shared live-smoke harness:

```bash
export DAEDALUS_GITHUB_SMOKE_REPO=your-org/your-repo
scripts/smoke-live.sh --only github-issue-runner
```

Optional controls:

```bash
export DAEDALUS_GITHUB_SMOKE_REPO_PATH=/path/to/local/checkout
export DAEDALUS_GITHUB_SMOKE_LABEL=daedalus-smoke
```

`DAEDALUS_GITHUB_SMOKE_REPO_PATH` only needs to exist locally. The tracker uses
`tracker.github_slug` and `gh --repo <owner>/<repo>`, so the path does not have
to be a git checkout.

## What It Proves

- `tracker.kind: github` can select issues via `gh`
- `tracker.github_slug` is the GitHub repository source of truth
- required-label filtering works against live GitHub data
- a supervised worker dispatches from the selected issue
- tracker feedback writes issue comments for selected, dispatched, running,
  failed, retry scheduled, and completed stages
- `tracker-feedback.comment-mode: upsert` avoids duplicate Daedalus comments
  when selected/running stages repeat during retry recovery
- scheduler state records failure retry/backoff and clears it after recovery
- `tracker-feedback.state-updates.on-completed: closed` closes the GitHub issue
- terminal GitHub state clears retry state and removes the issue workspace
