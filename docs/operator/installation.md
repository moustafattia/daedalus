# Daedalus installation

This is the supported community install path for the first public release.

## Requirements

- Linux
- Hermes with plugin loading enabled
- `python3` with `yaml` and `jsonschema` available
- `systemd --user` for supervised active/shadow mode
- the host CLIs required by the runtimes named in `workflow.yaml`

The bundled `code-review` template defaults to:

- `acpx-codex` for the coder runtime
- `claude-cli` for the internal reviewer runtime

If your host does not have those runtimes, edit `workflow.yaml` before starting the service.

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

## Scaffold a workflow root

```bash
hermes daedalus scaffold-workflow \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review \
  --github-slug your-org/your-repo
```

This creates the supported instance layout:

```text
~/.hermes/workflows/<owner>-<repo>-<workflow-type>/
```

## Configure the workflow

Edit:

```text
~/.hermes/workflows/<owner>-<repo>-<workflow-type>/config/workflow.yaml
```

At minimum, set:

- `repository.local-path`
- runtime kinds/models that exist on your host
- any gates, webhooks, or observability settings your repo needs

## Initialize and verify

```bash
hermes daedalus init \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review

hermes daedalus doctor \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review \
  --format json
```

## Supervise it

```bash
hermes daedalus service-install \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review \
  --service-mode active

hermes daedalus service-enable \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review \
  --service-mode active

hermes daedalus service-start \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review \
  --service-mode active
```

Use `--service-mode shadow` if you want read-only parity validation first.

## Operate it from Hermes

```bash
export DAEDALUS_WORKFLOW_ROOT=~/.hermes/workflows/your-org-your-repo-code-review
cd /path/to/your/repo
hermes
```

Then use:

```text
/daedalus status
/daedalus doctor
/workflow code-review status
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

`scripts/migrate_config.py` is only for migrating older JSON configs into the new `workflow.yaml` shape. It is not the primary onboarding path for new installs.
