# hermes-relay

![Hermes Relay wordmark](assets/hermes-relay-wordmark.svg)

Hermes Relay is the runtime and operator surface for workflow-oriented orchestration.
It gives you relay bootstrap, shadow observation, active execution gating, alert logic, and day-2 operator commands.

## Brand assets

- `assets/hermes-relay-wordmark.svg` — horizontal wordmark
- `assets/hermes-relay-icon.svg` — square app icon

## Why this repo exists

- Source of truth for the `hermes-relay` plugin payload
- Meant to be copied into a Hermes plugins directory
- Optimized for local editing, testing, and install-time validation

## Repo layout

- `__init__.py` — plugin registration
- `schemas.py` — CLI/slash parser wiring
- `tools.py` — operator surface and systemd helpers
- `runtime.py` — canonical relay runtime implementation
- `alerts.py` — outage alert decision logic
- `plugin.yaml` — plugin manifest
- `scripts/install.py` — Python installer for the plugin payload
- `scripts/install.sh` — shell wrapper around the installer
- `tests/test_install.py` — installer coverage
- `skills/operator/SKILL.md` — operator workflow notes

## Install

Default Hermes home install:

```bash
./scripts/install.sh
```

Install into a non-default Hermes home:

```bash
./scripts/install.sh --hermes-home /path/to/hermes-home
```

Install into an explicit destination:

```bash
./scripts/install.sh --destination /path/to/plugins/hermes-relay
```

The installer copies the plugin payload only.

## Quick start for developers

1. Run the tests.
2. Install the plugin into a scratch Hermes home or explicit destination.
3. Launch Hermes with project plugins enabled.
4. Exercise the `/relay` commands or call `runtime.py` directly.

```bash
python3 -m pytest
./scripts/install.sh --destination /tmp/hermes-relay
export HERMES_ENABLE_PROJECT_PLUGINS=true
cd <project-root>
hermes
```

## Plugin commands

Inside Hermes:

```text
/relay status
/relay shadow-report
/relay doctor
/relay active-gate-status
/relay iterate-active --json
```

## Direct runtime commands

Use these when you want to debug the relay without the Hermes shell in the middle:

```bash
python3 runtime.py init --workflow-root <workflow-root> --project-key yoyopod --json
python3 runtime.py status --workflow-root <workflow-root> --json
python3 runtime.py start --workflow-root <workflow-root> --project-key yoyopod --instance-id relay-active-service-1 --mode shadow --json
python3 runtime.py ingest-live --workflow-root <workflow-root> --json
python3 runtime.py heartbeat --workflow-root <workflow-root> --instance-id relay-active-service-1 --ttl-seconds 60 --json
python3 runtime.py iterate-shadow --workflow-root <workflow-root> --instance-id relay-shadow-1 --json
python3 runtime.py run-shadow --workflow-root <workflow-root> --project-key yoyopod --instance-id relay-shadow-1 --interval-seconds 30 --json
python3 runtime.py active-gate-status --workflow-root <workflow-root> --json
python3 runtime.py iterate-active --workflow-root <workflow-root> --instance-id relay-active-service-1 --json
python3 runtime.py run-active --workflow-root <workflow-root> --project-key yoyopod --instance-id relay-active-service-1 --interval-seconds 30 --json
```

## Working on the code

- Keep changes small and testable.
- Use `python3 -m pytest` before you ship anything.
- If installer behavior changes, update `tests/test_install.py` with it.
- If you add new payload files, update `scripts/install.py` so they get copied.

## Notes

- The runtime expects a compatible workflow root.
- `--json` is the best default when scripting or debugging.
- This repo is intentionally direct: no packaging theater, just the plugin payload and the tools around it.
