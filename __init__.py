"""Repo-root Hermes plugin entrypoint.

Hermes' official Git install path expects ``plugin.yaml`` and ``__init__.py``
at the repository root. The real implementation lives under ``./daedalus/``.
This wrapper keeps the repo installable via ``hermes plugins install`` without
moving the engine package again.
"""

try:
    from .daedalus import register as _register
except ImportError:
    from daedalus import register as _register


def register(ctx):
    return _register(ctx)
