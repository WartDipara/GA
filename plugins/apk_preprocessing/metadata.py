import asyncio
import os
import re
import shutil
from pathlib import Path

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.rpc import spawn, terminate


def resolve_aapt2(configured):
    if configured:
        executable = shutil.which(configured)
        if executable:
            return executable
        raise FrameworkError(
            ErrorCode.AAPT2_UNAVAILABLE, "Configured AAPT2 executable is unavailable"
        )
    executable = shutil.which("aapt2")
    if executable:
        return executable
    roots = [
        Path(value)
        for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT")
        if (value := os.environ.get(key))
    ]
    if local := os.environ.get("LOCALAPPDATA"):
        roots.append(Path(local) / "Android" / "Sdk")
    filename = "aapt2.exe" if os.name == "nt" else "aapt2"
    for root in roots:
        candidates = list((root / "build-tools").glob(f"*/{filename}"))
        candidates.sort(
            key=lambda path: tuple(int(value) for value in re.findall(r"\d+", path.parent.name)),
            reverse=True,
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
    raise FrameworkError(ErrorCode.AAPT2_UNAVAILABLE)


async def inspect_apk(apk, configured, event):
    executable = resolve_aapt2(configured)
    command = [executable, "dump", "badging", str(apk)]
    await event("metadata.started", {"arguments": command})
    process = None
    try:
        async with asyncio.timeout(60):
            process = await spawn(command, cwd=str(apk.parent / "work"))
            stdout, stderr = await process.communicate()
    except TimeoutError as exc:
        raise FrameworkError(ErrorCode.AAPT2_TIMEOUT) from exc
    except OSError as exc:
        raise FrameworkError(
            ErrorCode.AAPT2_UNAVAILABLE, "Cannot start the AAPT2 executable"
        ) from exc
    finally:
        if process is not None:
            await terminate(process)
    output = stdout.decode("utf-8", errors="replace")
    evidence = {
        "exit_code": process.returncode,
        "stdout": output,
        "stderr": stderr.decode("utf-8", errors="replace"),
    }
    await event("metadata.completed", evidence)
    if process.returncode != 0:
        raise FrameworkError(ErrorCode.APK_PARSE_FAILED)
    package_line = next((line for line in output.splitlines() if line.startswith("package: ")), "")
    fields = dict(re.findall(r"([A-Za-z]+)='([^']*)'", package_line))
    package = fields.get("name", "")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", package):
        raise FrameworkError(ErrorCode.APK_PACKAGE_MISSING)
    label = re.search(r"^application-label:'(.*)'$", output, re.M)
    metadata = {
        "apkName": label.group(1) if label and label.group(1) else package,
        "packageName": package,
        "versionName": fields.get("versionName") or None,
        "versionCode": fields.get("versionCode") or None,
    }
    return metadata
