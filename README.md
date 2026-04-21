# hermes-relay

YoYoPod Relay is a Hermes project plugin that provides a relay runtime, alert logic, and operator control surface for the YoYoPod workflow.

Contents:
- `__init__.py` — plugin registration
- `schemas.py` — CLI/slash parser wiring
- `tools.py` — operator surface and systemd helpers
- `runtime.py` — canonical Relay runtime implementation
- `alerts.py` — outage alert decision logic
- `plugin.yaml` — plugin manifest
- `skills/operator/SKILL.md` — operator workflow notes

This repository currently mirrors the plugin-root contents used inside the YoYoPod workflow at:
- `.hermes/plugins/hermes-relay/`

## Intended placement

This code is meant to live as a Hermes project plugin inside a workflow repository, for example:

```text
<workflow-root>/
  .hermes/
    plugins/
      hermes-relay/
```

## Usage

Inside a Hermes session with project plugins enabled:

```bash
export HERMES_ENABLE_PROJECT_PLUGINS=true
cd <workflow-root>
hermes
```

Then use:

```text
/relay status
/relay shadow-report
/relay doctor
/relay cutover-status
/relay iterate-active --json
```

For direct runtime invocation from the plugin path:

```bash
python3 .hermes/plugins/hermes-relay/runtime.py status --workflow-root <workflow-root> --json
python3 .hermes/plugins/hermes-relay/runtime.py run-active --workflow-root <workflow-root> --project-key yoyopod --instance-id relay-active-service-1 --interval-seconds 30 --json
python3 .hermes/plugins/hermes-relay/alerts.py --workflow-root <workflow-root> --json
```

## Notes

- The runtime is workflow-aware and expects a YoYoPod-style workflow root.
- Systemd service installation in `tools.py` points the active service at the plugin runtime path.
- This repository is the plugin source of truth; workflow-local wrappers can be removed as downstream consumers migrate.
