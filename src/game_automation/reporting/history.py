"""Read models and automatic indexes over recorded tasks and executions."""

import hashlib
import html
import json
from base64 import b64encode

from game_automation.common import atomic_json, atomic_text
from game_automation.contracts import validate

from .navigation import (
    ASSETS,
    REPORTS,
    decorate_document,
    navigation_script,
    page_document,
    report_links,
)


def history_view(store):
    if store is None:
        result = {"tasks": [], "authority": "No recorded task database yet"}
        return result
    rows = store.db.execute(
        "SELECT t.id AS task_id,COUNT(r.id) AS executions,MIN(r.created_at) AS created_at,"
        "MAX(r.updated_at) AS updated_at,"
        "SUM(CASE WHEN r.status='running' THEN 1 ELSE 0 END) AS running,"
        "SUM(CASE WHEN r.status IN ('blocked','failed') THEN 1 ELSE 0 END) AS issues "
        "FROM tasks t LEFT JOIN runs r ON r.task_id=t.id "
        "WHERE NOT EXISTS (SELECT 1 FROM task_aliases a WHERE a.alias_id=t.id) "
        "GROUP BY t.id ORDER BY MAX(r.rowid) DESC,t.rowid DESC"
    )
    tasks = []
    for row in rows:
        item = dict(row)
        title = store.db.execute(
            "SELECT json_extract(i.spec,'$.metadata.apkName') FROM run_inputs i "
            "JOIN runs r ON r.id=i.run_id WHERE r.task_id=? "
            "AND json_extract(i.spec,'$.metadata.apkName') IS NOT NULL "
            "ORDER BY r.rowid DESC LIMIT 1",
            (item["task_id"],),
        ).fetchone()
        item["title"] = title[0] if title else "Game testing task"
        item["href"] = f"../tasks/{item['task_id']}/reports/task.html"
        tasks.append(item)
    result = {"tasks": tasks, "authority": "Committed task and execution records in SQLite"}
    return result


def render_history(store):
    view = history_view(store)
    data = json.dumps(view, ensure_ascii=False).replace("<", "\\u003c")
    body = (
        '<p><a data-report-back href="history.html">Back</a></p>'
        "<p>Browse retained tasks, execution attempts and their recorded evidence.</p>"
        '<div class="history-toolbar"><input id="history-search" type="search" '
        'placeholder="Search task or game"><select id="history-state">'
        '<option value="all">All tasks</option><option value="running">Running</option>'
        '<option value="issues">With recorded issues</option></select>'
        '<span id="history-count"></span>'
        '<span id="history-connection">Recorded snapshot</span></div>'
        "<table><thead><tr><th>Task / game</th><th>Executions</th><th>Running</th>"
        "<th>Blocked / failed</th><th>Last recorded update</th></tr></thead>"
        '<tbody id="history-rows"></tbody></table>'
        '<div class="history-toolbar"><button id="history-previous">Previous</button>'
        '<button id="history-next">Next</button></div>'
        f'<script type="application/json" id="history-data">{data}</script>'
    )
    script = (ASSETS / "history.js").read_text(encoding="utf-8")
    digest = b64encode(hashlib.sha256(script.encode()).digest()).decode("ascii")
    document = page_document("Task history", body)
    document = document.replace(
        "default-src 'none';", f"default-src 'none'; script-src 'sha256-{digest}';", 1
    )
    document = document.replace("</body>", f"<script>{script}</script></body>", 1)
    return navigation_script(document)


