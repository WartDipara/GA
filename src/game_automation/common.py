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
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


# Defense in depth, not a guarantee that arbitrary prose never contains secrets.
SENSITIVE = re.compile(
    r"^(?:authorization|password|passwd|secret|api[_-]?key|(?:access_|refresh_)?token|cookie|set-cookie)$",
    re.I,
)
INLINE = re.compile(
    r"""(?i)((?:password|passwd|api[_-]?key|access_token|refresh_token|token|secret|authorization|cookie)\s*[=:]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&]+)"""
)


def redact(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if SENSITIVE.match(str(k))
            or re.sub(r"[^a-z]", "", str(k).lower()).endswith(
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
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
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
