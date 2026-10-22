"""Checkpointed batch orchestration over existing kernel execution boundaries."""

import time
import tomllib
from dataclasses import dataclass, field

from game_automation.common import FrameworkError, uid
from game_automation.contracts import validate
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.credentials import load_profile, profile_path

from .assembly import build_workflow
from .preprocessing import read_batch


@dataclass
class WorkflowContext:
    store: object
    run_id: str
    task: dict
    outputs: dict = field(default_factory=dict)
    apk_index: int | None = None


def workflow_task(root, source_file, instances, credentials, goal, max_turns, stages, adopt):
    submission = read_batch(source_file, root / "contracts/v1")
    application = tomllib.loads((root / "config/application.toml").read_text(encoding="utf-8"))
    task = {
        "kind": "apk_workflow",
        "source_ids": submission["source_ids"],
        "instances": instances or [],
        "credential_profile": credentials,
        "goal": goal,
        "max_turns": max_turns,
        "stages": stages if stages is not None else application["workflow"]["stages"],
        "adopt_existing": adopt,
    }
    validate_workflow(root, task)
    return task


def validate_workflow(root, task):
    validate(root / "contracts/v1", "workflow", task)
    stages = build_workflow(task["stages"])
    if any(stage.per_apk for stage in stages):
        instances = task["instances"]
        if len(instances) != len(task["source_ids"]) or len(set(instances)) != len(instances):
            raise FrameworkError(
                ErrorCode.WORKFLOW_INVALID, "Assign one distinct MuMu instance to each APK"
            )
    if any(stage.mode == "gameplay" for stage in stages):
        load_profile(profile_path(root, task["credential_profile"]))
    return stages


def workflow_status(store, run_id):
    steps = {}
    for event in store.iter_events(run_id):
        if event["kind"].startswith("workflow.stage."):
            payload = event["payload"]
            key = payload["key"]
            if event["kind"] == "workflow.stage.started":
                steps[key] = {}
            steps.setdefault(key, {}).update(payload)
    for step in steps.values():
        if step.get("child_run"):
            step["status"] = store.run(step["child_run"])["status"]
    result = {"run_id": run_id, "steps": list(steps.values())}
    return result


def stage_children(store, run_id):
    children = {}
    task_id = store.run(run_id)["task_id"]
    for run in store.task_runs(task_id):
        inputs = store.run_task(run["run_id"])
        if inputs.get("workflow_run") == run_id:
            key = inputs["workflow_key"]
            if key in children:
                raise FrameworkError(ErrorCode.WORKFLOW_INVALID, "Duplicate stage execution")
            children[key] = run["run_id"]
    for event in store.events(run_id, kinds=("workflow.stage.bound",)):
        payload = event["payload"]
        child = payload["child_run"]
        if store.run(child)["task_id"] != task_id:
            raise FrameworkError(ErrorCode.WORKFLOW_INVALID, "Stage belongs to another task")
        key = payload["key"]
        if key in children and children[key] != child:
            raise FrameworkError(ErrorCode.WORKFLOW_INVALID, "Stage execution binding changed")
        children[key] = child
    return children


