"""Native battle is a goal on the shared loop, not a second device execution engine."""

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


class NativeBattleProgress:
    def __init__(self, runtime):
        self.runtime = runtime
        self.data = runtime.progress.data.setdefault(
            "battle", {"phase": "unknown", "stage": None, "settlement": None, "advances": []}
        )

    def update(self, proposal, observation):
        if proposal is None:
            return
        current = observation["observation_id"]
        if current not in proposal["evidence_refs"]:
            raise FrameworkError(ErrorCode.AI_OUTPUT_INVALID, "Battle evidence is missing")
        phase, stage = proposal["phase"], proposal["stage"]
        previous = dict(self.data)
        if phase == "active" and not proposal["native_auto_observed"]:
            raise FrameworkError(
                ErrorCode.GAMEPLAY_BLOCKED,
                "Native automatic battle is not supported by current evidence",
            )
        if phase == "settlement" and proposal.get("settlement_result") == "victory":
            if previous["phase"] not in ("active", "settlement") or not previous["stage"]:
                raise FrameworkError(
                    ErrorCode.GAMEPLAY_BLOCKED,
                    "Settlement cannot be attributed to an observed battle",
                )
            self.data["settlement"] = {
                "stage": previous["stage"],
                "observation_id": current,
                "run_id": self.runtime.run_id,
                "result": "victory",
            }
        settlement = self.data["settlement"]
        if stage and settlement and stage != settlement["stage"]:
            advancement = {
                "from_stage": settlement["stage"],
                "to_stage": stage,
                "settlement": settlement,
                "observation_id": current,
                "source": "model-semantic-review",
                "run_id": self.runtime.run_id,
            }
            self.data["advances"].append(advancement)
            self.data["settlement"] = None
            self.runtime.host.record("gameplay.battle.advanced", advancement)
        self.data["phase"] = phase
        self.data["stage"] = stage or previous["stage"]
        self.runtime.host.record("gameplay.battle.observed", proposal)

    def goal_verified(self):
        return any(item["run_id"] == self.runtime.run_id for item in self.data["advances"])
