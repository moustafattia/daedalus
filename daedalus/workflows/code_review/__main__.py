"""Per-workflow direct-form entrypoint for the code-review workflow.

Invocation:

    python3 -m workflows.code_review --workflow-root <path> <cmd>

This form pins the workflow module to code-review regardless of what the
YAML declares. Used by developers + tests to force a specific module.
Normal operators should use the generic form instead:

    python3 -m workflows --workflow-root <path> <cmd>
"""
from __future__ import annotations

import subprocess
import sys

from workflows import run_cli
from workflows.__main__ import _resolve_workflow_root

# Public alias so tests and external callers can call resolve_workflow_root
# without knowing the private name from the parent package entrypoint.
resolve_workflow_root = _resolve_workflow_root


def main(argv: list[str] | None = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    workflow_root, command_argv = _resolve_workflow_root(raw)
    try:
        return run_cli(workflow_root, command_argv, require_workflow="code-review")
    except subprocess.CalledProcessError as exc:
        msg = f"Command failed with exit status {exc.returncode}"
        if exc.stderr:
            msg += f"\n{exc.stderr.strip()}"
        print(msg, file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
