import base64
import html
import json
from pathlib import Path

from game_automation.common import FrameworkError, atomic_json, within
from game_automation.observability.insights import build_insights


def export_trace(store, run_id):
    """Offline context dashboard and raw evidence viewer; no network or device calls."""
    events = store.events(run_id)
    spans = {e["span_id"]: e for e in events if e["kind"] == "call.started"}
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
                            f'<figure><img style="max-width:480px;width:100%" src="data:{mime};base64,{encoded}" alt="{html.escape(relative, quote=True)}"><figcaption>{html.escape(relative)}</figcaption></figure>'
                        )
                    else:
                        artifacts.append(
                            f'<a href="{html.escape(path.as_uri(), quote=True)}">{html.escape(relative)}</a>'
                        )
        return (
            f"<details><summary>#{event['seq']} {html.escape(event['kind'])} "
            f"{html.escape(event['timestamp'])}</summary><pre>{content}</pre>{''.join(artifacts)}</details>"
        )

    def render_span(span, visited=frozenset()):
        if span in visited:
            return "<p>Cycle in recorded trace</p>"
        event = spans[span]
        own = "".join(render_event(e) for e in events if e["span_id"] == span)
        nested = "".join(render_span(child, visited | {span}) for child in children.get(span, []))
        return f"<details open><summary>{html.escape(event['payload']['capability'])}  -  {html.escape(span)}</summary>{own}{nested}</details>"

    body = "".join(render_span(s) for s in children.get(None, []))
    body += "<h2>Workflow events</h2>" + "".join(
        render_event(e) for e in events if e["span_id"] not in spans
    )
    body += "<h2>Chronological index</h2>" + "".join(render_event(e) for e in events)
    insights = build_insights(store, run_id)
    # JSON embedded in HTML must not terminate its non-executable script element.
    embedded = (
        json.dumps(insights, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    assets = Path(__file__).parent
    script = (assets / "dashboard.js").read_text(encoding="utf-8")
    css = (assets / "dashboard.css").read_text(encoding="utf-8")
    import hashlib

    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode("ascii")
    document = f"""<!doctype html><html lang="en-US"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; script-src 'sha256-{script_hash}'; base-uri 'none'; form-action 'none'">
<title>Context insights  |  {html.escape(run_id)}</title><style>{css}</style></head><body><main>
<header><div class="eyebrow">GAME AUTOMATION  |  CONTEXT INSIGHTS</div><h1 id="run-title">Context insights</h1><p id="run-meta"></p></header>
<p class="notice" id="visibility"></p><section class="metrics" id="metrics" aria-label="Run overview"></section>
<div class="grid"><div>
<section class="panel"><h2>Turn trend</h2><small>Each bar is an observed application turn. Height estimates visible input text, not internal model requests or context occupancy.</small><div id="trend" class="trend"></div></section>
<section class="panel"><h2>Provider-reported usage</h2><div id="actuals" class="actuals"></div></section>
<section class="panel"><h2>Content composition</h2><small>Visible inputs and public responses for the selected turn. Skills are separate; hidden content is excluded.</small><div class="composition" id="composition"></div><div id="legend" class="legend"></div><p id="diff" class="muted"></p><p id="summary-state" class="muted"></p></section>
</div><div><section class="panel"><h2>Context browser</h2><select id="request-select" aria-label="Select turn"></select><p id="request-info" class="muted"></p><div id="categories" class="categories"></div><input id="content-search" class="search" placeholder="Search skills, instructions, inputs, or outputs" aria-label="Search context"><div id="browser" class="browser"></div><div id="notes"></div></section></div></div>
<section class="panel"><h2>Context and execution events</h2><div class="toolbar"><select id="event-filter" aria-label="Event scope"><option value="all">All turns and execution events</option><option value="request">Selected turn events</option></select><input id="event-search" placeholder="Search events" aria-label="Search events"></div><div id="context-events"></div></section>
<section class="panel raw"><details><summary>Expand raw call tree and event evidence</summary>{body}</details></section>
<noscript><p class="notice">JavaScript is disabled. Raw records remain available below; the complete read model is in context.json.</p></noscript>
<footer>This view filters saved records locally without network or device calls. Fixtures verify framework behavior only.
Design reference: <a href="https://github.com/bowenliang123/dsh-context">dsh-context</a> event projections and context browsing. This dashboard is an independent implementation.</footer>
</main><script type="application/json" id="insight-data">{embedded}</script><script data-local="dashboard">{script}</script></body></html>"""
    path = store.run_dir(run_id) / "reports/trace.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    atomic_json(path.with_name("context.json"), insights)
    return path
