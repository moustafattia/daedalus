# Daedalus projects

`projects/` is placeholder-only in the public repository.

This tree is **not** part of the public plugin contract and is **not shipped** in
the install payload. The public engine is configured from the repo-owned
`WORKFLOW*.md` contract; the canonical product checkout is the repository the
operator bootstraps from.

## Rules

- Keep public repository examples generic.
- Do not commit customer, product, or operator-specific files here.
- Do not put runtime data, cloned repositories, secrets, logs, or generated
  scheduler state under `projects/`.
- Agents should work against the user's real repo checkout recorded in
  `repository.local-path`.
- Workflow instance state lives under
  `~/.hermes/workflows/<owner>-<repo>-<workflow-type>/`.

## Placeholder

The repository keeps only [PLACE_HOLDER.md](PLACE_HOLDER.md) here so the
directory's role is visible without shipping project-specific material.

If a private project pack is useful for a deployment, keep it outside this public
repo or publish it as a separate plugin/package with its own contract.

## Supported Model

The public setup flow is:

1. clone or open the real target repo
2. run `hermes daedalus bootstrap` from that checkout
3. edit the generated `WORKFLOW.md`
4. run the workflow with `/workflow issue-runner run` or the workflow-specific loop command
