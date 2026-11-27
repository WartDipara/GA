"""Build CLI workflow submissions and project retained stage status."""

import tomllib

from .preprocessing import read_batch


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
    return task


def workflow_status(store, run_id):
    steps = {}
    device_preflight = None
    for event in store.iter_events(run_id):
        if event["kind"] == "workflow.device.preflight":
            device_preflight = event["payload"]
        if event["kind"].startswith("workflow.stage."):
            payload = event["payload"]
            key = payload["key"]
            if event["kind"] == "workflow.stage.started":
                steps[key] = {}
            steps.setdefault(key, {}).update(payload)
    for step in steps.values():
        if step.get("child_run"):
            step["child_status"] = store.run(step["child_run"])["status"]
    result = {"run_id": run_id, "steps": list(steps.values())}
    if device_preflight is not None:
        result["device_preflight"] = device_preflight
    return result
