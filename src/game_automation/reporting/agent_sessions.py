"""Replay application-visible turns and framework tools; hidden provider context stays unknown."""

import json

from game_automation.common import FrameworkError, within


def file_preview(store, run_id, reference):
    if not reference:
        return None
    try:
        path = within(store.run_dir(run_id), reference)
        if path.stat().st_size > 1024**2:
            return "Preview exceeds 1 MiB; original retained in the referenced call file"
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FrameworkError, OSError, ValueError):
        return "Referenced call evidence unavailable"
    return value


def session_replay(store, run_id, spans):
    items = {}
    kinds = ("ai.item.completed", "ai.tool.completed")
    for event in store.iter_events(run_id, kinds=kinds):
        items.setdefault(event["span_id"], []).append(
            {"event_id": event["event_id"], "seq": event["seq"], "item": event["payload"]["item"]}
        )
    sessions = {}
    for span in spans:
        if span["kind"] != "chat":
            continue
        attributes = span["attributes"]
        session_id = attributes.get("gen_ai.session.id") or f"unassociated:{span['span_id']}"
        session = sessions.setdefault(session_id, {"session_id": session_id, "turns": []})
        request = file_preview(store, run_id, attributes.get("game_automation.input_ref"))
        submitted = None
        if isinstance(request, dict):
            submitted = [
                {
                    "type": part.get("type"),
                    "name": part.get("name"),
                    "text_preview": part.get("text", "")[:2000],
                    "truncated": len(part.get("text", "")) > 2000,
                }
                for part in request.get("turn", {}).get("input", [])
            ]
        tools = [
            {
                "span_id": other["span_id"],
                "name": other["name"],
                "status": other["status"],
                "duration_ms": other["duration_ms"],
                "input_ref": other["attributes"].get("game_automation.input_ref"),
                "output_ref": other["attributes"].get("game_automation.output_ref"),
                "source": "framework tool, not a provider-issued Tool Call",
            }
            for other in spans
            if other["kind"] == "tool" and other["parent_span_id"] == span["parent_span_id"]
        ]
        turn = {
            "turn_id": attributes.get("gen_ai.turn.id"),
            "span_id": span["span_id"],
            "start_time": span["start_time"],
            "status": span["status"],
            "submitted": submitted,
            "input_ref": attributes.get("game_automation.input_ref"),
            "output_ref": attributes.get("game_automation.output_ref"),
            "public_items": items.get(span["span_id"], []),
            "framework_tools": tools,
            "usage": span.get("usage"),
        }
        session["turns"].append(turn)
    replay = {
        "sessions": list(sessions.values()),
        "visibility": "Submitted application input and public items only; no hidden context",
        "mode": "offline reconstruction; never reissues tools or device actions",
    }
    return replay
