import os

from game_automation.common import FrameworkError, uid, within
from game_automation.contracts import validate
from game_automation.errors import ErrorCode

from .evidence import verify_image


async def discover_emulators(store, run_id, host, manifest, task):
    run_dir = store.run_dir(run_id)
    workspace_ref = f"artifacts/{uid('emulators')}"
    workspace = within(run_dir, workspace_ref)
    request = {
        "manager_executable": os.environ.get("GAME_AUTO_MUMU_MANAGER") or None,
        "adb_executable": os.environ.get("GAME_AUTO_ADB") or None,
        "instance_index": task["instance_index"],
        "capture": task["capture"],
        "workspace": str(workspace),
        "command_timeout_seconds": manifest["application"]["environment"][
            "command_timeout_seconds"
        ],
    }
    validate(run_dir / "contracts", "emulator-discovery-request", request)
    result = await host.call("emulator.discover", request, timeout=0)
    indices = [instance["index"] for instance in result["instances"]]
    if len(indices) != len(set(indices)):
        raise FrameworkError(ErrorCode.INVALID_MUMU_OUTPUT)
    selected = task["instance_index"]
    if selected is not None and indices != [selected]:
        raise FrameworkError(ErrorCode.INVALID_MUMU_OUTPUT)
    for instance in result["instances"]:
        image = instance["screenshot"]
        if image is None:
            if task["capture"] and instance["status"] == "ready":
                raise FrameworkError(ErrorCode.INVALID_SCREENSHOT)
            continue
        if image["file_name"] != f"instance_{instance['index']}.png":
            raise FrameworkError(ErrorCode.INVALID_SCREENSHOT)
        verify_image(run_dir, workspace_ref, image)
    ready = any(instance["status"] == "ready" for instance in result["instances"])
    if ready != (result["status"] == "ready"):
        raise FrameworkError(ErrorCode.INVALID_MUMU_OUTPUT)
    summary_ref = host.artifact(f"{workspace_ref}/discovery.json", result)
    result["summary_ref"] = summary_ref
    host.record("emulators.discovered", result)
    if not ready:
        raise FrameworkError(ErrorCode.ENVIRONMENT_BLOCKED, "No ready MuMu instance was confirmed")
    return result
