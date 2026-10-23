"""Checkpointed batch orchestration over existing kernel execution boundaries."""

import tomllib
from dataclasses import dataclass, field

from game_automation.common import FrameworkError
from game_automation.contracts import validate
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.credentials import load_profile, profile_path

from .assembly import build_workflow
from .preprocessing import read_batch


@dataclass
class WorkflowContext:
    store: object
    run_id: str
    task: dict
    outputs: dict = field(default_factory=dict)
    apk_index: int | None = None


def workflow_task(root, source_file, instances, credentials, goal, max_turns, stages, adopt):
    submission = read_batch(source_file, root / "contracts/v1")
    application = tomllib.loads((root / "config/application.toml").read_text(encoding="utf-8"))
    task = {
        "kind": "apk_workflow",
        "source_ids": submission["source_ids"],
        "instances": instances or [],
        "credential_profile": credentials,
        "goal": goal,
        "max_turns": max_turns,
        "stages": stages if stages is not None else application["workflow"]["stages"],
        "adopt_existing": adopt,
    }
    validate_workflow(root, task)
    return task


def validate_workflow(root, task):
    validate(root / "contracts/v1", "workflow", task)
    stages = build_workflow(task["stages"])
    if any(stage.per_apk for stage in stages):
        instances = task["instances"]
        if len(instances) != len(task["source_ids"]) or len(set(instances)) != len(instances):
            raise FrameworkError(
                ErrorCode.WORKFLOW_INVALID, "Assign one distinct MuMu instance to each APK"
            )
    if any(stage.mode == "gameplay" for stage in stages):
        load_profile(profile_path(root, task["credential_profile"]))
    return stages


def workflow_status(store, run_id):
    steps = {}
    for event in store.iter_events(run_id):
        if event["kind"].startswith("workflow.stage."):
            payload = event["payload"]
            key = payload["key"]
            if event["kind"] == "workflow.stage.started":
                steps[key] = {}
            steps.setdefault(key, {}).update(payload)
    for step in steps.values():
        if step.get("child_run"):
            step["status"] = store.run(step["child_run"])["status"]
    result = {"run_id": run_id, "steps": list(steps.values())}
    return result
