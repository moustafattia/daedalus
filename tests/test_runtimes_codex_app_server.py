import base64
import hashlib
import json
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pytest


def _write_fake_app_server(path: Path, requests_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                f"requests_path = {str(requests_path)!r}",
                "tid = 'thread-1'",
                "trn = 'turn-1'",
                "",
                "def emit(payload):",
                "    print(json.dumps(payload), flush=True)",
                "",
                "def record(payload):",
                "    with open(requests_path, 'a', encoding='utf-8') as fh:",
                "        fh.write(json.dumps(payload) + '\\n')",
                "",
                "for line in sys.stdin:",
                "    payload = json.loads(line)",
                "    record(payload)",
                "    method = payload.get('method')",
                "    request_id = payload.get('id')",
                "    if method == 'initialize':",
                "        emit({'id': request_id, 'result': {'userAgent': 'fake-codex', 'codexHome': '/tmp/codex'}})",
                "    elif method == 'initialized':",
                "        continue",
                "    elif method == 'thread/start':",
                "        thread = {'id': tid, 'status': 'running', 'turns': []}",
                "        emit({'id': request_id, 'result': {'thread': thread}})",
                "    elif method == 'thread/resume':",
                "        tid = payload.get('params', {}).get('threadId') or tid",
                "        thread = {'id': tid, 'status': 'running', 'turns': []}",
                "        emit({'id': request_id, 'result': {'thread': thread}})",
                "    elif method == 'turn/start':",
                "        running_turn = {'id': trn, 'status': 'running', 'items': []}",
                "        emit({'id': request_id, 'result': {'turn': running_turn}})",
                "        emit({'method': 'turn/started', 'params': {'threadId': tid, 'turn': running_turn}})",
                "        item = {'threadId': tid, 'turnId': trn, 'itemId': 'item-1'}",
                "        emit({'method': 'agent/message_delta', 'params': {**item, 'delta': 'hello '}})",
                "        emit({'method': 'agent/message_delta', 'params': {**item, 'delta': 'world'}})",
                "        usage_base = {'cachedInputTokens': 0, 'reasoningOutputTokens': 0}",
                "        last_usage = dict(usage_base, inputTokens=2, outputTokens=3, totalTokens=5)",
                "        total_usage = dict(usage_base, inputTokens=11, outputTokens=7, totalTokens=18)",
                "        token_usage = {'last': last_usage, 'total': total_usage}",
                "        emit({'method': 'thread/tokenUsage/updated', 'params': {**item, 'tokenUsage': token_usage}})",
                "        rate_limits = {'limitName': 'primary', 'requests_remaining': 99}",
                "        emit({'method': 'account/rateLimits/updated', 'params': {'rateLimits': rate_limits}})",
                "        completed_turn = {'id': trn, 'status': 'completed', 'items': []}",
                "        emit({'method': 'turn/completed', 'params': {'threadId': tid, 'turn': completed_turn}})",
                "        break",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_fake_resume_rejecting_app_server(path: Path, requests_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                f"requests_path = {str(requests_path)!r}",
                "",
                "def emit(payload):",
                "    print(json.dumps(payload), flush=True)",
                "",
                "def record(payload):",
                "    with open(requests_path, 'a', encoding='utf-8') as fh:",
                "        fh.write(json.dumps(payload) + '\\n')",
                "",
                "for line in sys.stdin:",
                "    payload = json.loads(line)",
                "    record(payload)",
                "    method = payload.get('method')",
                "    request_id = payload.get('id')",
                "    if method == 'initialize':",
                "        emit({'id': request_id, 'result': {'userAgent': 'fake-codex'}})",
                "    elif method == 'initialized':",
                "        continue",
                "    elif method == 'thread/resume':",
                "        error = {'code': -32000, 'message': 'thread not found'}",
                "        emit({'id': request_id, 'error': error})",
                "        break",
                "    elif method == 'thread/start':",
                "        emit({'id': request_id, 'result': {'thread': {'id': 'unexpected-thread'}}})",
                "        break",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_fake_malformed_app_server(path: Path, requests_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                f"requests_path = {str(requests_path)!r}",
                "",
                "def record(payload):",
                "    with open(requests_path, 'a', encoding='utf-8') as fh:",
                "        fh.write(json.dumps(payload) + '\\n')",
                "",
                "for line in sys.stdin:",
                "    payload = json.loads(line)",
                "    record(payload)",
                "    if payload.get('method') == 'initialize':",
                "        print('not-json-from-app-server', flush=True)",
                "        break",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_fake_quiet_turn_app_server(path: Path, requests_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "import time",
                f"requests_path = {str(requests_path)!r}",
                "",
                "def emit(payload):",
                "    print(json.dumps(payload), flush=True)",
                "",
                "def record(payload):",
                "    with open(requests_path, 'a', encoding='utf-8') as fh:",
                "        fh.write(json.dumps(payload) + '\\n')",
                "",
                "for line in sys.stdin:",
                "    payload = json.loads(line)",
                "    record(payload)",
                "    method = payload.get('method')",
                "    request_id = payload.get('id')",
                "    if method == 'initialize':",
                "        emit({'id': request_id, 'result': {'userAgent': 'fake-codex'}})",
                "    elif method == 'initialized':",
                "        continue",
                "    elif method == 'thread/start':",
                "        emit({'id': request_id, 'result': {'thread': {'id': 'thread-quiet'}}})",
                "    elif method == 'turn/start':",
                "        turn = {'id': 'turn-quiet', 'status': 'running', 'items': []}",
                "        emit({'id': request_id, 'result': {'turn': turn}})",
                "        time.sleep(1.2)",
                "        item = {'threadId': 'thread-quiet', 'turnId': 'turn-quiet', 'itemId': 'item-1'}",
                "        emit({'method': 'item/agentMessage/delta', 'params': {**item, 'delta': 'quiet ok'}})",
                "        completed_turn = {'id': 'turn-quiet', 'status': 'completed', 'items': []}",
                "        emit({'method': 'turn/completed', 'params': {'threadId': 'thread-quiet', 'turn': completed_turn}})",
                "        break",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_fake_cancellable_app_server(path: Path, requests_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                f"requests_path = {str(requests_path)!r}",
                "",
                "def emit(payload):",
                "    print(json.dumps(payload), flush=True)",
                "",
                "def record(payload):",
                "    with open(requests_path, 'a', encoding='utf-8') as fh:",
                "        fh.write(json.dumps(payload) + '\\n')",
                "",
                "for line in sys.stdin:",
                "    payload = json.loads(line)",
                "    record(payload)",
                "    method = payload.get('method')",
                "    request_id = payload.get('id')",
                "    if method == 'initialize':",
                "        emit({'id': request_id, 'result': {'userAgent': 'fake-codex'}})",
                "    elif method == 'initialized':",
                "        continue",
                "    elif method == 'thread/start':",
                "        emit({'id': request_id, 'result': {'thread': {'id': 'thread-cancel'}}})",
                "    elif method == 'turn/start':",
                "        turn = {'id': 'turn-cancel', 'status': 'running', 'items': []}",
                "        emit({'id': request_id, 'result': {'turn': turn}})",
                "        emit({'method': 'turn/started', 'params': {'threadId': 'thread-cancel', 'turn': turn}})",
                "    elif method == 'turn/interrupt':",
                "        emit({'id': request_id, 'result': {}})",
                "        break",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


class _FakeWebSocketAppServer:
    def __init__(self, *, required_auth_token: str | None = None):
        self.requests: list[dict] = []
        self.websocket_authorizations: list[str | None] = []
        self.websocket_connections = 0
        self.required_auth_token = required_auth_token
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen()
        self.endpoint = f"ws://127.0.0.1:{self._sock.getsockname()[1]}"
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        try:
            socket.create_connection(("127.0.0.1", self._sock.getsockname()[1]), timeout=0.2).close()
        except OSError:
            pass
        self._sock.close()
        self._thread.join(timeout=2)

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle_connection, args=(conn,), daemon=True).start()

    def _handle_connection(self, conn: socket.socket):
        with conn:
            request = self._read_http_request(conn)
            if not request:
                return
            first_line = request.split("\r\n", 1)[0]
            headers = self._headers(request)
            if first_line.startswith("GET /readyz "):
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
                return
            if headers.get("upgrade", "").lower() != "websocket":
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                return
            authorization = headers.get("authorization")
            self.websocket_authorizations.append(authorization)
            if self.required_auth_token and authorization != f"Bearer {self.required_auth_token}":
                conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
                return
            self.websocket_connections += 1
            key = headers["sec-websocket-key"]
            accept = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
            ).decode("ascii")
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            )
            conn.sendall(response.encode("ascii"))
            self._run_jsonrpc(conn)

    def _run_jsonrpc(self, conn: socket.socket):
        thread_id = "thread-ws"
        while True:
            payload = self._read_ws_text(conn)
            if payload is None:
                return
            message = json.loads(payload)
            self.requests.append(message)
            method = message.get("method")
            request_id = message.get("id")
            if method == "initialize":
                self._send_ws_json(conn, {"id": request_id, "result": {"userAgent": "fake-ws-codex"}})
            elif method == "initialized":
                continue
            elif method == "thread/start":
                thread = {"id": thread_id, "status": "running", "turns": []}
                self._send_ws_json(conn, {"id": request_id, "result": {"thread": thread}})
            elif method == "thread/resume":
                thread_id = str((message.get("params") or {}).get("threadId") or thread_id)
                thread = {"id": thread_id, "status": "running", "turns": []}
                self._send_ws_json(conn, {"id": request_id, "result": {"thread": thread}})
            elif method == "turn/start":
                turn = {"id": "turn-ws", "status": "running", "items": []}
                self._send_ws_json(conn, {"id": request_id, "result": {"turn": turn}})
                self._send_ws_json(
                    conn,
                    {"method": "turn/started", "params": {"threadId": thread_id, "turn": turn}},
                )
                self._send_ws_json(
                    conn,
                    {
                        "method": "agent/message_delta",
                        "params": {"threadId": thread_id, "turnId": "turn-ws", "itemId": "item-1", "delta": "ws ok"},
                    },
                )
                completed = {"id": "turn-ws", "status": "completed", "items": []}
                self._send_ws_json(
                    conn,
                    {"method": "turn/completed", "params": {"threadId": thread_id, "turn": completed}},
                )
                return

    def _read_http_request(self, conn: socket.socket) -> str:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return ""
            data += chunk
        return data.decode("iso-8859-1")

    def _headers(self, request: str) -> dict[str, str]:
        out = {}
        for line in request.split("\r\n")[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            out[name.strip().lower()] = value.strip()
        return out

    def _read_ws_text(self, conn: socket.socket) -> str | None:
        header = self._recv_exact(conn, 2)
        if not header:
            return None
        opcode = header[0] & 0x0F
        if opcode == 0x8:
            return None
        length = header[1] & 0x7F
        if length == 126:
            length = int.from_bytes(self._recv_exact(conn, 2), "big")
        elif length == 127:
            length = int.from_bytes(self._recv_exact(conn, 8), "big")
        mask = self._recv_exact(conn, 4)
        data = self._recv_exact(conn, length)
        return bytes(byte ^ mask[index % 4] for index, byte in enumerate(data)).decode("utf-8")

    def _send_ws_json(self, conn: socket.socket, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        if len(data) < 126:
            header = bytes([0x81, len(data)])
        elif len(data) <= 0xFFFF:
            header = bytes([0x81, 126]) + len(data).to_bytes(2, "big")
        else:
            header = bytes([0x81, 127]) + len(data).to_bytes(8, "big")
        conn.sendall(header + data)

    def _recv_exact(self, conn: socket.socket, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = conn.recv(size - len(data))
            if not chunk:
                return b""
            data += chunk
        return data


class _FakePersistentWebSocketAppServer(_FakeWebSocketAppServer):
    def _run_jsonrpc(self, conn: socket.socket):
        thread_id = "thread-warm"
        turn_count = 0
        cumulative_input_tokens = 0
        cumulative_output_tokens = 0
        while True:
            payload = self._read_ws_text(conn)
            if payload is None:
                return
            message = json.loads(payload)
            self.requests.append(message)
            method = message.get("method")
            request_id = message.get("id")
            if method == "initialize":
                self._send_ws_json(conn, {"id": request_id, "result": {"userAgent": "fake-warm-codex"}})
            elif method == "initialized":
                continue
            elif method == "thread/start":
                thread = {"id": thread_id, "status": "running", "turns": []}
                self._send_ws_json(conn, {"id": request_id, "result": {"thread": thread}})
            elif method == "thread/resume":
                thread_id = str((message.get("params") or {}).get("threadId") or thread_id)
                thread = {"id": thread_id, "status": "running", "turns": []}
                self._send_ws_json(conn, {"id": request_id, "result": {"thread": thread}})
            elif method == "turn/start":
                turn_count += 1
                input_tokens = turn_count
                output_tokens = turn_count + 1
                cumulative_input_tokens += input_tokens
                cumulative_output_tokens += output_tokens
                turn = {"id": f"turn-warm-{turn_count}", "status": "running", "items": []}
                self._send_ws_json(conn, {"id": request_id, "result": {"turn": turn}})
                self._send_ws_json(
                    conn,
                    {"method": "turn/started", "params": {"threadId": thread_id, "turn": turn}},
                )
                self._send_ws_json(
                    conn,
                    {
                        "method": "agent/message_delta",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn["id"],
                            "itemId": f"item-{turn_count}",
                            "delta": f"warm {turn_count}",
                        },
                    },
                )
                item = {"threadId": thread_id, "turnId": turn["id"], "itemId": f"item-{turn_count}"}
                last_usage = {
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "totalTokens": input_tokens + output_tokens,
                }
                total_usage = {
                    "inputTokens": cumulative_input_tokens,
                    "outputTokens": cumulative_output_tokens,
                    "totalTokens": cumulative_input_tokens + cumulative_output_tokens,
                }
                self._send_ws_json(
                    conn,
                    {
                        "method": "thread/tokenUsage/updated",
                        "params": {**item, "tokenUsage": {"last": last_usage, "total": total_usage}},
                    },
                )
                self._send_ws_json(
                    conn,
                    {
                        "method": "account/rateLimits/updated",
                        "params": {"rateLimits": {"requests_remaining": 100 - turn_count}},
                    },
                )
                completed = {"id": turn["id"], "status": "completed", "items": []}
                self._send_ws_json(
                    conn,
                    {"method": "turn/completed", "params": {"threadId": thread_id, "turn": completed}},
                )


def test_codex_app_server_runtime_speaks_jsonrpc_and_maps_metrics(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    worktree = tmp_path / "repo"
    worktree.mkdir()
    server = tmp_path / "fake_app_server.py"
    requests_path = tmp_path / "requests.jsonl"
    _write_fake_app_server(server, requests_path)

    runtime = CodexAppServerRuntime(
        {
            "command": [sys.executable, str(server)],
            "approval_policy": "never",
            "thread_sandbox": "workspace-write",
            "turn_sandbox_policy": "workspace-write",
            "turn_timeout_ms": 5000,
            "read_timeout_ms": 1000,
            "stall_timeout_ms": 5000,
        },
        run=None,
    )

    result = runtime.run_prompt_result(
        worktree=worktree,
        session_name="ISSUE-1",
        prompt="Do the thing",
        model="gpt-5.5",
    )

    assert result.output == "hello world\n"
    assert result.session_id == "thread-1"
    assert result.thread_id == "thread-1"
    assert result.turn_id == "turn-1"
    assert result.turn_count == 1
    assert result.last_event == "turn/completed"
    assert result.tokens == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    assert result.rate_limits == {"limitName": "primary", "requests_remaining": 99}

    requests = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
    assert all("jsonrpc" not in item for item in requests)
    thread_start = next(item for item in requests if item.get("method") == "thread/start")
    turn_start = next(item for item in requests if item.get("method") == "turn/start")
    assert thread_start["params"]["approvalPolicy"] == "never"
    assert thread_start["params"]["sandbox"] == "workspace-write"
    assert thread_start["params"]["model"] == "gpt-5.5"
    assert thread_start["params"]["ephemeral"] is False
    assert turn_start["params"]["input"] == [{"type": "text", "text": "Do the thing"}]
    assert turn_start["params"]["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": [str(worktree)],
    }


def test_codex_app_server_runtime_filters_events_by_active_thread_and_turn():
    from runtimes.codex_app_server import CodexAppServerError, CodexAppServerRuntime, _RunState

    runtime = CodexAppServerRuntime({"command": [sys.executable, "-c", ""]}, run=None)
    state = _RunState(session_id="thread-current", thread_id="thread-current", turn_id="turn-current")

    assert (
        runtime._consume_message(
            {"method": "error", "params": {"threadId": "thread-stale", "turnId": "turn-stale", "message": "timed out"}},
            state=state,
        )
        is False
    )
    assert state.last_event is None

    assert (
        runtime._consume_message(
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-current", "turn": {"id": "turn-stale", "status": "completed"}},
            },
            state=state,
        )
        is False
    )
    assert state.last_event is None

    assert (
        runtime._consume_message(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "thread-current",
                    "turnId": "turn-stale",
                    "tokenUsage": {"last": {"inputTokens": 99, "outputTokens": 99, "totalTokens": 198}},
                },
            },
            state=state,
        )
        is False
    )
    assert state.tokens == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    assert (
        runtime._consume_message(
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-current", "turn": {"id": "turn-current", "status": "completed"}},
            },
            state=state,
        )
        is True
    )
    assert state.last_event == "turn/completed"

    with pytest.raises(CodexAppServerError, match="current failure"):
        runtime._consume_message(
            {
                "method": "error",
                "params": {"threadId": "thread-current", "turnId": "turn-current", "message": "current failure"},
            },
            state=state,
        )


def test_codex_app_server_runtime_tracks_item_notifications_without_unsupported_message():
    from runtimes.codex_app_server import CodexAppServerRuntime, _RunState

    runtime = CodexAppServerRuntime({"command": [sys.executable, "-c", ""]}, run=None)
    state = _RunState(session_id="thread-current", thread_id="thread-current", turn_id="turn-current")

    assert (
        runtime._consume_message(
            {
                "method": "item/started",
                "params": {"threadId": "thread-current", "turnId": "turn-current", "itemId": "item-current"},
            },
            state=state,
        )
        is False
    )

    assert state.last_event == "item/started"
    assert state.last_message is None


def test_codex_app_server_runtime_resumes_existing_thread(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    worktree = tmp_path / "repo"
    worktree.mkdir()
    server = tmp_path / "fake_app_server.py"
    requests_path = tmp_path / "requests.jsonl"
    _write_fake_app_server(server, requests_path)

    runtime = CodexAppServerRuntime(
        {
            "command": [sys.executable, str(server)],
            "approval_policy": "never",
            "turn_timeout_ms": 5000,
            "read_timeout_ms": 1000,
            "stall_timeout_ms": 5000,
        },
        run=None,
    )
    runtime.ensure_session(
        worktree=worktree,
        session_name="ISSUE-1",
        model="gpt-5.5",
        resume_session_id="thread-existing",
    )

    result = runtime.run_prompt_result(
        worktree=worktree,
        session_name="ISSUE-1",
        prompt="Continue",
        model="gpt-5.5",
    )

    assert result.thread_id == "thread-existing"
    requests = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
    assert "thread/start" not in [item.get("method") for item in requests]
    thread_resume = next(item for item in requests if item.get("method") == "thread/resume")
    assert thread_resume["params"]["threadId"] == "thread-existing"


def test_codex_app_server_runtime_does_not_fallback_when_resume_fails(tmp_path):
    from runtimes.codex_app_server import CodexAppServerError, CodexAppServerRuntime

    worktree = tmp_path / "repo"
    worktree.mkdir()
    server = tmp_path / "fake_resume_rejecting_app_server.py"
    requests_path = tmp_path / "requests.jsonl"
    _write_fake_resume_rejecting_app_server(server, requests_path)

    runtime = CodexAppServerRuntime(
        {
            "command": [sys.executable, str(server)],
            "approval_policy": "never",
            "turn_timeout_ms": 5000,
            "read_timeout_ms": 1000,
            "stall_timeout_ms": 5000,
        },
        run=None,
    )
    runtime.ensure_session(
        worktree=worktree,
        session_name="ISSUE-1",
        model="gpt-5.5",
        resume_session_id="missing-thread",
    )

    with pytest.raises(CodexAppServerError, match="thread/resume.*thread not found"):
        runtime.run_prompt_result(
            worktree=worktree,
            session_name="ISSUE-1",
            prompt="Continue",
            model="gpt-5.5",
        )

    requests = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
    methods = [item.get("method") for item in requests]
    assert "thread/resume" in methods
    assert "thread/start" not in methods
    assert "turn/start" not in methods


def test_codex_app_server_runtime_surfaces_malformed_stdout(tmp_path):
    from runtimes.codex_app_server import CodexAppServerError, CodexAppServerRuntime

    worktree = tmp_path / "repo"
    worktree.mkdir()
    server = tmp_path / "fake_malformed_app_server.py"
    requests_path = tmp_path / "requests.jsonl"
    _write_fake_malformed_app_server(server, requests_path)

    runtime = CodexAppServerRuntime(
        {
            "command": [sys.executable, str(server)],
            "approval_policy": "never",
            "turn_timeout_ms": 5000,
            "read_timeout_ms": 1000,
            "stall_timeout_ms": 5000,
        },
        run=None,
    )

    with pytest.raises(CodexAppServerError, match="non-JSON stdout"):
        runtime.run_prompt_result(
            worktree=worktree,
            session_name="ISSUE-BAD-PROTOCOL",
            prompt="Do the thing",
            model="gpt-5.5",
        )

    requests = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
    assert [item.get("method") for item in requests] == ["initialize"]


def test_codex_app_server_runtime_allows_quiet_period_longer_than_read_timeout(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    worktree = tmp_path / "repo"
    worktree.mkdir()
    server = tmp_path / "fake_quiet_turn_app_server.py"
    requests_path = tmp_path / "requests.jsonl"
    _write_fake_quiet_turn_app_server(server, requests_path)

    runtime = CodexAppServerRuntime(
        {
            "command": [sys.executable, str(server)],
            "approval_policy": "never",
            "turn_timeout_ms": 5000,
            "read_timeout_ms": 1000,
            "stall_timeout_ms": 4000,
        },
        run=None,
    )

    result = runtime.run_prompt_result(
        worktree=worktree,
        session_name="ISSUE-QUIET",
        prompt="Work quietly",
        model="gpt-5.5",
    )

    assert result.output == "quiet ok\n"
    assert result.thread_id == "thread-quiet"
    assert result.turn_id == "turn-quiet"


def test_codex_app_server_runtime_cancellation_interrupts_active_turn(tmp_path):
    from runtimes.codex_app_server import CodexAppServerError, CodexAppServerRuntime

    worktree = tmp_path / "repo"
    worktree.mkdir()
    server = tmp_path / "fake_cancellable_app_server.py"
    requests_path = tmp_path / "requests.jsonl"
    _write_fake_cancellable_app_server(server, requests_path)

    runtime = CodexAppServerRuntime(
        {
            "command": [sys.executable, str(server)],
            "approval_policy": "never",
            "turn_timeout_ms": 5000,
            "read_timeout_ms": 100,
            "stall_timeout_ms": 5000,
        },
        run=None,
    )
    cancel_event = threading.Event()
    runtime.set_cancel_event(cancel_event)
    errors: list[Exception] = []

    def run_turn():
        try:
            runtime.run_prompt_result(
                worktree=worktree,
                session_name="ISSUE-CANCEL",
                prompt="Work until canceled",
                model="gpt-5.5",
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_turn)
    thread.start()
    try:
        for _ in range(30):
            if requests_path.exists():
                methods = [json.loads(line).get("method") for line in requests_path.read_text(encoding="utf-8").splitlines()]
                if "turn/start" in methods:
                    break
            time.sleep(0.02)
        else:
            raise AssertionError("turn/start was not sent")

        cancel_event.set()
        thread.join(timeout=3)
    finally:
        cancel_event.set()

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CodexAppServerError)
    assert "turn canceled" in str(errors[0])
    requests = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
    interrupt = next(item for item in requests if item.get("method") == "turn/interrupt")
    assert interrupt["params"] == {"threadId": "thread-cancel", "turnId": "turn-cancel"}


def test_codex_app_server_runtime_interrupt_turn_sends_protocol_request(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    worktree = tmp_path / "repo"
    worktree.mkdir()
    server = tmp_path / "fake_cancellable_app_server.py"
    requests_path = tmp_path / "requests.jsonl"
    _write_fake_cancellable_app_server(server, requests_path)

    runtime = CodexAppServerRuntime(
        {
            "command": [sys.executable, str(server)],
            "approval_policy": "never",
            "read_timeout_ms": 100,
        },
        run=None,
    )

    assert runtime.interrupt_turn(thread_id="thread-cancel", turn_id="turn-cancel", worktree=worktree) is True

    requests = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
    interrupt = next(item for item in requests if item.get("method") == "turn/interrupt")
    assert interrupt["params"] == {"threadId": "thread-cancel", "turnId": "turn-cancel"}


def test_codex_app_server_runtime_rejects_non_protocol_approval_policy(tmp_path):
    from runtimes.codex_app_server import CodexAppServerError, CodexAppServerRuntime

    runtime = CodexAppServerRuntime(
        {"command": [sys.executable, "-c", ""], "approval_policy": "auto"},
        run=None,
    )

    with pytest.raises(CodexAppServerError, match="approval_policy"):
        runtime.run_prompt_result(
            worktree=tmp_path,
            session_name="ISSUE-1",
            prompt="Do the thing",
            model="gpt-5.5",
        )


def test_codex_app_server_runtime_rejects_keep_alive_in_managed_mode():
    from runtimes.codex_app_server import CodexAppServerError, CodexAppServerRuntime

    with pytest.raises(CodexAppServerError, match="keep_alive requires mode: external"):
        CodexAppServerRuntime(
            {
                "mode": "managed",
                "command": [sys.executable, "-c", ""],
                "keep_alive": True,
            },
            run=None,
        )


def test_codex_app_server_runtime_connects_to_external_websocket(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    with _FakeWebSocketAppServer() as server:
        runtime = CodexAppServerRuntime(
            {
                "mode": "external",
                "endpoint": server.endpoint,
                "approval_policy": "never",
                "thread_sandbox": "workspace-write",
                "turn_sandbox_policy": "workspace-write",
                "turn_timeout_ms": 5000,
                "read_timeout_ms": 1000,
                "stall_timeout_ms": 5000,
            },
            run=None,
        )

        health = runtime.assess_health({}, worktree=tmp_path)
        result = runtime.run_prompt_result(
            worktree=tmp_path,
            session_name="ISSUE-1",
            prompt="Do the thing",
            model="gpt-5.5",
        )

    assert health.healthy is True
    assert result.output == "ws ok\n"
    assert result.thread_id == "thread-ws"
    assert result.turn_id == "turn-ws"
    assert [item.get("method") for item in server.requests] == [
        "initialize",
        "initialized",
        "thread/start",
        "turn/start",
    ]
    turn_start = next(item for item in server.requests if item.get("method") == "turn/start")
    thread_start = next(item for item in server.requests if item.get("method") == "thread/start")
    assert thread_start["params"]["ephemeral"] is False
    assert turn_start["params"]["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": [str(tmp_path)],
    }


def test_codex_app_server_runtime_sends_external_websocket_auth_token(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    with _FakeWebSocketAppServer(required_auth_token="secret-token") as server:
        runtime = CodexAppServerRuntime(
            {
                "mode": "external",
                "endpoint": server.endpoint,
                "approval_policy": "never",
                "ws_token": "secret-token",
                "turn_timeout_ms": 5000,
                "read_timeout_ms": 1000,
                "stall_timeout_ms": 5000,
            },
            run=None,
        )

        result = runtime.run_prompt_result(
            worktree=tmp_path,
            session_name="ISSUE-AUTH",
            prompt="Do the thing",
            model="gpt-5.5",
        )

    assert result.output == "ws ok\n"
    assert result.thread_id == "thread-ws"
    assert server.websocket_authorizations == ["Bearer secret-token"]
    assert [item.get("method") for item in server.requests] == [
        "initialize",
        "initialized",
        "thread/start",
        "turn/start",
    ]


def test_codex_app_server_runtime_reuses_external_websocket_by_default(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    with _FakePersistentWebSocketAppServer() as server:
        runtime = CodexAppServerRuntime(
            {
                "mode": "external",
                "endpoint": server.endpoint,
                "approval_policy": "never",
                "turn_timeout_ms": 5000,
                "read_timeout_ms": 1000,
                "stall_timeout_ms": 5000,
            },
            run=None,
        )
        try:
            assert runtime.diagnostics()["keep_alive"] is True
            first = runtime.run_prompt_result(
                worktree=tmp_path,
                session_name="ISSUE-1",
                prompt="First",
                model="gpt-5.5",
            )
            runtime.ensure_session(
                worktree=tmp_path,
                session_name="ISSUE-1",
                model="gpt-5.5",
                resume_session_id=first.thread_id,
            )
            second = runtime.run_prompt_result(
                worktree=tmp_path,
                session_name="ISSUE-1",
                prompt="Second",
                model="gpt-5.5",
            )
        finally:
            runtime.close()

    methods = [item.get("method") for item in server.requests]
    assert server.websocket_connections == 1
    assert methods.count("initialize") == 1
    assert methods.count("turn/start") == 2
    assert second.output == "warm 2\n"


def test_codex_app_server_runtime_uses_last_token_usage_as_turn_delta(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    with _FakePersistentWebSocketAppServer() as server:
        runtime = CodexAppServerRuntime(
            {
                "mode": "external",
                "endpoint": server.endpoint,
                "approval_policy": "never",
                "keep_alive": True,
                "turn_timeout_ms": 5000,
                "read_timeout_ms": 1000,
                "stall_timeout_ms": 5000,
            },
            run=None,
        )
        try:
            first = runtime.run_prompt_result(
                worktree=tmp_path,
                session_name="ISSUE-1",
                prompt="First",
                model="gpt-5.5",
            )
            runtime.ensure_session(
                worktree=tmp_path,
                session_name="ISSUE-1",
                model="gpt-5.5",
                resume_session_id=first.thread_id,
            )
            second = runtime.run_prompt_result(
                worktree=tmp_path,
                session_name="ISSUE-1",
                prompt="Second",
                model="gpt-5.5",
            )
        finally:
            runtime.close()

    methods = [item.get("method") for item in server.requests]
    assert methods.count("thread/start") == 1
    assert methods.count("thread/resume") == 1
    assert first.tokens == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    assert second.tokens == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    assert second.rate_limits == {"requests_remaining": 98}


def test_codex_app_server_runtime_reuses_external_websocket_when_keep_alive(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    with _FakePersistentWebSocketAppServer() as server:
        runtime = CodexAppServerRuntime(
            {
                "mode": "external",
                "endpoint": server.endpoint,
                "approval_policy": "never",
                "keep_alive": True,
                "turn_timeout_ms": 5000,
                "read_timeout_ms": 1000,
                "stall_timeout_ms": 5000,
            },
            run=None,
        )
        try:
            first = runtime.run_prompt_result(
                worktree=tmp_path,
                session_name="ISSUE-1",
                prompt="First",
                model="gpt-5.5",
            )
            runtime.ensure_session(
                worktree=tmp_path,
                session_name="ISSUE-1",
                model="gpt-5.5",
                resume_session_id=first.thread_id,
            )
            second = runtime.run_prompt_result(
                worktree=tmp_path,
                session_name="ISSUE-1",
                prompt="Second",
                model="gpt-5.5",
            )
        finally:
            runtime.close()

    methods = [item.get("method") for item in server.requests]
    assert server.websocket_connections == 1
    assert methods.count("initialize") == 1
    assert methods.count("initialized") == 1
    assert methods.count("thread/start") == 1
    assert methods.count("thread/resume") == 1
    assert methods.count("turn/start") == 2
    assert first.output == "warm 1\n"
    assert first.turn_id == "turn-warm-1"
    assert second.output == "warm 2\n"
    assert second.turn_id == "turn-warm-2"


def test_codex_app_server_runtime_opens_fresh_external_websocket_when_keep_alive_false(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    with _FakePersistentWebSocketAppServer() as server:
        runtime = CodexAppServerRuntime(
            {
                "mode": "external",
                "endpoint": server.endpoint,
                "approval_policy": "never",
                "keep_alive": False,
                "turn_timeout_ms": 5000,
                "read_timeout_ms": 1000,
                "stall_timeout_ms": 5000,
            },
            run=None,
        )
        first = runtime.run_prompt_result(
            worktree=tmp_path,
            session_name="ISSUE-1",
            prompt="First",
            model="gpt-5.5",
        )
        second = runtime.run_prompt_result(
            worktree=tmp_path,
            session_name="ISSUE-2",
            prompt="Second",
            model="gpt-5.5",
        )

    methods = [item.get("method") for item in server.requests]
    assert server.websocket_connections == 2
    assert methods.count("initialize") == 2
    assert methods.count("thread/start") == 2
    assert first.output == "warm 1\n"
    assert second.output == "warm 1\n"


def test_codex_app_server_runtime_close_drops_warm_external_websocket(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    with _FakePersistentWebSocketAppServer() as server:
        runtime = CodexAppServerRuntime(
            {
                "mode": "external",
                "endpoint": server.endpoint,
                "approval_policy": "never",
                "keep_alive": True,
                "turn_timeout_ms": 5000,
                "read_timeout_ms": 1000,
                "stall_timeout_ms": 5000,
            },
            run=None,
        )
        first = runtime.run_prompt_result(
            worktree=tmp_path,
            session_name="ISSUE-1",
            prompt="First",
            model="gpt-5.5",
        )
        diagnostics = runtime.diagnostics()
        assert diagnostics["warm_client_present"] is True
        assert diagnostics["warm_client_open"] is True
        runtime.close()
        diagnostics = runtime.diagnostics()
        assert diagnostics["warm_client_present"] is False
        assert diagnostics["warm_client_open"] is False
        second = runtime.run_prompt_result(
            worktree=tmp_path,
            session_name="ISSUE-2",
            prompt="Second",
            model="gpt-5.5",
        )
        runtime.close()

    methods = [item.get("method") for item in server.requests]
    assert server.websocket_connections == 2
    assert methods.count("initialize") == 2
    assert first.output == "warm 1\n"
    assert second.output == "warm 1\n"


def test_codex_app_server_runtime_reconnects_when_warm_websocket_closes(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    with _FakeWebSocketAppServer() as server:
        runtime = CodexAppServerRuntime(
            {
                "mode": "external",
                "endpoint": server.endpoint,
                "approval_policy": "never",
                "keep_alive": True,
                "turn_timeout_ms": 5000,
                "read_timeout_ms": 1000,
                "stall_timeout_ms": 5000,
            },
            run=None,
        )
        try:
            first = runtime.run_prompt_result(
                worktree=tmp_path,
                session_name="ISSUE-1",
                prompt="First",
                model="gpt-5.5",
            )
            second = runtime.run_prompt_result(
                worktree=tmp_path,
                session_name="ISSUE-2",
                prompt="Second",
                model="gpt-5.5",
            )
        finally:
            runtime.close()

    methods = [item.get("method") for item in server.requests]
    assert server.websocket_connections == 2
    assert methods.count("initialize") == 2
    assert methods.count("turn/start") == 2
    assert first.output == "ws ok\n"
    assert second.output == "ws ok\n"


@pytest.mark.skipif(
    os.environ.get("DAEDALUS_REAL_CODEX_APP_SERVER") != "1",
    reason="set DAEDALUS_REAL_CODEX_APP_SERVER=1 to run the real Codex app-server smoke",
)
def test_codex_app_server_runtime_real_smoke_start_and_resume(tmp_path):
    from runtimes.codex_app_server import CodexAppServerRuntime

    if shutil.which("codex") is None:
        pytest.skip("codex CLI is not installed")

    worktree = tmp_path / "repo"
    worktree.mkdir()
    model = os.environ.get("DAEDALUS_REAL_CODEX_MODEL", "")
    runtime = CodexAppServerRuntime(
        {
            "command": "codex app-server",
            "approval_policy": "never",
            "thread_sandbox": "workspace-write",
            "turn_sandbox_policy": "workspace-write",
            "turn_timeout_ms": int(os.environ.get("DAEDALUS_REAL_CODEX_TURN_TIMEOUT_MS", "180000")),
            "read_timeout_ms": int(os.environ.get("DAEDALUS_REAL_CODEX_READ_TIMEOUT_MS", "5000")),
            "stall_timeout_ms": int(os.environ.get("DAEDALUS_REAL_CODEX_STALL_TIMEOUT_MS", "60000")),
            "ephemeral": False,
        },
        run=None,
    )

    first = runtime.run_prompt_result(
        worktree=worktree,
        session_name="REAL-CODEX-SMOKE",
        prompt="Reply with exactly this text: DAE-OK-1",
        model=model,
    )
    assert first.thread_id
    assert first.turn_id
    assert first.last_event == "turn/completed"
    assert first.output.strip()

    runtime.ensure_session(
        worktree=worktree,
        session_name="REAL-CODEX-SMOKE",
        model=model,
        resume_session_id=first.thread_id,
    )
    second = runtime.run_prompt_result(
        worktree=worktree,
        session_name="REAL-CODEX-SMOKE",
        prompt="Reply with exactly this text: DAE-OK-2",
        model=model,
    )

    assert second.thread_id == first.thread_id
    assert second.turn_id
    assert second.last_event == "turn/completed"
    assert second.output.strip()
