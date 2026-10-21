import json

import pytest
from jsonschema import Draft202012Validator

from game_automation.common import FrameworkError
from game_automation.contracts import validate
from game_automation.kernel.capability_router import Router, load_plugins


def test_all_schemas_and_example(project):
    for path in (project / "contracts/v1").glob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text()))
    validate(
        project / "contracts/v1",
        "task",
        json.loads((project / "contracts/v1/examples/task.json").read_text(encoding="utf-8")),
    )
    load_plugins(project)


def test_invalid_decision_cannot_validate(project):
    with pytest.raises(FrameworkError, match="decision"):
        validate(project / "contracts/v1", "decision", {"reason_summary": "looks good"})


def test_missing_and_optional_capabilities(project):
    plugins = load_plugins(project)
    fixture = plugins["fixture"]
    fixture["optional_capabilities"] = ["not.installed"]
    router = Router({"fixture": fixture})
    assert router.resolve("fixture.echo")["name"] == "fixture"
    with pytest.raises(FrameworkError, match="Required capability"):
        router.resolve("android.observe")
    fixture["required_capabilities"] = ["not.installed"]
    with pytest.raises(FrameworkError, match="requires"):
        router.resolve("fixture.echo")


def test_incompatible_protocol(project):
    path = project / "plugins/agent_codex/plugin.toml"
    path.write_text(
        path.read_text().replace('protocol_version = "1.0"', 'protocol_version = "2.0"')
    )
    with pytest.raises(FrameworkError):
        load_plugins(project)
