"""HTTP routes wiring for the optional status surface.

Uses :class:`http.server.ThreadingHTTPServer` from stdlib — no extra deps.
The server thread is a daemon thread so process exit on Ctrl-C is clean
even if the main thread forgot to call ``handle.shutdown()``.

Path layout (Symphony §13.7 / spec §6.3):

    GET  /                  → HTML dashboard
    GET  /api/v1/state      → state_view() JSON
    GET  /api/v1/<id>       → issue_view(id) JSON or 404
    POST /api/v1/refresh    → spawn a tick subprocess (debounced)
    *    other              → 404 JSON

Per-server handler subclassing keeps the workflow_root / db_path /
events_log_path / refresh_controller closures attached to the handler
class so the stdlib BaseHTTPRequestHandler signature is unchanged.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from workflows.code_review.paths import runtime_paths
from workflows.code_review.server.html import render_dashboard
from workflows.code_review.server.refresh import RefreshController
from workflows.code_review.server.views import issue_view, state_view


@dataclass
class ServerHandle:
    """Handle for a running HTTP server.

    Attributes:
        port: The bound port (relevant when ``port=0`` was requested).
        thread: The daemon thread running ``serve_forever``.
        shutdown: Callable that triggers a clean shutdown.
    """
    port: int
    thread: threading.Thread
    _server: ThreadingHTTPServer

    def shutdown(self) -> None:
        # ``shutdown()`` blocks until ``serve_forever`` returns.
        self._server.shutdown()
        self._server.server_close()


def _make_handler_class(
    *,
    workflow_root: Path,
    db_path: Path,
    events_log_path: Path,
    refresh_controller: RefreshController,
) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        # --- helpers ---
        def _respond(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _respond_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self._respond(status, "application/json; charset=utf-8", body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Silence the default access log; otherwise tests spam stderr.
            return

        # --- routes ---
        def do_GET(self) -> None:  # noqa: N802 (stdlib name)
            path = urllib.parse.urlsplit(self.path).path
            if path == "/" or path == "":
                state = state_view(db_path, events_log_path)
                html_body = render_dashboard(state).encode("utf-8")
                self._respond(200, "text/html; charset=utf-8", html_body)
                return
            if path == "/api/v1/state":
                self._respond_json(200, state_view(db_path, events_log_path))
                return
            if path.startswith("/api/v1/"):
                ident = urllib.parse.unquote(path[len("/api/v1/"):])
                # /api/v1/refresh is POST-only; reject GETs cleanly.
                if ident == "refresh":
                    self._respond_json(
                        405,
                        {"error": {"code": "method_not_allowed", "message": "POST required"}},
                    )
                    return
                view = issue_view(db_path, events_log_path, ident)
                if view is None:
                    self._respond_json(
                        404,
                        {"error": {"code": "issue_not_found", "message": f"unknown identifier: {ident}"}},
                    )
                    return
                self._respond_json(200, view)
                return
            self._respond_json(404, {"error": {"code": "not_found"}})

        def do_POST(self) -> None:  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/v1/refresh":
                triggered = refresh_controller.trigger()
                self._respond_json(202, {"triggered": triggered})
                return
            self._respond_json(404, {"error": {"code": "not_found"}})

    return _Handler


def start_server(
    workflow_root: Path,
    *,
    port: int = 0,
    bind: str = "127.0.0.1",
) -> ServerHandle:
    """Start a ThreadingHTTPServer in a daemon thread.

    Args:
        workflow_root: The Daedalus workflow root. Used to locate
            ``daedalus.db`` and ``daedalus-events.jsonl`` per request,
            and as the ``--workflow-root`` argument when the refresh
            endpoint shells out a tick subprocess.
        port: TCP port. ``0`` requests an OS-assigned ephemeral port,
            which the caller can read from ``ServerHandle.port`` after
            the call returns.
        bind: Address to bind. Defaults to loopback. Non-loopback binds
            are gated by the schema layer, not by this function.

    Returns:
        A :class:`ServerHandle` whose ``thread`` is already running.
    """
    workflow_root = Path(workflow_root)
    paths = runtime_paths(workflow_root)
    db_path = Path(paths["db_path"])
    events_log_path = Path(paths["event_log_path"])
    refresh_controller = RefreshController(workflow_root)

    handler_cls = _make_handler_class(
        workflow_root=workflow_root,
        db_path=db_path,
        events_log_path=events_log_path,
        refresh_controller=refresh_controller,
    )
    server = ThreadingHTTPServer((bind, port), handler_cls)
    actual_port = server.server_address[1]

    thread = threading.Thread(
        target=server.serve_forever,
        name=f"daedalus-status-server-{actual_port}",
        daemon=True,
    )
    thread.start()
    return ServerHandle(port=actual_port, thread=thread, _server=server)
