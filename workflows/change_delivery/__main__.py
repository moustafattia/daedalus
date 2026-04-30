"""Repo-root change-delivery workflow entrypoint wrapper."""

import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_ROOT_STR = str(_PLUGIN_ROOT)
if _PLUGIN_ROOT_STR not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT_STR)

from daedalus.workflows.change_delivery.__main__ import *  # noqa: F401,F403
from daedalus.workflows.change_delivery.__main__ import main as _main


if __name__ == "__main__":
    raise SystemExit(_main())
