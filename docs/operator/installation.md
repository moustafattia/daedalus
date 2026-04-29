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
git clone https://github.com/attmous/daedalus.git
cd daedalus
sudo apt install python3-yaml python3-jsonschema
./scripts/install.sh
```

The plugin source of truth is:

```text
~/.hermes/plugins/daedalus
```

## Scaffold a workflow root

```bash
python3 ~/.hermes/plugins/daedalus/tools.py scaffold-workflow \
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
python3 ~/.hermes/plugins/daedalus/tools.py init \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review

python3 ~/.hermes/plugins/daedalus/tools.py doctor \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review \
  --format json
```

## Supervise it

```bash
python3 ~/.hermes/plugins/daedalus/tools.py service-install \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review \
  --service-mode active

python3 ~/.hermes/plugins/daedalus/tools.py service-enable \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review \
  --service-mode active

python3 ~/.hermes/plugins/daedalus/tools.py service-start \
  --workflow-root ~/.hermes/workflows/your-org-your-repo-code-review \
  --service-mode active
```

Use `--service-mode shadow` if you want read-only parity validation first.

## Operate it from Hermes

```bash
export HERMES_ENABLE_PROJECT_PLUGINS=true
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

## Legacy migration

`scripts/migrate_config.py` is only for migrating older JSON configs into the new `workflow.yaml` shape. It is not the primary onboarding path for new installs.
