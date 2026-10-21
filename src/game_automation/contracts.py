import json
from pathlib import Path

from jsonschema import Draft202012Validator

from game_automation.common import FrameworkError


def schema(root: Path, name: str):
    return json.loads((root / f"{name}.schema.json").read_text(encoding="utf-8"))


def validate(root: Path, name: str, value):
    validator = Draft202012Validator(schema(root, name))
    errors = sorted(validator.iter_errors(value), key=lambda error: str(error.path))
    if errors:
        # Do not echo invalid values: they can contain credentials.
        raise FrameworkError("INVALID_CONTRACT", f"{name}: invalid field at {list(errors[0].path)}")
