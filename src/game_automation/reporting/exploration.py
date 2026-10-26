"""Read-only exploration catalog projection; never starts a writer, model or device."""

import json
from collections import Counter


def exploration_view(store, run_id):
    task_id = store.run(run_id)["task_id"]
    table = store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='exploration_records'"
    ).fetchone()
    records = []
    if table:
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
    result = {
        "task_id": task_id,
        "run_id": run_id,
        "scope": "Discovered reachable content; rendered evidence does not confirm hook delivery",
        "counts": dict(Counter(record["status"] for record in elements)),
        "records": records,
    }
    return result
