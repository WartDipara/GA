import asyncio
import hashlib
import os
import zipfile

from game_automation.common import FrameworkError, uid, within
from game_automation.contracts import validate
from game_automation.errors import ErrorCode
from game_automation.prompting.resources import resource_root

from .evidence import verify_image
from .preprocessing import preprocessing_status


def ready_apk(store, source_run, index):
    if store.run(source_run)["mode"] != "preprocessing":
        raise FrameworkError(ErrorCode.DEVICE_APK_REQUIRED)
    items = [item for item in preprocessing_status(store, source_run) if item["index"] == index]
    if len(items) != 1 or items[0]["status"] != "ready":
        raise FrameworkError(ErrorCode.DEVICE_APK_REQUIRED)
    item = items[0]
    if (
        not (store.run_dir(source_run) / "manifest.json").is_file()
        or not within(store.run_dir(source_run), item["apk_ref"]).is_file()
    ):
        raise FrameworkError(
            ErrorCode.DEVICE_APK_REQUIRED,
            "Prepared APK evidence is incomplete; preprocess the source again",
        )
    contracts = resource_root(store, source_run, "contracts")
    validate(contracts, "apk-metadata", item["metadata"])
    return item


def preparation_task(store, source_run, index, instance_index, adopt_existing=False):
    if index < 0 or instance_index < 0:
        raise FrameworkError(ErrorCode.DEVICE_SELECTION_REQUIRED)
    item = ready_apk(store, source_run, index)
    task = {
        "kind": "device_preparation",
        "target": item["metadata"]["packageName"],
        "source_run": source_run,
        "apk_index": index,
        "instance_index": instance_index,
        "metadata": item["metadata"],
        "adopt_existing": adopt_existing,
    }
    return task


async def device_session(store, run_id, host, manifest, task):
    previous = [
        event["payload"]
        for event in store.events(run_id, kinds=("device.session.bound",))
        if event["kind"] == "device.session.bound"
    ]
    if previous:
        request = previous[-1]["request"]
    else:
        request = {
            "manager_executable": os.environ.get("GAME_AUTO_MUMU_MANAGER") or None,
            "adb_executable": os.environ.get("GAME_AUTO_ADB") or None,
            "instance_index": task["instance_index"],
            "instance_id": None,
            "serial": None,
            "command_timeout_seconds": manifest["application"]["environment"][
                "command_timeout_seconds"
            ],
        }
    result = await host.call("emulator.session", request, timeout=0)
    if (
        result["instance_index"] != task["instance_index"]
        or request["instance_id"] not in (None, result["instance_id"])
        or request["serial"] not in (None, result["serial"])
    ):
        raise FrameworkError(ErrorCode.DEVICE_IDENTITY_CHANGED)
    bound = {**request, "instance_id": result["instance_id"], "serial": result["serial"]}
    if not previous:
        host.record("device.session.bound", {"request": bound, "session": result})
    return bound


def confirm_session(expected, actual):
    keys = ("instance_id", "instance_index", "serial")
    if any(expected[key] != actual[key] for key in keys):
        raise FrameworkError(ErrorCode.DEVICE_IDENTITY_CHANGED)


async def observe_device(store, run_id, host, manifest, task):
    session = await device_session(store, run_id, host, manifest, task)
    workspace_ref = f"artifacts/{uid('observation')}"
    request = {"session": session, "workspace": str(within(store.run_dir(run_id), workspace_ref))}
    result = await host.call("device.observe", request, timeout=0)
    confirm_session(session, result["session"])
    image = result["screenshot"]
    display = result["display"]
    if (image["width"], image["height"]) != (display["width"], display["height"]):
        raise FrameworkError(ErrorCode.INVALID_SCREENSHOT)
    verify_image(store.run_dir(run_id), workspace_ref, image)
    result["observation_id"] = uid("obs")
    result["evidence_ref"] = image["evidence_ref"]
    result["session"].pop("adb_executable")
    host.record("device.observed", result)
    host.artifact("reports/observation.json", result)


