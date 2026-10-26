"""Bounded, connection-free caches of incremental read-only trace folds."""

from collections import OrderedDict
from threading import RLock

from .trace_projection import DETAILS, ENDS, STARTS, TraceFold, project_trace

VIEW_KINDS = tuple(
    dict.fromkeys(
        (
            *STARTS,
            *ENDS,
            *DETAILS,
            "agent.review",
            "agent.actor_assigned",
            "agent.analysis.completed",
            "run.continued_from",
            "report.requested",
            "report.started",
            "report.finished",
            "trace.writer.health",
            "gameplay.checkpoint",
            "exploration.page.updated",
            "exploration.element.updated",
            "exploration.edge.updated",
            "exploration.scroll.updated",
            "exploration.locator.updated",
        )
    )
)


def view_revision(store, task_id):
    placeholders = ",".join("?" for _ in VIEW_KINDS)
    row = store.db.execute(
        "SELECT COALESCE(MAX(seq),0) FROM events WHERE run_id IN "
        f"(SELECT id FROM runs WHERE task_id=?) AND kind IN ({placeholders})",
        (task_id, *VIEW_KINDS),
    ).fetchone()
    return row[0]


class TraceProjectionCache:
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = RLock()

    def project(self, store, run_id):
        with self.lock:
            watermark = store.db.execute(
                "SELECT COALESCE(MAX(seq),0) FROM events WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            previous = self.entries.pop(run_id, None)
            fold = previous[0] if previous else TraceFold()
            if previous and fold.through > watermark:
                self.entries[run_id] = previous
                return project_trace(store, run_id)
            projection = project_trace(store, run_id, fold=fold)
            size = fold.size()
            if size <= 8 * 1024**2:
                self.entries[run_id] = (fold, size)
            while self.entries and (
                len(self.entries) > 4
                or sum(entry[1] for entry in self.entries.values()) > 8 * 1024**2
            ):
                self.entries.popitem(last=False)
            return projection
