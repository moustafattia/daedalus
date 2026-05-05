# Installation

Supported path:

```bash
hermes plugins install attmous/sprints --enable
```

On first load, Sprints checks Hermes plugin API compatibility and verifies the
Python packages it needs in the same environment that runs Hermes. Missing
`PyYAML`, `jsonschema`, or `rich` packages are installed with:

```bash
python -m pip install PyYAML jsonschema rich
```

If the Python environment rejects package installation, Sprints prints the exact
manual command and the Debian/Ubuntu package fallback:

```bash
sudo apt install python3-yaml python3-jsonschema python3-rich
```

## Bootstrap

Run from the target repo:

```bash
cd /path/to/repo
hermes sprints init
hermes sprints codex-app-server up
hermes sprints validate
hermes sprints doctor
hermes sprints daemon up
```

`init` asks for the repo, tracker, runtime, optional model override, labels, and
concurrency. It creates a workflow root, writes a repo-owned `WORKFLOW.md`
contract, validates it, and prints exact next steps. Use `bootstrap` or
`scaffold-workflow` when scripting setup or replacing the guided prompts.

Default workflow root:

```text
~/.hermes/workflows/<owner>-<repo>-change-delivery/
```

The repo pointer is written to:

```text
./.hermes/sprints/workflow-root
```

## Runtime

Bundled templates default actors to `codex-app-server`:

```yaml
runtimes:
  codex:
    kind: codex-app-server
    mode: external
    endpoint: ws://127.0.0.1:4500
```

Start the shared listener:

```bash
hermes sprints codex-app-server up
```

Or bind roles to another runtime:

```bash
hermes sprints configure-runtime --runtime hermes-final --role implementer
hermes sprints configure-runtime --runtime codex-app-server --role orchestrator
```

## Validate

```bash
hermes sprints validate
hermes sprints doctor
hermes sprints doctor --fix
hermes sprints runtime-matrix
```

`doctor --fix` only applies conservative local repairs: missing workflow
directories, pointer files, state/audit files, engine projections, clear runtime
binding drift, and missing systemd user unit files. It reports every change and
skips ambiguous repairs.

Use `runtime-matrix --execute` only when the configured runtimes are available.
It dispatches a minimal runtime turn.

## Daemon

Start the workflow daemon after the runtime listener and validation pass:

```bash
hermes sprints daemon up
hermes sprints daemon status
```

The daemon runs one workflow tick immediately, then keeps polling. Defaults:

```text
active lanes: 15s
idle workflow: 60s
retry wake cap: 30s
```

## Operate

Inside Hermes:

```text
/sprints status
/sprints doctor
/sprints watch
/sprints daemon status
/workflow change-delivery status
/workflow change-delivery validate
/workflow change-delivery tick
```

## Local Development Install

```bash
git clone https://github.com/attmous/sprints.git
cd sprints
./scripts/install.sh
hermes plugins enable sprints
```
