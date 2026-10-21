from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


class FrameworkError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable

    def as_dict(self):
        error_details = {"code": self.code, "message": str(self), "retryable": self.retryable}
        return error_details


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
SENSITIVE = re.compile(
    r"^(?:authorization|password|passwd|secret|api[_-]?key|(?:access_|refresh_)?token|cookie|set-cookie)$",
    re.I,
)
INLINE = re.compile(
    r"(?i)((?:password|passwd|api[_-]?key|access_token|refresh_token|token|secret|"
    r"authorization|cookie)\s*[=:]\s*)"
    r"""(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&]+)"""
)


def redact(value):
    if isinstance(value, dict):
        redacted_value = {
            field_name: "[REDACTED]"
            if SENSITIVE.match(str(field_name))
            or re.sub(r"[^a-z]", "", str(field_name).lower()).endswith(
                (
                    "password",
                    "passwd",
                    "secret",
                    "apikey",
                    "accesstoken",
                    "refreshtoken",
                    "authorization",
                    "cookie",
                )
            )
            else redact(field_value)
            for field_name, field_value in value.items()
        }
        return redacted_value
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
        return INLINE.sub(r"\1[REDACTED]", value)
    return value


def within(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise FrameworkError("INVALID_PATH", "Path escapes its assigned root")
    return candidate


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json_text(value), encoding="utf-8")
    temp.replace(path)
