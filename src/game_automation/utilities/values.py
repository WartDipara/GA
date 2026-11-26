import hashlib
import json
import uuid
from datetime import datetime, timezone


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
