"""Current-user Windows credential protection; no plaintext persistence or subprocess arguments."""

import base64
import ctypes
import json
import os
import re
from ctypes import wintypes
from pathlib import Path

from game_automation.common import FrameworkError, atomic_json
from game_automation.errors import ErrorCode


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, *, decrypt=False):
    if os.name != "nt":
        raise FrameworkError(ErrorCode.UNSUPPORTED_PLATFORM)
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
        )
    else:
        ok = crypt.CryptProtectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
        )
    if not ok:
        raise FrameworkError(ErrorCode.CREDENTIAL_UNAVAILABLE)
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def profile_path(root, name):
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", name):
        raise FrameworkError(ErrorCode.CREDENTIAL_UNAVAILABLE)
    return Path(root) / "var" / "secrets" / f"{name}.json"


def save_profile(root, name, username, password):
    if not username or not password or any(c in username + password for c in "\r\n\x00"):
        raise FrameworkError(ErrorCode.CREDENTIAL_UNAVAILABLE)
    values = {"username": username, "password": password}
    ciphertext = protect(json.dumps(values).encode("utf-8"))
    envelope = {
        "protection": "windows-current-user-dpapi",
        "version": 1,
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    atomic_json(profile_path(root, name), envelope)


def load_profile(path):
    try:
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        if envelope["protection"] != "windows-current-user-dpapi":
            raise ValueError
        values = json.loads(protect(base64.b64decode(envelope["ciphertext"]), decrypt=True))
        if set(values) != {"username", "password"} or not all(
            isinstance(value, str) and value and "\x00" not in value for value in values.values()
        ):
            raise ValueError
        return values
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FrameworkError(ErrorCode.CREDENTIAL_UNAVAILABLE) from exc


def scrub(text, values):
    for value in sorted(values.values(), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return text
