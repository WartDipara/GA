"""Kernel-owned gameplay execution; proposals never hold device authority."""

import asyncio
import json
from pathlib import Path

from game_automation.common import FrameworkError, uid, within
from game_automation.contracts import validate
from game_automation.disk import require_disk_space
from game_automation.errors import ErrorCode
from game_automation.kernel.gameplay.hybrid import run_hybrid
from game_automation.kernel.gameplay.local import Perception, execute_routine
from game_automation.kernel.gameplay.progress import ProgressStore
from game_automation.plugin_sdk.credentials import load_profile, profile_path

from .device import confirm_session, device_session, inspect_package
from .evidence import verify_image


def gameplay_task(store, prepared_run, credential_profile, goal, actions=None, max_turns=0):
    if store.run(prepared_run)["status"] != "succeeded":
        raise FrameworkError(ErrorCode.DEVICE_APK_REQUIRED)
    prepared = [
        event["payload"]
        for event in store.events(prepared_run)
        if event["kind"] == "device.prepared"
    ]
    if not prepared:
        raise FrameworkError(ErrorCode.DEVICE_APK_REQUIRED)
    device = prepared[-1]
    profile = profile_path(store.root, credential_profile)
    load_profile(profile)
    task = {
        "kind": "gameplay",
        "target": device["metadata"]["packageName"],
        "prepared_run": prepared_run,
        "source_run": device["source_run"],
        "apk_index": device["apk_index"],
        "instance_index": device["instance_index"],
        "instance_id": device["instance_id"],
        "metadata": device["metadata"],
        "credential_profile": credential_profile,
        "goal": goal,
        "actions": actions or [],
        "max_turns": max_turns,
    }
    return task


class EvidenceRecorder:
    def __init__(self, store, run_id, host, settings):
        self.store = store
        self.run_id = run_id
        self.host = host
        self.directory = store.run_dir(run_id)
        self.settings = settings

    def admit(self, *, initial=False):
        used = sum(path.stat().st_size for path in self.directory.rglob("*") if path.is_file())
        # Reserve enough for one bounded observation, protocol records, and an offline report.
        headroom = 128 * 1024**2
        remaining = self.settings["evidence_budget_bytes"] - used
        if remaining < headroom:
            raise FrameworkError(ErrorCode.EVIDENCE_BUDGET)
        needed = remaining if initial else headroom
        require_disk_space(self.directory, self.settings["reserve_bytes"] + needed)
        return used


class DeviceRuntime:
    def __init__(self, store, run_id, host, task, session, recorder, progress):
        self.store = store
        self.run_id = run_id
        self.host = host
        self.task = task
        self.session = session
        self.recorder = recorder
        self.progress = progress
        self.credential_path = str(profile_path(store.root, task["credential_profile"]))

    async def observe(self):
        for attempt in range(3):
            try:
                return await self._capture()
            except FrameworkError as exc:
                if exc.code != ErrorCode.DEVICE_OBSERVATION_UNSTABLE or attempt == 2:
                    raise
                self.host.record("gameplay.observation.retry", {"attempt": attempt + 1})
                await asyncio.sleep(1)

    async def _capture(self):
        self.recorder.admit()
        workspace_ref = f"artifacts/{uid('observation')}"
        request = {
            "session": self.session,
            "package_name": self.task["target"],
            "workspace": str(within(self.store.run_dir(self.run_id), workspace_ref)),
            "sensitive_regions": self.progress.data["sensitive_regions"],
            "credential_path": self.credential_path,
        }
        result = await self.host.call("device.capture", request, timeout=0)
        confirm_session(self.session, result["session"])
        verify_image(self.store.run_dir(self.run_id), workspace_ref, result["screenshot"])
        result["observation_id"] = uid("obs")
        result["evidence_ref"] = f"{workspace_ref}/screen.png"
        self.host.record("gameplay.observation", result)
        return result

    async def perform(self, action, observation, *, source="operator"):
        self.recorder.admit()
        self.host.check_cancel()
        validate(self.store.run_dir(self.run_id) / "contracts", "gameplay-action", action)
        kind = action["type"]
        if kind == "credential":
            if action["credential_field"] is None or action["bounds"] is None:
                raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
            self.progress.data["sensitive_regions"].append(action["bounds"])
        if kind == "launch":
            if observation["runtime"]["alive"] or self.progress.data["recovery_rounds"] >= 2:
                raise FrameworkError(ErrorCode.GAMEPLAY_BLOCKED)
            self.progress.data["recovery_rounds"] += 1
        self.progress.data["pending"] = {
            "observation_id": observation["observation_id"],
            "action": action,
            "run_id": self.run_id,
            "image_ref": observation["evidence_ref"],
        }
        self.progress.save()
        request = {
            "session": self.session,
            "package_name": self.task["target"],
            "runtime": observation["runtime"],
            "action": action,
            "sensitive_regions": observation["sensitive_regions"],
            "observation_path": str(
                within(self.store.run_dir(self.run_id), observation["evidence_ref"])
            ),
            "credential_path": self.credential_path,
        }
        result = await self.host.perform(request)
        self.host.record(
            "gameplay.action.origin", {"action_id": result["action_id"], "source": source}
        )
        self.progress.data["pending"]["action_id"] = result["action_id"]
        self.progress.save()
        return result


