import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from game_automation.common import FrameworkError, json_text, redact
from game_automation.contracts import validate
from game_automation.errors import ErrorCode
from game_automation.kernel.workflow import execute, new_run
from game_automation.kernel.workflow.intake import intake_status, read_document
from game_automation.kernel.workflow.preprocessing import preprocessing_status, read_batch
from game_automation.observability import build_insights, export_trace
from game_automation.storage import Store


def parser():
    cli = argparse.ArgumentParser(
        prog="game-auto",
        description="Game testing framework: skills, plugins, and end-to-end tracing",
    )
    cli.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: current directory)",
    )
    commands = cli.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit", help="Submit canonical JSON or a UTF-8 text document")
    source = submit.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", type=Path)
    source.add_argument("--document", type=Path, help="UTF-8 TXT or Markdown; copied at submission")
    submit.add_argument("--key", help="Idempotency key")
    run = commands.add_parser("run", help="Create a run; report missing capabilities as blocked")
    run.add_argument("task_id")
    intake = commands.add_parser("intake", help="Parse and validate a task without starting a game")
    intake.add_argument("task_id")
    preprocessing = commands.add_parser("preprocess", help="Download and inspect a JSON APK list")
    preprocessing.add_argument("--input", type=Path, required=True)
    preprocessing.add_argument("--key", help="Idempotency key for the submitted APK list")
    runs = commands.add_parser("runs", help="List recent runs with their task subjects and states")
    runs.add_argument("--limit", type=int, default=20)
    resume = commands.add_parser("resume")
    resume.add_argument("run_id")
    resume.add_argument("--input", type=Path, help="Original APK list for preprocessing recovery")
    for name in ("status", "cancel"):
        commands.add_parser(name).add_argument("run_id")
    provide = commands.add_parser(
        "provide", help="Add information to a blocked run without overwriting its original input"
    )
    provide.add_argument("run_id")
    provide.add_argument("--text", required=True)
    trace = commands.add_parser("trace")
    trace.add_argument("run_id")
    trace.add_argument("--follow", action="store_true")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("run_id")
    inspect.add_argument("--span", required=True)
    context = commands.add_parser(
        "context", help="Inspect the read-only context projection and reported usage"
    )
    context.add_argument("run_id")
    context.add_argument(
        "--json", action="store_true", help="Print the complete browser read model"
    )
    export = commands.add_parser("export-trace")
    export.add_argument("run_id")
    export.add_argument("--format", choices=["html"], default="html")
    return cli


def print_json(value):
    print(json_text(redact(value)))


def run_details(store, run_id):
    details = store.run(run_id)
    details["intake"] = intake_status(store, run_id)
    environment_events = [
        event for event in store.events(run_id) if event["kind"] == "environment.checked"
    ]
    details["environment"] = environment_events[-1]["payload"] if environment_events else None
    if details["mode"] == "preprocessing":
        details["apks"] = preprocessing_status(store, run_id)
    details["unknown_actions"] = store.unknown_actions(run_id)
    details["trace"] = str(store.run_dir(run_id) / "reports/trace.html")
    return details


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    store = None
    try:
        root = args.project.resolve()
        if not (root / "config/application.toml").is_file():
            raise FrameworkError(ErrorCode.PROJECT_NOT_FOUND)
        store = Store(root)
        if args.command == "submit":
            task = (
                read_document(args.document)
                if args.document
                else json.loads(args.task.read_text(encoding="utf-8-sig"))
            )
            validate(root / "contracts/v1", "document" if args.document else "task", task)
            print_json({"task_id": store.submit(task, args.key)})
        elif args.command == "preprocess":
            submission = read_batch(args.input, root / "contracts/v1")
            task_id = store.submit(submission, args.key)
            run_id = new_run(store, task_id, "preprocessing")
            identifiers = {"task_id": task_id, "run_id": run_id}
            print_json(identifiers)
            result = asyncio.run(execute(store, run_id, args.input))
            print_json(run_details(store, run_id))
            return 0 if result["status"] == "succeeded" else 2
        elif args.command in ("run", "intake"):
            task_id = args.task_id
            mode = "game" if args.command == "run" else "intake"
            run_id = new_run(store, task_id, mode)
            print_json({"task_id": task_id, "run_id": run_id})
            result = asyncio.run(execute(store, run_id))
            print_json(run_details(store, run_id))
            return 0 if result["status"] == "succeeded" else 2
        elif args.command == "resume":
            result = asyncio.run(execute(store, args.run_id, args.input))
            print_json(run_details(store, args.run_id))
            return 0 if result["status"] == "succeeded" else 2
        elif args.command == "status":
            print_json(run_details(store, args.run_id))
        elif args.command == "runs":
            print_json(store.list_runs(args.limit))
        elif args.command == "provide":
            store.supplement(args.run_id, args.text)
            print_json({"saved": True, "run_id": args.run_id})
        elif args.command == "cancel":
            store.cancel(args.run_id)
            print_json({"cancel_requested": True, "run_id": args.run_id})
        elif args.command == "inspect":
            events = store.events(args.run_id, span=args.span)
            if not events:
                raise FrameworkError(ErrorCode.NOT_FOUND)
            print_json(events)
        elif args.command == "export-trace":
            print(export_trace(store, args.run_id))
        elif args.command == "context":
            view = build_insights(store, args.run_id)
            if args.json:
                print_json(view)
            else:
                request_summaries = [
                    {
                        "id": request["id"],
                        "source": request["source"],
                        "status": request["status"],
                        "summary_available": request["summary_available"],
                    }
                    for request in view["requests"]
                ]
                context_details = {
                    "run": view["run"],
                    "summary": view["summary"],
                    "visibility": view["visibility"],
                    "requests": request_summaries,
                }
                print_json(context_details)
        elif args.command == "trace":
            after = 0
            while True:
                for event in store.events(args.run_id, after):
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                    after = event["seq"]
                state = store.run(args.run_id)["status"]
                if not args.follow or state in ("succeeded", "failed", "cancelled", "blocked"):
                    break
                time.sleep(0.25)
        return 0
    except KeyboardInterrupt:
        return 130
    except (FrameworkError, OSError, ValueError) as exc:
        error = (
            exc.as_dict()
            if isinstance(exc, FrameworkError)
            else {"code": ErrorCode.LOCAL_ERROR, "message": str(exc)}
        )
        print(json_text(redact(error)), file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
