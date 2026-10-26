"""Shared report navigation and retained-evidence links."""

import hashlib
import html
import json
import re
from base64 import b64encode
from pathlib import Path
from string import Template
from urllib.parse import quote

from .workspace import workspace_view

ASSETS = Path(__file__).parent / "web"
REPORTS = {
    "index.html": "Execution",
    "trace.html": "Call trace",
    "context.html": "Model context",
    "gameplay.html": "Actions",
    "exploration.html": "Elements",
    "execution.html": "Outcome",
}


def report_links(store, run_id):
    mode = store.run(run_id)["mode"]
    links = []
    for name, label in REPORTS.items():
        if name == "gameplay.html" and mode != "gameplay":
            continue
        if name == "exploration.html" and mode not in ("gameplay", "workflow"):
            continue
        links.append({"name": label, "href": name})
    return links


def decorate_document(store, run_id, document, current):
    if 'id="report-navigation"' in document:
        return workspace_document(document, store, run_id=run_id, current=current)
    run = store.run(run_id)
    task_id = run["task_id"]
    links = report_links(store, run_id)
    choices = []
    for link in links:
        selected = ' aria-current="page"' if link["href"] == current else ""
        choices.append(f'<a href="{link["href"]}"{selected}>{link["name"]}</a>')
    siblings = store.task_runs(task_id)
    position = next(index for index, item in enumerate(siblings) if item["run_id"] == run_id)
    for offset, label in ((-1, "Previous execution"), (1, "Next execution")):
        target = position + offset
        if 0 <= target < len(siblings):
            sibling = quote(siblings[target]["run_id"], safe="")
            choices.append(
                f'<a data-execution-neighbor href="../../{sibling}/reports/index.html">{label}</a>'
            )
    fallback = "../../../reports/task.html" if current == "index.html" else "index.html"
    markup = (
        '<nav class="report-navigation" id="report-navigation" aria-label="Report navigation">'
        f'<div><a data-report-back href="{fallback}">Back</a>'
        '<a href="../../../../../reports/history.html">Task history</a>'
        '<a href="../../../reports/task.html">Task overview</a>'
        f"<span>{html.escape(run['mode'])} / {html.escape(run['status'])}</span></div>"
        f"<div>{''.join(choices)}</div></nav>"
    )
    style = f"<style>{(ASSETS / 'navigation.css').read_text(encoding='utf-8')}</style>"
    if "</head>" in document:
        document = document.replace("</head>", f"{style}</head>", 1)
        document = re.sub(r"<body\b[^>]*>", lambda match: match[0] + markup, document, count=1)
    else:
        document = document.replace("</style>", f"</style>{style}{markup}", 1)
    document = workspace_document(document, store, run_id=run_id, current=current)
    return navigation_script(document)


def workspace_document(document, store, *, run_id=None, task_id=None, current=""):
    if 'id="workspace-shell"' in document:
        return document
    # Retained reports may contain the earlier client-rendered shell bootstrap.
    document = re.sub(
        r'<script\b[^>]*id="workspace-data"[^>]*>.*?</script>',
        "",
        document,
        flags=re.DOTALL,
    )
    if run_id:
        prefix = "../../../../../"
        task_link = "../../../reports/task.html"
        links = [
            {"label": "Task history", "href": prefix + "reports/history.html", "current": False},
            {"label": "Task overview", "href": task_link, "current": False},
        ]
        for link in report_links(store, run_id):
            links.append(
                {"label": link["name"], "href": link["href"], "current": link["href"] == current}
            )
    elif task_id:
        prefix = "../../../"
        links = [
            {"label": "Task history", "href": prefix + "reports/history.html", "current": False},
            {"label": "Task overview", "href": "task.html", "current": True},
        ]
    else:
        prefix = "../"
        links = [{"label": "Task history", "href": "history.html", "current": True}]
    view = workspace_view(store, run_id=run_id, task_id=task_id, links=links)
    data = json.dumps(view, ensure_ascii=False).replace("<", "\\u003c")
    charts = not run_id or current in ("trace.html", "index.html")
    source = (
        f'<script id="workspace-data" type="application/json" '
        f'data-charts="{str(charts).lower()}">{data}</script>'
    )
    document = structured_document(document)
    shell = workspace_shell(view)
    document = re.sub(
        r"<body\b([^>]*)>",
        lambda match: workspace_body(match[1]) + shell,
        document,
        count=1,
    )
    document = (
        document.replace("</head>", f"{source}</head>", 1)
        if "</head>" in document
        else document.replace("</style>", f"</style>{source}", 1)
    )
    return theme_document(document, prefix)