async def play(store, run_id, host, manifest, task):
    recorder = EvidenceRecorder(store, run_id, host, manifest["application"]["gameplay"])
    recorder.admit(initial=True)
    progress = ProgressStore(store, run_id, task)
    progress.save()
    session = await device_session(store, run_id, host, manifest, task)
    if session["instance_id"] != task["instance_id"]:
        raise FrameworkError(ErrorCode.DEVICE_IDENTITY_CHANGED)
    apk_key = f"{task['source_run']}:{task['apk_index']}"
    store.reserve_device(run_id, session["instance_id"], session["instance_index"], apk_key)
    installed = await inspect_package(host, session, task["target"])
    if installed["sha256"] != task["metadata"]["sha256"]:
        raise FrameworkError(ErrorCode.DEVICE_PACKAGE_CONFLICT)
    runtime = DeviceRuntime(store, run_id, host, task, session, recorder, progress)
    observation = await runtime.observe()
    progress.data["checkpoint"] = {
        "run_id": run_id,
        "observation_id": observation["observation_id"],
        "runtime": observation["runtime"],
    }
    progress.save()
    request = {
        "session": session,
        "package_name": task["target"],
        "credential_path": runtime.credential_path,
    }
    logs = await host.call("device.logs", request, timeout=0)
    host.record("gameplay.logs", logs)
    previous = store.latest_device_action(run_id)
    if previous and previous["status"] in ("verified", "reconciled"):
        for path in progress.data["paths"]:
            if path["action_id"] == previous["action_id"]:
                path["status"] = previous.get("result", {}).get("status", previous["status"])
    if previous and previous["status"] in ("unknown", "executed", "prepared"):
        reference = within(store.run_dir(previous["run_id"]), previous["input_ref"])
        intent = json.loads(reference.read_text(encoding="utf-8"))
        focused = observation["runtime"]["focused_display"]
        can_reconcile = (
            intent.get("action", {}).get("type") in ("return", "launch")
            and observation["runtime"]["alive"]
            and focused is not None
            and focused["package_name"] == task["target"]
        )
        passive_wait = intent.get("action", {}).get("type") == "wait"
        if previous["status"] == "prepared":
            store.finish_device_action(
                previous["run_id"], previous["action_id"], "rejected", {"dispatched": False}
            )
        elif can_reconcile or passive_wait:
            store.finish_device_action(
                previous["run_id"],
                previous["action_id"],
                "reconciled" if passive_wait else "verified",
                {
                    "observation_run": run_id,
                    "observation_id": observation["observation_id"],
                    "business_goal_verified": False,
                },
            )
            host.record(
                "gameplay.action.reconciled",
                {"action_id": previous["action_id"], "action_run": previous["run_id"]},
            )
        elif (
            task["goal"] not in ("capture", "activate-game", "return-to-game", "routine")
            and previous["capability"] == "device.execute"
        ):
            pending = progress.data["pending"] or {
                "observation_id": "interrupted-observation",
                "action": intent["action"],
                "expected_outcome": "Reconcile interrupted action",
                "image_ref": Path(intent["observation_path"])
                .relative_to(store.run_dir(previous["run_id"]))
                .as_posix(),
            }
            pending.update(run_id=previous["run_id"], action_id=previous["action_id"])
            progress.data["pending"] = pending
        else:
            raise FrameworkError(
                ErrorCode.ACTION_ALREADY_DISPATCHED,
                "Fresh observation saved; pending action requires reconciliation",
            )
    if (
        not previous
        or previous["status"] not in ("unknown", "executed")
        or can_reconcile
        or passive_wait
    ):
        progress.data["pending"] = None
    progress.save()
    if task["goal"] in ("return-to-game", "activate-game"):
        action = {
            "type": "launch"
            if task["goal"] == "activate-game" and not observation["runtime"]["alive"]
            else "return",
            "x": 0,
            "y": 0,
            "end_x": 0,
            "end_y": 0,
            "duration_ms": 1,
            "bounds": None,
            "credential_field": None,
        }
        dispatched = await runtime.perform(action, observation)
        after = await runtime.observe()
        focused = after["runtime"]["focused_display"]
        verified = (
            after["runtime"]["alive"]
            and focused is not None
            and focused["package_name"] == task["target"]
        )
        result = {"observation_id": after["observation_id"], "verified": verified}
        host.record("gameplay.outcome", result)
        if not verified:
            raise FrameworkError(ErrorCode.GAMEPLAY_BLOCKED)
        store.finish_device_action(run_id, dispatched["action_id"], "verified", result)
        progress.data["pending"] = None
        progress.data["checkpoint"] = {
            "run_id": run_id,
            "observation_id": after["observation_id"],
            "runtime": after["runtime"],
        }
        progress.save()
    if task["goal"] == "routine":
        observation = await execute_routine(runtime, observation, task["actions"])
    elif task["goal"] == "capture":
        observation, facts = await Perception(runtime).settle(observation)
        host.record(
            "gameplay.route",
            {
                "route": "plan",
                "reason": "unknown-state-or-new-goal",
                "screen_key": facts["screen_key"],
            },
        )
    elif task["goal"] not in ("return-to-game", "activate-game"):
        observation = await run_hybrid(runtime, manifest, observation)
