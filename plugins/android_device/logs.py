"""Bounded logcat windows keyed by verified process lifetime, never emulator logs."""

from runtime import same_runtime, snapshot


async def collect(device, request):
    before = await snapshot(device, request["package_name"])
    windows = []
    for process in before["processes"]:
        output = await device.run(
            "logcat",
            "-d",
            "-t",
            "200",
            "-v",
            "epoch",
            f"--pid={process['pid']}",
            allow_failure=True,
        )
        window = {
            "process": process,
            "text": output["stdout"],
            "available": output["exit_code"] == 0,
        }
        windows.append(window)
    after = await snapshot(device, request["package_name"])
    result = {
        "boot_id": before["boot_id"],
        "windows": windows,
        "lifetime_stable": same_runtime(before, after),
        "scope": "Exact package process names; isolated renderers are not attributed",
    }
    return result
