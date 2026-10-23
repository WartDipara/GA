"""Bounded diagnostic snapshots share the existing trace and never own action outcomes."""

import asyncio

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


class DiagnosticCollector:
    def __init__(self, runtime):
        self.runtime = runtime
        self.task = None
        self.windows = {}

    def request(self, action_id=None):
        host = self.runtime.host
        host.check_cancel()
        if self.task is not None and not self.task.done():
            host.record(
                "gameplay.logs.skipped",
                {"reason": "collection-in-progress", "action_id": action_id},
            )
            return
        self.task = asyncio.create_task(self.collect(action_id, self.runtime.step_id))

    async def collect(self, action_id, parent_span_id):
        runtime = self.runtime
        request = {
            "session": runtime.session,
            "package_name": runtime.task["target"],
            "credential_path": runtime.credential_path,
        }
        try:
            logs = await runtime.host.call("device.logs", request, parent_span_id=parent_span_id)
            logs["action_id"] = action_id
            logs["coverage"] = "bounded snapshots; intervening log loss is possible"
            current = {}
            for window in logs["windows"]:
                process = window["process"]
                key = (logs["boot_id"], process["uid"], process["pid"], process["start_ticks"])
                lines = window["text"].splitlines()
                previous = self.windows.get(key, [])
                overlap = 0
                for length in range(min(len(previous), len(lines)), 0, -1):
                    if previous[-length:] == lines[:length]:
                        overlap = length
                        break
                window["possible_gap"] = bool(previous and lines and not overlap)
                window["text"] = "\n".join(lines[overlap:])
                current[key] = lines
            self.windows = current
            runtime.host.record("gameplay.logs", logs, span_id=parent_span_id)
        except Exception as exc:
            if runtime.host.failure is not None:
                raise
            error = (
                exc.as_dict()
                if isinstance(exc, FrameworkError)
                else {"code": ErrorCode.LOCAL_ERROR, "message": type(exc).__name__}
            )
            runtime.host.record(
                "gameplay.logs.unavailable",
                {"action_id": action_id, "error": error},
                span_id=parent_span_id,
            )

    async def close(self):
        if self.task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=5)
        except TimeoutError:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            if self.runtime.host.failure is not None:
                raise self.runtime.host.failure
            self.runtime.host.record(
                "gameplay.logs.incomplete", {"reason": "finalization-deadline"}
            )
        except BaseException:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            raise
