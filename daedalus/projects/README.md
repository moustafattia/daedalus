# Daedalus projects

Each subdirectory under `projects/` is source-repo **playground material** for
one specific repository or operator environment.

This tree is **not** part of the public plugin contract and is **not shipped**
in the install payload. The public engine is configured from
`<workflow-root>/WORKFLOW.md`, and the canonical repo checkout is the one the
operator bootstraps from.

That means:

- agents should work against the user's real repo checkout recorded in
  `repository.local-path`
- workflow instance state lives under
  `~/.hermes/workflows/<owner>-<repo>-<workflow-type>/`
- `projects/` is only for source-controlled notes, legacy skills, and local
  playground reference material

The repo currently keeps `yoyopod_core/` here as historical/example material.
Treat it as archived playground content, not as a recommended deployment model.

## What belongs here

Keep only source-controlled reference material:

- project-specific docs or migration notes
- local-only skills that should not appear in the public plugin root
- archived example metadata if it helps explain older layouts

Do not treat `projects/` as:

- the canonical product checkout
- the default place where agents edit code
- a packaged plugin runtime surface
- a loader contract the engine resolves at runtime

## Current model

The supported public model is:

1. clone or open the real product repo
2. run `hermes daedalus bootstrap` from that checkout
3. edit the generated workflow root's `WORKFLOW.md`
4. run `hermes daedalus service-up`

If a project pack is useful, it should help humans understand or migrate that
project. It should not replace the workflow root or the user checkout.
