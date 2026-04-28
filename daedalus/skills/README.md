# Daedalus skills

All YoYoPod + Daedalus skills are consolidated here. When the plugin is
installed (``./scripts/install.sh``), this directory is copied to
``~/.hermes/plugins/daedalus/skills/``. With
``HERMES_ENABLE_PROJECT_PLUGINS=true`` set, Hermes discovers these skills
automatically.

Each skill is self-contained: one directory per skill, each with a single
``SKILL.md`` file whose YAML frontmatter declares the skill's name and
description.

## Layout

```
skills/
├── README.md                                               # this file
├── operator/                                               # plugin operator surface (/daedalus)
├── yoyopod-lane-automation/                                # primary operator workflow
├── yoyopod-workflow-watchdog-tick/                         # cron watchdog tick
├── yoyopod-closeout-notifier/                              # telegram closeout notifier
├── yoyopod-daedalus-alerts-monitoring/                        # outage alert cron job runner
├── yoyopod-daedalus-outage-alerts/                            # telegram outage alerts
├── daedalus-architecture/                              # design principles
├── daedalus-model1-project-layout/                     # Model-1 plugin layout
├── hermes-plugin-cli-wiring/                               # generic Hermes plugin CLI wiring
├── daedalus-hardening-slices/                          # reliability hardening follow-up
└── daedalus-retire-watchdog-and-migrate-control-schema/ # retire legacy watchdog pattern
```

## By role

**Day-to-day operator skills** (invoked during workflow operation):

- ``yoyopod-lane-automation`` — primary entrypoint for running/resuming/pausing the YoYoPod issue-lane workflow through the plugin CLI.
- ``yoyopod-workflow-watchdog-tick`` — run exactly one workflow-watchdog tick and return the mandated compact response shape.
- ``yoyopod-closeout-notifier`` — monitor newly-closed GitHub issues and send one Telegram update per closure.
- ``yoyopod-daedalus-alerts-monitoring`` — run the outage alert cron job with strict send-and-dedupe contract.
- ``yoyopod-daedalus-outage-alerts`` — alert shape, dedupe keys, and delivery semantics for Daedalus outage Telegram messages.
- ``operator`` — Daedalus operator control surface: ``/daedalus`` slash-command reference.

**Architecture / design reference** (read when changing the plugin shape):

- ``daedalus-architecture`` — long-running orchestrator design (state, event queues, bounded reasoning) instead of cron heartbeat loops.
- ``daedalus-model1-project-layout`` — single-plugin-plus-adapter layout pattern (this plugin's structure).
- ``hermes-plugin-cli-wiring`` — how to wire Hermes plugin CLI subcommands via argparse.

**Development workflow** (read when landing code):

- ``daedalus-hardening-slices`` — reliability-hardening follow-up workflow.
- ``daedalus-retire-watchdog-and-migrate-control-schema`` — historical playbook for retiring the legacy watchdog and migrating the SQLite control-schema.

## Adding a new skill

1. Create a directory under ``skills/`` named after the skill (kebab-case).
2. Add a ``SKILL.md`` with YAML frontmatter (``name``, ``description``) at minimum.
3. Run ``pytest tests/test_plugin_skills.py`` to verify the skill validates.
4. Run ``./scripts/install.sh`` to propagate to the installed plugin.