def task_document(store, task_id):
    rows = []
    for execution in reversed(store.task_runs(task_id)):
        run_id = execution["run_id"]
        links = (
            f'<a href="../executions/{run_id}/reports/index.html">Execution overview</a> · '
            f'<a href="../executions/{run_id}/reports/trace.html">Call trace</a>'
        )
        rows.append(
            f"<tr><td>{html.escape(execution['mode'])}<small>{html.escape(run_id)}</small></td>"
            f"<td>{html.escape(execution['status'])}</td>"
            f"<td>{html.escape(execution['stage'] or '')}</td><td>{links}</td></tr>"
        )
    body = (
        '<p><a data-report-back href="../../../reports/history.html">Back</a> · '
        '<a href="../../../reports/history.html">Task history</a> · '
        '<a data-live-view href="../">Live task workspace</a></p>'
        f"<p>{html.escape(task_id)}</p>"
        "<table><thead><tr><th>Execution</th><th>Status</th><th>Stage</th><th>Open</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )
    return navigation_script(page_document("Task overview", body))


def execution_index(store, run_id):
    run = store.run(run_id)
    states = store.events(run_id, kinds=("run.state",), limit=1)
    through = states[0]["seq"] if states else 0
    counts = store.db.execute(
        "SELECT COUNT(*) AS events,MIN(seq) AS first_seq,MAX(seq) AS last_seq,"
        "SUM(CASE WHEN kind='call.started' THEN 1 ELSE 0 END) AS calls,"
        "SUM(CASE WHEN kind='call.started' "
        "AND json_extract(data,'$.payload.capability')='ai.decide' "
        "THEN 1 ELSE 0 END) AS model_calls FROM events WHERE run_id=? AND seq<=?",
        (run_id, through),
    ).fetchone()
    spec = store.run_task(run_id)
    relations = []
    for field, relation in (
        ("workflow_run", "parent"),
        ("prepared_run", "prepared"),
        ("source_run", "source"),
    ):
        target = spec.get(field)
        target_run = (
            store.db.execute("SELECT task_id FROM runs WHERE id=?", (target,)).fetchone()
            if target
            else None
        )
        if target_run and target_run[0] == run["task_id"]:
            relations.append({"relation": relation, "run_id": target})
    for event in store.events(run_id, kinds=("run.continued_from",)):
        target = event["payload"]["run_id"]
        target_run = store.db.execute("SELECT task_id FROM runs WHERE id=?", (target,)).fetchone()
        if target_run and target_run[0] == run["task_id"]:
            relations.append({"relation": "previous_batch", "run_id": target})
    result = {
        "schema_version": "1.0",
        "task_id": run["task_id"],
        "run_id": run_id,
        "mode": run["mode"],
        "status": run["status"],
        "stage": run["stage"],
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "source_event_seq": through,
        "statistics": dict(counts),
        "relations": relations,
        "views": report_links(store, run_id),
        "storage": {
            "manifest": "../manifest.json",
            "calls": "../calls/",
            "artifacts": "../artifacts/",
            "events": "SQLite",
        },
        "generation": "kernel-report-publication",
    }
    validate(store.root / "contracts/v1", "execution-index", result)
    return result


def render_execution(store, run_id):
    index = execution_index(store, run_id)
    links = "".join(
        f'<li><a href="{item["href"]}">{item["name"]}</a></li>'
        for item in index["views"]
        if item["href"] != "index.html"
    )
    related = "".join(
        f"<li>{html.escape(item['relation'])}: "
        f'<a href="../../{item["run_id"]}/reports/index.html">{item["run_id"]}</a></li>'
        for item in index["relations"]
    )
    statistics = html.escape(json.dumps(index["statistics"], indent=2))
    body = (
        f"<p>{html.escape(run_id)} · {html.escape(index['status'])}</p>"
        f"<ul>{links}</ul><h2>Related executions</h2><ul>{related}</ul>"
        f"<h2>Recorded statistics</h2><pre>{statistics}</pre>"
        '<p><a href="index.json">Structured index</a> · '
        '<a href="publication.json">Report publication</a></p>'
    )
    document = decorate_document(
        store, run_id, page_document("Execution overview", body), "index.html"
    )
    result = (document, index)
    return result


def index_reports(store, run_id):
    document, index = render_execution(store, run_id)
    directory = store.run_dir(run_id) / "reports"
    atomic_json(directory / "index.json", index)
    atomic_text(directory / "index.html", document)
    for name in REPORTS:
        path = directory / name
        if path.is_file():
            original = path.read_text(encoding="utf-8")
            decorated = decorate_document(store, run_id, original, name)
            if decorated != original:
                atomic_text(path, decorated)
    write_history(store)


def write_history(store):
    directory = store.var / "reports"
    atomic_json(directory / "history.json", history_view(store))
    atomic_text(directory / "history.html", render_history(store))