def workspace_body(attributes):
    if 'class="' in attributes:
        attributes = attributes.replace('class="', 'class="ui-ready ', 1)
    else:
        attributes += ' class="ui-ready"'
    return f"<body{attributes}>"


def structured_document(document):
    if "</head>" in document:
        return document
    document = re.sub(r"(<html\b[^>]*>)", r"\1<head>", document, count=1)
    boundary = re.search(r"<(?:nav|h1|main)\b", document)
    if boundary:
        head = document[: boundary.start()]
        content = document[boundary.start() :].replace("</html>", "")
        document = f"{head}</head><body><main>{content}</main></body></html>"
    return document


def workspace_shell(view):
    links = []
    for index, link in enumerate(view["links"]):
        selected = ' aria-current="page"' if link["current"] else ""
        icon = "grid" if index == 0 else "trace"
        links.append(
            f'<a href="{html.escape(link["href"], quote=True)}"{selected}>'
            f'<svg class="ui-icon" aria-hidden="true"><use href="#workspace-icon-{icon}" />'
            f"</svg>{html.escape(link['label'])}</a>"
        )
    title = next((link["label"] for link in view["links"] if link["current"]), view["title"])
    template = Template((ASSETS / "workspace-shell.template.html").read_text(encoding="utf-8"))
    document = template.substitute(
        home=html.escape(view["links"][0]["href"], quote=True),
        links="".join(links),
        current=html.escape(title),
    )
    return document


def theme_document(document, prefix):
    if 'id="workspace-theme-bootstrap"' in document:
        return document
    document = re.sub(r'<link\b[^>]*href="[^"]*/trace_ui.css"[^>]*>', "", document)
    document = re.sub(r'<script\b[^>]*src="[^"]*/trace_ui.js"[^>]*></script>', "", document)
    assets = f"{prefix}cache/typescript/trace_ui"
    style = f'<link rel="stylesheet" href="{assets}.css">'
    script = f'<script defer blocking="render" src="{assets}.js"></script>'
    bootstrap = (ASSETS / "theme.js").read_text(encoding="utf-8")
    digest = b64encode(hashlib.sha256(bootstrap.encode()).digest()).decode("ascii")
    bootstrap_element = f'<script id="workspace-theme-bootstrap">{bootstrap}</script>'
    document = document.replace("script-src ", "script-src 'self' file: ", 1)
    if "script-src " not in document:
        document = document.replace(
            "default-src 'none';", "default-src 'none'; script-src 'self' file:;", 1
        )
    document = document.replace("style-src ", "style-src 'self' file: ", 1)
    document = document.replace("script-src ", f"script-src 'sha256-{digest}' ", 1)
    document = re.sub(
        r'<meta\b[^>]*http-equiv="Content-Security-Policy"[^>]*>',
        lambda match: match[0] + bootstrap_element,
        document,
        count=1,
        flags=re.IGNORECASE,
    )
    if "</head>" in document:
        document = document.replace("</head>", f"{style}{script}</head>", 1)
    else:
        document = document.replace("</style>", f"</style>{style}{script}", 1)
    return document


def navigation_script(document):
    script = (ASSETS / "navigation.js").read_text(encoding="utf-8")
    script_hash = b64encode(hashlib.sha256(script.encode()).digest()).decode("ascii")
    if "script-src " in document:
        document = document.replace("script-src ", f"script-src 'sha256-{script_hash}' ", 1)
    else:
        document = document.replace(
            "default-src 'none';", f"default-src 'none'; script-src 'sha256-{script_hash}';", 1
        )
    script_element = f"<script>{script}</script>"
    if "</body>" in document:
        document = document.replace("</body>", f"{script_element}</body>", 1)
    elif "</html>" in document:
        document = document.replace("</html>", f"{script_element}</html>", 1)
    else:
        document += script_element
    return document


def page_document(title, body):
    style = (ASSETS / "navigation.css").read_text(encoding="utf-8")
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; base-uri 'none'; "
        "form-action 'none'\">"
        f"<title>{html.escape(title)}</title><style>{style}</style></head>"
        f'<body class="history-page"><main><h1>{html.escape(title)}</h1>{body}</main></body></html>'
    )
    return document
