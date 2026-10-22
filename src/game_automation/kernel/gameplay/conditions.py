def condition_matches(condition, observation, perception):
    kind = condition["kind"]
    value = condition["value"]
    display = observation["runtime"]["focused_display"]
    if kind == "screen":
        return perception["screen_key"] == value
    if kind == "image":
        return observation["screenshot"]["sha256"] == value
    if kind == "control":
        return any(item["target_ref"] == value for item in perception["candidates"])
    if kind == "focused_package":
        return display is not None and display["package_name"] == value
    if kind == "alive":
        return observation["runtime"]["alive"] == (value == "true")
    return False
