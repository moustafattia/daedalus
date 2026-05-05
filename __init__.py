"""Hermes directory-plugin entrypoint for Sprints.

The product code lives in the uv workspace packages. This root module is the
canonical Git-install surface required by Hermes directory plugins.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for _path in (
    _ROOT / "packages" / "core" / "src",
    _ROOT / "packages" / "cli" / "src",
    _ROOT / "packages" / "plugins" / "hermes" / "src",
):
    _text = str(_path)
    if _text not in sys.path:
        sys.path.insert(0, _text)

from sprints_hermes import register as _register  # noqa: E402


def register(ctx):
    return _register(ctx)
