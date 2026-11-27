"""Prepare stage inputs selected by the authoritative TypeScript workflow."""

from dataclasses import dataclass

from game_automation.errors import ErrorCode, FrameworkError

from .device import preparation_task
from .gameplay import gameplay_task


@dataclass(frozen=True)
class StageInputs:
    store: object
    task: dict
    outputs: dict
    apk_index: int | None


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


_INPUT_BUILDERS = {
    "preprocess": preprocessing_input,
    "environment": environment_input,
    "prepare": preparation_input,
    "launch": launch_input,
    "play": play_input,
}


def stage_input(name, context):
    builder = _INPUT_BUILDERS.get(name)
    if builder is None:
        raise FrameworkError(ErrorCode.WORKFLOW_INVALID, f"No input adapter for stage: {name}")
    return builder(context)
