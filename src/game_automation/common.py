from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from game_automation.errors import ErrorCode


class FrameworkError(Exception):
    def __init__(
        self, code: ErrorCode | str, message: str | None = None, *, retryable: bool = False
    ):
        if message is None:
            message = code.message if isinstance(code, ErrorCode) else "Remote operation failed"
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def as_dict(self):
        error_details = {"code": self.code, "message": str(self), "retryable": self.retryable}
        return error_details


class FileLock:
    """OS-owned exclusive lock shared by the kernel and isolated plugin workers."""

    def __init__(self, path: Path, busy_code=ErrorCode.RUN_BUSY):
        self.path = path
        self.busy_code = busy_code

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        if os.fstat(self.file.fileno()).st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise FrameworkError(self.busy_code) from exc
        return self

    def __exit__(self, *_):
        self.file.close()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def skill_prefix(contents):
    """Materialize trusted frozen instructions without relying on provider discovery."""
    return (
        "\n\n".join(
            f'<runtime_skill name="{name}">\n{text}\n</runtime_skill>' for name, text in contents
        )
        + "\n\n"
    )


# Defense in depth, not a guarantee that arbitrary prose never contains secrets.
SENSITIVE_FIELD_PATTERN = re.compile(
    r"^(?:authorization|password|passwd|secret|api[_-]?key|(?:access_|refresh_)?token|cookie|set-cookie)$",
    re.I,
)
INLINE_SECRET_PATTERN = re.compile(
    r"(?i)((?:password|passwd|api[_-]?key|access_token|refresh_token|token|secret|"
    r"authorization|cookie)[\"']?\s*[=:]\s*)"
    r"""(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&]+)"""
)
SENSITIVE_FIELD_SUFFIXES = (
    "password",
    "passwd",
    "secret",
    "apikey",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "cookie",
)


def redact(value):
    if isinstance(value, dict):
        redacted_fields = {}
        for field_name, field_value in value.items():
            normalized_name = re.sub(r"[^a-z]", "", str(field_name).lower())
            sensitive_name = SENSITIVE_FIELD_PATTERN.match(str(field_name))
            sensitive_suffix = normalized_name.endswith(SENSITIVE_FIELD_SUFFIXES)
            sensitive_field = sensitive_name or sensitive_suffix
            redacted_fields[field_name] = "[REDACTED]" if sensitive_field else redact(field_value)
        return redacted_fields
    if isinstance(value, list):
        redacted_value = [redact(item) for item in value]
        return redacted_value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, (dict, list)):
                return json.dumps(redact(decoded), ensure_ascii=False)
        except (ValueError, TypeError):
            pass
        value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        return INLINE_SECRET_PATTERN.sub(r"\1[REDACTED]", value)
    return value


def within(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise FrameworkError(ErrorCode.INVALID_PATH)
    return candidate


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(json_text(value), encoding="utf-8")
    temporary_path.replace(path)