class Scheduler:
    def __init__(self, store, run_id, host, task, source_file):
        self.store = store
        self.run_id = run_id
        self.host = host
        self.source_file = source_file
        self.context = WorkflowContext(store, run_id, task)
        self.children = stage_children(store, run_id)
        self.previous_children = {}
        self.completed = set()
        continuations = store.events(run_id, kinds=("run.continued_from",), limit=1)
        if continuations:
            previous = continuations[-1]["payload"]["run_id"]
            old_run = store.run(previous)
            if (
                old_run["mode"] != "workflow"
                or old_run["task_id"] != store.run(run_id)["task_id"]
                or store.run_task(previous) != task
            ):
                raise FrameworkError(ErrorCode.WORKFLOW_INVALID, "Continuation input differs")
            self.previous_children = stage_children(store, previous)
        for event in store.events(run_id, kinds=("workflow.stage.completed",)):
            self.completed.add(event["payload"]["key"])
        for event in store.events(run_id, kinds=("workflow.stage.started",)):
            span = event["span_id"]
            closed = store.events(
                run_id,
                span=span,
                kinds=(
                    "workflow.stage.completed",
                    "workflow.stage.failed",
                    "workflow.stage.incomplete",
                ),
            )
            if not closed:
                payload = {**event["payload"], "status": "unknown", "duration_ms": None}
                store.event(run_id, "workflow.stage.incomplete", payload, span_id=span)

    async def step(self, stage, context):
        from .runner import execute_locked, new_run_locked

        self.host.check_cancel()
        key = f"apk:{context.apk_index}:{stage.name}" if stage.per_apk else stage.name
        child = self.children.get(key)
        if key in self.completed:
            context.outputs[stage.name] = child
            return
        span = uid("span")
        payload = {
            "key": key,
            "stage": stage.name,
            "capability": f"workflow.{stage.name}",
            "apk_index": context.apk_index,
            "status": "running",
        }
        self.store.transition(self.run_id, "running", stage.name)
        self.store.event(self.run_id, "workflow.stage.started", payload, span_id=span)
        started = time.monotonic()
        recovered = False
        try:
            if stage.mode is None:
                stage.inputs(context)
            else:
                previous = self.previous_children.get(key)
                if child is None and previous:
                    old_run = self.store.run(previous)
                    if old_run["mode"] != stage.mode:
                        raise FrameworkError(ErrorCode.WORKFLOW_INVALID, "Stage mode changed")
                    if old_run["status"] == "succeeded":
                        child = previous
                        self.children[key] = child
                        payload["reused_from_previous_batch"] = True
                if child is None:
                    inputs = stage.inputs(context)
                    inputs["workflow_run"] = self.run_id
                    inputs["workflow_key"] = key
                    task_id = self.store.run(self.run_id)["task_id"]
                    child = new_run_locked(
                        self.store, task_id, stage.mode, inputs, parent_run=self.run_id
                    )
                    self.children[key] = child
                    if previous:
                        self.store.event(child, "run.continued_from", {"run_id": previous})
                payload["child_run"] = child
                self.store.event(self.run_id, "workflow.stage.bound", payload, span_id=span)
                result = self.store.run(child)
                recovered = result["status"] == "succeeded"
                if result["status"] not in ("succeeded", "failed", "cancelled"):
                    result = await execute_locked(self.store, child, self.source_file)
                self.host.check_cancel()
                if result["status"] != "succeeded":
                    reasons = self.store.events(child, kinds=("run.state",), limit=1)
                    payload["reason"] = reasons[-1]["payload"].get("reason") if reasons else None
                    raise FrameworkError(
                        ErrorCode.WORKFLOW_BLOCKED,
                        f"Stage {stage.name} for APK {context.apk_index}: {child} is "
                        f"{result['status']}; inspect the child and resume the workflow",
                    )
            payload["duration_ms"] = round((time.monotonic() - started) * 1000)
            if recovered:
                payload["duration_ms"] = None
                payload["recovered"] = True
            payload["status"] = "succeeded"
            self.store.event(self.run_id, "workflow.stage.completed", payload, span_id=span)
            self.completed.add(key)
            context.outputs[stage.name] = child
        except BaseException:
            payload["duration_ms"] = round((time.monotonic() - started) * 1000)
            payload["status"] = self.store.run(child)["status"] if child else "incomplete"
            self.store.event(self.run_id, "workflow.stage.failed", payload, span_id=span)
            raise

    async def run(self, stages):
        index = 0
        while index < len(stages):
            if not stages[index].per_apk:
                await self.step(stages[index], self.context)
                index += 1
                continue
            end = index
            while end < len(stages) and stages[end].per_apk:
                end += 1
            for apk_index in range(len(self.context.task["source_ids"])):
                context = WorkflowContext(
                    self.store,
                    self.run_id,
                    self.context.task,
                    dict(self.context.outputs),
                    apk_index,
                )
                for stage in stages[index:end]:
                    await self.step(stage, context)
            index = end


async def schedule(store, run_id, host, task, source_file):
    stages = validate_workflow(store.root, task)
    for stage in stages:
        for capability in stage.capabilities:
            host.router.resolve(capability)
    scheduler = Scheduler(store, run_id, host, task, source_file)
    plan = {"stages": task["stages"], "instances": task["instances"], "order": "apk-list"}
    store.event(run_id, "workflow.plan", plan)
    try:
        await scheduler.run(stages)
    finally:
        store.artifact(run_id, "reports/workflow.json", workflow_status(store, run_id))
