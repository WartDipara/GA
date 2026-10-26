import argparse
import asyncio
import getpass
import json
import sqlite3
import sys
import time
import tomllib
from pathlib import Path

from game_automation.common import FileLock, FrameworkError, json_text, redact
from game_automation.contracts import validate
from game_automation.errors import ErrorCode
from game_automation.kernel.workflow.device import preparation_task
from game_automation.kernel.workflow.gameplay import gameplay_task
from game_automation.kernel.workflow.intake import intake_status, read_document
from game_automation.kernel.workflow.preprocessing import preprocessing_status, read_batch
from game_automation.kernel.workflow.runner import execute, new_run
from game_automation.kernel.workflow.scheduler import workflow_status, workflow_task
from game_automation.observability import build_insights
from game_automation.plugin_sdk.credentials import save_profile
from game_automation.reporting.trace import export_trace
from game_automation.storage.store import Store

READ_ONLY_COMMANDS = {
    "exploration",
    "status",
    "runs",
    "tasks",
    "task-status",
    "trace",
    "inspect",
    "context",
    "agent-trace",
    "compare-traces",
    "compare-games",
    "export-trace",
    "export-dataset",
}


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
    exploration = commands.add_parser("exploration", help="Inspect the recorded exploration graph")
    exploration.add_argument("run_id")
    exploration.add_argument("--format", choices=["json"], default="json")
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
    ownership = preprocessing.add_mutually_exclusive_group()
    ownership.add_argument("--key", help="Idempotency key for the submitted APK list")
    ownership.add_argument("--task-id", help="Attach preprocessing to an existing task")
    workflow = commands.add_parser("workflow", help="Run the composed APK workflow in list order")
    workflow.add_argument("--input", type=Path, required=True, help="Private APK URL JSON file")
    workflow.add_argument("--instances", type=int, nargs="+", help="One dedicated instance per APK")
    workflow.add_argument("--credentials", default="default", help="Local credential reference")
    workflow.add_argument(
        "--goal",
        default="explore",
        choices=["login-and-interact", "explore", "advance-battle"],
    )
    workflow.add_argument(
        "--max-turns",
        type=int,
        help="Override gameplay.max_turns in config/application.toml; zero means unlimited",
    )
    workflow.add_argument("--stages", nargs="+", help="Override the configured stage composition")
    workflow.add_argument("--adopt-existing", action="store_true")
    workflow.add_argument("--key", help="Idempotency key for submission")
    emulators = commands.add_parser(
        "emulators", help="Discover and inspect existing MuMu instances"
    )
    emulators.add_argument("--instance", type=int, help="MuMu instance index")
    emulators.add_argument("--capture", action="store_true", help="Save local device screenshots")
    emulators.add_argument("--task-id", help="Attach discovery to an existing task")
    observe = commands.add_parser("observe", help="Capture the focused Android display")
    observe.add_argument("--instance", type=int, required=True, help="MuMu instance index")
    observe.add_argument("--task-id", help="Attach observation to an existing task")
    device = commands.add_parser(
        "prepare-device", help="Install a ready APK in a dedicated instance"
    )
    device.add_argument("--from-run", required=True, help="Preprocessing run ID")
    device.add_argument("--apk-index", type=int, required=True, help="Zero-based APK list index")
    device.add_argument("--instance", type=int, required=True, help="Dedicated MuMu instance index")
    release = commands.add_parser(
        "release-device", help="Release a settled reservation without deleting evidence or app data"
    )
    release.add_argument("reservation_run", help="Execution that originally reserved the instance")
    release.add_argument(
        "--apply", action="store_true", help="Apply the release; otherwise preview"
    )
    device.add_argument(
        "--adopt-existing",
        action="store_true",
        help="Bind an installed matching APK without reinstalling",
    )
    credentials = commands.add_parser(
        "credentials", help="Save credentials in the local DPAPI vault"
    )
    credentials.add_argument("profile", help="Local credential reference")
    play = commands.add_parser("play", help="Execute a gameplay task in a prepared workspace")
    play.add_argument("--prepared-run", required=True)
    play.add_argument("--credentials", default="default", help="Local credential reference")
    play.add_argument(
        "--goal",
        default="capture",
        choices=[
            "capture",
            "return-to-game",
            "activate-game",
            "routine",
            "login-and-interact",
            "explore",
            "advance-battle",
        ],
    )
    play.add_argument("--routine", type=Path, help="Guarded JSON routine, at most five steps")
    play.add_argument(
        "--max-turns",
        type=int,
        help="Override gameplay.max_turns in config/application.toml; zero means unlimited",
    )
    runs = commands.add_parser("runs", help="List recent runs with their task subjects and states")
    runs.add_argument("--limit", type=int, default=20)
    tasks = commands.add_parser("tasks", help="List business task workspaces")
    tasks.add_argument("--limit", type=int, default=20)
    task_status = commands.add_parser(
        "task-status", help="Rebuild a task's offline execution index"
    )
    task_status.add_argument("task_id")
    commands.add_parser("migrate-workspaces", help="Group legacy execution folders by task")
    cleanup = commands.add_parser("cleanup", help="Preview or delete task files and SQLite history")
    selection = cleanup.add_mutually_exclusive_group()
    selection.add_argument("--task-id", action="append", help="Task to remove; may be repeated")
    selection.add_argument("--all", action="store_true", help="All tasks, including orphan folders")
    selection.add_argument("--missing", action="store_true", help="Tasks whose folders are missing")
    cleanup.add_argument("--apply", action="store_true", help="Permanently apply the deletion")
    resume = commands.add_parser("resume")
    resume.add_argument("run_id")
    resume.add_argument("--input", type=Path, help="Original APK list if downloads need recovery")
    resume.add_argument(
        "--new-batch",
        action="store_true",
        help="Continue the same task with current code and pinned shared Skill versions",
    )
    report = commands.add_parser("report", help="Rebuild outcome reports without replaying work")
    report.add_argument("run_id", nargs="?")
    report.add_argument("--pending", action="store_true", help="Retry pending report publications")
    compare = commands.add_parser("compare-games", help="Compare cross-game acceptance evidence")
    compare.add_argument("first_run")
    compare.add_argument("second_run")
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
    viewer = commands.add_parser("serve-trace", help="Poll task traces in a local read-only viewer")
    viewer.add_argument("run_id", nargs="?", help="Optional execution to open initially")
    viewer.add_argument(
        "--port", type=int, default=8767, help="Loopback port; zero selects a free port"
    )
    viewer.add_argument(
        "--interval", type=int, default=3, help="Polling interval, 1 through 60 seconds"
    )
    from game_automation.cli_observability import add_commands

    add_commands(commands)
    return cli


