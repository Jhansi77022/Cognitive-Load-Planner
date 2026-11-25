"""Tiny JSON-file persistence layer."""
import json
import os
import threading
from datetime import date, timedelta

DEFAULT_SETTINGS = {"hours_per_day": 6, "max_cognitive": 7.0,
                    "max_session_hours": 3, "skip_weekends": False}


def sample_tasks():
    t = date.today()
    f = lambda n: (t + timedelta(days=n)).isoformat()
    # (title, type, +days, effort, complexity, cognitive, depends_on titles)
    rows = [
        ("Calculus exam", "exam", 12, 14, 9, 9, []),
        ("Algorithms exam", "exam", 16, 16, 9, 9, []),
        ("Databases exam", "exam", 20, 10, 7, 8, []),
        ("Capstone: build prototype", "project", 15, 20, 9, 8, []),
        ("Capstone: final report", "project", 22, 10, 6, 6, ["Capstone: build prototype"]),
        ("Research paper outline", "project", 9, 4, 5, 5, []),
        ("Research paper draft", "project", 18, 12, 7, 7, ["Research paper outline"]),
        ("Calculus problem set 5", "assignment", 3, 3, 6, 6, []),
        ("Physics lab report", "assignment", 4, 3, 5, 5, []),
        ("Algorithms homework 4", "assignment", 5, 4, 8, 8, []),
        ("English reading response", "assignment", 2, 1.5, 3, 3, []),
        ("Databases ER diagram", "assignment", 7, 2.5, 5, 5, []),
        ("SQL practice sheet", "assignment", 8, 2, 4, 5, ["Databases ER diagram"]),
        ("History essay", "assignment", 10, 5, 6, 6, []),
        ("Statistics worksheet", "assignment", 6, 2, 4, 4, []),
        ("Ethics discussion post", "assignment", 3, 1, 2, 2, []),
        ("Lab notebook cleanup", "assignment", 11, 1.5, 2, 2, []),
    ]
    ids = {r[0]: i + 1 for i, r in enumerate(rows)}
    return [{"id": ids[r[0]], "title": r[0], "type": r[1], "deadline": f(r[2]),
             "effort_hours": r[3], "complexity": r[4], "cognitive_load": r[5],
             "depends_on": [ids[x] for x in r[6]], "completed": False, "hours_done": 0,
             "completed_at": None} for r in rows]


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.data = {"next_id": 1, "tasks": [], "settings": dict(DEFAULT_SETTINGS)}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.data.update(json.load(fh))
        else:
            self.seed()

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
        os.replace(tmp, self.path)

    def seed(self):
        with self.lock:
            self.data["tasks"] = sample_tasks()
            self.data["next_id"] = len(self.data["tasks"]) + 1
            self.save()

    def reset(self):
        with self.lock:
            self.data["tasks"], self.data["next_id"] = [], 1
            self.save()

    def new_id(self):
        i = self.data["next_id"]
        self.data["next_id"] += 1
        return i

    def get(self, tid):
        return next((t for t in self.data["tasks"] if t["id"] == tid), None)
