import sys
import tomllib
from pathlib import Path

from game_automation.common import FrameworkError
from game_automation.contracts import validate
from game_automation.errors import ErrorCode


def load_plugins(root: Path):
    registry = tomllib.loads((root / "config/plugins.toml").read_text(encoding="utf-8"))
    plugins = {}
    for name, entry in registry["plugins"].items():
        if not entry.get("enabled", False):
            continue
        manifest_path = (root / entry["manifest"]).resolve()
        if not manifest_path.is_relative_to(root):
            raise FrameworkError(ErrorCode.INVALID_PATH, "Manifest outside project")
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        validate(root / "contracts/v1", "plugin", manifest)
        if manifest["name"] != name:
            raise FrameworkError(ErrorCode.INVALID_PLUGIN)
        manifest["command"] = [
            argument.replace("{python}", sys.executable).replace(
                "{plugin}", str(manifest_path.parent)
            )
            for argument in manifest["command"]
        ]
        manifest["cwd"] = str(manifest_path.parent)
        plugins[name] = manifest
    return plugins


class Router:
    def __init__(self, plugins):
        self.providers = {}
        for plugin in plugins.values():
            for capability in plugin["capabilities"]:
                if capability in self.providers:
                    raise FrameworkError(ErrorCode.AMBIGUOUS_CAPABILITY, capability)
                self.providers[capability] = plugin

    def resolve(self, capability):
        if capability not in self.providers:
            raise FrameworkError(
                ErrorCode.MISSING_CAPABILITY, f"Required capability unavailable: {capability}"
            )
        plugin = self.providers[capability]
        for dependency in plugin["required_capabilities"]:
            if dependency not in self.providers:
                raise FrameworkError(
                    ErrorCode.MISSING_CAPABILITY, f"{plugin['name']} requires {dependency}"
                )
        return plugin
