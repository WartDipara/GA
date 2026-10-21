import shutil
from pathlib import Path

import pytest

from game_automation.kernel.capability_router import Router
from game_automation.kernel.plugin_host import Host
from game_automation.kernel.workflow import new_run
from game_automation.prompting import load
from game_automation.storage import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    for name in ("config", "contracts", "skills", "plugins", "src", "tests/fixtures"):
        shutil.copytree(
            ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__", ".venv")
        )
    return tmp_path


@pytest.fixture
def store(project):
    instance = Store(project)
    yield instance
    instance.close()


@pytest.fixture
def task():
    return {
        "objective": "fixture validation",
        "platform": "fixture",
        "target": "synthetic",
        "scope": ["transport"],
        "limits": {"max_actions": 10, "timeout_seconds": 30},
        "constraints": ["no device"],
    }


@pytest.fixture
def run_id(store, task):
    return new_run(store, store.submit(task), "demo")


@pytest.fixture
def host(store, run_id):
    manifest = load(store, run_id)
    return Host(store, run_id, Router(manifest["plugins"]), manifest)
