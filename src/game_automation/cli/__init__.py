import argparse
import asyncio
import getpass
import json
import sys
import time
from pathlib import Path

from game_automation.common import FrameworkError, json_text, redact
from game_automation.contracts import validate
from game_automation.errors import ErrorCode
from game_automation.kernel.workflow import execute, new_run
from game_automation.kernel.workflow.device import preparation_task
from game_automation.kernel.workflow.gameplay import gameplay_task
from game_automation.kernel.workflow.intake import intake_status, read_document
from game_automation.kernel.workflow.preprocessing import preprocessing_status, read_batch
from game_automation.observability import build_insights, export_trace
from game_automation.plugin_sdk.credentials import save_profile
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
    emulators = commands.add_parser(
        "emulators", help="Discover and inspect existing MuMu instances"
    )
    emulators.add_argument("--instance", type=int, help="MuMu instance index")
    emulators.add_argument("--capture", action="store_true", help="Save local device screenshots")
    observe = commands.add_parser("observe", help="Capture the focused Android display")
    observe.add_argument("--instance", type=int, required=True, help="MuMu instance index")
    device = commands.add_parser(
        "prepare-device", help="Install a ready APK in a dedicated instance"
    )
    device.add_argument("--from-run", required=True, help="Preprocessing run ID")
    device.add_argument("--apk-index", type=int, required=True, help="Zero-based APK list index")
    device.add_argument("--instance", type=int, required=True, help="Dedicated MuMu instance index")
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
        choices=["capture", "return-to-game", "activate-game", "routine"],
    )
    play.add_argument("--routine", type=Path, help="Guarded JSON routine, at most five steps")
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
    for mode, kind, key in (
        ("device", "device.prepared", "device"),
        ("observation", "device.observed", "observation"),
    ):
        if details["mode"] == mode:
            matching = [event["payload"] for event in store.events(run_id) if event["kind"] == kind]
            details[key] = matching[-1] if matching else None
    if details["mode"] == "device":
        action = store.latest_device_action(run_id)
        details["installation"] = (
            {key: action[key] for key in ("run_id", "action_id", "status")} if action else None
        )
    if details["mode"] == "emulators":
        discoveries = [
            event for event in store.events(run_id) if event["kind"] == "emulators.discovered"
        ]
        details["emulators"] = discoveries[-1]["payload"] if discoveries else None
    if details["mode"] == "preprocessing":
        details["apks"] = preprocessing_status(store, run_id)
        storage_events = [
            event
            for event in store.events(run_id)
            if event["kind"] == "preprocessing.storage.checked"
        ]
        details["storage"] = storage_events[-1]["payload"] if storage_events else None
    details["unknown_actions"] = store.unknown_actions(run_id)
    details["trace"] = str(store.run_dir(run_id) / "reports/trace.html")
    return details


def execution_result(store, run_id):
    details = run_details(store, run_id)
    print_json(details)
    reasons = [
        event["payload"].get("reason")
        for event in store.events(run_id)
        if event["kind"] == "run.state"
    ]
    reason = reasons[-1] if reasons else None
    if reason and reason.get("code") in (
        ErrorCode.DISK_SPACE_INSUFFICIENT,
        ErrorCode.DISK_SPACE_UNAVAILABLE,
        ErrorCode.DISK_BUDGET_EXCEEDED,
        ErrorCode.DEVICE_SPACE_INSUFFICIENT,
    ):
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
        store = Store(root)
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
            )
            task_id = store.submit(task)
            run_id = new_run(store, task_id, "gameplay")
            print_json({"task_id": task_id, "run_id": run_id})
            asyncio.run(execute(store, run_id))
            return execution_result(store, run_id)
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
            task_id = store.submit(task)
            run_id = new_run(store, task_id, "emulators")
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
            task_id = store.submit(task)
            run_id = new_run(store, task_id, mode)
            identifiers = {"task_id": task_id, "run_id": run_id}
            print_json(identifiers)
            asyncio.run(execute(store, run_id))
            return execution_result(store, run_id)
        elif args.command == "preprocess":
            submission = read_batch(args.input, root / "contracts/v1")
            task_id = store.submit(submission, args.key)
            run_id = new_run(store, task_id, "preprocessing")
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
            asyncio.run(execute(store, args.run_id, args.input))
            return execution_result(store, args.run_id)
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
