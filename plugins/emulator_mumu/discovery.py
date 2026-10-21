import ipaddress
import json
import os
import re
import shutil
from pathlib import Path

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode

PROCESS_QUERY = (
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "Get-CimInstance Win32_Process -Filter "
    "\"Name='MuMuNxMain.exe' OR Name='MuMuPlayer.exe' OR Name='MuMuNxDevice.exe'\" "
    "| Select-Object -ExpandProperty ExecutablePath | Where-Object { $_ } "
    "| Sort-Object -Unique | ConvertTo-Json -Compress"
)


def registry_directories():
    import winreg

    directories = []
    key_name = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, index)) as entry:
                                name = winreg.QueryValueEx(entry, "DisplayName")[0]
                                if "mumu" not in name.lower():
                                    continue
                                for field in ("InstallLocation", "DisplayIcon"):
                                    try:
                                        value = winreg.QueryValueEx(entry, field)[0].strip()
                                    except OSError:
                                        continue
                                    if not value:
                                        continue
                                    if field == "DisplayIcon":
                                        match = re.match(r'^"([^"]+)"', value)
                                        value = match[1] if match else value.rsplit(",", 1)[0]
                                        directories.append(Path(value).parent)
                                    else:
                                        directories.append(Path(value))
                        except OSError:
                            continue
            except OSError:
                continue
    return directories


async def resolve_tools(commands, request):
    if os.name != "nt":
        raise FrameworkError(ErrorCode.UNSUPPORTED_PLATFORM, "MuMu discovery requires Windows")
    configured = request["manager_executable"]
    if configured:
        manager = shutil.which(configured)
        if manager is None:
            raise FrameworkError(ErrorCode.MUMU_UNAVAILABLE)
    else:
        directories = []
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell:
            command = [powershell, "-NoProfile", "-NonInteractive", "-Command", PROCESS_QUERY]
            result = await commands.run(command)
            paths = json.loads(result["stdout"]) if result["stdout"].strip() else []
            if isinstance(paths, str):
                paths = [paths]
            if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
                raise FrameworkError(ErrorCode.INVALID_MUMU_OUTPUT)
            directories = [Path(path).parent for path in paths]
        candidates = manager_candidates(directories)
        if not candidates:
            candidates = manager_candidates(registry_directories())
        if not candidates:
            raise FrameworkError(ErrorCode.MUMU_UNAVAILABLE)
        if len(candidates) != 1:
            raise FrameworkError(ErrorCode.MUMU_SELECTION_REQUIRED)
        manager = candidates[0]
    manager = Path(manager).resolve()
    adb = request["adb_executable"]
    if adb:
        adb = shutil.which(adb)
    else:
        adb = shutil.which("adb") or shutil.which(str(manager.with_name("adb.exe")))
    if not adb:
        raise FrameworkError(ErrorCode.ADB_UNAVAILABLE)
    tools = {"manager": str(manager), "adb": str(Path(adb).resolve())}
    return tools


def manager_candidates(directories):
    candidates = {}
    for directory in directories:
        for relative in ("MuMuManager.exe", "shell/MuMuManager.exe", "nx_main/MuMuManager.exe"):
            path = (directory / relative).resolve()
            if path.is_file():
                candidates[str(path).casefold()] = str(path)
    results = list(candidates.values())
    return results


def adb_endpoint(instance):
    try:
        address = ipaddress.ip_address(instance["adb_host_ip"])
        port = instance["adb_port"]
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError
        if not address.is_private and not address.is_loopback:
            raise ValueError
    except (ValueError, KeyError, TypeError) as exc:
        raise FrameworkError(ErrorCode.INVALID_MUMU_OUTPUT) from exc
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"{host}:{port}"
