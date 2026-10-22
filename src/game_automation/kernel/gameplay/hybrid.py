"""One observation/execution loop for login, exploration and later battle goals."""

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode

from .battle import NativeBattleProgress
from .knowledge import KnowledgeCache
from .local import Perception, execute_routine
from .planning import Planner
from .routing import DecisionRouter


async def run_hybrid(runtime, manifest, observation):
    perception = Perception(runtime)
    planner = Planner(runtime, manifest)
    router = DecisionRouter()
    cache = KnowledgeCache(runtime)
    battle = NativeBattleProgress(runtime) if runtime.task["goal"] == "advance-battle" else None
    progress = runtime.progress.data
    observation, facts = await perception.settle(observation)
    last_state = None
    repeated = 0
    cached_steps = 0
    while True:
        runtime.recorder.admit()
        runtime.host.check_cancel()
        limit = runtime.task.get("max_turns", 0)
        if limit and planner.turns >= limit:
            raise FrameworkError(
                ErrorCode.BUDGET_EXHAUSTED,
                "Requested planning-turn boundary reached; resume to continue",
            )
        if not progress["pending"] and cached_steps < 5:
            steps = cache.routine(observation, facts)[: 5 - cached_steps]
            if steps:
                runtime.host.record("gameplay.cache.hit", {"steps": len(steps)})
                try:
                    observation = await execute_routine(runtime, observation, steps)
                    cached_steps += len(steps)
                    facts = await perception.analyze(observation)
                except FrameworkError as exc:
                    if exc.code not in (ErrorCode.GAMEPLAY_BLOCKED, ErrorCode.STALE_OBSERVATION):
                        raise
                    runtime.host.record("gameplay.cache.invalidated", {"reason": exc.code})
                    cache.entries.clear()
                    observation, facts = await perception.settle(observation)
                continue
        route, reason = router.route(observation, facts, None)
        if progress.get("semantic_stall", {}).get("count", 0) >= 3:
            reason = "stalled-semantic-state-and-goal"
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
        state_key = decision.get("state_key")
        if state_key:
            previous_stall = progress.get("semantic_stall", {})
            same_state = (
                previous_stall.get("state_key") == state_key
                and previous_stall.get("objective") == decision["objective"]
            )
            count = previous_stall.get("count", 0) + 1 if same_state else 1
            if decision.get("progress_observed"):
                count = 1
            progress["semantic_stall"] = {
                "state_key": state_key,
                "objective": decision["objective"],
                "count": count,
            }
            if count >= 3:
                runtime.host.record("gameplay.stall", progress["semantic_stall"])
            if count >= 5:
                runtime.progress.save()
                raise FrameworkError(
                    ErrorCode.GAMEPLAY_BLOCKED,
                    "Semantic state stalled after two recovery opportunities",
                )
        cached_steps = 0
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
                "observation_run": runtime.run_id,
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
            if assessment["status"] == "verified":
                cache.learn(pending, observation, facts)
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
        if battle:
            battle.update(decision.get("battle_progress"), observation)
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
            if battle and not battle.goal_verified():
                raise FrameworkError(
                    ErrorCode.GAMEPLAY_BLOCKED,
                    "Battle completion requires settlement and a new stage marker",
                )
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
            if runtime.task["goal"] == "login-and-interact" and not all(
                progress["identity"].get(key) for key in ("small_account", "server", "role")
            ):
                raise FrameworkError(
                    ErrorCode.GAMEPLAY_BLOCKED,
                    "Login completion requires the full observed identity",
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
        if limit and planner.turns >= limit:
            raise FrameworkError(
                ErrorCode.BUDGET_EXHAUSTED,
                "Planning boundary reached before dispatching another action",
            )
        action = decision["action"]
        if requested_wait:
            minimum = 30 if battle and battle.data["phase"] == "active" else 0
            observation, facts = await perception.wait_for_change(
                observation, minimum_seconds=minimum
            )
            continue
        if action["type"] == "credential" and not decision["sensitive_regions"]:
            raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
        before = observation
        try:
            dispatched = await runtime.perform(action, observation, source="planner")
            progress["planner_coordinate_action_dispatched"] = True
            planner.mapping_untried = False
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
        pending["risk"] = decision["risk"]
        pending["cache_scope"] = cache.scope(before)
        pending["cache_intent"] = cache.intent()
        pending["image_sha256"] = before["screenshot"]["sha256"]
        pending["sensitive_regions"] = before["sensitive_regions"]
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
