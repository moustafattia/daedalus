from __future__ import annotations

import sys
from pathlib import Path

from workflows.agentic import WORKFLOW, load_config
from workflows.contract import load_workflow_contract


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    workflow_root = Path.cwd()
    contract = load_workflow_contract(workflow_root)
    config = load_config(workflow_root=workflow_root, raw=contract.config)
    return WORKFLOW.run_cli(workspace=config, argv=raw_argv)


if __name__ == "__main__":
    raise SystemExit(main())
