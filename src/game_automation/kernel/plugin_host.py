"""Python workflow compatibility boundary; supervision and writes belong to TypeScript."""

import asyncio
from pathlib import Path

from game_automation.common import FrameworkError, uid, within
from game_automation.errors import ErrorCode
from game_automation.prompting.resources import resource_root
from game_automation.storage.control_client import ControlClient


class Host:
    def __init__(self, store, run_id, router, manifest):
        self.store = store
        self.run_id = run_id
        self.router = router
        self.manifest = manifest
        self.workflow_run = store.run_task(run_id).get("workflow_run")
        self.contracts = resource_root(store, run_id, "contracts", manifest=manifest)
        self.failure = None
        self.context_span_id = None

    def stop(self, error):
        if self.failure is None:
            self.failure = error

    def record(self, kind, payload, **trace_ids):
        if "span_id" not in trace_ids and self.context_span_id is not None:
            trace_ids["span_id"] = self.context_span_id
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

    def _track_artifacts(self, response):
        if self.run_id not in self.store.file_usage:
            return
        for reference in response["artifacts"]:
            path = within(self.store.run_dir(self.run_id), reference)
            self.store.files(self.run_id).track(path)

    async def _execute(self, operation, params, context):
        self.check_cancel()
        call_id = uid("host")
        configuration = {
            "application": self.manifest["application"],
            "providers": self.router.providers,
        }
        request = {
            "call_id": call_id,
            "run_id": self.run_id,
            "operation": operation,
            "input": params,
            "context": context,
            "configuration": configuration,
        }

        client = ControlClient(self.store.root)

        def invoke():
            try:
                client.connect()
                return client.request("host.execute", request, timeout=None)
            finally:
                client.close()

        pending = asyncio.create_task(asyncio.to_thread(invoke))
        try:
            try:
                response = await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancellation = {"call_id": call_id}
                try:
                    self.store.writer.request("host.cancel", cancellation)
                except Exception as exc:
                    self.stop(exc)
                    client.close()
                outcomes = await asyncio.gather(pending, return_exceptions=True)
                if isinstance(outcomes[0], dict):
                    self._track_artifacts(outcomes[0])
                raise
            self._track_artifacts(response)
            if response["error"] is not None:
                details = response["error"]
                error = FrameworkError(
                    ErrorCode(details["code"]),
                    details["message"],
                    retryable=details["retryable"],
                )
                if response["halted"]:
                    self.stop(error)
                raise error
            return response["result"]
        except FrameworkError as exc:
            if exc.code in (ErrorCode.TRACE_UNAVAILABLE, ErrorCode.CLOSED):
                self.stop(exc)
            raise
        finally:
            workspace = params.get("workspace")
            if workspace and self.run_id in self.store.file_usage:
                directory = Path(workspace).resolve()
                if directory.is_relative_to(self.store.run_dir(self.run_id).resolve()):
                    try:
                        self.store.files(self.run_id).scan(directory)
                    except Exception as exc:
                        self.stop(exc)
                        raise

    async def call(
        self,
        capability,
        params,
        *,
        parent_span_id=None,
        timeout=None,
        ancestry=(),
        span_id=None,
    ):
        context = {
            "capability": capability,
            "parent_span_id": parent_span_id or self.context_span_id,
            "timeout": timeout,
            "ancestry": list(ancestry),
            "span_id": span_id,
        }
        return await self._execute("call", params, context)

    async def install(self, params):
        from game_automation.prompting.snapshots import load

        load(self.store, self.run_id)
        context = {"parent_span_id": self.context_span_id}
        return await self._execute("install", params, context)

    async def uninstall(self, params):
        from game_automation.prompting.snapshots import load

        load(self.store, self.run_id)
        context = {"parent_span_id": self.context_span_id}
        return await self._execute("uninstall", params, context)

    async def perform(self, params, *, parent_span_id=None):
        from game_automation.prompting.snapshots import load

        load(self.store, self.run_id)
        context = {"parent_span_id": parent_span_id or self.context_span_id}
        return await self._execute("perform", params, context)
