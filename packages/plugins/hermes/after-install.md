# Sprints Installed

`hermes plugins install attmous/sprints --enable` cloned and enabled the plugin.

## Dependency And Compatibility Check

Sprints checks its runtime prerequisites the first time Hermes loads the enabled
plugin. If `PyYAML`, `jsonschema`, or `rich` are missing from the Python
environment that runs Hermes, Sprints attempts:

```bash
python -m pip install PyYAML jsonschema rich
```

If that fails, run one of these and restart Hermes:

```bash
python -m pip install PyYAML jsonschema rich
sudo apt install python3-yaml python3-jsonschema python3-rich
```

Sprints also checks that Hermes exposes the plugin APIs it needs:
`register_command`, `register_cli_command`, and `register_skill`. If the check
fails, update Hermes:

```bash
hermes update
```

## Next Steps

Run these from the repository you want Sprints to operate on:

```bash
cd /path/to/repo
hermes sprints init
hermes sprints codex-app-server up
hermes sprints validate
hermes sprints doctor
hermes sprints daemon up
```

Inside Hermes:

```text
/sprints status
/sprints doctor
/workflow change-delivery status
```