def print_json(value):
    print(json_text(redact(value)))


def planning_turn_limit(root, requested_limit):
    maximum = requested_limit
    if maximum is None:
        application = tomllib.loads((root / "config/application.toml").read_text(encoding="utf-8"))
        maximum = application.get("gameplay", {}).get("max_turns", 0)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise FrameworkError(
            ErrorCode.INVALID_CONTRACT,
            "The planning-turn limit must be a non-negative integer; zero means unlimited",
        )
    return maximum


def run_details(store, run_id):
    from game_automation.reporting.execution import publication_status

    details = store.run(run_id)
    details["task_directory"] = str(store.task_dir(details["task_id"]))
    details["execution_directory"] = str(store.run_dir(run_id))
    details["intake"] = intake_status(store, run_id)
    environment_events = store.events(run_id, kinds=("environment.checked",), limit=1)
    details["environment"] = environment_events[-1]["payload"] if environment_events else None
    for mode, kind, key in (
        ("device", "device.prepared", "device"),
        ("observation", "device.observed", "observation"),
    ):
        if details["mode"] == mode:
            matching = [event["payload"] for event in store.events(run_id, kinds=(kind,), limit=1)]
            details[key] = matching[-1] if matching else None
    if details["mode"] == "device":
        action = store.latest_device_action(run_id)
        details["installation"] = (
            {key: action[key] for key in ("run_id", "action_id", "status")} if action else None
        )
    if details["mode"] == "emulators":
        discoveries = store.events(run_id, kinds=("emulators.discovered",), limit=1)
        details["emulators"] = discoveries[-1]["payload"] if discoveries else None
    if details["mode"] == "preprocessing":
        details["apks"] = preprocessing_status(store, run_id)
        storage_events = store.events(run_id, kinds=("preprocessing.storage.checked",), limit=1)
        details["storage"] = storage_events[-1]["payload"] if storage_events else None
    if details["mode"] == "workflow":
        details["workflow"] = workflow_status(store, run_id)
    details["unknown_actions"] = store.unknown_actions(run_id)
    details["trace"] = str(store.run_dir(run_id) / "reports/trace.html")
    details["report_publication"] = publication_status(store, run_id)
    return details


