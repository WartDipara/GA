"""Checkpointed progress limits; model wording and animation do not replenish budgets."""

import json
import time

from game_automation.common import FrameworkError, digest
from game_automation.errors import ErrorCode


class ProgressWatchdog:
    def __init__(self, runtime, settings):
        self.runtime = runtime
        self.settings = settings
        self.data = runtime.progress.data.setdefault(
            "watchdog", {"rounds": 0, "elapsed_seconds": 0, "history": [], "proofs": []}
        )
        self.updated = time.monotonic()

    def check(self):
        current = time.monotonic()
        self.data["elapsed_seconds"] += current - self.updated
        self.updated = current
        if self.data["rounds"] >= self.settings.get("no_progress_rounds", 24) or self.data[
            "elapsed_seconds"
        ] >= self.settings.get("no_progress_seconds", 900):
            self.block("no-verified-progress")

    def review(self, decision, observation):
        self.check()
        self.data["rounds"] += 1
        state = decision.get("state_key") or "unknown"
        action = decision.get("action")
        pending = self.runtime.progress.data["pending"]
        assessment = decision["previous_outcome"]
        verified = (
            pending is not None
            and assessment["status"] == "verified"
            and observation["observation_id"] in assessment["evidence_refs"]
        )
        previous_state = self.data.get("state")
        if verified and previous_state and state != previous_state and state != "unknown":
            transition = [previous_state, state, pending["action"]["type"]]
            signature = digest(json.dumps(transition).encode())
            if signature not in self.data["proofs"]:
                self.data["proofs"] = (self.data["proofs"] + [signature])[-256:]
                self.data["rounds"] = 0
                self.data["elapsed_seconds"] = 0
        if verified and pending["action"]["type"] != "launch":
            self.runtime.progress.data["recovery_rounds"] = 0
        self.data["state"] = state

        # Ignore objective prose and progress_observed; only current evidence can reset limits.
        if action and action["type"] != "wait":
            fingerprint = digest(json.dumps([state, action], sort_keys=True).encode())
            self.data["history"] = (self.data["history"] + [fingerprint])[-16:]
            count = self.data["history"].count(fingerprint)
            if count >= 3:
                self.runtime.host.record(
                    "gameplay.stall", {"fingerprint": fingerprint, "count": count}
                )
            if count >= 5:
                self.block("repeated-state-and-action-cycle")
        self.runtime.progress.save()
        self.check()

    def block(self, reason):
        payload = {"reason": reason, **self.data}
        self.runtime.host.record("gameplay.watchdog.blocked", payload)
        self.runtime.progress.save()
        raise FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, f"Progress watchdog: {reason}")
