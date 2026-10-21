from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys

from game_automation.common import FrameworkError

MAX_MESSAGE = 8 * 1024 * 1024


class Peer:
    """Bidirectional JSON-RPC. Each request may receive nested host.event requests.

    Observers run before write/dispatch; a persistence failure stops the peer.
    Codex omits jsonrpc on the wire, whereas project plugins require it.
    """

    def __init__(self, reader, writer, *, handler=None, observer=None, codex=False):
        self.reader = reader
        self.writer = writer
        self.handler = handler
        self.observer = observer
        self.codex = codex
        self.pending = {}
        self.counter = 0
        self.handlers = set()
        self.failure = None
        self.read_task = asyncio.create_task(self._read())

    async def send(self, message):
        if self.failure:
            raise self.failure
        if not self.codex:
            message = {"jsonrpc": "2.0", **message}
        if self.observer:
            await self.observer("out", message)
        encoded = (json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(encoded) > MAX_MESSAGE:
            raise FrameworkError("MESSAGE_TOO_LARGE", "Use artifact references for large payloads")
        self.writer.write(encoded)
        await self.writer.drain()

    async def request(self, method, params, timeout=30):
        self.counter += 1
        request_id = f"rpc_{self.counter}"
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            request_message = {"id": request_id, "method": method, "params": params}
            await self.send(request_message)
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise FrameworkError(
                "TIMEOUT", f"Timed out waiting for {method}", retryable=True
            ) from exc
        finally:
            self.pending.pop(request_id, None)

    async def notify(self, method, params):
        await self.send({"method": method, "params": params})

    async def _dispatch(self, message):
        try:
            if not self.handler:
                raise FrameworkError("METHOD_NOT_FOUND", "Inbound method is not supported")
            result = await self.handler(message["method"], message.get("params", {}))
            if "id" in message:
                await self.send({"id": message["id"], "result": result})
        except Exception as exc:
            if "id" in message:
                error = (
                    exc
                    if isinstance(exc, FrameworkError)
                    else FrameworkError("HANDLER_FAILED", type(exc).__name__)
                )
                error_details = {
                    "code": -32000,
                    "message": str(error),
                    "data": error.as_dict(),
                }
                error_response = {"id": message["id"], "error": error_details}
                await self.send(error_response)

    def _handler_done(self, task):
        self.handlers.discard(task)
        if not task.cancelled() and task.exception():
            self._fail(task.exception())

    def _fail(self, exc):
        self.failure = exc
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(exc)

    async def _read(self):
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    raise FrameworkError(
                        "PROCESS_EXITED", "Protocol stream ended before completion", retryable=True
                    )
                message = json.loads(line)
                if not isinstance(message, dict) or (
                    not self.codex and message.get("jsonrpc") != "2.0"
                ):
                    raise FrameworkError("INVALID_PROTOCOL", "Expected JSON-RPC 2.0 object")
                if self.observer:
                    await self.observer("in", message)
                if "method" in message:
                    task = asyncio.create_task(self._dispatch(message))
                    self.handlers.add(task)
                    task.add_done_callback(self._handler_done)
                else:
                    future = self.pending.get(message.get("id"))
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            data = error.get("data") or {}
                            if not isinstance(data, dict):
                                data = {}
                            future.set_exception(
                                FrameworkError(
                                    data.get("code", "RPC_ERROR"),
                                    error.get("message", "Remote call failed"),
                                    retryable=data.get("retryable", False),
                                )
                            )
                        elif "result" in message:
                            future.set_result(message["result"])
                        else:
                            raise FrameworkError(
                                "INVALID_PROTOCOL", "Response has no result or error"
                            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not isinstance(exc, FrameworkError):
                exc = FrameworkError("INVALID_PROTOCOL", "Invalid or oversized protocol stream")
            self._fail(exc)

    async def close(self):
        self._fail(FrameworkError("CLOSED", "Connection closed"))
        self.read_task.cancel()
        for task in list(self.handlers):
            task.cancel()
        await asyncio.gather(self.read_task, *list(self.handlers), return_exceptions=True)
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


async def spawn(command, cwd=None, env=None):
    options = {"start_new_session": True} if os.name != "nt" else {}
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=MAX_MESSAGE,
        **options,
    )


async def terminate(process):
    if process.returncode is None:
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    await process.wait()


class StdioReader:
    async def readline(self):
        line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_MESSAGE + 1)
        if len(line) > MAX_MESSAGE:
            raise FrameworkError("MESSAGE_TOO_LARGE", "Protocol line too large")
        return line


class StdioWriter:
    def write(self, data):
        sys.stdout.buffer.write(data)

    async def drain(self):
        sys.stdout.buffer.flush()

    def close(self):
        pass

    async def wait_closed(self):
        pass


async def serve(handler_factory):
    peer = Peer(StdioReader(), StdioWriter())
    peer.handler = handler_factory(peer)
    await peer.read_task
    for task in list(peer.handlers):
        task.cancel()
    await asyncio.gather(*list(peer.handlers), return_exceptions=True)
