"""Loopback-only, read-only HTTP projection of committed task evidence."""

import hashlib
import html
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
from .history import execution_index, history_view, render_execution, render_history, task_document
from .navigation import decorate_document, page_document, theme_document
from .navigation_tree import live_workspace
from .trace import render_context, render_trace
from .trace_cache import TraceProjectionCache, view_revision
from .workspace import workspace_view


def trace_url(task_id, run_id):
    return f"/tasks/{task_id}/executions/{run_id}/reports/trace.html"


def task_snapshot(store, task_id):
    executions = store.task_runs(task_id)
    for execution in executions:
        execution["trace_url"] = trace_url(task_id, execution["run_id"])
        execution["index_url"] = (
            f"/tasks/{task_id}/executions/{execution['run_id']}/reports/index.html"
        )
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
    return theme_document(document, "../../")


class TraceHandler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *arguments):
        pass

    def respond(self, status, content, content_type, *, etag=None):
        if (
            content_type == "text/html; charset=utf-8"
            and "cache/typescript/trace_ui.css" not in content
        ):
            content = theme_document(content, "/")
        body = content.encode("utf-8") if isinstance(content, str) else content
        if etag and self.headers.get("If-None-Match") == etag:
            status = 304
            body = b""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if status != 304:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if etag else "no-store")
        if etag:
            self.send_header("ETag", etag)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if content_type != "text/html; charset=utf-8":
            self.send_header("Content-Security-Policy", "default-src 'none'")
        self.end_headers()
        self.wfile.write(body)

    def respond_document(self, store, document):
        path = unquote(urlsplit(self.path).path)
        document = live_workspace(store, document, path)
        document = theme_document(document, "/")
        self.respond(200, document, "text/html; charset=utf-8")

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
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if path in (
                "/cache/typescript/trace_ui.js",
                "/cache/typescript/trace_ui.css",
                "/cache/typescript/history_search.js",
            ):
                asset = self.server.project_root / "var" / path.lstrip("/")
                content_type = "text/javascript" if path.endswith(".js") else "text/css"
                content = asset.read_bytes()
                etag = f'"{hashlib.sha256(content).hexdigest()}"'
                self.respond(200, content, content_type + "; charset=utf-8", etag=etag)
                return
            if not (self.server.project_root / "var/state.sqlite").is_file():
                if path in ("/", "/reports/history.html"):
                    self.respond(200, render_history(None), "text/html; charset=utf-8")
                elif path in ("/history.json", "/reports/history.json"):
                    self.respond(200, json.dumps(history_view(None)), "application/json")
                elif path == "/reports/workspace.json":
                    self.respond(200, json.dumps(workspace_view(None)), "application/json")
                else:
                    raise FrameworkError(ErrorCode.NOT_FOUND)
                return
            store = Store.open_readonly(self.server.project_root)
            store.db.execute("BEGIN")
            self.route(store, path, parse_qs(parsed.query))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except FrameworkError as exc:
            status = 404 if exc.code == ErrorCode.NOT_FOUND else 503
            document = page_document(
                "Record unavailable",
                f'<p>{html.escape(str(exc.code))}</p><p><a href="/reports/history.html">'
                "Return to task history</a></p>",
            )
            self.respond(status, document, "text/html; charset=utf-8")
        except (OSError, sqlite3.Error, ValueError):
            document = page_document(
                "Trace temporarily unavailable",
                '<p><a href="/reports/history.html">Return to task history</a></p>',
            )
            self.respond(503, document, "text/html; charset=utf-8")
        finally:
            if store is not None:
                store.close()

    def route(self, store, path, query):
        if path == "/reports/workspace.json":
            self.respond(200, json.dumps(workspace_view(store)), "application/json")
            return
        if path in ("/", "/reports/history.html"):
            self.respond_document(store, render_history(store))
            return
        if path in ("/history.json", "/reports/history.json"):
            content = json.dumps(history_view(store), ensure_ascii=False)
            etag = f'"{hashlib.sha256(content.encode()).hexdigest()}"'
            self.respond(200, content, "application/json", etag=etag)
            return
        parts = path.strip("/").split("/")
        if len(parts) < 2 or parts[0] != "tasks":
            raise FrameworkError(ErrorCode.NOT_FOUND)
        task_id = parts[1]
        store.task(task_id)
        if path == f"/tasks/{task_id}/reports/workspace.json":
            self.respond(
                200, json.dumps(workspace_view(store, task_id=task_id)), "application/json"
            )
            return
        if path == f"/tasks/{task_id}/reports/task.html":
            self.respond_document(store, task_document(store, task_id))
            return
        if path == f"/tasks/{task_id}/reports/task.json":
            self.respond(200, json.dumps(task_snapshot(store, task_id)), "application/json")
            return
        if path == f"/tasks/{task_id}/":
            document = viewer_document(self.server.interval)
            self.respond_document(store, document)
            return
        if path == f"/tasks/{task_id}/snapshot":
            row = store.db.execute(
                "SELECT COALESCE(MAX((SELECT MAX(seq) FROM events WHERE run_id=r.id)),0) "
                "FROM runs r WHERE r.task_id=?",
                (task_id,),
            ).fetchone()
            revision = row[0]
            if query.get("after") == [str(revision)]:
                self.respond(204, b"", "application/json")
                return
            snapshot = task_snapshot(store, task_id)
            snapshot["revision"] = revision
            self.respond(200, json.dumps(snapshot, ensure_ascii=False), "application/json")
            return
        prefix = f"/tasks/{task_id}/executions/"
        if not path.startswith(prefix):
            raise FrameworkError(ErrorCode.NOT_FOUND)
        run_id, separator, relative = path[len(prefix) :].partition("/")
        run = store.run(run_id)
        if not separator or run["task_id"] != task_id:
            raise FrameworkError(ErrorCode.NOT_FOUND)
        if relative == "reports/workspace.json":
            self.respond(200, json.dumps(workspace_view(store, run_id=run_id)), "application/json")
            return
        if relative == "reports/index.html":
            document, _ = render_execution(store, run_id)
            self.respond_document(store, document)
            return
        if relative == "reports/index.json":
            self.respond(200, json.dumps(execution_index(store, run_id)), "application/json")
            return
        if relative == "reports/evidence.html":
            reference = query.get("ref", [""])[0]
            file_path, content_type = self.retained_file(store, run_id, reference)
            raw = "../" + reference
            source_link = html.escape(raw, quote=True)
            body = (
                f'<p>{html.escape(reference)}</p><p><a href="{source_link}" download>'
                "Download retained file</a></p>"
            )
            if content_type.startswith("image/"):
                body += f'<img src="{html.escape(raw, quote=True)}" alt="Recorded screenshot">'
            else:
                with file_path.open("rb") as source:
                    preview = source.read(512 * 1024).decode("utf-8", errors="replace")
                body += f"<pre>{html.escape(preview)}</pre>"
                if file_path.stat().st_size > 512 * 1024:
                    body += "<p>Preview truncated. Download retains the complete evidence.</p>"
            document = decorate_document(
                store, run_id, page_document("Recorded evidence", body), "evidence.html"
            )
            self.respond_document(store, document)
            return
        if relative == "reports/trace.html":
            try:
                document, _ = render_trace(
                    store, run_id, projector=self.server.projections, live=True
                )
            except Exception:
                document = render_compact(store, run_id, degraded=True)
            self.respond_document(store, decorate_document(store, run_id, document, "trace.html"))
            return
        if relative == "reports/exploration.html":
            document, _ = render_exploration(store, run_id, current=True)
            document = decorate_document(store, run_id, document, "exploration.html")
            self.respond_document(store, document)
            return
        if relative == "reports/exploration.json":
            view = exploration_view(store, run_id)
            self.respond(200, json.dumps(view, ensure_ascii=False), "application/json")
            return
        if relative == "reports/context.html":
            document, _ = render_context(store, run_id)
            self.respond_document(store, decorate_document(store, run_id, document, "context.html"))
            return
        if relative == "reports/agent-trace.json":
            revision = view_revision(store, task_id)
            detail = query.get("detail", [])
            if any(view not in ("sessions", "actions") for view in detail):
                raise FrameworkError(ErrorCode.NOT_FOUND)
            if query.get("after") == [str(revision)]:
                self.respond(204, b"", "application/json")
                return
            view = agent_view(store, run_id, projector=self.server.projections, detail_views=detail)
            view["revision"] = revision
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
        file_path, content_type = self.retained_file(store, run_id, relative)
        if content_type == "text/html; charset=utf-8":
            document = decorate_document(
                store, run_id, file_path.read_text(encoding="utf-8"), relative.rsplit("/", 1)[-1]
            )
            self.respond_document(store, document)
        else:
            self.respond(200, file_path.read_bytes(), content_type)

    def retained_file(self, store, run_id, relative):
        allowed_reports = {
            "reports/gameplay.html",
            "reports/context.json",
            "reports/gameplay.json",
            "reports/execution.html",
            "reports/execution.json",
            "reports/publication.json",
            "reports/workflow.json",
            "reports/index.json",
            "reports/agent-trace.json",
            "reports/exploration.json",
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
        result = (file_path, content_type)
        return result


def serve_trace(root, run_id=None, *, port=8767, interval=3):
    if not 0 <= port <= 65535 or not 1 <= interval <= 60:
        raise FrameworkError(ErrorCode.TRACE_VIEWER_CONFIG)
    store = None
    try:
        task_id = None
        if run_id is not None:
            store = Store.open_readonly(root)
            task_id = store.run(run_id)["task_id"]
        with ThreadingHTTPServer(("127.0.0.1", port), TraceHandler) as server:
            server.project_root = root
            server.interval = interval
            server.projections = TraceProjectionCache()
            initial = f"/tasks/{task_id}/" if task_id else "/reports/history.html"
            print(f"Trace viewer: http://127.0.0.1:{server.server_port}{initial}", flush=True)
            print(f"Refresh interval: {interval}s. Press Ctrl+C to stop.", flush=True)
            if store is not None:
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
