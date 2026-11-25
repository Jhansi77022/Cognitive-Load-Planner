"""
REST API + static file server. Python 3.8+ standard library only.

    python backend/server.py            # http://localhost:8000

Endpoints
  GET    /api/tasks                     list tasks
  POST   /api/tasks                     add a task  (or {"tasks":[...]} to add several)
  PUT    /api/tasks/<id>                edit a task
  DELETE /api/tasks/<id>                delete a task
  POST   /api/tasks/<id>/complete       {"completed": true|false}  -> mark done / reopen
  POST   /api/tasks/<id>/progress       {"hours_done": 2.5}        -> log partial progress
  GET    /api/plan                      freshly optimised plan (recomputed every call)
  GET/PUT /api/settings                 daily hours, cognitive budget, ...
  POST   /api/seed | /api/reset         load sample data | clear all
"""
import json
import mimetypes
import os
import re
import sys
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scheduler import build_plan, topological_sort  # noqa: E402
from store import Store, DEFAULT_SETTINGS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")
STORE = Store(os.environ.get("COGLOAD_DATA", os.path.join(ROOT, "data", "tasks.json")))
TYPES = ("assignment", "exam", "project", "other")


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def clean_task(raw, existing=None):
    """Validate/normalise a task payload. Raises ApiError on bad input."""
    base = dict(existing or {})
    base.update(raw)
    title = str(base.get("title", "")).strip()
    if not title:
        raise ApiError("Title is required.")
    ttype = base.get("type", "assignment")
    if ttype not in TYPES:
        raise ApiError("Type must be one of: " + ", ".join(TYPES))
    try:
        deadline = date.fromisoformat(str(base.get("deadline", "")))
    except ValueError:
        raise ApiError("Deadline must be a date (YYYY-MM-DD).")
    try:
        effort = float(base.get("effort_hours"))
        cx = int(base.get("complexity", 5))
        cog = int(base.get("cognitive_load", 5))
    except (TypeError, ValueError):
        raise ApiError("Effort, complexity and cognitive load must be numbers.")
    if not 0 < effort <= 500:
        raise ApiError("Effort must be between 0 and 500 hours.")
    if not (1 <= cx <= 10 and 1 <= cog <= 10):
        raise ApiError("Complexity and cognitive load must be between 1 and 10.")
    deps = base.get("depends_on", []) or []
    try:
        deps = sorted({int(x) for x in deps})
    except (TypeError, ValueError):
        raise ApiError("depends_on must be a list of task ids.")
    done = max(0.0, min(float(base.get("hours_done", 0) or 0), effort))
    completed = bool(base.get("completed", False))
    if completed:
        done = effort
    return {"id": base.get("id"), "title": title[:120], "type": ttype,
            "deadline": deadline.isoformat(), "effort_hours": effort, "complexity": cx,
            "cognitive_load": cog, "depends_on": deps, "completed": completed,
            "hours_done": done, "completed_at": base.get("completed_at")}


def check_deps(task):
    ids = {t["id"] for t in STORE.data["tasks"]}
    for d in task["depends_on"]:
        if d not in ids or d == task["id"]:
            raise ApiError(f"Invalid dependency id {d}.")
    others = {t["id"]: t for t in STORE.data["tasks"] if t["id"] != task["id"]}
    others[task["id"]] = task
    _, cycle, _ = topological_sort(others)
    if cycle:
        raise ApiError("That dependency would create a circular chain between tasks.")


def mark_complete(task, completed):
    task["completed"] = completed
    if completed:
        task["hours_done"] = task["effort_hours"]
        task["completed_at"] = datetime.now().isoformat(timespec="seconds")
    else:
        task["hours_done"] = 0
        task["completed_at"] = None


