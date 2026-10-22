from .conditions import condition_matches


class OutcomeVerifier:
    def verify(self, expected, before, after, perception):
        if not expected:
            status = "unknown"
        elif before["runtime"]["boot_id"] != after["runtime"]["boot_id"]:
            status = "deviated"
        elif all(condition_matches(item, after, perception) for item in expected):
            status = "verified"
        elif perception["stable"]:
            status = "deviated"
        else:
            status = "unknown"
        result = {
            "status": status,
            "observation_id": after["observation_id"],
            "before_observation_id": before["observation_id"],
            "source": "local-predicates",
            "business_goal_verified": False,
        }
        return result
