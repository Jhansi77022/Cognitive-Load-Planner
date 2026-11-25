# Cognitive Load Planner

Orders your tasks (assignments, exams, projects) by deadline pressure, dependencies **and** mental demand,
then builds a day-by-day plan. Zero dependencies: only Python 3.8+.

## Run
    python backend/server.py          # then open http://localhost:8000
    PORT=9000 python backend/server.py   # custom port
    python -m unittest discover tests    # run tests

Data is stored in `data/tasks.json`. Click "Load sample student data" to try 17 sample tasks.

## Using it
* **Complete a task**: tick its checkbox. The plan rebuilds immediately (untick under "Completed" to reopen).
* **Partial progress**: type hours done in the task row; only the remaining effort is scheduled.
* **Add tasks any time**: use the form (set "Depends on" for prerequisites). Edit/Delete work too.
* **Capacity**: set hours per day, comfortable mental load, weekends off.

## Algorithms (backend/scheduler.py)
| Algorithm | Role |
|---|---|
| Topological sort | Dependency order, cycle detection, deadlines propagated back to prerequisites |
| Min heap | Urgency queue; tasks under critical deadline pressure are forced into today |
| DP (2-constraint knapsack) | Best combination of tasks per day under the time and cognitive budgets |
| Simplex (LP) | Splits the day's hours across chosen tasks to maximise priority-weighted progress |
| Max heap | Ranks the heaviest tasks; puts the hardest first each day, alternating with light tasks (min heap) |

## API
See the docstring at the top of `backend/server.py`.
