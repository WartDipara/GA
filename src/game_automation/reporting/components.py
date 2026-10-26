"""Read-only rich renderers; publication policy belongs to the TypeScript control plane."""

import traceback
from pathlib import Path

from game_automation.common import FrameworkError, atomic_text
from game_automation.errors import ErrorCode

from .compact_trace import render_compact
from .exploration import exploration_report
from .gameplay import gameplay_report
from .history import index_reports
from .task_index import task_report
from .trace import export_agent_trace, export_context


def render_component(store, run_id, component):
    try:
        if component == "gameplay":
            gameplay_report(store, run_id)
        elif component == "exploration":
            exploration_report(store, run_id)
        elif component == "trace":
            export_agent_trace(store, run_id)
        elif component == "context":
            export_context(store, run_id)
        elif component == "task_index":
            task_report(store, store.run(run_id)["task_id"])
        elif component == "index":
            index_reports(store, run_id)
        elif component == "compact_trace":
            document = render_compact(store, run_id, degraded=True)
            atomic_text(store.run_dir(run_id) / "reports/trace.html", document)
        else:
            raise FrameworkError(ErrorCode.INVALID_PROTOCOL)
    except Exception as exc:
        error = {
            "code": exc.code
            if isinstance(exc, FrameworkError)
            else ErrorCode.REPORT_GENERATION_FAILED,
            "exception_type": type(exc).__name__,
            "frames": [
                {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                for frame in traceback.extract_tb(exc.__traceback__)[-8:]
            ],
        }
        result = {"status": "failed", "error": error}
        return result
    result = {"status": "complete"}
    return result