def execution_result(store, run_id):
    details = run_details(store, run_id)
    print_json(details)
    if details["report_publication"]["status"] != "complete":
        print(
            "Warning: report publication is incomplete. Business state is preserved; "
            "use game-auto report to rebuild it without replaying work.",
            file=sys.stderr,
        )
    reasons = [
        event["payload"].get("reason")
        for event in store.events(run_id, kinds=("run.state",), limit=1)
    ]
    if details["mode"] == "workflow":
        child_reasons = [step.get("reason") for step in details["workflow"]["steps"]]
        reasons.extend(child_reasons)
    disk_warning_codes = (
        ErrorCode.DISK_SPACE_INSUFFICIENT,
        ErrorCode.DISK_SPACE_UNAVAILABLE,
        ErrorCode.DISK_BUDGET_EXCEEDED,
        ErrorCode.DEVICE_SPACE_INSUFFICIENT,
    )
    for reason in reasons:
        if reason and reason.get("code") in disk_warning_codes:
            print(f"Warning: {reason['message']}", file=sys.stderr)
    return 0 if details["status"] == "succeeded" else 2


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
        if args.command in ("play", "workflow"):
            args.max_turns = planning_turn_limit(root, args.max_turns)
        if args.command == "serve-trace":
            from game_automation.reporting.live import serve_trace

            serve_trace(root, args.run_id, port=args.port, interval=args.interval)
            return 0
        if args.command in READ_ONLY_COMMANDS:
            store = Store.open_readonly(root)
        else:
            store = Store(root, allow_cleanup=args.command == "cleanup")
        if args.command == "credentials":
            username = getpass.getpass("Username (hidden): ")
            password = getpass.getpass("Password (hidden): ")
            save_profile(root, args.profile, username, password)
            print_json({"saved": True, "profile": args.profile})
        elif args.command == "play":
            routine = None
            if args.routine:
                routine = json.loads(args.routine.read_text(encoding="utf-8"))
                validate(root / "contracts/v1", "gameplay-routine", routine)
            if (args.goal == "routine") != (routine is not None):
                raise FrameworkError(
                    ErrorCode.INVALID_CONTRACT, "Routine input and goal must match"
                )
            task = gameplay_task(
                store,
                args.prepared_run,
                args.credentials,
                args.goal,
                routine["steps"] if routine else None,
                args.max_turns,
            )
            task_id = store.run(args.prepared_run)["task_id"]
            run_id = new_run(store, task_id, "gameplay", task)
            print_json({"task_id": task_id, "run_id": run_id})
            asyncio.run(execute(store, run_id))
            return execution_result(store, run_id)
        elif args.command == "release-device":
            from game_automation.kernel.report_finalization import finalize_reports

            with FileLock(store.var / "execution.lock"):
                result = store.release_device(args.reservation_run, apply=args.apply)
                if args.apply:
                    result["publication"] = finalize_reports(store, args.reservation_run)
            print_json(result)
        elif args.command == "submit":
            task = (
                read_document(args.document)
                if args.document
                else json.loads(args.task.read_text(encoding="utf-8-sig"))
            )
            validate(root / "contracts/v1", "document" if args.document else "task", task)
            print_json({"task_id": store.submit(task, args.key)})
        elif args.command == "emulators":
            if args.instance is not None and args.instance < 0:
                raise FrameworkError(
                    ErrorCode.DEVICE_SELECTION_REQUIRED, "Instance index is negative"
                )
            task = {
                "kind": "emulator_discovery",
                "target": "mumu",
                "instance_index": args.instance,
                "capture": args.capture,
            }
            task_id = args.task_id or store.submit(task)
            run_id = new_run(store, task_id, "emulators", task)
            identifiers = {"task_id": task_id, "run_id": run_id}
            print_json(identifiers)
            asyncio.run(execute(store, run_id))
            return execution_result(store, run_id)
        elif args.command in ("observe", "prepare-device"):
            if args.instance < 0:
                raise FrameworkError(ErrorCode.DEVICE_SELECTION_REQUIRED)
            if args.command == "prepare-device":
                task = preparation_task(
                    store, args.from_run, args.apk_index, args.instance, args.adopt_existing
                )
                mode = "device"
            else:
                task = {
                    "kind": "device_observation",
                    "target": "mumu-observation",
                    "instance_index": args.instance,
                }
                mode = "observation"
            task_id = (
                store.run(args.from_run)["task_id"]
                if args.command == "prepare-device"
                else args.task_id or store.submit(task)
            )
            run_id = new_run(store, task_id, mode, task)
            identifiers = {"task_id": task_id, "run_id": run_id}
            print_json(identifiers)
            asyncio.run(execute(store, run_id))
            return execution_result(store, run_id)
        elif args.command == "workflow":
            task = workflow_task(
                root,
                args.input,
                args.instances,
                args.credentials,
                args.goal,
                args.max_turns,
                args.stages,
                args.adopt_existing,
            )
            task_id = store.submit(task, args.key)
            run_id = new_run(store, task_id, "workflow", task)
            identifiers = {"task_id": task_id, "run_id": run_id}
            print_json(identifiers)
            asyncio.run(execute(store, run_id, args.input))
            return execution_result(store, run_id)
        elif args.command == "preprocess":
            submission = read_batch(args.input, root / "contracts/v1")
            task_id = args.task_id or store.submit(submission, args.key)
            run_id = new_run(store, task_id, "preprocessing", submission)
            identifiers = {"task_id": task_id, "run_id": run_id}
            print_json(identifiers)
            asyncio.run(execute(store, run_id, args.input))
            return execution_result(store, run_id)
        elif args.command in ("run", "intake"):
            task_id = args.task_id
            mode = "game" if args.command == "run" else "intake"
            run_id = new_run(store, task_id, mode)
            print_json({"task_id": task_id, "run_id": run_id})
            asyncio.run(execute(store, run_id))
            return execution_result(store, run_id)
        elif args.command == "resume":
            run_id = args.run_id
            if store.run_task(run_id).get("workflow_run"):
                raise FrameworkError(
                    ErrorCode.WORKFLOW_INVALID, "Resume the owning workflow instead of its child"
                )
            if args.new_batch:
                previous = store.run(run_id)
                run_id = new_run(
                    store, previous["task_id"], previous["mode"], store.run_task(args.run_id)
                )
                store.event(run_id, "run.continued_from", {"run_id": args.run_id})
                print_json({"run_id": run_id, "continued_from": args.run_id})
            asyncio.run(execute(store, run_id, args.input))
            return execution_result(store, run_id)
        elif args.command == "report":
            from game_automation.kernel.report_finalization import (
                finalize_reports,
                repair_pending_reports,
            )
            from game_automation.kernel.workflow.recovery import reconcile_interrupted_runs

            if bool(args.run_id) == args.pending:
                raise FrameworkError(ErrorCode.INVALID_CONTRACT, "Choose a run ID or --pending")
            with FileLock(store.var / "execution.lock"):
                reconcile_interrupted_runs(store)
                if args.pending:
                    receipts = repair_pending_reports(store)
                else:
                    receipts = [finalize_reports(store, args.run_id)]
            result = {"publications": receipts}
            if args.run_id:
                filename = (
                    "gameplay.html"
                    if store.run(args.run_id)["mode"] == "gameplay"
                    else "execution.html"
                )
                result["report"] = str(store.run_dir(args.run_id) / "reports" / filename)
            print_json(result)
            return 0 if all(receipt["status"] == "complete" for receipt in receipts) else 2
        elif args.command == "exploration":
            from game_automation.reporting.exploration import exploration_view

            print_json(exploration_view(store, args.run_id))
        elif args.command == "compare-games":
            from game_automation.reporting.game_comparison import compare_games

            print_json(compare_games(store, args.first_run, args.second_run))
        elif args.command == "status":
            print_json(run_details(store, args.run_id))
        elif args.command == "runs":
            print_json(store.list_runs(args.limit))
        elif args.command == "tasks":
            print_json(store.list_tasks(args.limit))
        elif args.command == "task-status":
            from game_automation.reporting.task_index import task_report

            print_json(task_report(store, args.task_id))
        elif args.command == "migrate-workspaces":
            from game_automation.storage.layout import relocate

            print_json(relocate(store))
        elif args.command == "cleanup":
            from game_automation.reporting.history import write_history
            from game_automation.storage.cleanup import cleanup

            result = cleanup(
                store,
                args.task_id,
                all_tasks=args.all,
                missing=args.missing,
                apply=args.apply,
            )
            if args.apply:
                write_history(store)
            print_json(result)
        elif args.command == "provide":
            store.supplement(args.run_id, args.text)
            print_json({"saved": True, "run_id": args.run_id})
        elif args.command == "cancel":
            store.cancel(args.run_id)
            print_json({"cancel_requested": True, "run_id": args.run_id})
        elif args.command in (
            "agent-trace",
            "analyze-trace",
            "review",
            "export-dataset",
            "compare-traces",
            "trace-owner",
        ):
            from game_automation.cli_observability import handle

            result = handle(store, args)
            print_json(result)
            if args.command == "analyze-trace" and result["status"] != "succeeded":
                return 2
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
                for event in store.iter_events(args.run_id, after):
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                    after = event["seq"]
                state = store.run(args.run_id)["status"]
                if not args.follow or state in ("succeeded", "failed", "cancelled", "blocked"):
                    break
                time.sleep(0.25)
        return 0
    except KeyboardInterrupt:
        return 130
    except (FrameworkError, OSError, ValueError, sqlite3.Error) as exc:
        if isinstance(exc, sqlite3.Error):
            exc = FrameworkError(ErrorCode.TRACE_UNAVAILABLE)
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
