import asyncio

from game_automation.common import FrameworkError, uid, within
from game_automation.contracts import validate
from game_automation.disk import free_disk_bytes, insufficient_disk_space, require_disk_space
from game_automation.errors import ErrorCode
from game_automation.kernel.storage_policy import storage_settings


async def admit_apk_batch(store, run_id, host, manifest, submission, source_file, worker_count):
    settings = manifest["application"]["preprocessing"]
    storage = storage_settings(manifest)
    run_dir = store.run_dir(run_id)
    plans = [None] * len(submission["source_ids"])
    overhead = len(plans) * storage["overhead_bytes_per_apk"]
    require_disk_space(run_dir, storage["reserve_bytes"] + overhead)
    slots = asyncio.Semaphore(worker_count)

    async def plan_item(index, source_id):
        async with slots:
            try:
                request = {
                    "source_file": str(source_file.resolve()),
                    "source_index": index,
                    "source_id": source_id,
                    "workspace": str(within(run_dir, f"apks/apk_{index + 1:04d}")),
                    "request_timeout_seconds": settings["request_timeout_seconds"],
                    "max_apk_bytes": settings["max_apk_bytes"],
                }
                validate(host.contracts, "apk-plan-request", request)
                timeout = settings["request_timeout_seconds"] * 2 + 15
                plan = await host.call("apk.plan", request, timeout=timeout)
                budget = plan["budget_bytes"]
                local = plan["basis"] == "local_apk"
                valid_budget = budget == 0 if local else 0 < budget <= settings["max_apk_bytes"]
                if not valid_budget or (not local and plan["size_bytes"] not in (None, budget)):
                    raise FrameworkError(ErrorCode.INVALID_CONTRACT)
                plan["index"] = index
                plans[index] = plan
            except BaseException as exc:
                host.stop(exc)
                raise

    try:
        async with asyncio.TaskGroup() as tasks:
            for index, source_id in enumerate(submission["source_ids"]):
                tasks.create_task(plan_item(index, source_id))
    except ExceptionGroup as failures:
        error = host.failure if host.failure is not None else failures.exceptions[0]
        raise error

    download_bytes = sum(plan["budget_bytes"] for plan in plans)
    margin_bytes = (download_bytes * storage["safety_margin_percent"] + 99) // 100
    required_bytes = download_bytes + overhead + margin_bytes + storage["reserve_bytes"]
    available_bytes = free_disk_bytes(run_dir)
    report = {
        "accepted": available_bytes >= required_bytes,
        "available_bytes": available_bytes,
        "required_bytes": required_bytes,
        "download_bytes": download_bytes,
        "overhead_bytes": overhead,
        "margin_bytes": margin_bytes,
        "reserve_bytes": storage["reserve_bytes"],
        "items": plans,
    }
    report_path = f"artifacts/{uid('storage-admission')}.json"
    report_ref = host.artifact(report_path, report)
    report["report_ref"] = report_ref
    host.record("preprocessing.storage.checked", report)
    if not report["accepted"]:
        raise insufficient_disk_space(required_bytes, available_bytes)
    return plans
