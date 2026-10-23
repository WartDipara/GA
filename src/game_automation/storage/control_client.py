"""Trusted local client; Python never opens a writable business database."""

import json
import os
import select
import shutil
import socket
import subprocess
import threading
import time

from game_automation.common import FileLock, FrameworkError, digest, uid
from game_automation.errors import ErrorCode

MAX_MESSAGE = 8 * 1024 * 1024


def verify_build(root):
    try:
        proof = json.loads((root / "var/cache/typescript/build.json").read_text(encoding="utf-8"))
        files = proof["files"]
        if not isinstance(files, dict) or "var/cache/typescript/control_plane.mjs" not in files:
            raise ValueError
        for relative, expected in files.items():
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or digest(path.read_bytes()) != expected:
                raise ValueError
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FrameworkError(ErrorCode.TYPESCRIPT_BUILD_REQUIRED) from exc


class ControlClient:
    def __init__(self, root):
        self.root = root
        self.socket = None
        self.lock = threading.Lock()
        self.failure = None
        self.closed = False
        self.fingerprint = digest((root / "var/cache/typescript/control_plane.mjs").read_bytes())

    def connect(self):
        if self.closed:
            raise FrameworkError(ErrorCode.CLOSED)
        endpoint_path = self.root / "var/cache/control-plane/endpoint.json"
        endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
        port = endpoint["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            raise FrameworkError(ErrorCode.INVALID_PROTOCOL)
        hello = {"token": endpoint["token"], "fingerprint": self.fingerprint}
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=10)
        if self.closed:
            self.close()
            raise FrameworkError(ErrorCode.CLOSED)
        try:
            response = self.request("hello", hello)
            if not isinstance(response, dict) or response.get("fingerprint") != self.fingerprint:
                raise FrameworkError(ErrorCode.RUNTIME_CHANGED)
        except BaseException:
            self.close()
            raise
        return self

    @classmethod
    def start(cls, root):
        """Caller holds layout.lock. Never retry a submitted business request."""
        verify_build(root)
        if os.name != "nt":
            raise FrameworkError(ErrorCode.UNSUPPORTED_PLATFORM)
        try:
            return cls(root).connect()
        except (OSError, ValueError, KeyError):
            pass
        executable = shutil.which("node")
        if not executable:
            raise FrameworkError(ErrorCode.NODE_UNAVAILABLE)
        log_path = root / "var/logs/development.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [executable, str(root / "var/cache/typescript/control_plane.mjs"), str(root)]
        with FileLock(root / "var/execution.lock"):
            process = subprocess.Popen(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    return cls(root).connect()
                except (OSError, ValueError, KeyError):
                    if process.poll() is not None:
                        break
                    time.sleep(0.05)
        raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE, "Cannot connect to the control plane")

    def request(self, method, params, *, timeout=30):
        with self.lock:
            if self.failure is not None:
                raise self.failure
            if self.socket is None:
                raise FrameworkError(ErrorCode.CLOSED)
            connection = self.socket
            request_id = uid("control")
            envelope = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            encoded = (json.dumps(envelope, ensure_ascii=False, allow_nan=False) + "\n").encode()
            if len(encoded) > MAX_MESSAGE:
                raise FrameworkError(ErrorCode.MESSAGE_TOO_LARGE)
            try:
                connection.settimeout(10)
                connection.sendall(encoded)
                line = self._receive(connection, timeout)
                response = json.loads(line)
                if (
                    not isinstance(response, dict)
                    or response.get("jsonrpc") != "2.0"
                    or response.get("id") != request_id
                ):
                    raise ValueError
                if ("result" in response) == ("error" in response):
                    raise ValueError
                if "error" in response:
                    failure = response["error"]
                    if (
                        not isinstance(failure, dict)
                        or not isinstance(failure.get("message"), str)
                        or not isinstance(failure.get("data"), dict)
                    ):
                        raise ValueError
            except (OSError, ValueError, FrameworkError) as exc:
                self.failure = FrameworkError(
                    ErrorCode.TRACE_UNAVAILABLE,
                    "Control connection lost; request outcome is unknown and was not replayed",
                )
                self.close()
                raise self.failure from exc
            if "error" in response:
                details = response["error"].get("data", {})
                try:
                    code = ErrorCode(details.get("code"))
                except ValueError:
                    code = ErrorCode.RPC_ERROR
                raise FrameworkError(
                    code,
                    response["error"].get("message"),
                    retryable=details.get("retryable") is True,
                )
            return response.get("result")

    def _receive(self, connection, timeout):
        deadline = time.monotonic() + timeout if timeout is not None else None
        received = bytearray()
        while True:
            remaining = deadline - time.monotonic() if deadline is not None else 10
            if remaining <= 0:
                raise TimeoutError
            readable, _, _ = select.select([connection], [], [], min(10, remaining))
            if not readable:
                if deadline is None:
                    # Liveness checks never resubmit the outstanding operation.
                    client = ControlClient(self.root).connect()
                    try:
                        client.request("status", {}, timeout=10)
                    finally:
                        client.close()
                continue
            chunk = connection.recv(min(65_536, MAX_MESSAGE + 1 - len(received)))
            if not chunk:
                raise ConnectionError
            received.extend(chunk)
            if len(received) > MAX_MESSAGE:
                raise ValueError
            if b"\n" in received:
                if not received.endswith(b"\n") or received.count(b"\n") != 1:
                    raise ValueError
                return received

    def close(self):
        self.closed = True
        if self.socket is not None:
            connection = self.socket
            self.socket = None
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
