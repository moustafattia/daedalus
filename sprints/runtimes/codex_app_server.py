from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import queue
import shlex
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from . import PromptRunResult, SessionHandle, SessionHealth, register


class CodexAppServerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        result: PromptRunResult | None = None,
        stderr: str | None = None,
        returncode: int | None = None,
    ):
        super().__init__(message)
        self.result = result
        self.stderr = stderr
        self.returncode = returncode


@dataclass
class _RunState:
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    last_event: str | None = None
    last_message: str | None = None
    turn_count: int = 0
    tokens: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
    )
    rate_limits: dict | None = None
    output_parts: list[str] = field(default_factory=list)


class _AppServerClient:
    _EOF = object()

    def __init__(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        on_activity,
    ):
        self._next_request_id = 1
        self._messages: queue.Queue[dict[str, Any] | object] = queue.Queue()
        self._stderr_lines: list[str] = []
        self._on_activity = on_activity
        self._proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    @property
    def returncode(self) -> int | None:
        return self._proc.poll()

    @property
    def stderr_text(self) -> str:
        return "".join(self._stderr_lines)

    def _read_stdout(self) -> None:
        try:
            assert self._proc.stdout is not None
            for raw_line in self._proc.stdout:
                self._on_activity()
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    self._messages.put(
                        {
                            "method": "protocol/error",
                            "params": {"message": f"non-JSON stdout: {line}"},
                        }
                    )
                    continue
                if isinstance(payload, dict):
                    self._messages.put(payload)
                else:
                    self._messages.put(
                        {
                            "method": "protocol/error",
                            "params": {"message": f"non-object stdout: {line}"},
                        }
                    )
        finally:
            self._messages.put(self._EOF)

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            self._on_activity()
            self._stderr_lines.append(line)

    def send_request(self, method: str, params: dict[str, Any] | None = None) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        payload = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        self._write(payload)
        return request_id

    def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        self._write({"method": method, "params": params or {}})

    def send_response(self, request_id: Any, result: dict[str, Any]) -> None:
        self._write({"id": request_id, "result": result})

    def send_error_response(self, request_id: Any, message: str) -> None:
        self._write({"id": request_id, "error": {"code": -32601, "message": message}})

    def request(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        timeout_s: float,
        on_message,
    ) -> dict[str, Any]:
        request_id = self.send_request(method, params)
        deadline = time.monotonic() + timeout_s
        while True:
            message = self.next_message(deadline=deadline)
            if self._is_server_request(message):
                on_message(message)
                self._reject_server_request(message)
                continue
            if message.get("id") == request_id and (
                "result" in message or "error" in message
            ):
                if "error" in message:
                    raise CodexAppServerError(
                        self._jsonrpc_error_message(
                            method=method, error=message.get("error")
                        )
                    )
                result = message.get("result")
                if isinstance(result, dict):
                    return result
                return {}
            on_message(message)

    def next_message(self, *, deadline: float) -> dict[str, Any]:
        message = self.poll_message(deadline=deadline)
        if message is None:
            raise CodexAppServerError(
                "codex-app-server protocol response timed out",
                stderr=self.stderr_text,
                returncode=self.returncode,
            )
        return message

    def poll_message(self, *, deadline: float) -> dict[str, Any] | None:
        timeout = max(deadline - time.monotonic(), 0)
        if timeout <= 0:
            return None
        try:
            message = self._messages.get(timeout=timeout)
        except queue.Empty:
            return None
        if message is self._EOF:
            raise CodexAppServerError(
                "codex-app-server exited before completing the turn",
                stderr=self.stderr_text,
                returncode=self.returncode,
            )
        assert isinstance(message, dict)
        return message

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)

    def _write(self, payload: dict[str, Any]) -> None:
        if self._proc.poll() is not None:
            raise CodexAppServerError(
                "codex-app-server exited before accepting protocol input",
                stderr=self.stderr_text,
                returncode=self.returncode,
            )
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self._proc.stdin.flush()
        self._on_activity()

    def _is_server_request(self, message: dict[str, Any]) -> bool:
        return (
            "id" in message
            and "method" in message
            and "result" not in message
            and "error" not in message
        )

    def _reject_server_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            self.send_response(request_id, {"decision": "cancel"})
            return
        self.send_error_response(
            request_id, f"Sprints does not handle app-server request {method!r}"
        )

    def _jsonrpc_error_message(self, *, method: str, error: Any) -> str:
        if isinstance(error, dict):
            detail = error.get("message") or json.dumps(error, sort_keys=True)
        else:
            detail = str(error)
        return f"codex-app-server request {method!r} failed: {detail}"


