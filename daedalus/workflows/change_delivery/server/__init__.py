"""HTTP status surface for the change-delivery workflow (Symphony §13.7).

Standalone long-running CLI subcommand that reads ``daedalus.db`` and the
events log read-only — no shared in-process state with the tick loop.
The Daedalus architecture is CLI-tick, so the server reads its data
fresh from disk per request.

Public surface:

- :func:`start_server` — bind a ThreadingHTTPServer and return a handle.
- :class:`ServerHandle` — exposes ``.port``, ``.thread``, ``.shutdown()``.
"""
from __future__ import annotations

from workflows.change_delivery.server.routes import ServerHandle, start_server

__all__ = ["ServerHandle", "start_server"]
