"""Bounded logcat windows keyed by verified process lifetime, never emulator logs."""

import re

from runtime import snapshot


def current_lifetime(data, start_seconds):
    retained = []
    keep = False
    recognized = False
    for line in data.decode("utf-8", errors="replace").splitlines():
        stamp = re.match(r"\s*\[?\s*(\d+\.\d+)\]?\s+", line)
        if stamp:
            recognized = True
            keep = float(stamp[1]) >= start_seconds
        if keep:
            retained.append(line)
    result = "\n".join(retained), recognized
    return result


async def collect(device, request):
    before = await snapshot(device, request["package_name"])
    frequency = await device.run("shell", "getconf", "CLK_TCK", allow_failure=True)
    frequency_text = frequency["stdout"].strip()
    ticks = int(frequency_text) if frequency_text.isdecimal() else 0
    windows = []
    for process in before["processes"]:
        output = await device.run(
            "logcat",
            "-d",
            "-t",
            "200",
            "-v",
            "monotonic",
            f"--pid={process['pid']}",
            allow_failure=True,
            raw=True,
        )
        if ticks:
            text, recognized = current_lifetime(output["data"], process["start_ticks"] / ticks)
        else:
            text, recognized = "", False
        window = {
            "process": process,
            "text": device.commands.sanitize(text),
            "available": output["exit_code"] == 0
            and ticks > 0
            and (recognized or not output["data"]),
        }
        windows.append(window)
    after = await snapshot(device, request["package_name"])
    identities = {(item["uid"], item["pid"], item["start_ticks"]) for item in after["processes"]}
    for window in windows:
        process = window["process"]
        if (
            before["boot_id"] != after["boot_id"]
            or (process["uid"], process["pid"], process["start_ticks"]) not in identities
        ):
            window.update(text="", available=False)
    result = {
        "boot_id": before["boot_id"],
        "windows": windows,
        "lifetime_stable": before["boot_id"] == after["boot_id"]
        and all(
            (item["uid"], item["pid"], item["start_ticks"]) in identities
            for item in before["processes"]
        ),
        "scope": "Exact package process names; isolated renderers are not attributed",
    }
    return result
