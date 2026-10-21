import asyncio
import contextlib
import os
import time

from game_automation.common import FrameworkError, redact, uid
from game_automation.contracts import validate
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.rpc import Peer, spawn, terminate


class Host:
    def __init__(self, store, run_id, router, manifest):
        self.store = store
        self.run_id = run_id
        self.router = router
        self.manifest = manifest
        self.failure = None

    def stop(self, error):
        if self.failure is None:
            self.failure = error

    def record(self, kind, payload, **trace_ids):
        try:
            return self.store.event(self.run_id, kind, payload, **trace_ids)
        except Exception as exc:
            self.stop(exc)
            raise

    def artifact(self, relative, value):
        try:
            return self.store.artifact(self.run_id, relative, value)
        except Exception as exc:
            self.stop(exc)
            raise

    def check_cancel(self):
        if self.failure is not None:
            raise self.failure
        if self.store.run(self.run_id)["cancel_requested"]:
            raise FrameworkError(ErrorCode.CANCELLED)

    async def call(
        self, capability, params, *, parent_span_id=None, timeout=None, ancestry=(), span_id=None
    ):
        self.check_cancel()
        if capability in ancestry or len(ancestry) >= 8:
            raise FrameworkError(ErrorCode.DEPENDENCY_CYCLE)
        plugin = self.router.resolve(capability)
        if capability in plugin["mutating"]:
            raise FrameworkError(ErrorCode.DEVICE_NOT_IMPLEMENTED)
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
        initial_span_id = span_id
        for attempt in range(1, retries + 1):
            self.check_cancel()
            span_id = initial_span_id if initial_span_id and attempt == 1 else uid("span")
            request_id = uid("req")
            trace_ids = {
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "request_id": request_id,
            }
            started = time.monotonic()
            input_ref = self.artifact(f"calls/{span_id}/input.json", params)
            unavailable_capabilities = [
                dependency
                for dependency in plugin["optional_capabilities"]
                if dependency not in self.router.providers
            ]
            started_payload = {
                "capability": capability,
                "plugin": plugin["name"],
                "attempt": attempt,
                "input_ref": input_ref,
                "unavailable_optional_capabilities": unavailable_capabilities,
            }
            self.record("call.started", started_payload, **trace_ids)
            try:
                result = await self._attempt(
                    plugin, capability, params, timeout, trace_ids, ancestry
                )
                if capability == "task.intake":
                    validate(self.store.run_dir(self.run_id) / "contracts", "intake-result", result)
                elif capability == "environment.check":
                    validate(
                        self.store.run_dir(self.run_id) / "contracts", "environment-result", result
                    )
                elif capability == "apk.prepare":
                    validate(self.store.run_dir(self.run_id) / "contracts", "apk-metadata", result)
                elif capability == "apk.plan":
                    validate(
                        self.store.run_dir(self.run_id) / "contracts", "apk-download-plan", result
                    )
                output_ref = self.artifact(f"calls/{span_id}/output.json", result)
                completed_payload = {
                    "output_ref": output_ref,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "attempt": attempt,
                }
                self.record("call.completed", completed_payload, **trace_ids)
                return result
            except BaseException as exc:
                if isinstance(exc, FrameworkError):
                    error = exc
                else:
                    code = (
                        ErrorCode.CANCELLED
                        if isinstance(exc, asyncio.CancelledError)
                        else ErrorCode.CALL_FAILED
                    )
                    error = FrameworkError(code, type(exc).__name__)
                incomplete = error.code in (
                    ErrorCode.TIMEOUT,
                    ErrorCode.PROCESS_EXITED,
                    ErrorCode.CANCELLED,
                )
                failure_kind = "call.incomplete" if incomplete else "call.failed"
                failure_payload = {
                    "error": error.as_dict(),
                    "attempt": attempt,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
                self.record(failure_kind, failure_payload, **trace_ids)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if attempt < retries and error.retryable:
                    self.record(
                        "call.retry",
                        {"previous_span_id": span_id, "next_attempt": attempt + 1},
                        **trace_ids,
                    )
                    await asyncio.sleep(0.05 * attempt)
                    continue
                raise error from exc

    async def _attempt(self, plugin, capability, params, timeout, trace_ids, ancestry):
        async def inbound(method, payload):
            if method == "host.event":
                self.check_cancel()
                kind = payload.get("kind", "")
                if not kind.startswith(("provider.", "ai.", "plugin.")):
                    raise FrameworkError(ErrorCode.INVALID_EVENT)
                if kind == "ai.session":
                    self.store.save_session(
                        self.run_id, payload["payload"]["role"], payload["payload"]["thread_id"]
                    )
                self.record(
                    kind,
                    payload.get("payload", {}),
                    event_id=payload.get("event_id"),
                    **trace_ids,
                )
                acknowledgement = {"persisted": True}
                return acknowledgement
            if method == "host.call":
                needed = payload["capability"]
                if needed not in plugin["required_capabilities"] + plugin["optional_capabilities"]:
                    raise FrameworkError(
                        ErrorCode.CAPABILITY_DENIED, "Undeclared plugin dependency"
                    )
                return await self.call(
                    needed,
                    payload["input"],
                    parent_span_id=trace_ids["span_id"],
                    ancestry=(*ancestry, capability),
                )
            raise FrameworkError(ErrorCode.METHOD_NOT_FOUND, method)

        async def observe(direction, message):
            # Nested provider event is persisted by inbound before its acknowledgement.
            self.record("rpc." + direction, {"message": message}, **trace_ids)

        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        process = await spawn(plugin["command"], cwd=plugin["cwd"], env=env)
        peer = Peer(process.stdout, process.stdin, handler=inbound, observer=observe)

        async def stderr():
            while chunk := await process.stderr.readline():
                self.record(
                    "plugin.stderr",
                    {"text": chunk.decode("utf-8", errors="replace")},
                    **trace_ids,
                )

        async def execute():
            result = await peer.request("initialize", {"protocol_version": "1.0"}, timeout)
            if (
                result.get("protocol_version") != "1.0"
                or result.get("capabilities") != plugin["capabilities"]
                or result.get("version") != plugin["version"]
            ):
                raise FrameworkError(ErrorCode.PLUGIN_INCOMPATIBLE)
            health = await peer.request("health", {}, timeout)
            if health != {"ok": True}:
                raise FrameworkError(ErrorCode.PLUGIN_UNHEALTHY)
            invocation_context = {
                "task_id": self.store.run(self.run_id)["task_id"],
                "run_id": self.run_id,
                **trace_ids,
            }
            invocation_request = {
                "capability": capability,
                "input": redact(params),
                "context": invocation_context,
            }
            return await peer.request("invoke", invocation_request, timeout)

        async def cancelled():
            while True:
                self.check_cancel()
                await asyncio.sleep(0.1)

        invocation_task = asyncio.create_task(execute())
        stderr_task = asyncio.create_task(stderr())
        cancellation_task = asyncio.create_task(cancelled())
        try:
            deadline = time.monotonic() + timeout
            watchers = {invocation_task, stderr_task, cancellation_task}
            while True:
                done, _ = await asyncio.wait(
                    watchers,
                    timeout=max(0, deadline - time.monotonic()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    raise FrameworkError(
                        ErrorCode.TIMEOUT, "Plugin call deadline exceeded", retryable=True
                    )
                for task in done:
                    if task.exception():
                        raise task.exception()
                if invocation_task in done:
                    return invocation_task.result()
                watchers -= done
        finally:
            for task in (invocation_task, stderr_task, cancellation_task):
                task.cancel()
            await asyncio.gather(
                invocation_task, stderr_task, cancellation_task, return_exceptions=True
            )
            with contextlib.suppress(Exception):
                await peer.close()
            await terminate(process)
