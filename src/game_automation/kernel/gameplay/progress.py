"""Durable identity and progress are separate from visual caches and action history."""

from game_automation.common import FrameworkError, digest
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.credentials import profile_path


class ProgressStore:
    def __init__(self, store, run_id, task):
        self.store = store
        self.run_id = run_id
        self.key = ":".join(
            (task["instance_id"], task["metadata"]["sha256"], task["credential_profile"])
        )
        previous = store.progress(self.key)
        self.data = (
            previous["data"]
            if previous
            else {
                "identity": None,
                "checkpoint": None,
                "pending": None,
                "paths": [],
                "sensitive_regions": [],
                "recovery_rounds": 0,
            }
        )
        self.previous_run = previous["run_id"] if previous else None
        revision = digest(profile_path(store.root, task["credential_profile"]).read_bytes())
        if self.data.get("credential_revision", revision) != revision:
            raise FrameworkError(
                ErrorCode.CREDENTIAL_UNAVAILABLE,
                "Credential profile changed; use a new profile reference",
            )
        self.data["credential_revision"] = revision
        self.data.setdefault("goals", {})
        self.data.setdefault("milestones", {})
        if previous:
            store.event(
                run_id,
                "gameplay.progress.restored",
                {
                    "previous_run": self.previous_run,
                    "identity": self.data["identity"],
                    "checkpoint": self.data["checkpoint"],
                    "server_state_requires_observation": True,
                },
            )

    def update(self, decision, observation_id):
        recovery = decision.get("recovery")
        if recovery:
            attempts = dict(self.data.get("recovery_attempts", {}))
            fault = recovery["fault"]
            if recovery["phase"] == "begin":
                attempts[fault] = attempts.get(fault, 0) + 1
                if attempts[fault] > 2:
                    raise FrameworkError(
                        ErrorCode.GAMEPLAY_BLOCKED,
                        "Automatic recovery limit reached for this fault",
                    )
            self.data["recovery_attempts"] = attempts
            self.store.event(self.run_id, "gameplay.recovery", recovery)
        goals = dict(self.data["goals"])
        milestones = dict(self.data["milestones"])
        for proposal in decision.get("subgoals", []):
            known_records = [goals.get(proposal["id"]), milestones.get(proposal["id"])]
            known_refs = {
                reference
                for record in known_records
                if record and record["status"] == "verified"
                for reference in record["evidence_refs"]
            }
            prior_proof = set(proposal["evidence_refs"]).issubset(known_refs)
            if (
                proposal["status"] == "verified"
                and not prior_proof
                and observation_id not in proposal["evidence_refs"]
            ):
                raise FrameworkError(ErrorCode.AI_OUTPUT_INVALID, "Subgoal proof is missing")
            goals[proposal["id"]] = proposal
        if len(goals) > 100:
            raise FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, "Subgoal queue requires a new scope")
        for goal in goals.values():
            visited = set()
            pending = list(goal["requires"])
            while pending:
                dependency = pending.pop()
                if (
                    dependency == goal["id"]
                    or dependency not in goals
                    and dependency not in milestones
                ):
                    raise FrameworkError(ErrorCode.AI_OUTPUT_INVALID, "Invalid subgoal dependency")
                if dependency not in visited:
                    pending.extend(goals.get(dependency, {}).get("requires", []))
                    visited.add(dependency)
            if goal["status"] == "active" and any(
                (goals.get(key) or milestones[key])["status"] != "verified"
                for key in goal["requires"]
            ):
                raise FrameworkError(ErrorCode.AI_OUTPUT_INVALID, "Subgoal prerequisites are unmet")
        for proposal in decision.get("progress_update", []):
            if observation_id not in proposal["evidence_refs"]:
                raise FrameworkError(ErrorCode.AI_OUTPUT_INVALID)
            milestones[proposal["id"]] = {**proposal, "run_id": self.run_id}
        if len(milestones) > 500:
            raise FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, "Milestone scope is full")
        self.data["goals"] = goals
        self.data["milestones"] = milestones

    def save(self):
        # Complete history remains in events/actions; only recent edges belong in model context.
        self.data["paths"] = self.data["paths"][-50:]
        self.store.checkpoint(self.run_id, self.key, self.data)
