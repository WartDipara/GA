"""Export the existing authoritative error enum without importing the runtime."""

import ast
import json
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "src/game_automation/errors.py"
module = ast.parse(source.read_text(encoding="utf-8"))
codes = {}
for declaration in module.body:
    if not isinstance(declaration, ast.ClassDef) or declaration.name != "ErrorCode":
        continue
    for member in declaration.body:
        if isinstance(member, ast.Assign) and isinstance(member.targets[0], ast.Name):
            value = ast.literal_eval(member.value)
            codes[member.targets[0].id] = {"code": value[0], "message": value[1]}
print(json.dumps(codes))
