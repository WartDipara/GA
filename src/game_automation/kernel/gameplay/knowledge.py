"""Reuse measured transitions as guarded data, never generated per-game scripts."""

import json

from game_automation.common import digest


class KnowledgeCache:
    def __init__(self, runtime):
        self.runtime = runtime
        self.entries = runtime.progress.data.setdefault("learned_steps", [])

    def scope(self, observation):
        identity = self.runtime.progress.data["identity"]
        display = observation["runtime"]["focused_display"]
        if not identity or display is None:
            return None
        value = {
            "apk": self.runtime.task["metadata"]["sha256"],
            "identity": identity,
            "boot_id": observation["runtime"]["boot_id"],
            "display": display,
            "process": observation["runtime"]["main_process"],
        }
        if value["process"]:
            value["process"] = {key: value["process"][key] for key in ("pid", "uid", "start_ticks")}
        return digest(json.dumps(value, sort_keys=True).encode())

    def intent(self):
        goals = self.runtime.progress.data["goals"]
        active = [key for key, value in goals.items() if value["status"] == "active"]
        if len(active) == 1:
            return active[0]
        if self.runtime.task["goal"] == "login-and-interact":
            return "login-and-interact"
        return None

    def learn(self, pending, observation, facts):
        if (
            pending.get("risk") != "none"
            or not pending.get("cache_scope")
            or pending["action"]["type"] not in ("tap", "back")
            or observation["sensitive_regions"]
            or not facts["stable"]
            or pending.get("sensitive_regions")
            or not pending.get("cache_intent")
        ):
            return
        if pending["cache_scope"] != self.scope(observation):
            return
        entry = {
            "scope": pending["cache_scope"],
            "intent": pending["cache_intent"],
            "before": pending["image_sha256"],
            "before_screen": pending["screen_key"],
            "after": observation["screenshot"]["sha256"],
            "after_screen": facts["screen_key"],
            "action": pending["action"],
            "source_run": pending["run_id"],
            "action_id": pending["action_id"],
            "observation_id": observation["observation_id"],
        }
        if entry["before"] == entry["after"]:
            return
        self.entries.append(entry)
        self.entries[:] = self.entries[-100:]
        self.runtime.host.record("gameplay.cache.learned", entry)

    def routine(self, observation, facts):
        if not facts["stable"] or observation["sensitive_regions"]:
            result = []
            return result
        scope = self.scope(observation)
        intent = self.intent()
        current = observation["screenshot"]["sha256"]
        visited = set()
        steps = []
        while len(steps) < 5 and current not in visited:
            matches = [
                entry
                for entry in self.entries
                if entry["scope"] == scope
                and entry["intent"] == intent
                and entry["before"] == current
            ]
            unique = {
                digest(json.dumps(entry["action"], sort_keys=True).encode()) for entry in matches
            }
            if len(unique) != 1:
                break
            entry = matches[-1]
            package = observation["runtime"]["package_name"]
            before = [
                {"kind": "image", "value": current},
                {"kind": "screen", "value": entry["before_screen"]},
                {"kind": "focused_package", "value": package},
            ]
            after = [
                {"kind": "image", "value": entry["after"]},
                {"kind": "focused_package", "value": package},
            ]
            step = {"before": before, "action": entry["action"], "after": after}
            steps.append(step)
            visited.add(current)
            current = entry["after"]
        return steps
