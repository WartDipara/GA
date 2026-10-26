import base64
import hashlib
import html
import json
from pathlib import Path
from string import Template
from urllib.parse import quote

from game_automation.common import FrameworkError, atomic_json, atomic_text, within
from game_automation.observability import build_insights


def export_trace(store, run_id):
    """Offline context dashboard and raw evidence viewer; no network or device calls."""
    path = export_agent_trace(store, run_id)
    export_context(store, run_id)
    return path


def export_agent_trace(store, run_id):
    document, view = render_trace(store, run_id)
    path = store.run_dir(run_id) / "reports/trace.html"
    atomic_json(path.with_name("agent-trace.json"), view)
    atomic_text(path, document)
    return path


def export_context(store, run_id):
    context, insights = render_context(store, run_id)
    path = store.run_dir(run_id) / "reports/context.html"
    atomic_text(path, context)
    if insights is not None:
        atomic_json(path.with_name("context.json"), insights)
    return path


def render_trace(store, run_id, *, projector=None, live=False):
    from .agent_trace import render_agent_trace

    return render_agent_trace(store, run_id, projector=projector, live=live)


def render_context(store, run_id):
    """Render current committed evidence without changing files or database state."""
    if store.event_bytes(run_id) > 16 * 1024**2:
        from game_automation.reporting.compact_trace import render_compact

        result = (render_compact(store, run_id), None)
        return result
    events = store.events(run_id)
    start_kinds = ("call.started", "gameplay.step.started", "workflow.stage.started")
    spans = {event["span_id"]: event for event in events if event["kind"] in start_kinds}
    children = {}
    for span, event in spans.items():
        parent = event["parent_span_id"]
        if parent not in spans:
            parent = None
        children.setdefault(parent, []).append(span)

    def render_event(event):
        payload = event["payload"]
        content = html.escape(json.dumps(payload, ensure_ascii=False, indent=2))
        artifacts = []
        child_run = payload.get("child_run")
        if (
            event["kind"].startswith("workflow.stage.")
            and child_run
            and store.run(child_run)["task_id"] == store.run(run_id)["task_id"]
        ):
            child_link = html.escape(f"../../{child_run}/reports/trace.html", quote=True)
            artifacts.append(f'<p><a href="{child_link}">Open stage trace</a></p>')
        for key in ("input_ref", "output_ref", "evidence_ref", "context_ref"):
            relative = payload.get(key)
            if isinstance(relative, str):
                try:
                    path = within(store.run_dir(run_id), relative)
                except FrameworkError:
                    artifacts.append("<p>Invalid evidence reference</p>")
                    continue
                if path.is_file():
                    if path.suffix == ".json":
                        text = path.read_text(encoding="utf-8")
                        artifacts.append(
                            f"<details><summary>{html.escape(key)}</summary><pre>{html.escape(text)}</pre></details>"
                        )
                    elif (
                        path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".svg")
                        and path.stat().st_size <= 8 * 1024 * 1024
                    ):
                        mime = {
                            ".png": "image/png",
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".webp": "image/webp",
                            ".svg": "image/svg+xml",
                        }[path.suffix.lower()]
                        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                        artifacts.append(
                            '<figure><img style="max-width:480px;width:100%" '
                            f'src="data:{mime};base64,{encoded}" '
                            f'alt="{html.escape(relative, quote=True)}">'
                            f"<figcaption>{html.escape(relative)}</figcaption></figure>"
                        )
                    else:
                        evidence_link = html.escape(f"../{quote(relative, safe='/')}", quote=True)
                        artifacts.append(f'<a href="{evidence_link}">{html.escape(relative)}</a>')
        return (
            f"<details><summary>#{event['seq']} {html.escape(event['kind'])} "
            f"{html.escape(event['timestamp'])}</summary><pre>{content}</pre>{''.join(artifacts)}</details>"
        )

    def render_span(span, visited=frozenset()):
        if span in visited:
            return "<p>Cycle in recorded trace</p>"
        event = spans[span]
        own = "".join(render_event(event) for event in events if event["span_id"] == span)
        nested = "".join(render_span(child, visited | {span}) for child in children.get(span, []))
        rendered_span = (
            f"<details open><summary>{html.escape(event['payload']['capability'])}  -  "
            f"{html.escape(span)}</summary>{own}{nested}</details>"
        )
        return rendered_span

    body = "".join(render_span(span_id) for span_id in children.get(None, []))
    body += "<h2>Workflow events</h2>" + "".join(
        render_event(event) for event in events if event["span_id"] not in spans
    )
    body += "<h2>Chronological index</h2>" + "".join(render_event(event) for event in events)
    insights = build_insights(store, run_id)
    # JSON embedded in HTML must not terminate its non-executable script element.
    embedded = (
        json.dumps(insights, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    assets = Path(__file__).parent / "web"
    script = (assets / "dashboard.js").read_text(encoding="utf-8")
    css = (assets / "dashboard.css").read_text(encoding="utf-8")
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode("ascii")
    template = Template((assets / "trace.template.html").read_text(encoding="utf-8"))
    document = template.substitute(
        script_hash=script_hash,
        run_title=html.escape(run_id),
        style=css,
        raw_evidence=body,
        dashboard_data=embedded,
        dashboard_script=script,
    )
    result = (document, insights)
    return result
