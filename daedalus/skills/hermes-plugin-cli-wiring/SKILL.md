---
name: hermes-plugin-cli-wiring
description: Wire general Hermes plugin CLI subcommands into argparse and distinguish them from in-session slash commands and memory-provider CLI conventions.
version: 1.0.0
author: Hermes Agent
license: MIT
---

# Hermes Plugin CLI Wiring

Use this when a Hermes plugin registers `ctx.register_cli_command(...)` but `hermes <plugin> ...` is still missing from CLI help or parses as an invalid choice.

## Core distinction

Hermes has two unrelated extension paths:

- `ctx.register_command(...)` → in-session slash commands like `/daedalus`
- `ctx.register_cli_command(...)` → terminal subcommands like `hermes daedalus ...`

Do not confuse these with the wrapper-CLI subclass hooks from the `Extending the CLI` doc. Those are for custom TUIs, not for adding `hermes <subcommand>` entries.

## Actual source-of-truth wiring

General plugin CLI commands must be wired through two places:

1. `hermes_cli.plugins`
   - `PluginContext.register_cli_command(...)` stores entries in `PluginManager._cli_commands`
   - add a helper `get_plugin_cli_commands()` that lazily runs plugin discovery and returns `_cli_commands`

2. `hermes_cli.main`
   - during argparse setup, import `get_plugin_cli_commands()`
   - iterate `get_plugin_cli_commands().values()`
   - for each entry:
     - `subparsers.add_parser(cmd_info["name"], ...)`
     - call `cmd_info["setup_fn"](plugin_parser)`
     - if `handler_fn` exists and `setup_fn` did not already set `func`, call `plugin_parser.set_defaults(func=handler_fn)`

Without step 2, plugin CLI commands are registered in memory but never become real argparse subcommands.

## Memory-plugin exception

Memory-provider plugins are different.

They use:
- `plugins.memory.discover_plugin_cli_commands()`
- active-provider gating
- a `cli.py` file with `register_cli(subparser)`

Do not assume that memory-provider discovery means general plugin CLI registration is already working.

## Fast diagnosis

If the plugin code contains:

```python
ctx.register_cli_command(...)
```

but the CLI says the command is an invalid choice, check in this order:

1. Does `hermes_cli.plugins` expose a lazy helper returning `_cli_commands`?
2. Does `hermes_cli.main` actually iterate those commands into `subparsers`?
3. Did `setup_fn` build the argparse tree correctly?
4. If no `func` is set in `setup_fn`, does main apply `handler_fn` via `set_defaults(func=...)`?
5. Is the plugin enabled in config?
6. For project plugins, is project-plugin discovery enabled?

## Test strategy

Write tests for both layers:

### 1. Registration helper test
- create a `PluginManager`
- register a CLI command through `PluginContext.register_cli_command(...)`
- assert `get_plugin_cli_commands()` returns it after lazy discovery

### 2. End-to-end argparse test
- monkeypatch `hermes_cli.plugins.get_plugin_cli_commands()` to return a fake command entry
- monkeypatch memory-plugin discovery to return `[]`
- set `sys.argv` to `['hermes', '<cmd>', ...]`
- call `hermes_cli.main.main()`
- assert the handler receives parsed args

This proves the command is not just stored, but actually parseable and executable.

## Hardening findings

The minimal wiring is not enough. Harden both registration and argparse integration.

### Registration hardening in `PluginContext.register_cli_command(...)`
- normalize names before storing them:
  - lowercase
  - trim whitespace
  - strip leading `/`
  - replace spaces with `-`
- reject empty names after normalization
- reject duplicate plugin CLI command names instead of silently overwriting
- preserve the first registration and log a warning for the loser

This should behave more like plugin slash-command registration. Silent overwrite is garbage and makes debugging plugin collisions harder than it needs to be.

### Argparse integration hardening in `hermes_cli.main`
Do **not** inject plugin CLI commands before all built-ins are registered.

Why:
- if a plugin registers `version`, `tools`, `memory`, etc. before the built-ins are added, argparse later crashes with `conflicting subparser`
- collision handling works cleanly only after `subparsers.choices` already contains the built-in command set

Preferred pattern:
1. build all built-in subparsers first
2. at the end of parser construction, iterate general plugin CLI commands
3. skip any plugin command whose normalized name already exists in `subparsers.choices`
4. then add memory-plugin CLI commands through the existing active-provider path, using the same collision guard

Recommended helper shape in `main.py`:
- add a tiny local helper like `_add_dynamic_plugin_cli_command(cmd_info)`
- validate non-empty name
- if `name in subparsers.choices`, log warning and skip
- else `add_parser(...)`, call `setup_fn(...)`, and apply `handler_fn` only if `func` is still unset

## Project plugin ownership rule

For project-local operator plugins, keep the real implementation inside the plugin package and leave any `scripts/...` files as thin compatibility wrappers.

Preferred shape:
- `.hermes/plugins/<plugin>/__init__.py`
- `.hermes/plugins/<plugin>/schemas.py`
- `.hermes/plugins/<plugin>/cli.py`
- `.hermes/plugins/<plugin>/runtime.py` (or `core/runtime.py`)
- optional `.hermes/plugins/<plugin>/alerts.py`
- `scripts/<entrypoint>.py` wrappers that import the plugin module and call `main()`

Why this is better:
- plugin is the true source of truth
- operator surfaces (`/plugin`, `hermes plugin ...`) and internals live together
- systemd/cron/manual shell entrypoints keep working through the script wrappers
- tests can target plugin modules directly while still smoke-testing wrapper entrypoints

Avoid the inverted ownership pattern where plugin code imports and depends on `scripts/<entrypoint>.py`. That shape is backwards and turns the extension layer into a passenger of ad-hoc script files.

## Service and script end-state for project plugins

After moving project-specific runtime logic into the plugin package, finish the migration instead of stopping at wrappers-only.

Recommended end-state:
- systemd / service installers should point directly at plugin-owned runtime modules, for example:
  - `python3 .hermes/plugins/<plugin>/runtime.py ...`
- compatibility wrappers under `scripts/` may remain temporarily for manual users and old tests
- plugin operator code should load plugin-local modules directly, not bounce through `scripts/...`

Concrete Daedalus finding:
- the clean Daedalus service unit should execute plugin runtime directly rather than `scripts/daedalus.py`
- plugin alert logic should call plugin-local command execution, not try to import stale files like `daedalus_control.py`

Migration order that worked:
1. move implementation into plugin modules (`runtime.py`, `alerts.py`)
2. keep `scripts/...` as thin wrappers for compatibility
3. update plugin tooling to import/load plugin modules directly
4. update generated systemd units to point at plugin runtime path
5. reinstall/restart the service and verify status
6. only then consider deleting wrappers after docs/tests/manual callers are updated

This avoids the half-migrated mess where the code lives in the plugin but operations still secretly depend on stale wrapper behavior.

## Practical takeaway

If the docs claim `ctx.register_cli_command(...)` adds `hermes <name> ...` but the CLI disagrees, trust the source over the docs. The docs can be ahead of the implementation.
