"""Unified, read-only Agent observability view over the existing event store."""

import base64
import hashlib
import html
import json
from pathlib import Path
from string import Template

from game_automation.contracts import validate

from .action_evidence import action_figures
from .agent_metrics import trace_metrics, version_identity
from .agent_quality import quality_report
from .agent_sessions import session_replay
from .execution import snapshot_metadata
from .gameplay import action_path
from .trace_projection import project_trace


def agent_view(store, run_id):
    view = project_trace(store, run_id)
    view["snapshot"] = snapshot_metadata(store, run_id)
    owners = store.events(run_id, kinds=("agent.actor_assigned",), limit=1)
    actor = owners[0]["payload"]["actor_ref"] if owners else None
    view["actor_ref"] = actor
    view["version"] = version_identity(store, run_id)
    view["metrics"] = trace_metrics(view["spans"], actor)
    view["quality"] = quality_report(store, run_id, view["spans"])
    view["replay"] = session_replay(store, run_id, view["spans"])
    view["actions"] = action_path(store, run_id) if store.run(run_id)["mode"] == "gameplay" else []
    view["action_cards"] = render_action_cards(store, run_id, view["actions"])
    view["analyses"] = []
    for run in store.task_runs(view["trace_id"]):
        if run["mode"] != "analysis" or store.run_task(run["run_id"]).get("source_run") != run_id:
            continue
        events = store.events(run["run_id"], kinds=("agent.analysis.completed",), limit=1)
        if events:
            view["analyses"].append({"run_id": run["run_id"], **events[0]["payload"]})
    validate(store.root / "contracts/v1", "agent-trace", view)
    return view


def render_action_cards(store, run_id, actions):
    action_cards = []
    for action in actions[-2000:]:
        figures = action_figures(store, run_id, action)
        payload = html.escape(json.dumps(action, ensure_ascii=False, indent=2))
        action_id = html.escape(action["action_id"], quote=True)
        kind = html.escape((action["proposal"] or {}).get("type", "unknown"))
        state = html.escape(action["status"])
        duration = action["execution_duration_ms"]
        timing = f"{duration} ms" if duration is not None else "Unavailable"
        span_id = html.escape(action["span_id"], quote=True)
        action_cards.append(
            f'<article class="action-card" id="{action_id}" data-span="{span_id}">'
            f"<h3>{kind} <span class=muted>{state} · {timing}</span></h3>"
            f'<p><a href="trace.html#view=calls&amp;span={span_id}">Inspect execution call</a></p>'
            f"{figures}<details><summary>Recorded action data</summary><pre>{payload}</pre>"
            "</details></article>"
        )
    return action_cards


def render_agent_trace(store, run_id):
    view = agent_view(store, run_id)
    assets = Path(__file__).parent / "web"
    script = (assets / "agent-trace.js").read_text(encoding="utf-8")
    script_element = f"<script>{script}</script>"
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode("ascii")
    data = json.dumps(view, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    style = (assets / "agent-trace.css").read_text(encoding="utf-8")
    style += (assets / "action-evidence.css").read_text(encoding="utf-8")
    template = Template((assets / "agent-trace.template.html").read_text(encoding="utf-8"))
    document = template.substitute(
        title=html.escape(run_id),
        style=style,
        data=data,
        script_hash=script_hash,
        script_element=script_element,
        actions="",
        exploration_link='<a href="exploration.html">Exploration catalog</a>'
        if store.run(run_id)["mode"] in ("gameplay", "workflow")
        else "",
    )
    result = (document, view)
    return result
