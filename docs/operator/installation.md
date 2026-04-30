# Daedalus installation

This is the supported community install path for the first public release.

## Requirements

- Linux
- Hermes with plugin loading enabled
- `python3` with `yaml` and `jsonschema` available
- `systemd --user` for supervised active/shadow mode
- the host CLIs required by the runtimes named in `WORKFLOW.md`

The bundled `change-delivery` template defaults to:

- `acpx-codex` for the coder runtime
- `claude-cli` for the internal reviewer runtime

If your host does not have those runtimes, edit `WORKFLOW.md` before starting the service.

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

This is the preferred path. `bootstrap`:

- detects the git repo root from the current checkout
- derives `github-slug` from `origin`
- creates the supported instance layout below
- writes a starter `WORKFLOW.md`
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

## Configure the workflow

Edit:

```text
~/.hermes/workflows/<owner>-<repo>-<workflow-type>/WORKFLOW.md
```

At minimum, set:

- `repository.local-path`
- runtime kinds/models that exist on your host
- any gates, webhooks, or observability settings your repo needs

The YAML front matter is the structured config. The Markdown body below it is
shared workflow policy that Daedalus prepends to its role-specific prompts.

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
