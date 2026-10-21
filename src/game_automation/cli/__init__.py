import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from game_automation.common import FrameworkError, json_text, redact
from game_automation.contracts import validate
from game_automation.kernel.workflow import execute, new_run
from game_automation.kernel.workflow.intake import intake_status, read_document
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
    commands.add_parser(
        "intake", help="Parse and validate a task without starting a game"
    ).add_argument("task_id")
    for name in ("resume", "status", "cancel"):
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
    commands.add_parser("demo", help="Run fixture plugins only; no device or model calls")
    commands.add_parser(
        "codex-probe", help="Verify the local Codex version and handshake without inference"
    )
    commands.add_parser(
        "codex-smoke",
        help="Verify skills and structured output with real inference; uses account quota",
    )
    return cli


def print_json(value):
    print(json_text(redact(value)))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    store = None
    try:
        root = args.project.resolve()
        if not (root / "config/application.toml").is_file():
            raise FrameworkError("PROJECT_NOT_FOUND", "Run from the repository or pass --project")
        store = Store(root)
        if args.command == "submit":
            task = (
                read_document(args.document)
                if args.document
                else json.loads(args.task.read_text(encoding="utf-8-sig"))
            )
            validate(root / "contracts/v1", "document" if args.document else "task", task)
            print_json({"task_id": store.submit(task, args.key)})
        elif args.command in ("run", "intake", "demo", "codex-probe", "codex-smoke"):
            if args.command in ("run", "intake"):
                task_id, mode = args.task_id, "game" if args.command == "run" else "intake"
            else:
                mode = args.command
                task_id = store.submit(
                    {
                        "objective": "Framework validation only; no real game coverage",
                        "platform": "fixture",
                        "target": "synthetic",
                        "scope": ["framework"],
                        "limits": {"max_actions": 1, "timeout_seconds": 300},
                        "constraints": ["No real device actions"],
                    }
                )
            run_id = new_run(store, task_id, mode)
            print_json({"task_id": task_id, "run_id": run_id})
            result = asyncio.run(execute(store, run_id))
            print_json(
                {
                    **result,
                    "intake": intake_status(store, run_id),
                    "trace": str(store.run_dir(run_id) / "reports/trace.html"),
                }
            )
            return 0 if result["status"] == "succeeded" else 2
        elif args.command == "resume":
            result = asyncio.run(execute(store, args.run_id))
            print_json({**result, "intake": intake_status(store, args.run_id)})
            return 0 if result["status"] == "succeeded" else 2
        elif args.command == "status":
            print_json(
                {
                    **store.run(args.run_id),
                    "unknown_actions": store.unknown_actions(args.run_id),
                    "intake": intake_status(store, args.run_id),
                }
            )
        elif args.command == "provide":
            store.supplement(args.run_id, args.text)
            print_json({"saved": True, "run_id": args.run_id})
        elif args.command == "cancel":
            store.cancel(args.run_id)
            print_json({"cancel_requested": True, "run_id": args.run_id})
        elif args.command == "inspect":
            events = store.events(args.run_id, span=args.span)
            if not events:
                raise FrameworkError("NOT_FOUND", "Span not found")
            print_json(events)
        elif args.command == "export-trace":
            print(export_trace(store, args.run_id))
        elif args.command == "context":
            view = build_insights(store, args.run_id)
            print_json(
                view
                if args.json
                else {
                    "run": view["run"],
                    "summary": view["summary"],
                    "visibility": view["visibility"],
                    "requests": [
                        {
                            "id": r["id"],
                            "source": r["source"],
                            "status": r["status"],
                            "summary_available": r["summary_available"],
                        }
                        for r in view["requests"]
                    ],
                }
            )
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
            else {"code": "LOCAL_ERROR", "message": str(exc)}
        )
        print(json_text(redact(error)), file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
