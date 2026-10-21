import asyncio
import contextlib
import os
import time

from game_automation.common import FrameworkError, redact, uid, within
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
        mutating = capability in plugin["mutating"]
        if mutating:
            # The current release has no real device executor. Fixture path is explicit.
            if capability != "fixture.act" or self.store.run(self.run_id)["mode"] != "demo":
                raise FrameworkError(
                    "DEVICE_NOT_IMPLEMENTED", "Real-device action execution is not available"
                )
            self._validate_action(params)
        retries = (
            self.manifest["application"]["max_attempts"]
            if capability in plugin["retry_safe"] and not mutating
            else 1
        )
        timeout = timeout or self.manifest["application"]["call_timeout_seconds"]
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
                if mutating:
                    self.store.reserve_action(self.run_id, params["action"]["id"])
                result = await self._attempt(plugin, capability, params, timeout, ids, ancestry)
                if mutating:
                    validate(self.store.run_dir(self.run_id) / "contracts", "action-result", result)
                    if result["action_id"] != params["action"]["id"]:
                        raise FrameworkError("INVALID_CONTRACT", "Action result ID mismatch")
                    self.store.finish_action(self.run_id, params["action"]["id"], result)
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

    def _validate_action(self, params):
        contracts = self.store.run_dir(self.run_id) / "contracts"
        validate(contracts, "action", params["action"])
        validate(contracts, "observation", params["observation"])
        action, observation = params["action"], params["observation"]
        recorded = [
            e for e in self.store.events(self.run_id) if e["kind"] == "observation.captured"
        ]
        if not recorded or recorded[-1]["payload"].get("observation") != observation:
            raise FrameworkError(
                "STALE_OBSERVATION", "Observation is not the latest persisted frame"
            )
        for ref in observation["evidence_refs"]:
            if not within(self.store.run_dir(self.run_id), ref).is_file():
                raise FrameworkError("EVIDENCE_MISSING", "Observation evidence is unavailable")
        if (
            action["observation_id"] != observation["id"]
            or action["session_id"] != observation["session_id"]
        ):
            raise FrameworkError(
                "STALE_OBSERVATION", "Action does not refer to current observation/session"
            )
        if action["type"] == "tap":
            if not (
                0 <= action.get("x", -1) < observation["width"]
                and 0 <= action.get("y", -1) < observation["height"]
            ):
                raise FrameworkError("INVALID_COORDINATES", "Tap is outside screenshot")
        count = self.store.db.execute(
            "SELECT COUNT(*) FROM actions WHERE run_id=?", (self.run_id,)
        ).fetchone()[0]
        task = self.store.task(self.store.run(self.run_id)["task_id"])
        if count >= min(task["limits"]["max_actions"], self.manifest["application"]["max_actions"]):
            raise FrameworkError("BUDGET_EXHAUSTED", "Action limit reached")

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
                return {"persisted": True}
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
