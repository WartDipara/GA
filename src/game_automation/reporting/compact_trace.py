"""Bounded HTML projection for long runs; complete records remain in SQLite and call files."""

import html
import json


def export_compact(store, run_id):
    kinds = (
        "call.started",
        "gameplay.step.started",
        "call.completed",
        "call.failed",
        "call.incomplete",
        "run.state",
        "action.intent",
        "action.dispatched",
        "action.result",
        "gameplay.decision",
        "gameplay.outcome",
        "gameplay.goal.completed",
        "gameplay.observation",
    )
    events = store.events(run_id, kinds=kinds, limit=2000)
    parents = {
        event["span_id"]: event["parent_span_id"]
        for event in events
        if event["kind"] in ("call.started", "gameplay.step.started")
    }
    rows = []
    for event in events:
        ancestors = set()
        parent = parents.get(event["span_id"])
        while parent and parent not in ancestors:
            ancestors.add(parent)
            parent = parents.get(parent)
        indent = min(6, len(ancestors)) * 20
        payload = json.dumps(event["payload"], ensure_ascii=False, indent=2)
        preview = html.escape(payload[:2000])
        label = html.escape(f"#{event['seq']} {event['kind']} {event['span_id'] or ''}")
        rows.append(
            f'<details style="margin-left:{indent}px"><summary>{label}</summary>'
            f"<pre>{preview}</pre></details>"
        )
    document = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'\">"
        "<title>Execution timeline</title><style>body{font:16px system-ui;max-width:1100px;"
        "margin:32px auto;padding:20px;background:#101820;color:#e6edf3}"
        "details{padding:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#84caff}</style>"
        f"<h1>Execution timeline</h1><p>{html.escape(run_id)}</p>"
        "<p>This large run shows the latest 2,000 call and execution events with bounded previews. "
        "Complete provider events and earlier records remain in SQLite and the calls directory. "
        "Use game-auto trace or game-auto inspect for complete stored records.</p>"
        '<p><a href="gameplay.html">Gameplay actions, screenshots and decisions</a></p>'
        + "".join(rows)
    )
    target = store.run_dir(run_id) / "reports/trace.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8", newline="\n")
    return target
