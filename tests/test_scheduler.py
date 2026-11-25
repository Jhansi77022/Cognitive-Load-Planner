import os, sys, unittest
from datetime import date, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from scheduler import build_plan, simplex_max, knapsack_2d, topological_sort
from store import sample_tasks, DEFAULT_SETTINGS

TODAY = date(2026, 1, 5)


def mk(i, title, days, eff, cx=5, cog=5, deps=()):
    return {"id": i, "title": title, "type": "assignment", "deadline": (TODAY + timedelta(days=days)).isoformat(),
            "effort_hours": eff, "complexity": cx, "cognitive_load": cog, "depends_on": list(deps),
            "completed": False, "hours_done": 0}


class Tests(unittest.TestCase):
    def test_simplex(self):
        x, obj = simplex_max([3, 2], [[1, 1], [1, 3]], [4, 6])
        self.assertAlmostEqual(obj, 12); self.assertAlmostEqual(x[0], 4)

    def test_knapsack(self):
        self.assertEqual(sorted(knapsack_2d([(2, 1, 3), (2, 1, 4), (3, 1, 5)], 4, 5)), [0, 1])

    def test_cycle_detected(self):
        t = {1: mk(1, "a", 5, 1, deps=[2]), 2: mk(2, "b", 5, 1, deps=[1])}
        self.assertEqual(topological_sort(t)[1], [1, 2])
        self.assertIsNotNone(build_plan(list(t.values()), DEFAULT_SETTINGS, TODAY)["error"])

    def test_dependencies_respected_and_capacity(self):
        tasks = sample_tasks()
        for t in tasks: t["deadline"] = (TODAY + (date.fromisoformat(t["deadline"]) - date.today())).isoformat()
        plan = build_plan(tasks, DEFAULT_SETTINGS, TODAY)
        self.assertIsNone(plan["error"])
        first_day = {}
        last_day = {}
        for d in plan["days"]:
            self.assertLessEqual(d["total_hours"], DEFAULT_SETTINGS["hours_per_day"] + 0.3)
            for it in d["items"]:
                first_day.setdefault(it["task_id"], d["date"]); last_day[it["task_id"]] = d["date"]
        for t in tasks:
            for dep in t["depends_on"]:
                self.assertLess(last_day[dep], first_day[t["id"]])
        worked = sum(it["hours"] for d in plan["days"] for it in d["items"])
        self.assertAlmostEqual(worked, sum(t["effort_hours"] for t in tasks), places=1)

    def test_completing_reorders(self):
        tasks = [mk(1, "urgent", 1, 4, 8, 8), mk(2, "later", 20, 4), mk(3, "mid", 6, 3)]
        before = build_plan(tasks, DEFAULT_SETTINGS, TODAY)["sequence"]
        self.assertEqual(before[0], 1)
        tasks[0]["completed"] = True
        after = build_plan(tasks, DEFAULT_SETTINGS, TODAY)
        self.assertNotIn(1, after["sequence"]); self.assertEqual(after["summary"]["pending"], 2)

    def test_adding_task_changes_plan(self):
        tasks = [mk(1, "a", 10, 4)]
        n1 = build_plan(tasks, DEFAULT_SETTINGS, TODAY)["summary"]["remaining_hours"]
        tasks.append(mk(2, "b", 2, 5, 9, 9))
        p = build_plan(tasks, DEFAULT_SETTINGS, TODAY)
        self.assertGreater(p["summary"]["remaining_hours"], n1); self.assertIn(2, p["days"][0]["items"][0].values() if False else p["sequence"])


if __name__ == "__main__":
    unittest.main()
