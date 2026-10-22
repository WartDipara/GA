"""The only semantic planning entry; no device or database permission is exposed to the model."""

from copy import deepcopy

from game_automation.common import FrameworkError, within
from game_automation.contracts import schema, validate
from game_automation.errors import ErrorCode
from game_automation.prompting.snapshots import build_input

from .image_coordinates import device_decision, planner_image, planner_perception


class Planner:
    def __init__(self, runtime, manifest):
        self.runtime = runtime
        self.manifest = manifest
        self.turns = 0
        progress = runtime.progress.data
        self.coordinates_changed = progress.get("planner_coordinate_space") != "bounded-image-v1"
        if self.coordinates_changed:
            if progress.pop("semantic_stall", None):
                runtime.host.record(
                    "gameplay.stall.reset", {"reason": "planner-coordinate-space-changed"}
                )
            progress["planner_coordinate_space"] = "bounded-image-v1"
            runtime.progress.save()
        self.mapping_untried = not progress.get("planner_coordinate_action_dispatched", False)

    async def decide(self, observation, facts, reason):
        runtime = self.runtime
        runtime.begin_step(reason)
        directory = runtime.store.run_dir(runtime.run_id)
        workspace = directory / "ai-workspace"
        workspace.mkdir(exist_ok=True)
        progress = runtime.progress.data
        pending = progress["pending"]
        image_path = directory / "artifacts/planner" / f"{observation['observation_id']}.png"
        coordinates = planner_image(within(directory, observation["evidence_ref"]), image_path)
        progress_view = deepcopy(
            {key: value for key, value in progress.items() if key not in ("learned_steps", "paths")}
        )
        progress_view["sensitive_regions"] = [
            coordinates.rectangle(bounds) for bounds in progress["sensitive_regions"]
        ]
        last_action = runtime.store.latest_device_action(runtime.run_id)
        execution = None
        if last_action:
            execution = {
                key: last_action[key]
                for key in ("run_id", "action_id", "capability", "status", "result")
            }
        context = {
            "task": {
                "goal": runtime.task["goal"],
                "credential_profile": runtime.task["credential_profile"],
            },
            "observation_id": observation["observation_id"],
            "runtime": observation["runtime"],
            "perception": planner_perception(facts, coordinates),
            "progress": progress_view,
            "last_action_execution": execution,
            "image_coordinates": {observation["observation_id"]: coordinates.describe()},
            "planning_reason": reason,
            "execution_conditions": {
                "coordinate_mapping_corrected": self.mapping_untried,
                "coordinate_space": "planner_image_pixels",
            },
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
        images = [image_path]
        if pending and pending.get("image_ref"):
            previous = within(runtime.store.run_dir(pending["run_id"]), pending["image_ref"])
            previous_image = directory / "artifacts/planner" / f"{pending['observation_id']}.png"
            previous_coordinates = planner_image(previous, previous_image)
            images.append(previous_image)
            context["image_order"].append(pending["observation_id"])
            context["image_coordinates"][pending["observation_id"]] = (
                previous_coordinates.describe()
            )
            progress_view["pending"]["action"] = previous_coordinates.action(pending["action"])
        thread = runtime.store.session(runtime.run_id, "planner") if self.turns % 30 else None
        turn = build_input(
            runtime.store,
            runtime.run_id,
            "battle" if runtime.task["goal"] == "advance-battle" else "exploration",
            context,
            schema(runtime.host.contracts, "gameplay-decision"),
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
        validate(runtime.host.contracts, "gameplay-decision", decision)
        if decision["observation_id"] != observation["observation_id"]:
            raise FrameworkError(ErrorCode.STALE_OBSERVATION)
        if observation["observation_id"] not in decision["evidence_refs"]:
            raise FrameworkError(ErrorCode.AI_OUTPUT_INVALID)
        geometry = {
            "observation_id": observation["observation_id"],
            "image_ref": image_path.relative_to(directory).as_posix(),
            "coordinates": coordinates.describe(),
            "planner_action": decision["action"],
        }
        runtime.host.record("gameplay.planner.coordinates", geometry, span_id=runtime.step_id)
        decision = device_decision(decision, coordinates)
        validate(runtime.host.contracts, "gameplay-decision", decision)
        runtime.host.record("gameplay.decision", decision, span_id=runtime.step_id)
        self.turns += 1
        return decision
