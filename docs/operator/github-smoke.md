# GitHub Smoke Test

Use this only against a repository where temporary issues are acceptable. The
test creates one labeled issue, lets `issue-runner` select and dispatch it, then
closes the issue and verifies terminal cleanup.

## Prerequisites

- `gh` installed and authenticated with issue read/write access
- a repository you can create and close issues in
- normal Python test dependencies installed

## Run

```bash
export DAEDALUS_GITHUB_SMOKE_REPO=your-org/your-repo
pytest tests/test_github_issue_runner_smoke.py -q
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
- a no-op runtime can dispatch from the selected issue
- scheduler state records the continuation retry
- terminal GitHub state clears retry state and removes the issue workspace
