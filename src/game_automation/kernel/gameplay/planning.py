"""The only semantic planning entry; no device or database permission is exposed to the model."""

from game_automation.common import FrameworkError, within
from game_automation.contracts import schema, validate
from game_automation.errors import ErrorCode
from game_automation.prompting.snapshots import build_input


class Planner:
    def __init__(self, runtime, manifest):
        self.runtime = runtime
        self.manifest = manifest
        self.turns = 0

    async def decide(self, observation, facts, reason):
        runtime = self.runtime
        runtime.begin_step(reason)
        directory = runtime.store.run_dir(runtime.run_id)
        workspace = directory / "ai-workspace"
        workspace.mkdir(exist_ok=True)
        progress = runtime.progress.data
        pending = progress["pending"]
        context = {
            "task": {
                "goal": runtime.task["goal"],
                "credential_profile": runtime.task["credential_profile"],
            },
            "observation_id": observation["observation_id"],
            "visible_account_matches_credential_profile": observation.get(
                "credential_profile_match"
            ),
            "runtime": observation["runtime"],
            "perception": facts,
            "progress": {key: value for key, value in progress.items() if key != "learned_steps"},
            "planning_reason": reason,
            "image_order": [observation["observation_id"]],
            "available_actions": [
                "tap",
                "swipe",
                "long_press",
                "back",
                "wait",
                "credential",
                "return",
                "launch",
            ],
        }
        images = [within(directory, observation["evidence_ref"])]
        if pending and pending.get("image_ref"):
            previous = within(runtime.store.run_dir(pending["run_id"]), pending["image_ref"])
            images.append(previous)
            context["image_order"].append(pending["observation_id"])
        thread = runtime.store.session(runtime.run_id, "planner") if self.turns % 30 else None
        turn = build_input(
            runtime.store,
            runtime.run_id,
            "battle" if runtime.task["goal"] == "advance-battle" else "exploration",
            context,
            schema(directory / "contracts", "gameplay-decision"),
            images=images,
        )
        params = {
            "codex": self.manifest["application"]["codex"],
            "workspace": str(workspace),
            "role": "planner",
            "thread_id": thread,
            "turn": turn,
        }
        runtime.host.record(
            "gameplay.planner.requested",
            {
                "observation_id": observation["observation_id"],
                "reason": reason,
                "thread_reused": thread is not None,
            },
        )
        result = await runtime.host.call("ai.decide", params, parent_span_id=runtime.step_id)
        decision = result["decision"]
        validate(directory / "contracts", "gameplay-decision", decision)
        if decision["observation_id"] != observation["observation_id"]:
            raise FrameworkError(ErrorCode.STALE_OBSERVATION)
        if observation["observation_id"] not in decision["evidence_refs"]:
            raise FrameworkError(ErrorCode.AI_OUTPUT_INVALID)
        runtime.host.record("gameplay.decision", decision, span_id=runtime.step_id)
        self.turns += 1
        return decision
