import json
import re

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
