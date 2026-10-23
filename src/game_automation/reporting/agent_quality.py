"""Evidence-based automatic evaluators and a separate human-label denominator."""


def quality_report(store, run_id, spans):
    reviews = {}
    for event in store.iter_events(run_id, kinds=("agent.review",)):
        value = event["payload"]
        reviews[(value["span_id"], value["metric"], value["actor_ref"])] = value
    labels = list(reviews.values())
    human = {}
    for metric in ("accuracy", "tool_correctness", "task_completion"):
        scored = [
            value for value in labels if value["metric"] == metric and value["label"] != "unknown"
        ]
        human[metric] = {
            "pass_rate": sum(value["label"] == "pass" for value in scored) / len(scored)
            if scored
            else None,
            "scored_labels": len(scored),
            "basis": "Latest human labels per span, metric and opaque reviewer reference",
        }
    calls = [span for span in spans if span["kind"] in ("chat", "tool")]
    actions = store.action_records(run_id)
    goals = store.events(run_id, kinds=("gameplay.goal.completed",))
    quality = {
        "automatic": {
            "call_completion_rate": sum(span["status"] == "ok" for span in calls) / len(calls)
            if calls
            else None,
            "calls": len(calls),
            "verified_action_rate": sum(action["status"] == "verified" for action in actions)
            / len(actions)
            if actions
            else None,
            "actions": len(actions),
            "verified_business_goals": len(goals),
            "basis": (
                "Recorded kernel outcomes and their local/model review source; "
                "not ground-truth accuracy"
            ),
            "unclosed_spans": sum(span["status"] == "incomplete" for span in spans),
        },
        "human": human,
        "annotations": labels,
        "accuracy": "Unknown without labeled ground truth; task success is not model accuracy",
    }
    return quality
