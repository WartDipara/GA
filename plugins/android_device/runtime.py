"""Target process identity is independent of the currently focused logical display."""

import re

from display import focused_display

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


def same_runtime(expected, current):
    keys = ("boot_id", "focused_display")
    old = expected["main_process"]
    new = current["main_process"]
    identity = ("pid", "uid", "start_ticks")
    process_matches = old is None and new is None
    if old is not None and new is not None:
        process_matches = all(old[key] == new[key] for key in identity)
    return process_matches and all(expected[key] == current[key] for key in keys)


async def snapshot(device, package):
    boot = await device.run("shell", "cat", "/proc/sys/kernel/random/boot_id")
    listing = await device.run("shell", "ps", "-A", "-o", "PID,UID,STAT,NAME")
    processes = []
    for line in listing["stdout"].splitlines()[1:]:
        fields = line.split()
        if len(fields) != 4 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        pid, user, state, name = fields
        if name != package and not name.startswith(package + ":"):
            continue
        stat = await device.run("shell", "cat", f"/proc/{pid}/stat", allow_failure=True)
        if stat["exit_code"]:
            continue
        tail = stat["stdout"].rsplit(")", 1)[-1].split()
        if len(tail) < 20:
            raise FrameworkError(ErrorCode.INVALID_ADB_OUTPUT)
        process = {
            "pid": int(pid),
            "uid": int(user),
            "name": name,
            "state": state,
            "start_ticks": int(tail[19]),
        }
        processes.append(process)
    windows = await device.run("shell", "dumpsys", "window")
    try:
        display = focused_display(windows["stdout"])
    except FrameworkError as exc:
        if exc.code != ErrorCode.DEVICE_OBSERVATION_UNSTABLE:
            raise
        display = None
    activities = await device.run("shell", "dumpsys", "activity", "activities")
    tasks = []
    current_display = None
    for line in activities["stdout"].splitlines():
        header = re.match(r"\s*Display #(\d+)", line)
        if header:
            current_display = int(header[1])
        task = re.search(r"Task\{[^\n]*?#(\d+)\b", line)
        if task and package in line and current_display is not None:
            entry = {"task_id": int(task[1]), "display_id": current_display}
            if entry not in tasks:
                tasks.append(entry)
    main = next((entry for entry in processes if entry["name"] == package), None)
    alive = main is not None and not main["state"].startswith("Z")
    result = {
        "boot_id": boot["stdout"].strip(),
        "package_name": package,
        "main_process": main,
        "processes": processes,
        "tasks": tasks,
        "alive": alive,
        "focused_display": display,
    }
    return result


async def inspect_runtime(device, request):
    result = {"session": device.session, "runtime": await snapshot(device, request["package_name"])}
    return result
