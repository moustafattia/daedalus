"""Plugin-side entrypoint for the YoYoPod workflow CLI.

Run with::

    python3 -m adapters.yoyopod_core <command> [--json]

or directly::

    python3 /path/to/hermes-relay/adapters/yoyopod_core/__main__.py <command>

The entrypoint resolves the workflow root (where ``config/yoyopod-workflow.json``
lives) and constructs a workspace accessor via
:func:`adapters.yoyopod_core.workspace.load_workspace_from_config`. It then
delegates dispatch to :func:`adapters.yoyopod_core.cli.main`.

Resolution order for the workflow root (first match wins):

1. ``--workflow-root <path>`` (before the subcommand)
2. ``$YOYOPOD_WORKFLOW_ROOT``
3. ``<THIS_FILE_DIR>/../../../..`` — matches the default install layout
   where the plugin lives under ``<workflow_root>/.hermes/plugins/hermes-relay/``
4. ``~/.hermes/workflows/yoyopod``

This entrypoint replaces the retired ``<workflow_root>/scripts/yoyopod_workflow.py``
wrapper. The wrapper has been deleted; every external caller (systemd services,
cron job prompts, skill docs, hermes-relay ``runtime.py``/``tools.py``
subprocess spawns) now points here.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _default_workflow_root_from_install_layout() -> Path | None:
    # __main__.py lives at <plugin_root>/adapters/yoyopod_core/__main__.py
    # <plugin_root> == <workflow_root>/.hermes/plugins/hermes-relay/
    here = Path(__file__).resolve()
    plugin_root = here.parents[2]
    candidate = plugin_root.parent.parent.parent
    if (candidate / "config" / "yoyopod-workflow.json").exists():
        return candidate
    return None


def resolve_workflow_root(argv: list[str]) -> tuple[Path, list[str]]:
    """Pop ``--workflow-root`` from argv if present; fall back to env/default."""
    filtered: list[str] = []
    workflow_root: Path | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--workflow-root":
            if i + 1 >= len(argv):
                raise SystemExit("--workflow-root requires a path argument")
            workflow_root = Path(argv[i + 1]).expanduser().resolve()
            i += 2
            continue
        if arg.startswith("--workflow-root="):
            workflow_root = Path(arg.split("=", 1)[1]).expanduser().resolve()
            i += 1
            continue
        filtered.append(arg)
        i += 1
    if workflow_root is None:
        env = os.environ.get("YOYOPOD_WORKFLOW_ROOT")
        if env:
            workflow_root = Path(env).expanduser().resolve()
    if workflow_root is None:
        workflow_root = _default_workflow_root_from_install_layout()
    if workflow_root is None:
        workflow_root = Path.home() / ".hermes" / "workflows" / "yoyopod"
    return workflow_root, filtered


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    workflow_root, command_argv = resolve_workflow_root(raw_argv)

    # Ensure sibling adapter modules + adapters package are importable when
    # invoked directly as a script (``python3 __main__.py``).
    plugin_root = Path(__file__).resolve().parents[2]
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))

    from adapters.yoyopod_core.workspace import load_workspace_from_config
    from adapters.yoyopod_core.cli import main as cli_main

    ws = load_workspace_from_config(workspace_root=workflow_root)
    try:
        return cli_main(ws, argv=command_argv)
    except subprocess.CalledProcessError as exc:
        print(ws._subprocess_failure_message(exc), file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
