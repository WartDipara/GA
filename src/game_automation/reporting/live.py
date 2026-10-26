"""Loopback-only, read-only HTTP projection of committed task evidence."""

import hashlib
import json
import sqlite3
from base64 import b64encode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, unquote, urlsplit

from game_automation.common import FrameworkError, within
from game_automation.errors import ErrorCode
from game_automation.storage.store import Store

from .agent_trace import agent_view
from .compact_trace import render_compact
from .execution import publication_status
from .exploration import exploration_view, render_exploration
from .trace import render_context, render_trace


def trace_url(task_id, run_id):
    return f"/tasks/{task_id}/executions/{run_id}/reports/trace.html"


def task_snapshot(store, task_id):
    executions = store.task_runs(task_id)
    for execution in executions:
        execution["trace_url"] = trace_url(task_id, execution["run_id"])
        execution["exploration_url"] = (
            f"/tasks/{task_id}/executions/{execution['run_id']}/reports/exploration.html"
        )
        inputs = store.run_task(execution["run_id"])
        execution["label"] = inputs.get("workflow_key", execution["mode"])
        execution["parent_run"] = inputs.get("workflow_run")
        execution["apk_name"] = inputs.get("metadata", {}).get("apkName")
        execution["report_publication"] = publication_status(store, execution["run_id"])
    rows = store.db.execute(
        "SELECT e.seq,e.data FROM events e JOIN runs r ON r.id=e.run_id "
        "WHERE r.task_id=? "
        "AND e.kind NOT IN ('rpc.in','rpc.out','provider.wire','ai.progress',"
        "'ai.reasoning_summary') "
        "AND e.kind NOT LIKE 'plugin.%.command.%' "
        "ORDER BY e.seq DESC LIMIT 200",
        (task_id,),
    )
    events = []
    for row in rows:
        event = json.loads(row["data"])
        payload = event["payload"]
        detail = json.dumps(payload, ensure_ascii=False, indent=2)
        preview = {
            "seq": row["seq"],
            "run_id": event["run_id"],
            "kind": event["kind"],
            "span_id": event["span_id"],
            "parent_span_id": event["parent_span_id"],
            "label": payload.get("capability") or payload.get("stage") or event["kind"],
            "duration_ms": payload.get("duration_ms"),
            "detail": detail[:4000],
            "truncated": len(detail) > 4000,
        }
        events.append(preview)
    source_ids = store.task(task_id).get("source_ids", [])
    source_count = len(source_ids)
    apk_label = "APK" if source_count == 1 else "APKs"
    snapshot = {
        "task_id": task_id,
        "title": f"{source_count} {apk_label}" if source_ids else "Game testing task",
        "executions": executions,
        "events": events,
    }
    return snapshot


def viewer_document(interval):
    assets = Path(__file__).parent / "web"
    script = (assets / "live.js").read_text(encoding="utf-8")
    script_element = f"<script>{script}</script>"
    script_hash = b64encode(hashlib.sha256(script.encode()).digest()).decode("ascii")
    template = Template((assets / "live.template.html").read_text(encoding="utf-8"))
    document = template.substitute(
        interval=interval,
        script_hash=script_hash,
        script_element=script_element,
        style=(assets / "live.css").read_text(encoding="utf-8"),
    )
    return document


