"""
Cognitive-load-aware scheduling engine (pure Python, no dependencies).

Pipeline (re-run from scratch on every request, so completing / adding /
editing a task instantly produces a fresh optimal order):

 1. Topological sort (Kahn)  -> dependency order, cycle detection, and
                                 "effective deadlines" propagated backwards
                                 (a prerequisite must finish before its dependents).
 2. Min-heap                  -> ready tasks ordered by effective deadline; tasks
                                 under critical deadline pressure are forced into today.
 3. Dynamic programming       -> 2-constraint 0/1 knapsack: which *combination* of the
                                 remaining ready tasks to work on today, subject to the
                                 time budget and the cognitive budget.
 4. Simplex (LP)              -> distributes the day's hours across the chosen tasks to
                                 maximise priority-weighted progress.
 5. Max-heap + min-heap       -> orders the day: hardest task first (peak focus), then
                                 alternates light/heavy to avoid back-to-back overload.
 Also: a max-heap ranks the most demanding tasks overall.
"""
import heapq
import math
from datetime import date, timedelta

STEP = 0.5          # DP time granularity (hours)
MAX_DAYS = 365
EPS = 1e-9


# ----------------------------------------------------------------- helpers
def remaining_hours(t):
    return max(0.0, float(t["effort_hours"]) - float(t.get("hours_done", 0)))


def demand(t):
    """Cognitive demand 1-10 = mean of complexity and cognitive load."""
    return (t["complexity"] + t["cognitive_load"]) / 2.0


def _ceil_step(x, step=STEP):
    return math.ceil(x / step - 1e-9) * step


