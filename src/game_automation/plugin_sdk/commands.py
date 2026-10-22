import asyncio
import time

from game_automation.common import FrameworkError, digest, uid
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.images import png_dimensions
from game_automation.plugin_sdk.rpc import spawn, terminate


async def read_limited(stream, limit):
    data = bytearray()
    while chunk := await stream.read(65536):
        if len(data) + len(chunk) > limit:
            raise FrameworkError(ErrorCode.COMMAND_OUTPUT_LIMIT)
        data.extend(chunk)
    return bytes(data)


class Commands:
    def __init__(self, peer, timeout, *, namespace):
        self.peer = peer
        self.timeout = timeout
        self.namespace = namespace
        self.sanitize = lambda text: text

    async def event(self, kind, payload):
        request = {"kind": f"plugin.{self.namespace}.{kind}", "payload": payload}
        result = await self.peer.request("host.event", request)
        if result != {"persisted": True}:
            raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE)

    async def run(
        self,
        command,
        *,
        screenshot=None,
        allow_failure=False,
        stdin=None,
        transform=None,
        sensitive=False,
        raw=False,
    ):
        command_id = uid("cmd")
        details = {"command_id": command_id, "arguments": command}
        await self.event("command.started", details)
        started = time.monotonic()
        process = None
        readers = []
        result = {"command_id": command_id}
        try:
            async with asyncio.timeout(self.timeout):
                process = await spawn(command)
                if stdin is not None:
                    process.stdin.write(stdin)
                    await process.stdin.drain()
                    process.stdin.close()
                limit = 16 * 1024 * 1024 if screenshot or raw else 1024 * 1024
                readers = [
                    asyncio.create_task(read_limited(process.stdout, limit)),
                    asyncio.create_task(read_limited(process.stderr, 65536)),
                ]
                stdout, stderr = await asyncio.gather(*readers)
                await process.wait()
            result["exit_code"] = process.returncode
            result["stderr"] = self.sanitize(stderr.decode("utf-8", errors="replace"))
            if sensitive:
                stdout = b""
                result["stderr"] = "[Sensitive command output suppressed]"
            if screenshot and process.returncode == 0:
                if transform:
                    stdout = transform(stdout)
                width, height = png_dimensions(stdout[:24])
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                screenshot.write_bytes(stdout)
                result["image"] = {
                    "file_name": screenshot.name,
                    "size_bytes": len(stdout),
                    "sha256": digest(stdout),
                    "width": width,
                    "height": height,
                }
            elif not raw:
                result["stdout"] = self.sanitize(stdout.decode("utf-8", errors="replace"))
            result["duration_ms"] = round((time.monotonic() - started) * 1000)
            if process.returncode != 0 and not allow_failure:
                raise FrameworkError(ErrorCode.LOCAL_COMMAND_FAILED)
            await self.event("command.completed", result)
            if raw:
                result["data"] = stdout
            return result
        except BaseException as exc:
            if isinstance(exc, TimeoutError):
                error = FrameworkError(ErrorCode.TIMEOUT, "Local command timed out")
            elif isinstance(exc, asyncio.CancelledError):
                raise
            elif isinstance(exc, FrameworkError):
                error = exc
            else:
                error = FrameworkError(ErrorCode.LOCAL_COMMAND_FAILED, type(exc).__name__)
            result["error"] = error.as_dict()
            result["duration_ms"] = round((time.monotonic() - started) * 1000)
            kind = "command.completed" if "exit_code" in result else "command.incomplete"
            await self.event(kind, result)
            raise error from exc
        finally:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if process is not None:
                await terminate(process)
