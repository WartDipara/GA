from enum import StrEnum

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode

from .conditions import condition_matches


class Route(StrEnum):
    ROUTINE = "routine"
    WAIT = "wait"
    PLAN = "plan"
    BLOCKED = "blocked"


class RoutineExecutor:
    def __init__(self, steps):
        if not 1 <= len(steps) <= 5:
            raise FrameworkError(ErrorCode.INVALID_CONTRACT, "Routines require one to five steps")
        self.steps = steps
        self.index = 0

    @property
    def complete(self):
        return self.index == len(self.steps)

    def next_step(self, observation, perception):
        if self.complete:
            return None
        step = self.steps[self.index]
        if not perception["stable"]:
            return None
        if not all(condition_matches(item, observation, perception) for item in step["before"]):
            return None
        # A screen guard is mandatory even when a control ID still exists behind a modal.
        if not any(item["kind"] == "screen" for item in step["before"]):
            return None
        return step


class DecisionRouter:
    def route(self, observation, perception, routine, *, settling=False):
        if observation["runtime"]["focused_display"] is None:
            result = Route.WAIT, "display-transition"
            return result
        if settling and not perception["stable"]:
            result = Route.WAIT, "screen-not-stable"
            return result
        if routine and not routine.complete:
            if routine.next_step(observation, perception):
                result = Route.ROUTINE, "verified-preconditions"
                return result
            result = Route.PLAN, "routine-precondition-mismatch"
            return result
        result = Route.PLAN, "unknown-state-or-new-goal"
        return result