class TraceHandler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *arguments):
        pass

    def respond(self, status, content, content_type):
        body = content.encode("utf-8") if isinstance(content, str) else content
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if content_type != "text/html; charset=utf-8":
            self.send_header("Content-Security-Policy", "default-src 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        expected_host = f"127.0.0.1:{self.server.server_port}"
        origin = self.headers.get("Origin")
        if self.headers.get("Host") != expected_host or origin not in (
            None,
            f"http://{expected_host}",
        ):
            self.respond(403, "Forbidden", "text/plain; charset=utf-8")
            return
        store = None
        try:
            store = Store.open_readonly(self.server.project_root)
            store.db.execute("BEGIN")
            store.task(self.server.task_id)
            parsed = urlsplit(self.path)
            self.route(store, unquote(parsed.path), parse_qs(parsed.query))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except FrameworkError as exc:
            status = 404 if exc.code == ErrorCode.NOT_FOUND else 503
            self.respond(status, str(exc.code), "text/plain; charset=utf-8")
        except (OSError, sqlite3.Error, ValueError):
            self.respond(503, "Trace temporarily unavailable", "text/plain; charset=utf-8")
        finally:
            if store is not None:
                store.close()

    def route(self, store, path, query):
        if path == "/":
            document = viewer_document(self.server.interval)
            self.respond(200, document, "text/html; charset=utf-8")
            return
        if path == "/snapshot":
            row = store.db.execute(
                "SELECT COALESCE(MAX((SELECT MAX(seq) FROM events WHERE run_id=r.id)),0) "
                "FROM runs r WHERE r.task_id=?",
                (self.server.task_id,),
            ).fetchone()
            revision = row[0]
            if query.get("after") == [str(revision)]:
                self.respond(204, b"", "application/json")
                return
            snapshot = task_snapshot(store, self.server.task_id)
            snapshot["revision"] = revision
            self.respond(200, json.dumps(snapshot, ensure_ascii=False), "application/json")
            return
        prefix = f"/tasks/{self.server.task_id}/executions/"
        if not path.startswith(prefix):
            raise FrameworkError(ErrorCode.NOT_FOUND)
        run_id, separator, relative = path[len(prefix) :].partition("/")
        run = store.run(run_id)
        if not separator or run["task_id"] != self.server.task_id:
            raise FrameworkError(ErrorCode.NOT_FOUND)
        if relative == "reports/trace.html":
            try:
                document, _ = render_trace(store, run_id)
            except Exception:
                document = render_compact(store, run_id, degraded=True)
            self.respond(200, document, "text/html; charset=utf-8")
            return
        if relative == "reports/exploration.html":
            document, _ = render_exploration(store, run_id, current=True)
            self.respond(200, document, "text/html; charset=utf-8")
            return
        if relative == "reports/exploration.json":
            view = exploration_view(store, run_id)
            self.respond(200, json.dumps(view, ensure_ascii=False), "application/json")
            return
        if relative == "reports/context.html":
            document, _ = render_context(store, run_id)
            self.respond(200, document, "text/html; charset=utf-8")
            return
        if relative == "reports/agent-trace.json":
            row = store.db.execute(
                "SELECT COALESCE(MAX(e.seq),0) FROM events e JOIN runs r ON r.id=e.run_id "
                "WHERE r.task_id=?",
                (self.server.task_id,),
            ).fetchone()
            if query.get("after") == [str(row[0])]:
                self.respond(204, b"", "application/json")
                return
            view = agent_view(store, run_id)
            view["revision"] = row[0]
            self.respond(200, json.dumps(view, ensure_ascii=False), "application/json")
            return
        if relative == "reports/events.json":
            span_id = query.get("span", [None])[0]
            after = int(query.get("after", ["0"])[0])
            if not span_id or after < 0:
                raise FrameworkError(ErrorCode.NOT_FOUND)
            events = []
            for event in store.iter_events(
                run_id, after=after, span=None if span_id == run_id else span_id
            ):
                events.append(event)
                if len(events) == 501:
                    break
            page = {
                "events": events[:500],
                "has_more": len(events) > 500,
                "next_after": events[min(499, len(events) - 1)]["seq"] if events else after,
            }
            self.respond(200, json.dumps(page, ensure_ascii=False), "application/json")
            return
        allowed_reports = {
            "reports/gameplay.html",
            "reports/context.json",
            "reports/gameplay.json",
            "reports/execution.html",
            "reports/execution.json",
            "reports/publication.json",
            "reports/workflow.json",
        }
        evidence_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".txt": "text/plain; charset=utf-8",
            ".log": "text/plain; charset=utf-8",
            ".json": "application/json",
        }
        file_path = within(store.run_dir(run_id), relative)
        evidence_type = evidence_types.get(file_path.suffix.lower())
        if relative in allowed_reports:
            content_type = (
                "text/html; charset=utf-8" if relative.endswith(".html") else "application/json"
            )
        elif (evidence_type and file_path.is_relative_to(store.run_dir(run_id) / "artifacts")) or (
            file_path.is_relative_to(store.run_dir(run_id) / "calls")
            and file_path.name in ("input.json", "output.json")
        ):
            evidence_type = evidence_type or "application/json"
            content_type = evidence_type
        else:
            raise FrameworkError(ErrorCode.NOT_FOUND)
        if not file_path.is_file():
            raise FrameworkError(ErrorCode.NOT_FOUND)
        self.respond(200, file_path.read_bytes(), content_type)


def serve_trace(root, run_id, *, port=8767, interval=3):
    if not 0 <= port <= 65535 or not 1 <= interval <= 60:
        raise FrameworkError(ErrorCode.TRACE_VIEWER_CONFIG)
    store = None
    try:
        store = Store.open_readonly(root)
        task_id = store.run(run_id)["task_id"]
        with ThreadingHTTPServer(("127.0.0.1", port), TraceHandler) as server:
            server.project_root = root
            server.task_id = task_id
            server.interval = interval
            print(f"Trace viewer: http://127.0.0.1:{server.server_port}/", flush=True)
            print(f"Refresh interval: {interval}s. Press Ctrl+C to stop.", flush=True)
            store.close()
            store = None
            try:
                server.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                pass
    except (OSError, sqlite3.Error) as exc:
        raise FrameworkError(ErrorCode.TRACE_VIEWER_UNAVAILABLE) from exc
    finally:
        if store is not None:
            store.close()
