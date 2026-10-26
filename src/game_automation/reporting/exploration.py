"""Read-only exploration catalog projection; never starts a writer, model or device."""

import hashlib
import json
from base64 import b64encode
from collections import Counter
from pathlib import Path
from string import Template

from game_automation.common import atomic_text


def exploration_view(store, run_id, *, through=None):
    task_id = store.run(run_id)["task_id"]
    table = store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='exploration_records'"
    ).fetchone()
    records = []
    if through is not None:
        retained = {}
        rows = store.db.execute(
            "SELECT e.run_id,e.data FROM events e JOIN runs r ON r.id=e.run_id "
            "WHERE r.task_id=? AND e.seq<=? AND e.kind LIKE 'exploration.%.updated' "
            "ORDER BY e.seq",
            (task_id, through),
        )
        for row in rows:
            payload = json.loads(row["data"])["payload"]
            record = dict(payload["record"])
            record["workspace_key"] = payload["workspace_key"]
            record["run_id"] = row["run_id"]
            retained[(record["workspace_key"], record["kind"], record["id"])] = record
        records = list(retained.values())
    elif table:
        rows = store.db.execute(
            "SELECT workspace_key,run_id,data FROM exploration_records "
            "WHERE task_id=? ORDER BY rowid",
            (task_id,),
        )
        for row in rows:
            record = json.loads(row["data"])
            record["workspace_key"] = row["workspace_key"]
            record["run_id"] = row["run_id"]
            records.append(record)
    elements = [record for record in records if record["kind"] == "element"]
    limit = through if through is not None else 9223372036854775807
    locations = []
    for row in store.db.execute(
        "SELECT e.run_id,e.span_id,e.data FROM events e JOIN runs r ON r.id=e.run_id "
        "WHERE r.task_id=? AND e.seq<=? AND e.kind='exploration.location' "
        "ORDER BY e.seq DESC LIMIT 2000",
        (task_id, limit),
    ):
        location = json.loads(row["data"])["payload"]
        location["run_id"] = row["run_id"]
        location["span_id"] = row["span_id"]
        locations.append(location)
    result = {
        "task_id": task_id,
        "run_id": run_id,
        "scope": "Discovered reachable content; rendered evidence does not confirm hook delivery",
        "counts": dict(Counter(record["status"] for record in elements)),
        "records": records,
        "locations": locations,
        "source_event_seq": through,
        "view": "live task state" if through is None else "recorded business outcome",
    }
    return result


def render_exploration(store, run_id, *, current=False):
    states = store.events(run_id, kinds=("run.state",), limit=1)
    through = None if current else states[0]["seq"] if states else 0
    view = exploration_view(store, run_id, through=through)
    assets = Path(__file__).parent / "web"
    script = (assets / "exploration.js").read_text(encoding="utf-8")
    template = Template((assets / "exploration.template.html").read_text(encoding="utf-8"))
    document = template.substitute(
        style=(assets / "exploration.css").read_text(encoding="utf-8"),
        data=json.dumps(view, ensure_ascii=False).replace("<", "\\u003c"),
        script_hash=b64encode(hashlib.sha256(script.encode()).digest()).decode("ascii"),
        script_element=f"<script>{script}</script>",
    )
    result = (document, view)
    return result


def exploration_report(store, run_id):
    document, view = render_exploration(store, run_id)
    store.artifact(run_id, "reports/exploration.json", view)
    atomic_text(store.run_dir(run_id) / "reports/exploration.html", document)
