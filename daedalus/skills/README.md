# Daedalus skills

This directory contains the project-agnostic Daedalus skills. When the plugin
is installed (``./scripts/install.sh``), this directory is copied to
``~/.hermes/plugins/daedalus/skills/``. With
``HERMES_ENABLE_PROJECT_PLUGINS=true`` set, Hermes discovers these skills
automatically.

Project-specific skills belong in private project packs instead of the public
plugin root.

Each skill is self-contained: one directory per skill, each with a single
``SKILL.md`` file whose YAML frontmatter declares the skill's name and
description.

## Layout

```
skills/
├── README.md                                               # this file
├── operator/                                               # plugin operator surface (/daedalus)
├── daedalus-architecture/                              # design principles
├── hermes-plugin-cli-wiring/                               # generic Hermes plugin CLI wiring
├── daedalus-hardening-slices/                          # reliability hardening follow-up
└── daedalus-retire-watchdog-and-migrate-control-schema/ # retire legacy watchdog pattern
```

## By role

**Day-to-day operator skills** (invoked during workflow operation):

- ``operator`` — Daedalus operator control surface: ``/daedalus`` slash-command reference.

**Architecture / design reference** (read when changing the plugin shape):

- ``daedalus-architecture`` — long-running orchestrator design (state, event queues, bounded reasoning) instead of cron heartbeat loops.
- ``hermes-plugin-cli-wiring`` — how to wire Hermes plugin CLI subcommands via argparse.

**Development workflow** (read when landing code):

- ``daedalus-hardening-slices`` — reliability-hardening follow-up workflow.
- ``daedalus-retire-watchdog-and-migrate-control-schema`` — historical playbook for retiring the legacy watchdog and migrating the SQLite control-schema.

## Project-specific skills

Project-specific skills should stay in the private project pack or plugin that
owns them. Do not commit deployment-only skills to this public plugin tree.

## Adding a new skill

1. Create a directory under ``skills/`` named after the skill (kebab-case).
2. Add a ``SKILL.md`` with YAML frontmatter (``name``, ``description``) at minimum.
3. Run ``pytest tests/test_plugin_skills.py`` to verify the skill validates.
4. Run ``./scripts/install.sh`` to propagate to the installed plugin.
