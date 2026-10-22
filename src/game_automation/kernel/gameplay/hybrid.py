"""One observation/execution loop for login, exploration and later battle goals."""

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode

from .local import Perception
from .planning import Planner
from .routing import DecisionRouter


async def run_hybrid(runtime, manifest, observation):
    perception = Perception(runtime)
    planner = Planner(runtime, manifest)
    router = DecisionRouter()
    progress = runtime.progress.data
    observation, facts = await perception.settle(observation)
    last_state = None
    repeated = 0
    while True:
        runtime.recorder.admit()
        runtime.host.check_cancel()
        limit = runtime.task.get("max_turns", 0)
        if limit and planner.turns >= limit:
            raise FrameworkError(
                ErrorCode.BUDGET_EXHAUSTED,
                "Requested planning-turn boundary reached; resume to continue",
            )
        route, reason = router.route(observation, facts, None)
        state = (facts["screen_key"], runtime.task["goal"])
        repeated = repeated + 1 if state == last_state else 1
        last_state = state
        if repeated >= 3:
            reason = "stalled-state-and-goal"
        if repeated >= 5:
            raise FrameworkError(
                ErrorCode.GAMEPLAY_BLOCKED, "No progress after two stall-recovery rounds"
            )
        runtime.host.record("gameplay.route", {"route": route, "reason": reason})
        decision = await planner.decide(observation, facts, reason)
        pending = progress["pending"]
        requested_wait = (
            decision["disposition"] == "wait"
            or decision["action"] is not None
            and decision["action"]["type"] == "wait"
        )
        if pending:
            assessment = decision["previous_outcome"]
            if assessment["status"] == "unknown" and requested_wait:
                observation, facts = await perception.wait_for_change(observation)
                continue
            if (
                assessment["status"] not in ("verified", "deviated")
                or observation["observation_id"] not in assessment["evidence_refs"]
            ):
                raise FrameworkError(
                    ErrorCode.GAMEPLAY_BLOCKED,
                    "Previous action outcome remains unverified; no action replay",
                )
            outcome = {
                "source": "model-semantic-review",
                "observation_id": observation["observation_id"],
                **assessment,
            }
            status = "verified" if assessment["status"] == "verified" else "reconciled"
            runtime.store.finish_device_action(
                pending["run_id"], pending["action_id"], status, outcome
            )
            runtime.host.record("gameplay.outcome", outcome)
            for path in progress["paths"]:
                if path["action_id"] == pending["action_id"]:
                    path["status"] = assessment["status"]
                    path["after"] = observation["observation_id"]
            progress["pending"] = None
        if decision["identity"] is not None:
            identity = progress["identity"] or {}
            for field, value in decision["identity"].items():
                if value is None:
                    continue
                if identity.get(field) not in (None, value):
                    raise FrameworkError(
                        ErrorCode.GAMEPLAY_BLOCKED, "Observed identity differs from the checkpoint"
                    )
                identity[field] = value
            progress["identity"] = identity or None
        runtime.progress.update(decision, observation["observation_id"])
        progress["checkpoint"] = {
            "run_id": runtime.run_id,
            "observation_id": observation["observation_id"],
            "runtime": observation["runtime"],
            "objective": decision["objective"],
            "screen_key": facts["screen_key"],
        }
        progress["sensitive_regions"] = decision["sensitive_regions"]
        runtime.progress.save()
        disposition = decision["disposition"]
        if disposition == "blocked" or decision["risk"] in (
            "real_payment",
            "account_delete",
            "unknown",
        ):
            raise FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, decision["reason_summary"])
        if disposition == "complete":
            interactions = [
                path
                for path in progress["paths"]
                if path["status"] == "verified" and path.get("type") == "tap"
            ]
            if not progress["identity"] or not interactions:
                raise FrameworkError(
                    ErrorCode.GAMEPLAY_BLOCKED,
                    "Completion requires a bound identity and verified interaction",
                )
            runtime.host.record(
                "gameplay.goal.completed",
                {
                    "goal": runtime.task["goal"],
                    "observation_id": observation["observation_id"],
                    "source": "model-semantic-review",
                    "identity": progress["identity"],
                },
            )
            return observation
        if decision["action"] is None:
            raise FrameworkError(ErrorCode.AI_OUTPUT_INVALID)
        action = decision["action"]
        if requested_wait:
            observation, facts = await perception.wait_for_change(observation)
            continue
        if action["type"] == "credential" and not decision["sensitive_regions"]:
            raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
        before = observation
        try:
            dispatched = await runtime.perform(action, observation, source="planner")
        except FrameworkError as exc:
            if exc.code != ErrorCode.STALE_OBSERVATION:
                raise
            progress["pending"] = None
            runtime.progress.save()
            runtime.host.record("gameplay.correction", {"reason": "stale-target-not-executed"})
            observation, facts = await perception.settle(before)
            continue
        pending = progress["pending"]
        pending["expected_outcome"] = decision["expected_outcome"]
        pending["image_ref"] = before["evidence_ref"]
        pending["screen_key"] = facts["screen_key"]
        pending["action_id"] = dispatched["action_id"]
        progress["paths"].append(
            {
                "run_id": runtime.run_id,
                "action_id": dispatched["action_id"],
                "before": before["observation_id"],
                "objective": decision["objective"],
                "status": "attempted",
            }
        )
        progress["paths"][-1]["type"] = action["type"]
        runtime.progress.save()
        observation, facts = await perception.settle(before)
