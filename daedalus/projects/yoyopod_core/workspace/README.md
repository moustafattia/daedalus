# yoyopod_core workspace

This directory documents the older project-pack layout and is kept only as
historical playground material.

It is not part of the shipped plugin payload, and it is not the supported home
for the canonical product checkout. In the supported public model:

- the operator works from the real repo checkout they bootstrapped from
- `repository.local-path` in `WORKFLOW.md` points at that checkout
- Daedalus creates any runtime-managed workspaces or worktrees from there

So this directory is reference-only. It should not be treated as the default
place where agents edit code.
