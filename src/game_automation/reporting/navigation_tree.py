"""Pre-rendered live navigation over task and execution summaries."""

import html
import json
import re
from urllib.parse import quote

from game_automation.contracts import validate

from .navigation import links_for_mode, workspace_shell

REPORT_ICONS = {
    "index.html": "grid",
    "trace.html": "trace",
    "context.html": "document",
    "gameplay.html": "pointer",
    "exploration.html": "grid",
    "execution.html": "check",
}


def icon(name):
    return f'<svg class="ui-icon" aria-hidden="true"><use href="#workspace-icon-{name}" /></svg>'


def folder(key, title, content, *, opened=False, note="", status=""):
    expanded = " open" if opened else ""
    state = f' data-status="{html.escape(status, quote=True)}"' if status else ""
    return (
        f'<details class="ui-nav-folder" data-nav-key="{html.escape(key, quote=True)}"{expanded}>'
        f'<summary>{icon("chevron")}<span class="ui-nav-name">{html.escape(title)}</span>'
        f'<span class="ui-nav-badge"{state}>{html.escape(note)}</span></summary>'
        f'<div class="ui-nav-children">{content}</div></details>'
    )


def leaf(label, href, *, selected=False, symbol="document", note=""):
    current = ' aria-current="page"' if selected else ""
    return (
        f'<a href="{html.escape(href, quote=True)}"{current}>{icon(symbol)}'
        f'<span class="ui-nav-name">{html.escape(label)}</span>'
        f'<span class="ui-nav-badge">{html.escape(note)}</span></a>'
    )


def navigation_tree(store, path):
    parts = path.strip("/").split("/")
    task_id = parts[1] if len(parts) > 1 and parts[0] == "tasks" else None
    run_id = parts[3] if len(parts) > 3 and parts[2] == "executions" else None
    current = parts[-1]
    rows = store.db.execute(
        "SELECT t.id AS task_id,r.id AS run_id,r.mode,r.status,"
        "json_extract(i.spec,'$.metadata.apkName') AS title,"
        "ROW_NUMBER() OVER (PARTITION BY t.id ORDER BY r.rowid) AS ordinal "
        "FROM tasks t LEFT JOIN runs r ON r.task_id=t.id "
        "LEFT JOIN run_inputs i ON i.run_id=r.id "
        "WHERE NOT EXISTS (SELECT 1 FROM task_aliases a WHERE a.alias_id=t.id) "
        "ORDER BY t.rowid DESC,r.rowid DESC"
    )
    tasks = {}
    for row in rows:
        task = tasks.setdefault(row["task_id"], {"title": "Game testing task", "runs": []})
        if task["title"] == "Game testing task" and row["title"]:
            task["title"] = row["title"]
        if row["run_id"]:
            task["runs"].append(dict(row))
    groups = []
    for identifier, task in tasks.items():
        base = f"/tasks/{quote(identifier, safe='')}/"
        executions = []
        for run in task["runs"]:
            report_base = f"{base}executions/{quote(run['run_id'], safe='')}/reports/"
            reports = []
            for report in links_for_mode(run["mode"]):
                reports.append(
                    leaf(
                        report["name"],
                        report_base + report["href"],
                        selected=run["run_id"] == run_id and current == report["href"],
                        symbol=REPORT_ICONS[report["href"]],
                    )
                )
            executions.append(
                folder(
                    run["run_id"],
                    f"{run['mode'].replace('_', ' ').title()} #{run['ordinal']}",
                    "".join(reports),
                    opened=run["run_id"] == run_id,
                    note=run["status"],
                    status=run["status"],
                )
            )
        overview = leaf(
            "Task overview",
            base + "reports/task.html",
            selected=identifier == task_id and run_id is None,
            symbol="grid",
        )
        monitor = leaf("Live monitor", base, selected=path == base, symbol="grid")
        content = (
            overview
            + monitor
            + folder(
                f"executions:{identifier}",
                "Executions",
                "".join(executions),
                opened=identifier == task_id,
                note=str(len(executions)),
            )
        )
        groups.append(folder(identifier, task["title"], content, opened=identifier == task_id))
    root = leaf(
        "Task history",
        "/reports/history.html",
        selected=task_id is None,
        symbol="grid",
        note=str(len(tasks)),
    )
    return root + '<p class="ui-tree-label">TASKS</p>' + "".join(groups)


def live_workspace(store, document, path):
    source = re.search(
        r'<script\b[^>]*id="workspace-data"[^>]*>(.*?)</script>', document, re.DOTALL
    )
    if not source:
        return document
    view = json.loads(source[1])
    validate(store.root / "contracts/v1", "workspace-view", view)
    shell = workspace_shell(view, navigation=navigation_tree(store, path))
    opening = re.search(r'<div\b[^>]*id="workspace-shell"[^>]*>', document)
    if not opening:
        return document
    depth = 0
    for tag in re.finditer(r"</?div\b[^>]*>", document[opening.start() :]):
        depth += -1 if tag[0].startswith("</") else 1
        if depth == 0:
            end = opening.start() + tag.end()
            document = document[: opening.start()] + shell + document[end:]
            break
    return document
