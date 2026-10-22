import asyncio
import time

from game_automation.common import FrameworkError, within
from game_automation.errors import ErrorCode

from .routing import DecisionRouter, Route, RoutineExecutor
from .verification import OutcomeVerifier


class Perception:
    def __init__(self, runtime):
        self.runtime = runtime

    async def analyze(self, observation, previous=None):
        directory = self.runtime.store.run_dir(self.runtime.run_id)
        request = {
            "image_path": str(within(directory, observation["evidence_ref"])),
            "previous_image_path": str(within(directory, previous["evidence_ref"]))
            if previous
            else None,
            "controls": observation["controls"],
            "excluded_regions": observation["sensitive_regions"],
        }
        result = await self.runtime.host.call("perception.analyze", request)
        self.runtime.host.record(
            "gameplay.perception", {"observation_id": observation["observation_id"], **result}
        )
        return result

    async def settle(self, before):
        started = time.monotonic()
        for _ in range(5):
            self.runtime.host.check_cancel()
            await asyncio.sleep(0.5)
            after = await self.runtime.observe()
            facts = await self.analyze(after, before)
            if facts["stable"]:
                break
            before = after
        self.runtime.host.record(
            "gameplay.wait",
            {"duration_ms": round((time.monotonic() - started) * 1000), "stable": facts["stable"]},
        )
        result = after, facts
        return result

    async def wait_for_change(self, before, seconds=90, minimum_seconds=0):
        started = time.monotonic()
        deadline = started + seconds
        original_runtime = before["runtime"]
        while True:
            self.runtime.host.check_cancel()
            await asyncio.sleep(2)
            after = await self.runtime.observe()
            facts = await self.analyze(after, before)
            changed = (
                after["runtime"]["focused_display"] != original_runtime["focused_display"]
                or after["runtime"]["alive"] != original_runtime["alive"]
                or facts["difference"] is not None
                and facts["difference"] >= 0.08
            )
            if (
                changed
                and time.monotonic() - started >= minimum_seconds
                or time.monotonic() >= deadline
            ):
                break
        self.runtime.host.record(
            "gameplay.wait",
            {
                "duration_ms": round((time.monotonic() - started) * 1000),
                "reason": "known-transition",
                "changed": changed,
                "ai_calls": 0,
            },
        )
        result = after, facts
        return result


async def execute_routine(runtime, observation, steps):
    perception = Perception(runtime)
    routine = RoutineExecutor(steps)
    router = DecisionRouter()
    verifier = OutcomeVerifier()
    observation, facts = await perception.settle(observation)
    while not routine.complete:
        route, reason = router.route(observation, facts, routine)
        runtime.host.record("gameplay.route", {"route": route, "reason": reason})
        if route != Route.ROUTINE:
            raise FrameworkError(
                ErrorCode.GAMEPLAY_BLOCKED, "Routine conditions changed; planning is required"
            )
        step = routine.next_step(observation, facts)
        dispatched = await runtime.perform(step["action"], observation, source="routine")
        after, facts = await perception.settle(observation)
        result = verifier.verify(step["after"], observation, after, facts)
        runtime.host.record("gameplay.outcome", result)
        if result["status"] != "verified":
            raise FrameworkError(
                ErrorCode.GAMEPLAY_BLOCKED,
                "Routine stopped because the expected result is unverified",
            )
        runtime.store.finish_device_action(
            runtime.run_id, dispatched["action_id"], "verified", result
        )
        runtime.progress.data["pending"] = None
        runtime.progress.data["checkpoint"] = {
            "run_id": runtime.run_id,
            "observation_id": after["observation_id"],
            "runtime": after["runtime"],
        }
        runtime.progress.save()
        routine.index += 1
        observation = after
    return observation
