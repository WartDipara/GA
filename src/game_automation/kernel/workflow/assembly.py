"""The composition root for the APK workflow; stage implementations stay independent."""

from dataclasses import dataclass
from typing import Callable

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode

from .device import preparation_task
from .gameplay import gameplay_task


@dataclass(frozen=True)
class Stage:
    name: str
    mode: str | None
    inputs: Callable
    requires: tuple[str, ...] = ()
    per_apk: bool = False
    capabilities: tuple[str, ...] = ()


class WorkflowBuilder:
    def __init__(self):
        self.stages = []

    def add(self, stage):
        self.stages.append(stage)
        return self

    def build(self):
        available = set()
        item_stages = set()
        item_group_closed = False
        for stage in self.stages:
            missing = set(stage.requires) - available
            if stage.name in available or missing:
                raise FrameworkError(
                    ErrorCode.WORKFLOW_INVALID,
                    f"Stage {stage.name}: duplicate name or missing dependencies {sorted(missing)}",
                )
            if stage.per_apk:
                if item_group_closed:
                    raise FrameworkError(
                        ErrorCode.WORKFLOW_INVALID, "APK stages must form one contiguous group"
                    )
                item_stages.add(stage.name)
            else:
                if set(stage.requires) & item_stages:
                    raise FrameworkError(
                        ErrorCode.WORKFLOW_INVALID, "Batch stages cannot consume one APK's output"
                    )
                item_group_closed = bool(item_stages)
            available.add(stage.name)
        if not available:
            raise FrameworkError(ErrorCode.WORKFLOW_INVALID, "Select at least one stage")
        stages = tuple(self.stages)
        return stages


def preprocessing_input(context):
    task = {"kind": "apk_batch", "source_ids": context.task["source_ids"]}
    return task


def environment_input(context):
    task = {
        "kind": "emulator_discovery",
        "target": "mumu",
        "instance_index": context.task["instances"][context.apk_index],
        "capture": False,
    }
    return task


def preparation_input(context):
    return preparation_task(
        context.store,
        context.outputs["preprocess"],
        context.apk_index,
        context.task["instances"][context.apk_index],
        context.task["adopt_existing"],
    )


def gameplay_input(context, goal):
    return gameplay_task(
        context.store,
        context.outputs["prepare"],
        context.task["credential_profile"],
        goal,
        max_turns=context.task["max_turns"],
    )


def launch_input(context):
    return gameplay_input(context, "activate-game")


def play_input(context):
    return gameplay_input(context, context.task["goal"])


def report_output(context):
    request = {"publication": "after_business_outcome"}
    context.store.event(context.run_id, "workflow.report.requested", request)
    return request


def build_workflow(names):
    """Add, remove or replace modules here; the scheduler has no stage-name branches."""
    modules = (
        Stage(
            "preprocess",
            "preprocessing",
            preprocessing_input,
            capabilities=("apk.preflight", "apk.plan", "apk.prepare"),
        ),
        Stage(
            "environment",
            "emulators",
            environment_input,
            requires=("preprocess",),
            per_apk=True,
            capabilities=("emulator.discover",),
        ),
        Stage(
            "prepare",
            "device",
            preparation_input,
            requires=("preprocess", "environment"),
            per_apk=True,
            capabilities=("emulator.session", "device.inspect", "device.install"),
        ),
        Stage(
            "launch",
            "gameplay",
            launch_input,
            requires=("prepare",),
            per_apk=True,
            capabilities=("device.capture", "device.execute", "device.logs"),
        ),
        Stage(
            "play",
            "gameplay",
            play_input,
            requires=("prepare", "launch"),
            per_apk=True,
            capabilities=("ai.decide", "perception.analyze"),
        ),
        Stage("report", None, report_output),
    )
    registered = {stage.name: stage for stage in modules}
    builder = WorkflowBuilder()
    for name in names:
        if name not in registered:
            raise FrameworkError(ErrorCode.WORKFLOW_INVALID, f"Unknown stage: {name}")
        builder.add(registered[name])
    return builder.build()