class Handler(BaseHTTPRequestHandler):
    server_version = "CogLoad/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("COGLOAD_QUIET") != "1":
            super().log_message(fmt, *args)

    # ---- plumbing
    def _send(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            data = json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            raise ApiError("Request body must be valid JSON.")
        if not isinstance(data, dict):
            raise ApiError("Request body must be a JSON object.")
        return data

    def _dispatch(self, method):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        try:
            if path.startswith("/api/"):
                with STORE.lock:
                    self._send(*self._api(method, path, parse_qs(url.query)))
            elif method == "GET":
                self._static(path)
            else:
                raise ApiError("Not found", 404)
        except ApiError as e:
            self._send(e.code, {"error": str(e)})
        except Exception as e:  # pragma: no cover
            self._send(500, {"error": f"Server error: {e}"})

    do_GET = lambda self: self._dispatch("GET")
    do_POST = lambda self: self._dispatch("POST")
    do_PUT = lambda self: self._dispatch("PUT")
    do_DELETE = lambda self: self._dispatch("DELETE")

    def _static(self, path):
        rel = "index.html" if path == "/" else path.lstrip("/")
        full = os.path.normpath(os.path.join(FRONTEND, rel))
        if not full.startswith(FRONTEND) or not os.path.isfile(full):
            raise ApiError("Not found", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype + ("; charset=utf-8" if ctype.startswith("text") or "javascript" in ctype else ""))

    # ---- API
    def _api(self, method, path, query):
        data, tasks = STORE.data, STORE.data["tasks"]

        if path == "/api/tasks" and method == "GET":
            return 200, tasks
        if path == "/api/tasks" and method == "POST":
            body = self._body()
            batch = body["tasks"] if isinstance(body.get("tasks"), list) else [body]
            created = []
            for raw in batch:
                t = clean_task(raw)
                t["id"] = STORE.new_id()
                t["completed"], t["hours_done"], t["completed_at"] = False, 0, None
                check_deps(t)
                tasks.append(t)
                created.append(t)
            STORE.save()
            return 201, created if len(created) != 1 or "tasks" in body else created[0]

        m = re.fullmatch(r"/api/tasks/(\d+)(?:/(complete|progress))?", path)
        if m:
            task = STORE.get(int(m.group(1)))
            if not task:
                raise ApiError("Task not found.", 404)
            action = m.group(2)
            if action == "complete" and method == "POST":
                body = self._body()
                mark_complete(task, bool(body.get("completed", True)))
            elif action == "progress" and method == "POST":
                try:
                    h = float(self._body().get("hours_done"))
                except (TypeError, ValueError):
                    raise ApiError("hours_done must be a number.")
                if h < 0:
                    raise ApiError("hours_done cannot be negative.")
                task["hours_done"] = min(h, task["effort_hours"])
                if task["hours_done"] >= task["effort_hours"]:
                    mark_complete(task, True)
                elif task["completed"]:
                    mark_complete(task, False)
                    task["hours_done"] = h
            elif action is None and method == "PUT":
                new = clean_task(self._body(), existing=task)
                new["id"] = task["id"]
                check_deps(new)
                task.update(new)
            elif action is None and method == "DELETE":
                tasks.remove(task)
                for t in tasks:
                    t["depends_on"] = [d for d in t["depends_on"] if d != task["id"]]
            else:
                raise ApiError("Method not allowed", 405)
            STORE.save()
            return 200, task

        if path == "/api/plan" and method == "GET":
            today = date.today()
            if query.get("today"):
                try:
                    today = date.fromisoformat(query["today"][0])
                except ValueError:
                    raise ApiError("today must be YYYY-MM-DD")
            return 200, build_plan(tasks, data["settings"], today)

        if path == "/api/settings":
            if method == "GET":
                return 200, data["settings"]
            if method == "PUT":
                body, s = self._body(), data["settings"]
                try:
                    if "hours_per_day" in body:
                        s["hours_per_day"] = min(16.0, max(1.0, float(body["hours_per_day"])))
                    if "max_cognitive" in body:
                        s["max_cognitive"] = min(10.0, max(1.0, float(body["max_cognitive"])))
                    if "max_session_hours" in body:
                        s["max_session_hours"] = min(8.0, max(0.5, float(body["max_session_hours"])))
                except (TypeError, ValueError):
                    raise ApiError("Settings must be numbers.")
                if "skip_weekends" in body:
                    s["skip_weekends"] = bool(body["skip_weekends"])
                STORE.save()
                return 200, s

        if path == "/api/seed" and method == "POST":
            STORE.seed()
            return 200, STORE.data["tasks"]
        if path == "/api/reset" and method == "POST":
            STORE.reset()
            return 200, []
        raise ApiError("Not found", 404)


def main():
    port = int(os.environ.get("PORT", 8000))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Cognitive Load Planner running at http://localhost:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
