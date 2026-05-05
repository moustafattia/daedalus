"""Workflow dispatcher module entrypoint.

Invocation:

    python3 -m sprints.workflows --workflow-root <path> <subcommand> [args ...]

If ``--workflow-root`` is omitted, the entrypoint delegates to the shared
workflow-root resolver. That keeps ``SPRINTS_WORKFLOW_ROOT`` as the canonical
override for installed package execution.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _resolve_workflow_root(argv: list[str]) -> tuple[Path, list[str]]:
    """Peel --workflow-root / --workflow-root=<path> out of argv."""
    out: list[str] = []
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
        out.append(arg)
        i += 1

    if workflow_root is None:
        from sprints.core.paths import resolve_default_workflow_root

        workflow_root = resolve_default_workflow_root(
            plugin_dir=Path(__file__).resolve().parent.parent
        )
    return workflow_root, out


def main(argv: list[str] | None = None) -> int:
    from sprints.workflows import run_cli

    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw in ([], ["-h"], ["--help"]):
        print(
            "usage: python -m sprints.workflows --workflow-root <path> "
            "{validate,show,status,lanes,retry,release,complete,tick,actor-run} ..."
        )
        return 0
    workflow_root, command_argv = _resolve_workflow_root(raw)
    try:
        return run_cli(workflow_root, command_argv)
    except subprocess.CalledProcessError as exc:
        msg = f"Command failed with exit status {exc.returncode}"
        if exc.stderr:
            msg += f"\n{exc.stderr.strip()}"
        print(msg, file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