def installation_budget(path, metadata, reserve_bytes):
    if not path.is_file() or path.stat().st_size != metadata["sizeBytes"]:
        raise FrameworkError(ErrorCode.LOCAL_APK_CHANGED)
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != metadata["sha256"]:
        raise FrameworkError(ErrorCode.LOCAL_APK_CHANGED)
    try:
        with zipfile.ZipFile(path) as archive:
            expanded_bytes = sum(entry.file_size for entry in archive.infolist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise FrameworkError(ErrorCode.INVALID_APK) from exc
    required = metadata["sizeBytes"] + 2 * expanded_bytes + reserve_bytes
    budget = {
        "apk_bytes": metadata["sizeBytes"],
        "expanded_bytes": expanded_bytes,
        "reserve_bytes": reserve_bytes,
        "required_bytes": required,
    }
    return budget


async def inspect_package(host, session, package_name):
    request = {"session": session, "package_name": package_name}
    result = await host.call("device.inspect", request, timeout=0)
    confirm_session(session, result["session"])
    if result["package_name"] != package_name:
        raise FrameworkError(ErrorCode.INVALID_ADB_OUTPUT)
    host.record("device.package.observed", result)
    return result


async def prepare_device(store, run_id, host, manifest, task):
    item = ready_apk(store, task["source_run"], task["apk_index"])
    metadata = task["metadata"]
    if metadata != item["metadata"]:
        raise FrameworkError(ErrorCode.LOCAL_APK_CHANGED)
    path = within(store.run_dir(task["source_run"]), item["apk_ref"])
    settings = manifest["application"]["device"]
    if type(settings["reserve_bytes"]) is not int or settings["reserve_bytes"] <= 0:
        raise FrameworkError(ErrorCode.INVALID_STORAGE_POLICY)
    budget = await asyncio.to_thread(installation_budget, path, metadata, settings["reserve_bytes"])
    session = await device_session(store, run_id, host, manifest, task)
    apk_key = f"{task['source_run']}:{task['apk_index']}"
    store.reserve_device(run_id, session["instance_id"], session["instance_index"], apk_key)
    before = await inspect_package(host, session, metadata["packageName"])
    previous = store.latest_device_action(run_id)
    if previous:
        history = {
            "action_run": previous["run_id"],
            "action_id": previous["action_id"],
            "status": previous["status"],
        }
        host.record("device.installation.history", history)
    if previous and previous["status"] == "prepared":
        not_dispatched = {"reason": "Previous process ended before dispatch authorization"}
        store.finish_device_action(
            previous["run_id"], previous["action_id"], "rejected", not_dispatched
        )
        previous = store.latest_device_action(run_id)
    if previous and previous["status"] in ("unknown", "executed", "verified"):
        if not before["installed"] or before["sha256"] != metadata["sha256"]:
            raise FrameworkError(ErrorCode.DEVICE_INSTALL_UNKNOWN)
        if previous["status"] != "verified":
            store.finish_device_action(
                previous["run_id"], previous["action_id"], "verified", before
            )
        reconciled = {
            "action_run": previous["run_id"],
            "action_id": previous["action_id"],
            "sha256": before["sha256"],
            "verified": True,
        }
        host.record("device.installation.reconciled", reconciled)
    elif before["installed"] and task.get("adopt_existing"):
        if before["sha256"] != metadata["sha256"]:
            raise FrameworkError(ErrorCode.DEVICE_PACKAGE_CONFLICT)
        host.record(
            "device.installation.adopted",
            {"sha256": before["sha256"], "preserved_application_data": True},
        )
    else:
        if before["installed"]:
            raise FrameworkError(ErrorCode.DEVICE_PACKAGE_CONFLICT)
        budget["available_bytes"] = before["available_bytes"]
        host.record("device.storage.checked", budget)
        if before["available_bytes"] < budget["required_bytes"]:
            raise FrameworkError(ErrorCode.DEVICE_SPACE_INSUFFICIENT)
        request = {
            "session": session,
            "package_name": metadata["packageName"],
            "apk_path": str(path),
            "sha256": metadata["sha256"],
            "size_bytes": metadata["sizeBytes"],
            "required_bytes": budget["required_bytes"],
            "install_timeout_seconds": settings["install_timeout_seconds"],
        }
        result = await host.install(request)
        confirm_session(session, result["session"])
        if result["status"] == "rejected":
            raise FrameworkError(ErrorCode.DEVICE_INSTALL_REJECTED)
        verified = await inspect_package(host, session, metadata["packageName"])
        if not verified["installed"] or verified["sha256"] != metadata["sha256"]:
            raise FrameworkError(ErrorCode.DEVICE_INSTALL_UNKNOWN)
        action = store.latest_device_action(run_id)
        store.finish_device_action(run_id, action["action_id"], "verified", verified)
    prepared = {
        "instance_id": session["instance_id"],
        "instance_index": session["instance_index"],
        "serial": session["serial"],
        "source_run": task["source_run"],
        "apk_index": task["apk_index"],
        "metadata": metadata,
        "installation_verified": True,
    }
    reference = host.artifact("reports/device.json", prepared)
    prepared["evidence_ref"] = reference
    host.record("device.prepared", prepared)
