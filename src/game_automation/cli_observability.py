"""CLI / JSON observability operations; the browser service remains read-only."""

import asyncio

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode
from game_automation.kernel.agent_quality import (
    actor_reference,
    compare_traces,
    export_dataset,
    record_review,
)
from game_automation.kernel.workflow.runner import execute, new_run
from game_automation.reporting.agent_trace import agent_view


def add_commands(commands):
    export = commands.add_parser("agent-trace", help="Export semantic Agent spans as JSON")
    export.add_argument("run_id")
    analyze = commands.add_parser("analyze-trace", help="Ask AI about retained execution evidence")
    analyze.add_argument("run_id")
    analyze.add_argument("--question", required=True)
    review = commands.add_parser("review", help="Append an evidence-backed human quality label")
    review.add_argument("run_id")
    review.add_argument("--span", required=True)
    review.add_argument(
        "--metric", choices=("accuracy", "tool_correctness", "task_completion"), required=True
    )
    review.add_argument("--label", choices=("pass", "fail", "unknown"), required=True)
    review.add_argument("--evidence", nargs="+", required=True)
    review.add_argument("--note", required=True)
    review.add_argument("--actor-ref", default="local_operator")
    dataset = commands.add_parser(
        "export-dataset", help="Rebuild a task's local human-label dataset"
    )
    dataset.add_argument("task_id")
    compare = commands.add_parser(
        "compare-traces", help="Compare recorded versions and quality coverage"
    )
    compare.add_argument("first_run")
    compare.add_argument("second_run")
    owner = commands.add_parser(
        "trace-owner", help="Associate an opaque caller reference with a run"
    )
    owner.add_argument("run_id")
    owner.add_argument("--actor-ref", required=True)


def handle(store, args):
    if args.command == "agent-trace":
        return agent_view(store, args.run_id)
    if args.command == "review":
        return record_review(
            store,
            args.run_id,
            args.span,
            args.metric,
            args.label,
            args.evidence,
            args.note,
            args.actor_ref,
        )
    if args.command == "export-dataset":
        return export_dataset(store, args.task_id)
    if args.command == "compare-traces":
        return compare_traces(store, args.first_run, args.second_run)
    if args.command == "trace-owner":
        payload = {"actor_ref": actor_reference(args.actor_ref)}
        store.event(args.run_id, "agent.actor_assigned", payload)
        return payload
    if not args.question.strip() or len(args.question) > 4000:
        raise FrameworkError(
            ErrorCode.OBSERVABILITY_INVALID, "Question requires 1 to 4,000 characters"
        )
    task_id = store.run(args.run_id)["task_id"]
    task = {"kind": "trace_analysis", "source_run": args.run_id, "question": args.question}
    run_id = new_run(store, task_id, "analysis", task)
    asyncio.run(execute(store, run_id))
    result = {
        "run_id": run_id,
        "status": store.run(run_id)["status"],
        "report": str(store.run_dir(run_id) / "reports/analysis.json"),
    }
    return result
