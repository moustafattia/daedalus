# Daedalus installation

This is the supported community install path for the first public release.
The default managed path is for the bundled `change-delivery` workflow, but
`issue-runner` now uses the same repo-owned contract and `service-up` surface.

## Requirements

- Linux
- Hermes with plugin loading enabled
- `gh` authenticated for GitHub-backed workflows
- `python3` with `yaml` and `jsonschema` available
- `systemd --user` for supervised active/shadow mode
- the host CLIs required by the runtimes named in `WORKFLOW.md`

The bundled `change-delivery` template defaults to:

- `acpx-codex` for the coder runtime
- `claude-cli` for the internal reviewer runtime

If your host does not have those runtimes, edit `WORKFLOW.md` before starting the service.

The bundled `issue-runner` template defaults to `tracker.kind: local-json` so
it is runnable without an external tracker. For first-class tracker operation,
switch it to `tracker.kind: github` and keep `gh` authenticated in the repo
checkout before running `service-up`. Linear exists as an experimental adapter,
but it is deferred for the public GitHub-first path.

## Bundled workflows

Daedalus currently ships two workflow packages:

- `change-delivery`
  This is the supported managed workflow behind `bootstrap` and `service-up`.
- `issue-runner`
  This is the bundled generic tracker-driven workflow. Use
  `bootstrap --workflow issue-runner` or `scaffold-workflow --workflow issue-runner`,
  then bring it up with `service-up` in `active` mode.

## Install the plugin

```bash
sudo apt install python3-yaml python3-jsonschema
hermes plugins install attmous/daedalus --enable
```

The plugin source of truth is:

```text
~/.hermes/plugins/daedalus
```

Daedalus also ships a standard Hermes pip plugin entry point. If you install it
as a Python package instead of through `hermes plugins install`, Hermes will
discover it on the next startup and you must enable it explicitly:

```bash
python3 -m pip install .
hermes plugins enable daedalus
```

## Bootstrap a workflow root

```bash
cd /path/to/your/repo
hermes daedalus bootstrap
```

This is the preferred path for `change-delivery`. To bootstrap the generic
workflow instead, run `hermes daedalus bootstrap --workflow issue-runner`.

`bootstrap`:

- detects the git repo root from the current checkout
- derives `github-slug` from `origin`
- creates the supported instance layout below
- writes or promotes the repo-owned workflow contract
- creates a dedicated bootstrap branch
- commits the workflow contract changes
- writes `./.hermes/daedalus/workflow-root` in the repo checkout so later
  Daedalus commands can resolve the workflow root automatically

```text
~/.hermes/workflows/<owner>-<repo>-<workflow-type>/
```

## Manual scaffold path

If you want explicit control over the target root or slug:

```bash
hermes daedalus scaffold-workflow \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-change-delivery \
  --github-slug your-org/your-repo
```

That creates the same supported instance layout:

```text
~/.hermes/workflows/<owner>-<repo>-<workflow-type>/
```

If you want the bundled generic workflow instead of the managed default:

```bash
hermes daedalus scaffold-workflow \
  --workflow issue-runner \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-issue-runner \
  --github-slug your-org/your-repo
```

The first workflow in a repo is written to:

```text
/path/to/repo/WORKFLOW.md
```

If the repo later carries multiple workflows, Daedalus promotes the contracts
to:

```text
/path/to/repo/WORKFLOW-change-delivery.md
/path/to/repo/WORKFLOW-issue-runner.md
```

Promotion is fail-safe. If `WORKFLOW.md` exists but is not a Daedalus contract,
bootstrap stops and leaves the file unchanged. If a target named contract
already exists, bootstrap also stops instead of overwriting user edits.

## Configure the workflow

Edit the path printed by `bootstrap` as `edit next`. For a repo with one
workflow this is usually:

```text
/path/to/repo/WORKFLOW.md
```

For a repo with multiple workflows, edit the workflow-specific file, for
example:

```text
/path/to/repo/WORKFLOW-issue-runner.md
```

At minimum, set:

- `repository.local-path`
- runtime kinds/models that exist on your host
- any gates, webhooks, or observability settings your repo needs

The YAML front matter is the structured config. The Markdown body below it is
the workflow policy contract. `change-delivery` composes it into its role
prompts; `issue-runner` renders it as the issue prompt template.

## Bring it up

```bash
hermes daedalus service-up
```

`service-up` runs the supported post-edit path in one command:

- initialize runtime state
- validate `WORKFLOW.md` and workflow preflight rules
- install the user systemd unit
- enable the unit
- start the service

Use `--service-mode shadow` if you want read-only parity validation first.
That `shadow` mode applies to `change-delivery`. `issue-runner` supports
`active` mode only.

If your workflow contract uses an external `codex-app-server` runtime, bring up
the shared Codex listener once:

```bash
hermes daedalus codex-app-server up
```

Then point the workflow runtime at `ws://127.0.0.1:4500`.
Use `hermes daedalus codex-app-server doctor` for the full operator check:
managed service state, readiness, auth posture, and persisted Codex thread
mappings. If the listener is not loopback-only, pass one of the supported auth
flags during `install` or `up`, for example `--ws-token-file
/absolute/path/to/token`. See [Codex app-server operations](codex-app-server.md)
for external-mode diagnostics and troubleshooting.

## Manual low-level path

If you want to inspect or script each step separately, the lower-level commands
remain available:

```bash
hermes daedalus init \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-change-delivery

hermes daedalus doctor \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-change-delivery \
  --format json

hermes daedalus service-install \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-change-delivery \
  --service-mode active

hermes daedalus service-enable \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-change-delivery \
  --service-mode active

hermes daedalus service-start \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-change-delivery \
  --service-mode active
```

## Operate it from Hermes

```bash
cd /path/to/your/repo
hermes
```

Then use:

```text
/daedalus status
/daedalus doctor
/workflow change-delivery status
```

For the bundled generic workflow:

```text
/workflow issue-runner status
/workflow issue-runner doctor
/workflow issue-runner tick
/workflow issue-runner run --max-iterations 1 --json
```

To validate the GitHub-backed tracker path against a disposable live issue, see
[github-smoke.md](github-smoke.md).

## Plugin state

Hermes plugins are opt-in. `hermes plugins install ... --enable` is the
supported path because it installs the repo and enables the plugin in one step.

If you install Daedalus by some other method, enable it explicitly:

```bash
hermes plugins enable daedalus
```

`HERMES_ENABLE_PROJECT_PLUGINS=true` is only for project-local plugins under
`./.hermes/plugins/`. It is not required for a global `~/.hermes/plugins/daedalus`
install.

## Manage the plugin

```bash
hermes plugins list
hermes plugins update daedalus
hermes plugins disable daedalus
```

## Local-dev fallback

If you want to install straight from a local checkout instead of the Hermes
plugin manager:

```bash
git clone https://github.com/attmous/daedalus.git
cd daedalus
./scripts/install.sh
hermes plugins enable daedalus
```

## Legacy migration

`scripts/migrate_config.py` is only for migrating older JSON configs into the new `WORKFLOW.md` shape. It is not the primary onboarding path for new installs.
