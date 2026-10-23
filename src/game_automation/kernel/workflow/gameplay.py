"""Build gameplay CLI inputs; execution is owned by the TypeScript control plane."""

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.credentials import load_profile, profile_path


def gameplay_task(store, prepared_run, credential_profile, goal, actions=None, max_turns=0):
    if store.run(prepared_run)["status"] != "succeeded":
        raise FrameworkError(ErrorCode.DEVICE_APK_REQUIRED)
    prepared = [
        event["payload"]
        for event in store.events(prepared_run)
        if event["kind"] == "device.prepared"
    ]
    if not prepared:
        raise FrameworkError(ErrorCode.DEVICE_APK_REQUIRED)
    device = prepared[-1]
    profile = profile_path(store.root, credential_profile)
    load_profile(profile)
    task = {
        "kind": "gameplay",
        "target": device["metadata"]["packageName"],
        "prepared_run": prepared_run,
        "source_run": device["source_run"],
        "apk_index": device["apk_index"],
        "instance_index": device["instance_index"],
        "instance_id": device["instance_id"],
        "metadata": device["metadata"],
        "credential_profile": credential_profile,
        "goal": goal,
        "actions": actions or [],
        "max_turns": max_turns,
    }
    return task