# ------------------------------------------------------ 1. topological sort
def topological_sort(tasks):
    """Kahn's algorithm. `tasks`: dict id -> task. Dependencies pointing outside
    the dict are ignored. Ties are broken with a min-heap on deadline.
    Returns (order, cycle_ids, children)."""
    ids = set(tasks)
    indeg = {i: 0 for i in ids}
    children = {i: [] for i in ids}
    for i, t in tasks.items():
        for d in set(t.get("depends_on", [])):
            if d in ids:
                indeg[i] += 1
                children[d].append(i)
    heap = [(tasks[i]["deadline"], i) for i in ids if indeg[i] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        _, i = heapq.heappop(heap)
        order.append(i)
        for c in children[i]:
            indeg[c] -= 1
            if indeg[c] == 0:
                heapq.heappush(heap, (tasks[c]["deadline"], c))
    cycle = sorted(i for i in ids if indeg[i] > 0)
    return order, cycle, children


def effective_deadlines(pending, order, children, hours_per_day):
    eff = {i: date.fromisoformat(pending[i]["deadline"]) for i in pending}
    for i in reversed(order):
        for c in children[i]:
            days_needed = max(1, math.ceil(remaining_hours(pending[c]) / hours_per_day))
            eff[i] = min(eff[i], eff[c] - timedelta(days=days_needed))
    return eff


# ------------------------------------------------------- 3. DP (knapsack)
def knapsack_2d(items, T, C):
    """items: [(time_units, cog_cost, value)]; maximise value s.t. sum time<=T,
    sum cog<=C. Returns indices of chosen items."""
    n = len(items)
    dp = [[[0.0] * (C + 1) for _ in range(T + 1)] for _ in range(n + 1)]
    for k in range(1, n + 1):
        u, c, v = items[k - 1]
        prev, cur = dp[k - 1], dp[k]
        for t in range(T + 1):
            for cc in range(C + 1):
                best = prev[t][cc]
                if t >= u and cc >= c:
                    cand = prev[t - u][cc - c] + v
                    if cand > best:
                        best = cand
                cur[t][cc] = best
    chosen, t, cc = [], T, C
    for k in range(n, 0, -1):
        if dp[k][t][cc] != dp[k - 1][t][cc]:
            u, c, _ = items[k - 1]
            chosen.append(k - 1)
            t -= u
            cc -= c
    return chosen[::-1]


# ------------------------------------------------------------ 4. simplex
def simplex_max(c, A, b):
    """Maximise c.x  s.t.  A x <= b, x >= 0, b >= 0  (tableau simplex, Bland's rule).
    Returns (x, objective)."""
    m, n = len(A), len(c)
    T = [list(map(float, A[i])) + [1.0 if j == i else 0.0 for j in range(m)] + [float(b[i])]
         for i in range(m)]
    z = [-float(v) for v in c] + [0.0] * m + [0.0]
    basis = [n + i for i in range(m)]
    for _ in range(10000):
        col = next((j for j in range(n + m) if z[j] < -1e-9), None)
        if col is None:
            break
        row, best = None, None
        for i in range(m):
            if T[i][col] > 1e-9:
                ratio = T[i][-1] / T[i][col]
                if best is None or ratio < best - 1e-12 or \
                        (abs(ratio - best) <= 1e-12 and basis[i] < basis[row]):
                    row, best = i, ratio
        if row is None:
            raise ValueError("LP is unbounded")
        p = T[row][col]
        T[row] = [v / p for v in T[row]]
        for i in range(m):
            if i != row and abs(T[i][col]) > 1e-12:
                f = T[i][col]
                T[i] = [a - f * bb for a, bb in zip(T[i], T[row])]
        f = z[col]
        z = [a - f * bb for a, bb in zip(z, T[row])]
        basis[row] = col
    x = [0.0] * n
    for i, bv in enumerate(basis):
        if bv < n:
            x[bv] = T[i][-1]
    return x, z[-1]


def allocate_hours(sel, H, B):
    """sel: list of dicts with lo, hi, w, dem. Returns hours per item via simplex."""
    k = len(sel)
    lo_sum = sum(s["lo"] for s in sel)
    cog_lo = sum(s["dem"] * s["lo"] for s in sel)
    A = [[1.0] * k, [s["dem"] for s in sel]]
    b = [max(0.0, H - lo_sum), max(0.0, B - cog_lo)]
    for i, s in enumerate(sel):
        A.append([1.0 if j == i else 0.0 for j in range(k)])
        b.append(max(0.0, s["hi"] - s["lo"]))
    y, _ = simplex_max([s["w"] for s in sel], A, b)
    return [s["lo"] + max(0.0, yi) for s, yi in zip(sel, y)]


# ---------------------------------------------------------- the planner
def _weight(t, rem, eff, d, H):
    days_left = (eff - d).days
    urgency = 1.5 if days_left < 0 else 1.0 / (1 + days_left)
    avail = (max(days_left, 0) + 1) * H
    pressure = min(2.0, rem / avail)
    return 10 * urgency + 4 * pressure + 0.3 * t["complexity"], days_left


def _order_day(items):
    """Max-heap / min-heap alternation: hardest first, then lighter, then hard..."""
    maxh = [(-it["demand"], it["task_id"]) for it in items]
    minh = [(it["demand"], it["task_id"]) for it in items]
    heapq.heapify(maxh)
    heapq.heapify(minh)
    by_id = {it["task_id"]: it for it in items}
    used, seq, heavy = set(), [], True
    while len(seq) < len(items):
        h = maxh if heavy else minh
        while h and h[0][1] in used:
            heapq.heappop(h)
        _, tid = heapq.heappop(h)
        used.add(tid)
        it = by_id[tid]
        it["reasons"].append("Focus block: hardest work first" if not seq
                             else ("Heavy block" if heavy else "Lighter task to recover"))
        seq.append(it)
        heavy = not heavy
    return seq


def build_plan(tasks, settings, today=None):
    today = today or date.today()
    H = max(0.5, float(settings.get("hours_per_day", 6)))
    cap = float(settings.get("max_cognitive", 6.5))
    B = H * cap
    max_session = max(STEP, float(settings.get("max_session_hours", 3)))
    skip_weekends = bool(settings.get("skip_weekends", False))

    pending = {t["id"]: t for t in tasks
               if not t.get("completed") and remaining_hours(t) > EPS}
    order, cycle, children = topological_sort(pending)
    if cycle:
        return {"error": "Circular dependency between tasks: " + ", ".join(
            pending[i]["title"] for i in cycle), "cycle": cycle}

    eff = effective_deadlines(pending, order, children, H)
    rem = {i: remaining_hours(t) for i, t in pending.items()}
    finished, first_start, finish = set(), {}, {}
    days, seq_no = [], []
    d, spins = today, 0

    while any(r > EPS for r in rem.values()) and spins < MAX_DAYS:
        spins += 1
        if skip_weekends and d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        ready = [i for i, r in rem.items() if r > EPS and
                 all((dp not in pending) or dp in finished for dp in pending[i].get("depends_on", []))]
        if not ready:
            d += timedelta(days=1)
            continue

        info = {}
        for i in ready:
            w, dl = _weight(pending[i], rem[i], eff[i], d, H)
            rate = rem[i] / (max(dl, 0) + 1)
            info[i] = {"w": w, "dl": dl, "rate": rate}

        # --- min-heap: urgency queue, critical tasks are forced into today
        mh = [(eff[i], -info[i]["rate"], i) for i in ready]
        heapq.heapify(mh)
        time_left, forced, rest = H, [], []
        while mh:
            _, _, i = heapq.heappop(mh)
            f = info[i]
            critical = f["dl"] < 0 or f["rate"] >= 0.5 * H
            if critical and time_left >= STEP - EPS:
                hrs = min(rem[i], time_left,
                          max_session if f["dl"] < 0 else max(STEP, _ceil_step(f["rate"])))
                forced.append((i, hrs))
                time_left -= hrs
            else:
                rest.append(i)

        # --- DP: choose the best combination of the remaining ready tasks
        cog_left = B - sum(demand(pending[i]) * h for i, h in forced)
        cands = sorted(rest, key=lambda i: -info[i]["w"])[:20]
        T, C = int((time_left + EPS) / STEP), max(0, int(cog_left + EPS))
        items, lo_map = [], {}
        for i in cands:
            lo = min(rem[i], 1.0)
            lo_map[i] = lo
            items.append((max(1, math.ceil(lo / STEP - 1e-9)),
                          math.ceil(demand(pending[i]) * lo - 1e-9), info[i]["w"] * lo))
        picked = [cands[k] for k in knapsack_2d(items, T, C)] if cands and T > 0 else []

        # --- Simplex: distribute the day's hours
        sel = []
        for i, h in forced:
            sel.append({"id": i, "lo": h, "hi": min(rem[i], max(h, max_session)),
                        "w": info[i]["w"] + 5, "dem": demand(pending[i]), "forced": True})
        for i in picked:
            sel.append({"id": i, "lo": lo_map[i], "hi": min(rem[i], max_session),
                        "w": info[i]["w"], "dem": demand(pending[i]), "forced": False})
        if not sel:  # safety net: always make progress
            i = min(ready, key=lambda k: eff[k])
            sel.append({"id": i, "lo": min(rem[i], H), "hi": min(rem[i], H),
                        "w": 1.0, "dem": demand(pending[i]), "forced": True})
        hours = allocate_hours(sel, H, B)

        items_out = []
        for s, x in zip(sel, hours):
            i = s["id"]
            h = math.floor(x * 4 + 1e-6) / 4
            if rem[i] <= h + 0.25:
                h = rem[i]
            h = min(h, rem[i])
            if h <= EPS:
                continue
            reasons = []
            if s["forced"]:
                reasons.append("Overdue" if info[i]["dl"] < 0 else "Deadline pressure")
            items_out.append({"task_id": i, "title": pending[i]["title"], "type": pending[i]["type"],
                              "hours": round(h, 2), "demand": round(demand(pending[i]), 1),
                              "reasons": reasons})
        items_out = _order_day(items_out)

        clock, tot_dem = 9.0, 0.0
        for it in items_out:
            i = it["task_id"]
            rem[i] -= it["hours"]
            first_start.setdefault(i, d)
            seq_no.append(i)
            it["start"] = f"{int(clock):02d}:{int(round((clock % 1) * 60)):02d}"
            clock += it["hours"]
            it["end"] = f"{int(clock):02d}:{int(round((clock % 1) * 60)):02d}"
            tot_dem += it["demand"] * it["hours"]
            if rem[i] <= EPS:
                rem[i] = 0.0
                finish[i] = d
        total = sum(it["hours"] for it in items_out)
        avg = tot_dem / total if total else 0
        status = ("overloaded" if avg > cap + 0.75 else "heavy" if avg > cap
                  else "balanced" if avg >= cap - 1.5 else "light")
        days.append({"date": d.isoformat(), "weekday": d.strftime("%a"), "items": items_out,
                     "total_hours": round(total, 2), "avg_demand": round(avg, 1),
                     "load_status": status})
        finished |= {i for i in ready if rem[i] <= EPS}
        d += timedelta(days=1)

    # ---- per-task schedule + summary
    schedule, late = [], 0
    for i in order:
        t = pending[i]
        dl = date.fromisoformat(t["deadline"])
        f = finish.get(i)
        if f is None:
            state, days_late = "unscheduled", None
        else:
            days_late = (f - dl).days
            state = "late" if days_late > 0 else ("at_risk" if days_late == 0 else "on_track")
        late += state in ("late", "unscheduled")
        blockers = [pending[x]["title"] for x in t.get("depends_on", []) if x in pending]
        schedule.append({"task_id": i, "title": t["title"],
                         "start": first_start[i].isoformat() if i in first_start else None,
                         "finish": f.isoformat() if f else None, "deadline": t["deadline"],
                         "days_late": days_late, "state": state, "blocked_by": blockers})
    seen, sequence = set(), []
    for i in seq_no:
        if i not in seen:
            seen.add(i)
            sequence.append(i)
    # max-heap: most demanding tasks overall
    mx = [(-(demand(t) * 10 + t["complexity"]), i) for i, t in pending.items()]
    heapq.heapify(mx)
    heaviest = [heapq.heappop(mx)[1] for _ in range(min(3, len(mx)))]
    return {"error": None, "today": today.isoformat(), "days": days, "sequence": sequence,
            "schedule": schedule, "heaviest": heaviest,
            "summary": {"pending": len(pending),
                        "remaining_hours": round(sum(remaining_hours(t) for t in pending.values()), 1),
                        "days_needed": len(days),
                        "projected_finish": days[-1]["date"] if days else None,
                        "late_or_unscheduled": late,
                        "overloaded_days": sum(1 for x in days if x["load_status"] in ("heavy", "overloaded")),
                        "hours_per_day": H, "max_cognitive": cap}}