class _WebSocketAppServerClient(_AppServerClient):
    _WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(
        self,
        *,
        endpoint: str,
        auth_token: str | None,
        timeout_s: float,
        on_activity,
    ):
        self._next_request_id = 1
        self._messages: queue.Queue[dict[str, Any] | object] = queue.Queue()
        self._stderr_lines: list[str] = []
        self._on_activity = on_activity
        self._closed = False
        self._write_lock = threading.Lock()
        self._socket = self._connect(
            endpoint=endpoint, auth_token=auth_token, timeout_s=timeout_s
        )
        self._reader_thread = threading.Thread(target=self._read_websocket, daemon=True)
        self._reader_thread.start()

    @property
    def returncode(self) -> int | None:
        return None if not self._closed else 0

    def close(self) -> None:
        self._closed = True
        try:
            self._send_frame(opcode=0x8, payload=b"")
        except OSError:
            pass
        try:
            self._socket.close()
        except OSError:
            pass

    def _write(self, payload: dict[str, Any]) -> None:
        if self._closed:
            raise CodexAppServerError(
                "codex-app-server websocket is closed", stderr=self.stderr_text
            )
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            self._send_frame(opcode=0x1, payload=data)
        except OSError as exc:
            self._closed = True
            raise CodexAppServerError(
                f"codex-app-server websocket write failed: {exc}",
                stderr=self.stderr_text,
            ) from exc
        self._on_activity()

    def _connect(
        self, *, endpoint: str, auth_token: str | None, timeout_s: float
    ) -> socket.socket:
        parsed = urlparse(endpoint)
        if parsed.scheme != "ws":
            raise CodexAppServerError(
                f"external codex-app-server endpoint must use ws://, got {endpoint!r}"
            )
        if not parsed.hostname or not parsed.port:
            raise CodexAppServerError(
                f"external codex-app-server endpoint requires host and port: {endpoint!r}"
            )
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        sock = socket.create_connection(
            (parsed.hostname, parsed.port), timeout=timeout_s
        )
        sock.settimeout(timeout_s)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        host = parsed.netloc
        headers = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if auth_token:
            headers.append(f"Authorization: Bearer {auth_token}")
        request = "\r\n".join(headers) + "\r\n\r\n"
        sock.sendall(request.encode("ascii"))
        response = self._read_http_response(sock)
        status_line = response.split("\r\n", 1)[0]
        if " 101 " not in status_line:
            raise CodexAppServerError(
                f"codex-app-server websocket handshake failed: {status_line}"
            )
        accept = base64.b64encode(
            hashlib.sha1((key + self._WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        response_headers = self._parse_http_headers(response)
        if response_headers.get("sec-websocket-accept", "") != accept:
            raise CodexAppServerError(
                "codex-app-server websocket handshake returned an invalid accept key"
            )
        # Keep the handshake bounded, then let poll_message, turn_timeout, and
        # stall_timeout control idle reads during long-running turns.
        sock.settimeout(None)
        return sock

    def _read_http_response(self, sock: socket.socket) -> str:
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                raise CodexAppServerError(
                    "codex-app-server websocket closed during handshake"
                )
            chunks.append(chunk)
            combined = b"".join(chunks)
            if b"\r\n\r\n" in combined:
                return combined.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")

    def _parse_http_headers(self, response: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        for line in response.split("\r\n")[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        return headers

    def _read_websocket(self) -> None:
        text_parts: list[bytes] = []
        try:
            while not self._closed:
                opcode, payload, fin = self._read_frame()
                self._on_activity()
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    self._send_frame(opcode=0xA, payload=payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode == 0x1:
                    text_parts = [payload]
                elif opcode == 0x0 and text_parts:
                    text_parts.append(payload)
                else:
                    continue
                if not fin:
                    continue
                text = b"".join(text_parts).decode("utf-8")
                text_parts = []
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    self._messages.put(
                        {
                            "method": "protocol/error",
                            "params": {"message": f"non-JSON websocket frame: {text}"},
                        }
                    )
                    continue
                if isinstance(message, dict):
                    self._messages.put(message)
                else:
                    self._messages.put(
                        {
                            "method": "protocol/error",
                            "params": {
                                "message": f"non-object websocket frame: {text}"
                            },
                        }
                    )
        except OSError as exc:
            if not self._closed:
                self._messages.put(
                    {"method": "protocol/error", "params": {"message": str(exc)}}
                )
        finally:
            self._closed = True
            self._messages.put(self._EOF)

    def _read_frame(self) -> tuple[int, bytes, bool]:
        header = self._recv_exact(2)
        first, second = header[0], header[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = int.from_bytes(self._recv_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._recv_exact(8), "big")
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(
                byte ^ mask[index % 4] for index, byte in enumerate(payload)
            )
        return opcode, payload, fin

    def _send_frame(self, *, opcode: int, payload: bytes) -> None:
        if len(payload) > 0x7FFFFFFFFFFFFFFF:
            raise CodexAppServerError("websocket payload too large")
        first = 0x80 | opcode
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes([first, 0x80 | length])
        elif length <= 0xFFFF:
            header = bytes([first, 0x80 | 126]) + length.to_bytes(2, "big")
        else:
            header = bytes([first, 0x80 | 127]) + length.to_bytes(8, "big")
        masked_payload = bytes(
            byte ^ mask[index % 4] for index, byte in enumerate(payload)
        )
        with self._write_lock:
            self._socket.sendall(header + mask + masked_payload)

    def _recv_exact(self, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self._socket.recv(remaining)
            if not chunk:
                raise OSError("codex-app-server websocket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


@register("codex-app-server")
class CodexAppServerRuntime:
    def __init__(self, cfg: dict, *, run, run_json=None):
        del run, run_json
        self._cfg = cfg
        self._command = cfg.get("command") or "codex app-server"
        self._mode = str(
            cfg.get("mode") or ("external" if cfg.get("endpoint") else "managed")
        ).strip()
        self._endpoint = str(cfg.get("endpoint") or "").strip() or None
        self._healthcheck_path = (
            str(cfg.get("healthcheck_path") or "/readyz").strip() or "/readyz"
        )
        self._ws_token = str(cfg.get("ws_token") or "").strip() or None
        self._ws_token_env = str(cfg.get("ws_token_env") or "").strip() or None
        self._ws_token_file = str(cfg.get("ws_token_file") or "").strip() or None
        self._ephemeral = self._bool_config(cfg.get("ephemeral"), default=False)
        self._turn_timeout_ms = int(cfg.get("turn_timeout_ms") or 3600000)
        self._read_timeout_ms = int(cfg.get("read_timeout_ms") or 5000)
        self._stall_timeout_ms = int(cfg.get("stall_timeout_ms") or 300000)
        self._approval_policy = cfg.get("approval_policy")
        self._thread_sandbox = str(cfg.get("thread_sandbox") or "").strip() or None
        self._turn_sandbox_policy = cfg.get("turn_sandbox_policy")
        self._keep_alive = self._bool_config(
            cfg.get("keep_alive", cfg.get("keep-alive")),
            default=(self._mode == "external"),
        )
        if self._keep_alive and self._mode != "external":
            raise CodexAppServerError(
                "codex-app-server keep_alive requires mode: external"
            )
        self._last_activity: float | None = None
        self._last_result: PromptRunResult | None = None
        self._resume_thread_ids: dict[str, str] = {}
        self._cancel_event: threading.Event | None = None
        self._progress_callback: Callable[[PromptRunResult], None] | None = None
        self._client_lock = threading.Lock()
        self._warm_client: _AppServerClient | None = None

    def _record_activity(self) -> None:
        self._last_activity = time.monotonic()

    def last_activity_ts(self) -> float | None:
        return self._last_activity

    def last_result(self) -> PromptRunResult | None:
        return self._last_result

    def set_cancel_event(self, event: threading.Event | None) -> None:
        self._cancel_event = event

    def set_progress_callback(
        self, callback: Callable[[PromptRunResult], None] | None
    ) -> None:
        self._progress_callback = callback

    def interrupt_turn(
        self,
        *,
        thread_id: str,
        turn_id: str,
        worktree: Path | None = None,
    ) -> bool:
        thread_id = str(thread_id or "").strip()
        turn_id = str(turn_id or "").strip()
        if not thread_id or not turn_id:
            return False
        cwd = worktree or Path.cwd()
        client = self._build_client(worktree=cwd, env=os.environ.copy())
        state = _RunState(session_id=thread_id, thread_id=thread_id, turn_id=turn_id)
        try:
            self._initialize(client=client, state=state)
            self._interrupt_turn(client=client, state=state)
            return True
        finally:
            client.close()

    def close(self) -> None:
        with self._client_lock:
            self._drop_warm_client()

    def diagnostics(self) -> dict[str, Any]:
        with self._client_lock:
            warm_client_present = self._warm_client is not None
            warm_client_open = warm_client_present and not self._client_is_closed(
                self._warm_client
            )
        return {
            "kind": "codex-app-server",
            "mode": self._mode,
            "transport": "websocket" if self._mode == "external" else "stdio",
            "endpoint": self._endpoint,
            "keep_alive": self._keep_alive,
            "warm_client_present": warm_client_present,
            "warm_client_open": warm_client_open,
        }

    def ensure_session(
        self,
        *,
        worktree: Path,
        session_name: str,
        model: str,
        resume_session_id: str | None = None,
    ) -> SessionHandle:
        del model
        key = self._session_key(worktree=worktree, session_name=session_name)
        if resume_session_id:
            self._resume_thread_ids[key] = resume_session_id
        else:
            self._resume_thread_ids.pop(key, None)
        return SessionHandle(record_id=None, session_id=None, name=session_name)

    def run_prompt(
        self,
        *,
        worktree: Path,
        session_name: str,
        prompt: str,
        model: str,
    ) -> str:
        return self.run_prompt_result(
            worktree=worktree,
            session_name=session_name,
            prompt=prompt,
            model=model,
        ).output

    def run_prompt_result(
        self,
        *,
        worktree: Path,
        session_name: str,
        prompt: str,
        model: str,
    ) -> PromptRunResult:
        env = {
            "SPRINTS_MODEL": model,
            "SPRINTS_SESSION_NAME": session_name,
        }
        approval_policy = self._approval_policy_value()
        if approval_policy:
            env["SPRINTS_APPROVAL_POLICY"] = (
                json.dumps(approval_policy)
                if isinstance(approval_policy, dict)
                else str(approval_policy)
            )
        if self._thread_sandbox:
            env["SPRINTS_THREAD_SANDBOX"] = self._thread_sandbox
        if self._turn_sandbox_policy:
            env["SPRINTS_TURN_SANDBOX_POLICY"] = (
                json.dumps(self._turn_sandbox_policy)
                if isinstance(self._turn_sandbox_policy, dict)
                else str(self._turn_sandbox_policy)
            )

        state = _RunState()
        client: _AppServerClient | None = None
        keep_client = False
        try:
            self._record_activity()
            if self._keep_alive:
                with self._client_lock:
                    result: PromptRunResult | None = None
                    for attempt in range(2):
                        state = _RunState()
                        client = self._warm_client_for_run(
                            worktree=worktree, env={**os.environ, **env}, state=state
                        )
                        keep_client = True
                        try:
                            result = self._run_prompt_result_on_client(
                                client=client,
                                state=state,
                                worktree=worktree,
                                session_name=session_name,
                                prompt=prompt,
                                model=model,
                            )
                            break
                        except CodexAppServerError:
                            if attempt == 0 and self._client_is_closed(client):
                                self._drop_warm_client()
                                client = None
                                continue
                            raise
                    if result is None:
                        raise CodexAppServerError(
                            "codex-app-server did not return a result"
                        )
            else:
                client = self._build_client(
                    worktree=worktree, env={**os.environ, **env}
                )
                self._initialize(client=client, state=state)
                result = self._run_prompt_result_on_client(
                    client=client,
                    state=state,
                    worktree=worktree,
                    session_name=session_name,
                    prompt=prompt,
                    model=model,
                )
            self._last_result = result
            return result
        except CodexAppServerError as exc:
            result = exc.result or self._result_from_state(state)
            self._last_result = result
            exc.result = result
            if exc.stderr is None and client is not None:
                exc.stderr = client.stderr_text
            if exc.returncode is None and client is not None:
                exc.returncode = client.returncode
            if keep_client and client is not None and self._client_is_closed(client):
                self._drop_warm_client()
            raise
        finally:
            if client is not None and not keep_client:
                client.close()

    def _run_prompt_result_on_client(
        self,
        *,
        client: _AppServerClient,
        state: _RunState,
        worktree: Path,
        session_name: str,
        prompt: str,
        model: str,
    ) -> PromptRunResult:
        resume_thread_id = self._resume_thread_id(
            worktree=worktree, session_name=session_name
        )
        if resume_thread_id:
            state.thread_id = resume_thread_id
            state.session_id = resume_thread_id
            thread_result = client.request(
                "thread/resume",
                self._thread_resume_params(
                    thread_id=resume_thread_id, worktree=worktree, model=model
                ),
                timeout_s=self._read_timeout_s(),
                on_message=lambda message: self._consume_message(message, state=state),
            )
        else:
            thread_result = client.request(
                "thread/start",
                self._thread_start_params(worktree=worktree, model=model),
                timeout_s=self._read_timeout_s(),
                on_message=lambda message: self._consume_message(message, state=state),
            )
        self._consume_thread_start_response(thread_result, state=state)
        self._notify_progress(state)
        turn_result = client.request(
            "turn/start",
            self._turn_start_params(
                worktree=worktree, thread_id=state.thread_id, prompt=prompt, model=model
            ),
            timeout_s=self._read_timeout_s(),
            on_message=lambda message: self._consume_message(message, state=state),
        )
        self._consume_turn_response(turn_result, state=state)
        self._notify_progress(state)
        return self._read_turn_to_completion(client=client, state=state)

    def _warm_client_for_run(
        self, *, worktree: Path, env: dict[str, str], state: _RunState
    ) -> _AppServerClient:
        if self._warm_client is not None and self._client_is_closed(self._warm_client):
            self._drop_warm_client()
        if self._warm_client is None:
            self._warm_client = self._build_client(worktree=worktree, env=env)
            try:
                self._initialize(client=self._warm_client, state=state)
            except Exception:
                self._drop_warm_client()
                raise
        return self._warm_client

    def _client_is_closed(self, client: _AppServerClient) -> bool:
        return client.returncode is not None

    def _drop_warm_client(self) -> None:
        client = self._warm_client
        self._warm_client = None
        if client is not None:
            client.close()

    def _command_argv(self) -> list[str]:
        if isinstance(self._command, list):
            argv = [str(part) for part in self._command if str(part).strip()]
        else:
            argv = shlex.split(str(self._command).strip())
        if not argv:
            raise RuntimeError("codex-app-server runtime requires a non-empty command")
        return argv

    def _build_client(self, *, worktree: Path, env: dict[str, str]) -> _AppServerClient:
        if self._mode == "managed":
            return _AppServerClient(
                argv=self._command_argv(),
                cwd=worktree,
                env=env,
                on_activity=self._record_activity,
            )
        if self._mode == "external":
            if not self._endpoint:
                raise CodexAppServerError(
                    "external codex-app-server runtime requires endpoint: ws://HOST:PORT"
                )
            self._check_external_ready()
            return _WebSocketAppServerClient(
                endpoint=self._endpoint,
                auth_token=self._resolve_ws_token(),
                timeout_s=self._read_timeout_s(),
                on_activity=self._record_activity,
            )
        raise CodexAppServerError(
            "codex-app-server mode must be one of ['managed', 'external']"
        )

    def _session_key(self, *, worktree: Path, session_name: str) -> str:
        return f"{worktree.expanduser().resolve(strict=False)}::{session_name}"

    def _resume_thread_id(self, *, worktree: Path, session_name: str) -> str | None:
        return self._resume_thread_ids.get(
            self._session_key(worktree=worktree, session_name=session_name)
        )

    def _resolve_ws_token(self) -> str | None:
        if self._ws_token:
            return self._ws_token
        if self._ws_token_env:
            return os.environ.get(self._ws_token_env, "").strip() or None
        if self._ws_token_file:
            return (
                Path(self._ws_token_file)
                .expanduser()
                .read_text(encoding="utf-8")
                .strip()
                or None
            )
        return None

    def _check_external_ready(self) -> None:
        if not self._endpoint:
            return
        ok, reason = self._external_healthcheck()
        if not ok:
            raise CodexAppServerError(
                f"external codex-app-server is not ready: {reason}"
            )

    def _external_healthcheck(self) -> tuple[bool, str | None]:
        if not self._endpoint:
            return False, "missing endpoint"
        parsed = urlparse(self._endpoint)
        if parsed.scheme != "ws":
            return False, f"unsupported endpoint scheme {parsed.scheme!r}"
        if not parsed.hostname or not parsed.port:
            return False, "endpoint requires host and port"
        connection = http.client.HTTPConnection(
            parsed.hostname, parsed.port, timeout=self._read_timeout_s()
        )
        try:
            connection.request("GET", self._healthcheck_path)
            response = connection.getresponse()
            response.read()
        except OSError as exc:
            return False, str(exc)
        finally:
            connection.close()
        if response.status == 200:
            return True, None
        return False, f"GET {self._healthcheck_path} returned HTTP {response.status}"

    def _initialize(self, *, client: _AppServerClient, state: _RunState) -> None:
        client.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "sprints",
                    "title": "Sprints",
                    "version": "0.1.0",
                },
            },
            timeout_s=self._read_timeout_s(),
            on_message=lambda message: self._consume_message(message, state=state),
        )
        client.send_notification("initialized", {})

    def _thread_start_params(self, *, worktree: Path, model: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "cwd": str(worktree),
            "ephemeral": self._ephemeral,
            "serviceName": "sprints",
        }
        approval_policy = self._approval_policy_value()
        if approval_policy is not None:
            params["approvalPolicy"] = approval_policy
        if self._thread_sandbox:
            params["sandbox"] = self._thread_sandbox
        if model:
            params["model"] = model
        return params

    def _thread_resume_params(
        self, *, thread_id: str, worktree: Path, model: str
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": str(worktree),
            "serviceName": "sprints",
        }
        approval_policy = self._approval_policy_value()
        if approval_policy is not None:
            params["approvalPolicy"] = approval_policy
        if self._thread_sandbox:
            params["sandbox"] = self._thread_sandbox
        if model:
            params["model"] = model
        return params

    def _turn_start_params(
        self, *, worktree: Path, thread_id: str | None, prompt: str, model: str
    ) -> dict[str, Any]:
        if not thread_id:
            raise CodexAppServerError("codex-app-server did not return a thread id")
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": str(worktree),
            "input": [{"type": "text", "text": prompt}],
        }
        approval_policy = self._approval_policy_value()
        if approval_policy is not None:
            params["approvalPolicy"] = approval_policy
        sandbox_policy = self._sandbox_policy(worktree=worktree)
        if sandbox_policy is not None:
            params["sandboxPolicy"] = sandbox_policy
        if model:
            params["model"] = model
        return params

    def _approval_policy_value(self) -> str | dict | None:
        if self._approval_policy in (None, ""):
            return None
        if isinstance(self._approval_policy, dict):
            return self._approval_policy
        value = str(self._approval_policy).strip()
        allowed = {"untrusted", "on-failure", "on-request", "never"}
        if value not in allowed:
            raise CodexAppServerError(
                f"codex-app-server approval_policy must be one of {sorted(allowed)}, got {value!r}"
            )
        return value

    def _bool_config(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        raise CodexAppServerError(f"expected boolean config value, got {value!r}")

    def _sandbox_policy(self, *, worktree: Path) -> dict[str, Any] | None:
        raw = self._turn_sandbox_policy
        if raw in (None, "", "auto"):
            return None
        if isinstance(raw, dict):
            return raw
        value = str(raw).strip()
        if value == "danger-full-access":
            return {"type": "dangerFullAccess"}
        if value == "read-only":
            return {"type": "readOnly"}
        if value == "workspace-write":
            return {"type": "workspaceWrite", "writableRoots": [str(worktree)]}
        raise CodexAppServerError(
            "codex-app-server turn_sandbox_policy must be one of "
            "['read-only', 'workspace-write', 'danger-full-access'] or a SandboxPolicy object"
        )

    def _consume_thread_start_response(
        self, payload: dict[str, Any], *, state: _RunState
    ) -> None:
        thread = payload.get("thread")
        if isinstance(thread, dict):
            state.thread_id = (
                str(thread.get("id") or state.thread_id or "") or state.thread_id
            )
        state.thread_id = (
            str(
                payload.get("threadId")
                or payload.get("thread_id")
                or state.thread_id
                or ""
            )
            or state.thread_id
        )
        state.session_id = state.thread_id

    def _consume_turn_response(
        self, payload: dict[str, Any], *, state: _RunState
    ) -> None:
        turn = payload.get("turn")
        if isinstance(turn, dict):
            state.turn_id = str(turn.get("id") or state.turn_id or "") or state.turn_id
            self._record_turn_failure_if_present(turn, state=state)
        state.turn_id = (
            str(payload.get("turnId") or payload.get("turn_id") or state.turn_id or "")
            or state.turn_id
        )

    def _read_turn_to_completion(
        self, *, client: _AppServerClient, state: _RunState
    ) -> PromptRunResult:
        deadline = time.monotonic() + max(self._turn_timeout_ms / 1000, 1)
        stall_timeout = (
            self._stall_timeout_ms / 1000 if self._stall_timeout_ms > 0 else None
        )
        while True:
            now = time.monotonic()
            if self._cancel_event is not None and self._cancel_event.is_set():
                self._interrupt_turn(client=client, state=state)
                result = self._result_from_state(state)
                raise CodexAppServerError(
                    "codex-app-server turn canceled",
                    result=result,
                    stderr=client.stderr_text,
                    returncode=client.returncode,
                )
            if now >= deadline:
                self._interrupt_turn(client=client, state=state)
                result = self._result_from_state(state)
                raise CodexAppServerError(
                    "codex-app-server turn timed out",
                    result=result,
                    stderr=client.stderr_text,
                    returncode=client.returncode,
                )
            if (
                stall_timeout is not None
                and self._last_activity is not None
                and now - self._last_activity >= stall_timeout
            ):
                self._interrupt_turn(client=client, state=state)
                result = self._result_from_state(state)
                raise CodexAppServerError(
                    "codex-app-server turn stalled",
                    result=result,
                    stderr=client.stderr_text,
                    returncode=client.returncode,
                )

            read_deadline = min(deadline, now + self._read_timeout_s())
            if stall_timeout is not None and self._last_activity is not None:
                read_deadline = min(read_deadline, self._last_activity + stall_timeout)
            message = client.poll_message(deadline=read_deadline)
            if message is None:
                continue
            if client._is_server_request(message):
                self._consume_message(message, state=state)
                client._reject_server_request(message)
                continue
            completed = self._consume_message(message, state=state)
            if completed:
                return self._result_from_state(state)

    def _interrupt_turn(self, *, client: _AppServerClient, state: _RunState) -> None:
        if not state.thread_id or not state.turn_id:
            return
        try:
            client.request(
                "turn/interrupt",
                {"threadId": state.thread_id, "turnId": state.turn_id},
                timeout_s=self._read_timeout_s(),
                on_message=lambda message: self._consume_message(message, state=state),
            )
        except CodexAppServerError:
            return

    def _notify_progress(self, state: _RunState) -> None:
        if self._progress_callback is None:
            return
        try:
            self._progress_callback(self._result_from_state(state))
        except Exception:
            return

    def _consume_message(self, message: dict[str, Any], *, state: _RunState) -> bool:
        method = str(message.get("method") or "").strip()
        if not method:
            return False
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}
        if not self._message_matches_active_run(method, params, state=state):
            return False
        state.last_event = method

        if method in {"protocol/error", "error"}:
            detail = params.get("error")
            if isinstance(detail, dict):
                message_text = str(
                    detail.get("message") or json.dumps(detail, sort_keys=True)
                )
            else:
                message_text = str(
                    params.get("message") or detail or "codex-app-server turn failed"
                )
            state.last_message = message_text
            if method == "error" and params.get("willRetry") is True:
                return False
            raise CodexAppServerError(
                f"codex-app-server failed: {message_text}",
                result=self._result_from_state(state),
            )

        if method == "thread/started":
            self._consume_thread_start_response(params, state=state)
        elif method == "turn/started":
            state.turn_count += 1
            self._consume_turn_response(params, state=state)
        elif method in {"agent/message_delta", "item/agentMessage/delta"}:
            delta = params.get("delta")
            if isinstance(delta, str):
                state.output_parts.append(delta)
                state.last_message = delta
        elif method in {
            "reasoning/text_delta",
            "reasoning/summary_text_delta",
            "plan/delta",
            "item/reasoning/textDelta",
            "item/reasoning/summaryTextDelta",
            "item/plan/delta",
        }:
            delta = params.get("delta")
            if isinstance(delta, str):
                state.last_message = delta
        elif method == "thread/tokenUsage/updated":
            state.thread_id = (
                str(params.get("threadId") or state.thread_id or "") or state.thread_id
            )
            state.turn_id = (
                str(params.get("turnId") or state.turn_id or "") or state.turn_id
            )
            usage = params.get("tokenUsage")
            if isinstance(usage, dict):
                state.tokens = self._coerce_usage(usage, current=state.tokens)
        elif method == "account/rateLimits/updated":
            rate_limits = params.get("rateLimits")
            if isinstance(rate_limits, dict):
                state.rate_limits = rate_limits
        elif method == "turn/completed":
            self._consume_turn_response(params, state=state)
            turn = params.get("turn")
            if isinstance(turn, dict):
                failure = self._turn_failure_message(turn)
                if failure:
                    state.last_message = failure
                    raise CodexAppServerError(
                        f"codex-app-server failed: {failure}",
                        result=self._result_from_state(state),
                    )
            self._notify_progress(state)
            return True
        elif self._is_request_notification(method):
            pass
        self._notify_progress(state)
        return False

    def _message_matches_active_run(
        self, method: str, params: dict[str, Any], *, state: _RunState
    ) -> bool:
        thread_id = self._message_thread_id(params)
        turn_id = self._message_turn_id(params)

        # Shared app-server endpoints can emit unscoped errors for other turns.
        # Request/response errors are still handled by _AppServerClient.request.
        if (
            method == "error"
            and not thread_id
            and not turn_id
            and state.thread_id
            and state.turn_id
        ):
            return False
        if thread_id and state.thread_id and thread_id != state.thread_id:
            return False
        if turn_id and state.turn_id and turn_id != state.turn_id:
            return False
        if thread_id and state.thread_id is None:
            return method == "thread/started"
        if turn_id and state.turn_id is None:
            return method == "turn/started"
        return True

    def _message_thread_id(self, params: dict[str, Any]) -> str | None:
        return self._first_message_id(
            params,
            direct_keys=("threadId", "thread_id"),
            id_object_key="thread",
            nested_keys=("item",),
        )

    def _message_turn_id(self, params: dict[str, Any]) -> str | None:
        return self._first_message_id(
            params,
            direct_keys=("turnId", "turn_id"),
            id_object_key="turn",
            nested_keys=("item",),
        )

    def _first_message_id(
        self,
        params: dict[str, Any],
        *,
        direct_keys: tuple[str, ...],
        id_object_key: str,
        nested_keys: tuple[str, ...],
    ) -> str | None:
        for key in direct_keys:
            value = params.get(key)
            if value not in (None, ""):
                return str(value)
        nested = params.get(id_object_key)
        if isinstance(nested, dict):
            value = nested.get("id")
            if value not in (None, ""):
                return str(value)
        for key in nested_keys:
            nested = params.get(key)
            if not isinstance(nested, dict):
                continue
            for direct_key in direct_keys:
                value = nested.get(direct_key)
                if value not in (None, ""):
                    return str(value)
        return None

    def _is_request_notification(self, method: str) -> bool:
        return method.startswith("item/") or method.startswith("mcpServer/")

    def _record_turn_failure_if_present(
        self, turn: dict[str, Any], *, state: _RunState
    ) -> None:
        failure = self._turn_failure_message(turn)
        if failure:
            state.last_message = failure

    def _turn_failure_message(self, turn: dict[str, Any]) -> str | None:
        error = turn.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        status = str(turn.get("status") or "").strip().lower()
        if status in {"failed", "interrupted", "cancelled", "canceled"}:
            return f"turn status {status}"
        return None

    def _result_from_state(self, state: _RunState) -> PromptRunResult:
        output = "".join(state.output_parts).strip()
        if output:
            output += "\n"
        return PromptRunResult(
            output=output,
            session_id=state.session_id or state.thread_id,
            thread_id=state.thread_id,
            turn_id=state.turn_id,
            last_event=state.last_event,
            last_message=state.last_message,
            turn_count=state.turn_count,
            tokens=state.tokens,
            rate_limits=state.rate_limits,
        )

    def _read_timeout_s(self) -> float:
        return max(self._read_timeout_ms / 1000, 1)

    def _failure_detail(
        self, *, result: PromptRunResult, stderr: str, returncode: int
    ) -> str:
        if result.last_message:
            return f"codex-app-server failed: {result.last_message}"
        stderr_text = stderr.strip()
        if stderr_text:
            return f"codex-app-server failed: {stderr_text}"
        if result.last_event:
            return f"codex-app-server failed during event {result.last_event!r}"
        return f"codex-app-server exited with code {returncode}"

    def _coerce_usage(
        self, payload: dict[str, Any], *, current: dict[str, int]
    ) -> dict[str, int]:
        if isinstance(payload.get("last"), dict):
            payload = payload["last"]
        elif isinstance(payload.get("total"), dict):
            payload = payload["total"]

        input_tokens = payload.get("input_tokens")
        if input_tokens is None:
            input_tokens = payload.get("inputTokens")
        if input_tokens is None:
            input_tokens = payload.get("prompt_tokens")
        if input_tokens is None:
            input_tokens = payload.get("promptTokens")

        output_tokens = payload.get("output_tokens")
        if output_tokens is None:
            output_tokens = payload.get("outputTokens")
        if output_tokens is None:
            output_tokens = payload.get("completion_tokens")
        if output_tokens is None:
            output_tokens = payload.get("completionTokens")

        total_tokens = payload.get("total_tokens")
        if total_tokens is None:
            total_tokens = payload.get("totalTokens")

        next_usage = dict(current)
        if input_tokens is not None:
            next_usage["input_tokens"] = int(input_tokens)
        if output_tokens is not None:
            next_usage["output_tokens"] = int(output_tokens)
        if total_tokens is not None:
            next_usage["total_tokens"] = int(total_tokens)
        else:
            next_usage["total_tokens"] = int(next_usage["input_tokens"]) + int(
                next_usage["output_tokens"]
            )
        return next_usage

    def assess_health(
        self,
        session_meta: dict | None,
        *,
        worktree: Path | None,
        now_epoch: int | None = None,
    ) -> SessionHealth:
        del session_meta, worktree, now_epoch
        if self._mode == "external":
            ok, reason = self._external_healthcheck()
            return SessionHealth(healthy=ok, reason=reason, last_used_at=None)
        return SessionHealth(healthy=True, reason=None, last_used_at=None)

    def close_session(self, *, worktree: Path, session_name: str) -> None:
        del worktree, session_name
        return None

    def run_command(
        self,
        *,
        worktree: Path,
        command_argv: list[str],
        env: dict | None = None,
    ) -> str:
        completed = subprocess.run(
            command_argv,
            cwd=str(worktree),
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        self._record_activity()
        return completed.stdout or ""
