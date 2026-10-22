import asyncio
import contextlib
import os
import time

from game_automation.common import FrameworkError, redact, uid
from game_automation.contracts import validate
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.rpc import Peer, spawn, terminate
from game_automation.prompting.resources import resource_root

CAPABILITY_CONTRACTS = {
    "task.intake": ("intake-request", "intake-result"),
    "environment.check": ("environment-request", "environment-result"),
    "apk.prepare": ("apk-prepare-request", "apk-metadata"),
    "apk.plan": ("apk-plan-request", "apk-download-plan"),
    "apk.preflight": ("apk-preflight-request", "apk-preflight-result"),
    "emulator.discover": ("emulator-discovery-request", "emulator-discovery-result"),
    "emulator.session": ("emulator-session-request", "emulator-session-result"),
    "device.inspect": ("device-inspect-request", "device-inspect-result"),
    "device.install": ("device-install-request", "device-install-result"),
    "device.observe": ("device-observe-request", "device-observe-result"),
    "device.runtime": ("device-runtime-request", "device-runtime-result"),
    "device.capture": ("device-capture-request", "device-capture-result"),
    "device.execute": ("device-execute-request", "device-execute-result"),
    "emulator.activate": ("emulator-activate-request", "emulator-activate-result"),
    "device.logs": ("device-logs-request", "device-logs-result"),
    "perception.analyze": ("perception-request", "perception-result"),
}


