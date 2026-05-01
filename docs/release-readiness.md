# Release Readiness

This scorecard tracks what must stay true before Daedalus is presented as a
community-ready SDLC workflow engine. It is intentionally mechanical: every item
should be backed by documentation, tests, or an operator smoke path.

## Current Position

- Overall posture: **public beta candidate**, not a strict Symphony
  implementation.
- Reference workflow: `issue-runner`.
- Flagship workflow: `change-delivery`.
- First-class tracker: GitHub.
- First-class code host: GitHub.
- Experimental tracker: Linear.
- Preferred contract: repo-owned `WORKFLOW.md` or `WORKFLOW-<workflow>.md`.

## Symphony Alignment

| Area | Status | Evidence |
|---|---|---|
| Repo-owned workflow contract | Strong | `WORKFLOW*.md` loader, bootstrap, examples, template drift tests |
| Tracker abstraction | Good | Shared GitHub, local JSON, and experimental Linear clients; `change-delivery` separates `tracker` from `code-host` |
| Code-host abstraction | Good | Shared GitHub client owns PR create/list/ready/merge, reactions, and review-thread GraphQL |
| Long-running scheduler | Good | `issue-runner run`, worker supervision, retries, persisted scheduler state |
| Workspace lifecycle | Good | Sanitized issue workspaces, hooks, terminal cleanup, root containment |
| Codex app-server | Good | Managed stdio, external WebSocket, thread resume, token/rate-limit metrics |
| Observability | Good | `/daedalus watch`, status, HTTP state, JSONL audit events |
| strict Symphony contract | Partial | Daedalus still requires extension fields outside the core Symphony blocks |
| Cross-workflow uniformity | Partial | `issue-runner` is cleaner; `change-delivery` remains intentionally opinionated |

## Harness Engineering Alignment

| Area | Status | Evidence |
|---|---|---|
| Repo knowledge as system of record | Strong | Architecture, workflow, operator, security, and conformance docs |
| Public-surface guardrails | Strong | Generic examples, placeholder-only `projects/`, packaging checks |
| Agent-legible workflows | Good | Workflow docs link the default templates and operator paths |
| Custom structural checks | Good | Public harness tests and workflow-template drift checks |
| Live integration evidence | Partial | GitHub and real Codex app-server smoke tests are opt-in |
| Recurring cleanup discipline | Partial | Guardrails exist, but no scheduled quality task yet |

## Gates Before Community Launch

1. Keep `daedalus/projects/` placeholder-only in the public repository.
2. Keep README quick start short and route details to `docs/operator/installation.md`.
3. Keep `issue-runner` as the Symphony-shaped reference workflow.
4. Keep GitHub as the documented first-class tracker until live coverage expands.
5. Keep Linear documented as experimental until it has first-class operator docs.
6. Keep workflow examples synchronized with packaged templates.
7. Keep Codex app-server real-runtime tests opt-in and fake protocol tests in CI.
8. Add live GitHub coverage for comments, labels, and failure recovery before
   calling GitHub automation production-grade.
9. Add an end-to-end `change-delivery` Codex app-server smoke before calling the
   flagship workflow app-server-complete.
10. Add scheduled cleanup or scorecard refresh work before claiming mature
    harness-engineering discipline.

## Next Hardening Slice

The highest-leverage next implementation slice is live integration evidence:

1. Extend the GitHub smoke to cover issue comments, labels, and retry/failure
   recovery.
2. Add a skipped-by-default `change-delivery` Codex app-server end-to-end smoke.
3. Add docs/CLI drift checks for commands shown in operator docs.
