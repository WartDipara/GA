"""Resolve version-checked shared resources and read legacy execution snapshots."""

import json
from pathlib import Path

from game_automation.common import FrameworkError, digest, within
from game_automation.errors import ErrorCode


def read_manifest(store, run_id):
    path = store.run_dir(run_id) / "manifest.json"
    if not path.is_file():
        raise FrameworkError(ErrorCode.SNAPSHOT_MISSING)
    snapshots = store.events(run_id, kinds=("run.snapshot",), limit=1)
    data = path.read_bytes()
    if not snapshots or snapshots[0]["payload"]["manifest_sha256"] != digest(data):
        raise FrameworkError(ErrorCode.SNAPSHOT_CHANGED)
    return json.loads(data)


def resource_root(store, run_id, kind, *, manifest=None):
    if kind not in ("skills", "contracts"):
        raise FrameworkError(ErrorCode.INVALID_PATH)
    if manifest is None:
        manifest = read_manifest(store, run_id)
    roots = manifest.get("resource_roots")
    root = Path(roots[kind]).resolve() if roots else store.run_dir(run_id) / kind
    prefix = f"{kind}/"
    expected = {
        name.removeprefix(prefix): value
        for name, value in manifest["files"].items()
        if name.startswith(prefix)
    }
    if not expected:
        raise FrameworkError(ErrorCode.SNAPSHOT_CHANGED, f"Missing {kind} dependency hashes")
    folders = (
        {within(root, name.split("/", 1)[0]) for name in expected} if kind == "skills" else {root}
    )
    actual = {}
    for folder in folders:
        for path in folder.rglob("*"):
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise FrameworkError(ErrorCode.SNAPSHOT_CHANGED, f"Invalid {kind} dependency path")
            if path.is_file():
                actual[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    if actual != expected:
        raise FrameworkError(
            ErrorCode.SNAPSHOT_CHANGED,
            f"{kind} dependency changed or is missing; create a new execution batch",
        )
    return root