class Host:
    def __init__(self, store, run_id, router, manifest):
        self.store = store
        self.run_id = run_id
        self.router = router
        self.manifest = manifest
        self.workflow_run = store.run_task(run_id).get("workflow_run")
        self.contracts = resource_root(store, run_id, "contracts", manifest=manifest)
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
        if self.workflow_run and self.store.run(self.workflow_run)["cancel_requested"]:
            raise FrameworkError(ErrorCode.CANCELLED)

    async def call(
        self,
        capability,
        params,
        *,
        parent_span_id=None,
        timeout=None,
        ancestry=(),
        span_id=None,
        _action_id=None,
    ):
        self.check_cancel()
        if capability in ancestry or len(ancestry) >= 8:
            raise FrameworkError(ErrorCode.DEPENDENCY_CYCLE)
        plugin = self.router.resolve(capability)
        mutating = capability in plugin["mutating"]
        if capability in ("device.install", "device.execute", "emulator.activate") and not mutating:
            raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
        if mutating:
            action = self.store.latest_device_action(self.run_id)
            if (
                capability not in ("device.install", "device.execute", "emulator.activate")
                or action is None
                or action["action_id"] != _action_id
                or action["status"]
                != ("unknown" if capability == "emulator.activate" else "prepared")
                or action["run_id"] != self.run_id
                or (capability == "emulator.activate" and ancestry != ("device.execute",))
            ):
                raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
        contracts = CAPABILITY_CONTRACTS.get(capability)
        if contracts:
            validate(self.contracts, contracts[0], params)
        retries = (
            self.manifest["application"]["max_attempts"]
            if not mutating and capability in plugin["retry_safe"]
            else 1
        )
        if timeout is None:
            if capability == "ai.decide":
                timeout = self.manifest["application"]["codex"]["timeout_seconds"] + 45
            elif capability != "emulator.session":
                timeout = self.manifest["application"]["call_timeout_seconds"]
        elif timeout == 0:
            timeout = None
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
                    plugin, capability, params, timeout, trace_ids, ancestry, _action_id
                )
                if contracts:
                    validate(self.contracts, contracts[1], result)
                output_ref = self.artifact(f"calls/{span_id}/output.json", result)
                completed_payload = {
                    "capability": capability,
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
                    ErrorCode.DEVICE_INSTALL_UNKNOWN,
                )
                if mutating:
                    action = self.store.latest_device_action(self.run_id)
                    incomplete = incomplete or action["status"] == "unknown"
                failure_kind = "call.incomplete" if incomplete else "call.failed"
                failure_payload = {
                    "capability": capability,
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

    async def install(self, params):
        from game_automation.prompting.snapshots import load

        load(self.store, self.run_id)
        self.check_cancel()
        plugin = self.router.resolve("device.install")
        if "device.install" not in plugin["mutating"]:
            raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
        validate(self.contracts, "device-install-request", params)
        max_actions = self.manifest["application"]["max_actions"]
        if type(max_actions) is not int or max_actions <= 0:
            raise FrameworkError(ErrorCode.BUDGET_EXHAUSTED)
        span_id = uid("span")
        input_ref = self.artifact(f"artifacts/{uid('install')}/input.json", params)
        action_id = self.store.begin_device_action(
            self.run_id,
            params["session"]["instance_id"],
            input_ref,
            max_actions,
            span_id,
        )
        try:
            result = await self.call(
                "device.install", params, timeout=0, _action_id=action_id, span_id=span_id
            )
            if self.store.latest_device_action(self.run_id)["status"] != "unknown":
                raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
        except BaseException as exc:
            action = self.store.latest_device_action(self.run_id)
            if action["status"] == "prepared":
                error = (
                    exc.as_dict()
                    if isinstance(exc, FrameworkError)
                    else {"code": ErrorCode.CALL_FAILED, "message": type(exc).__name__}
                )
                self.store.finish_device_action(self.run_id, action_id, "rejected", error)
            raise
        status = "executed" if result["status"] == "installed" else "rejected"
        self.store.finish_device_action(self.run_id, action_id, status, result)
        return result

    async def perform(self, params, *, parent_span_id=None):
        from game_automation.prompting.snapshots import load

        load(self.store, self.run_id)
        self.check_cancel()
        validate(self.contracts, "device-execute-request", params)
        span_id = uid("span")
        input_ref = self.artifact(f"artifacts/{uid('action')}/input.json", params)
        action_id = self.store.begin_device_action(
            self.run_id,
            params["session"]["instance_id"],
            input_ref,
            None,
            span_id,
            capability="device.execute",
        )
        try:
            result = await self.call(
                "device.execute",
                params,
                timeout=0,
                _action_id=action_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )
            if self.store.latest_device_action(self.run_id)["status"] != "unknown":
                raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
            self.store.finish_device_action(self.run_id, action_id, "executed", result)
        except BaseException as exc:
            action = self.store.latest_device_action(self.run_id)
            if action["status"] == "prepared":
                failure = {"error": type(exc).__name__, "dispatched": False}
                self.store.finish_device_action(self.run_id, action_id, "rejected", failure)
            raise
        result["action_id"] = action_id
        return result

    async def _attempt(self, plugin, capability, params, timeout, trace_ids, ancestry, action_id):
        async def inbound(method, payload):
            if method == "host.action":
                self.check_cancel()
                if capability not in ("device.install", "device.execute") or action_id is None:
                    raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
                expected_session = {
                    "instance_id": params["session"]["instance_id"],
                    "serial": params["session"]["serial"],
                }
                if payload != expected_session:
                    raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
                self.store.dispatch_device_action(self.run_id, action_id)
                acknowledgement = {"persisted": True}
                return acknowledgement
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
                delegated_action = None
                if needed == "emulator.activate":
                    expected = {
                        "session": params["session"],
                        "package_name": params["package_name"],
                    }
                    if (
                        capability != "device.execute"
                        or payload["input"] != expected
                        or params["action"]["type"] not in ("launch", "return")
                    ):
                        raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
                    delegated_action = action_id
                return await self.call(
                    needed,
                    payload["input"],
                    parent_span_id=trace_ids["span_id"],
                    ancestry=(*ancestry, capability),
                    _action_id=delegated_action,
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
            handshake_timeout = self.manifest["application"]["call_timeout_seconds"]
            result = await peer.request(
                "initialize", {"protocol_version": "1.0"}, handshake_timeout
            )
            if (
                result.get("protocol_version") != "1.0"
                or result.get("capabilities") != plugin["capabilities"]
                or result.get("version") != plugin["version"]
            ):
                raise FrameworkError(ErrorCode.PLUGIN_INCOMPATIBLE)
            health = await peer.request("health", {}, handshake_timeout)
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
            deadline = time.monotonic() + timeout if timeout is not None else None
            watchers = {invocation_task, stderr_task, cancellation_task}
            while True:
                done, _ = await asyncio.wait(
                    watchers,
                    timeout=max(0, deadline - time.monotonic()) if deadline is not None else None,
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
