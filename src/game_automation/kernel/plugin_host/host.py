import asyncio
import contextlib
import os
import time

from game_automation.common import FrameworkError, redact, uid
from game_automation.contracts import validate
from game_automation.plugin_sdk.rpc import Peer, spawn, terminate


class Host:
    def __init__(self, store, run_id, router, manifest):
        self.store, self.run_id, self.router, self.manifest = store, run_id, router, manifest

    def check_cancel(self):
        if self.store.run(self.run_id)["cancel_requested"]:
            raise FrameworkError("CANCELLED", "Cancellation requested")

    async def call(self, capability, params, *, parent_span_id=None, timeout=None, ancestry=()):
        self.check_cancel()
        if capability in ancestry or len(ancestry) >= 8:
            raise FrameworkError("DEPENDENCY_CYCLE", "Capability call cycle or depth limit")
        plugin = self.router.resolve(capability)
        if capability in plugin["mutating"]:
            raise FrameworkError("DEVICE_NOT_IMPLEMENTED", "Device execution is not available")
        retries = (
            self.manifest["application"]["max_attempts"]
            if capability in plugin["retry_safe"]
            else 1
        )
        timeout = timeout or (
            self.manifest["application"]["codex"]["timeout_seconds"] + 45
            if capability == "ai.decide"
            else self.manifest["application"]["call_timeout_seconds"]
        )
        for attempt in range(1, retries + 1):
            self.check_cancel()
            span, request = uid("span"), uid("req")
            ids = {"span_id": span, "parent_span_id": parent_span_id, "request_id": request}
            started = time.monotonic()
            input_ref = self.store.artifact(self.run_id, f"calls/{span}/input.json", params)
            self.store.event(
                self.run_id,
                "call.started",
                {
                    "capability": capability,
                    "plugin": plugin["name"],
                    "attempt": attempt,
                    "input_ref": input_ref,
                    "unavailable_optional_capabilities": [
                        c for c in plugin["optional_capabilities"] if c not in self.router.providers
                    ],
                },
                **ids,
            )
            try:
                result = await self._attempt(plugin, capability, params, timeout, ids, ancestry)
                if capability == "task.intake":
                    validate(self.store.run_dir(self.run_id) / "contracts", "intake-result", result)
                output_ref = self.store.artifact(self.run_id, f"calls/{span}/output.json", result)
                self.store.event(
                    self.run_id,
                    "call.completed",
                    {
                        "output_ref": output_ref,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "attempt": attempt,
                    },
                    **ids,
                )
                return result
            except BaseException as exc:
                error = (
                    exc
                    if isinstance(exc, FrameworkError)
                    else FrameworkError(
                        "CANCELLED" if isinstance(exc, asyncio.CancelledError) else "CALL_FAILED",
                        type(exc).__name__,
                    )
                )
                self.store.event(
                    self.run_id,
                    "call.incomplete"
                    if error.code in ("TIMEOUT", "PROCESS_EXITED", "CANCELLED")
                    else "call.failed",
                    {
                        "error": error.as_dict(),
                        "attempt": attempt,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    },
                    **ids,
                )
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if attempt < retries and error.retryable:
                    self.store.event(
                        self.run_id,
                        "call.retry",
                        {"previous_span_id": span, "next_attempt": attempt + 1},
                        **ids,
                    )
                    await asyncio.sleep(0.05 * attempt)
                    continue
                raise error from exc

    async def _attempt(self, plugin, capability, params, timeout, ids, ancestry):
        async def inbound(method, payload):
            if method == "host.event":
                kind = payload.get("kind", "")
                if not kind.startswith(("provider.", "ai.", "plugin.")):
                    raise FrameworkError("INVALID_EVENT", "Unsupported plugin event kind")
                if kind == "ai.session":
                    self.store.save_session(
                        self.run_id, payload["payload"]["role"], payload["payload"]["thread_id"]
                    )
                self.store.event(
                    self.run_id,
                    kind,
                    payload.get("payload", {}),
                    event_id=payload.get("event_id"),
                    **ids,
                )
                acknowledgement = {"persisted": True}
                return acknowledgement
            if method == "host.call":
                needed = payload["capability"]
                if needed not in plugin["required_capabilities"] + plugin["optional_capabilities"]:
                    raise FrameworkError("CAPABILITY_DENIED", "Undeclared plugin dependency")
                return await self.call(
                    needed,
                    payload["input"],
                    parent_span_id=ids["span_id"],
                    ancestry=(*ancestry, capability),
                )
            raise FrameworkError("METHOD_NOT_FOUND", method)

        async def observe(direction, message):
            # Nested provider event is persisted by inbound before its acknowledgement.
            self.store.event(self.run_id, "rpc." + direction, {"message": message}, **ids)

        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        process = await spawn(plugin["command"], cwd=plugin["cwd"], env=env)
        peer = Peer(process.stdout, process.stdin, handler=inbound, observer=observe)

        async def stderr():
            while chunk := await process.stderr.readline():
                self.store.event(
                    self.run_id,
                    "plugin.stderr",
                    {"text": chunk.decode("utf-8", errors="replace")},
                    **ids,
                )

        async def execute():
            result = await peer.request("initialize", {"protocol_version": "1.0"}, timeout)
            if (
                result.get("protocol_version") != "1.0"
                or result.get("capabilities") != plugin["capabilities"]
                or result.get("version") != plugin["version"]
            ):
                raise FrameworkError(
                    "PLUGIN_INCOMPATIBLE", "Handshake differs from registered manifest"
                )
            health = await peer.request("health", {}, timeout)
            if health != {"ok": True}:
                raise FrameworkError("PLUGIN_UNHEALTHY", "Plugin health check failed")
            return await peer.request(
                "invoke",
                {
                    "capability": capability,
                    "input": redact(params),
                    "context": {
                        "task_id": self.store.run(self.run_id)["task_id"],
                        "run_id": self.run_id,
                        **ids,
                    },
                },
                timeout,
            )

        async def cancelled():
            while True:
                self.check_cancel()
                await asyncio.sleep(0.1)

        operation, errors, cancel = (
            asyncio.create_task(execute()),
            asyncio.create_task(stderr()),
            asyncio.create_task(cancelled()),
        )
        try:
            deadline = time.monotonic() + timeout
            watchers = {operation, errors, cancel}
            while True:
                done, _ = await asyncio.wait(
                    watchers,
                    timeout=max(0, deadline - time.monotonic()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    raise FrameworkError("TIMEOUT", "Plugin call deadline exceeded", retryable=True)
                for task in done:
                    if task.exception():
                        raise task.exception()
                if operation in done:
                    return operation.result()
                watchers -= done
        finally:
            for task in (operation, errors, cancel):
                task.cancel()
            await asyncio.gather(operation, errors, cancel, return_exceptions=True)
            with contextlib.suppress(Exception):
                await peer.close()
            await terminate(process)
