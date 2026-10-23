"""Temporary stage tools for the TypeScript executor; no scheduling or terminal-state decisions."""

import asyncio
from pathlib import Path

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode
from game_automation.kernel.capability_router import Router
from game_automation.kernel.plugin_host import Host
from game_automation.storage.control_client import ControlClient
from game_automation.storage.store import Store


def invoke(root, operation, *, _client=None, **request):
    client = _client or ControlClient(root)
    client.handler = lambda method, params: handle_tool(root, method, params)
    try:
        client.connect()
        return client.request("execution." + operation, request, timeout=None)
    finally:
        client.close()


async def invoke_async(root, operation, **request):
    client = ControlClient(root)
    pending = asyncio.create_task(
        asyncio.to_thread(invoke, root, operation, _client=client, **request)
    )
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        client.close()
        await asyncio.gather(pending, return_exceptions=True)
        raise


def handle_tool(root, method, params):
    if method != "execution.tool":
        raise FrameworkError(ErrorCode.METHOD_NOT_FOUND)
    store = Store.open_readonly(root) if params["operation"] == "render" else Store(root)
    try:
        return tool(store, params["run_id"], params["operation"], params["input"])
    finally:
        store.close()


def tool(store, run_id, operation, inputs):
    if operation in ("freeze", "load"):
        from game_automation.prompting.snapshots import freeze, load

        if operation == "freeze":
            return freeze(store, run_id, parent_run=inputs.get("parent_run"))
        return load(store, run_id)
    if operation == "render":
        from game_automation.reporting.components import render_component

        return render_component(store, run_id, inputs["component"])
    if operation in ("workflow_validate", "workflow_input"):
        from .assembly import build_workflow
        from .scheduler import WorkflowContext, validate_workflow

        if operation == "workflow_validate":
            validate_workflow(store.root, inputs["task"])
            return None
        stages = build_workflow(
            ["preprocess", "environment", "prepare", "launch", "play", "report"]
        )
        stage = next(stage for stage in stages if stage.name == inputs["stage"])
        context = WorkflowContext(
            store, run_id, inputs["task"], inputs["outputs"], inputs["apk_index"]
        )
        return stage.inputs(context)
    if operation != "stage":
        raise FrameworkError(ErrorCode.METHOD_NOT_FOUND)
    return asyncio.run(run_stage(store, run_id, inputs))


async def run_stage(store, run_id, inputs):
    from .device import observe_device, prepare_device
    from .emulators import discover_emulators
    from .environment import check_environment
    from .intake import intake
    from .preprocessing import preprocess
    from .trace_analysis import analyze_trace

    manifest = inputs["manifest"]
    task = inputs["task"]
    host = Host(store, run_id, Router(manifest["plugins"]), manifest)
    mode = inputs["mode"]
    timeout = inputs["timeout_seconds"]
    try:
        async with asyncio.timeout(timeout or None):
            if mode in ("game", "intake"):
                return await intake(store, run_id, host, manifest)
            if mode == "preprocessing":
                source = Path(inputs["source_file"]) if inputs["source_file"] else None
                await preprocess(store, run_id, host, manifest, task, source)
            elif mode == "emulators":
                await discover_emulators(store, run_id, host, manifest, task)
            elif mode == "device":
                await prepare_device(store, run_id, host, manifest, task)
            elif mode == "observation":
                await observe_device(store, run_id, host, manifest, task)
            elif mode == "analysis":
                await analyze_trace(store, run_id, host, manifest, task)
            elif mode == "environment":
                await check_environment(store, run_id, host, manifest, task)
            else:
                raise FrameworkError(ErrorCode.RUN_MODE_REMOVED)
        host.check_cancel()
    except TimeoutError as exc:
        raise FrameworkError(ErrorCode.BUDGET_EXHAUSTED) from exc
    return None
