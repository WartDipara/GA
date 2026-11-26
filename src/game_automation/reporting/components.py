"""Read-only rich renderers; publication policy belongs to the TypeScript control plane."""

import json
import sys
import traceback
from pathlib import Path

from game_automation.errors import ErrorCode, FrameworkError
from game_automation.storage.store import Store

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


def main():
    root, run_id, component = sys.argv[1:]
    store = Store.open_readonly(Path(root))
    try:
        result = render_component(store, run_id, component)
        print(json.dumps(result))
    finally:
        store.close()


if __name__ == "__main__":
    main()
