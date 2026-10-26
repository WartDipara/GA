"""Shared report navigation and retained-evidence links."""

import hashlib
import html
import re
from base64 import b64encode
from pathlib import Path
from urllib.parse import quote

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
        return document
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
            choices.append(f'<a href="../../{sibling}/reports/index.html">{label}</a>')
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
    return navigation_script(document)


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
