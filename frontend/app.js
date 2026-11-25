"use strict";
const $ = (s) => document.querySelector(s);
const state = { tasks: [], settings: {}, plan: null, editing: null };

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
const fmtH = (h) => (Math.round(h * 100) / 100) + "h";
const todayISO = () => new Date().toISOString().slice(0, 10);

async function api(method, path, body) {
  const res = await fetch("/api" + path, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

async function refresh() {
  [state.tasks, state.settings, state.plan] = await Promise.all([api("GET", "/tasks"), api("GET", "/settings"), api("GET", "/plan")]);
  render();
}

// ------------------------------------------------------------------ render
function render() {
  renderStats(); renderTasks(); renderPlan(); renderDeps(); renderSettings();
}

function renderStats() {
  const p = state.plan, el = $("#stats"), alert = $("#alert");
  if (!p || p.error) {
    el.innerHTML = ""; alert.hidden = !p; alert.textContent = p ? p.error : ""; return;
  }
  alert.hidden = true;
  const s = p.summary, done = state.tasks.filter((t) => t.completed).length;
  const stat = (v, l, c = "") => `<div class="stat ${c}"><b>${esc(v)}</b><span>${esc(l)}</span></div>`;
  el.innerHTML =
    stat(s.pending, "tasks to do") + stat(done, "completed", "good") + stat(s.remaining_hours + "h", "work remaining") +
    stat(s.projected_finish ? fmtDate(s.projected_finish) : "-", "projected finish") +
    stat(s.late_or_unscheduled, "at risk of missing deadline", s.late_or_unscheduled ? "bad" : "good") +
    stat(s.overloaded_days, "heavy days", s.overloaded_days ? "bad" : "good");
}

function taskMeta(t, sched) {
  const bits = [`${esc(t.type)}`, `due ${fmtDate(t.deadline)}`, `${fmtH(t.effort_hours)} effort`, `complexity ${t.complexity}/10`, `load ${t.cognitive_load}/10`];
  let tag = "";
  if (sched) {
    const label = { on_track: "on track", at_risk: "tight", late: `${sched.days_late}d late`, unscheduled: "no time left" }[sched.state];
    tag = `<span class="tag ${sched.state}">${label}</span>`;
    if (sched.blocked_by.length) tag += ` <span class="tag blocked">after: ${sched.blocked_by.map(esc).join(", ")}</span>`;
  }
  return `<div class="meta">${bits.map((b) => `<span>${b}</span>`).join("")}</div><div class="meta">${tag}</div>`;
}

function renderTasks() {
  const sched = {};
  if (state.plan && !state.plan.error) state.plan.schedule.forEach((s) => (sched[s.task_id] = s));
  const rank = {};
  if (state.plan && !state.plan.error) state.plan.sequence.forEach((id, i) => (rank[id] = i));
  const pending = state.tasks.filter((t) => !t.completed).sort((a, b) => (rank[a.id] ?? 1e9) - (rank[b.id] ?? 1e9));
  const done = state.tasks.filter((t) => t.completed);
  $("#count-pending").textContent = pending.length; $("#count-done").textContent = done.length;
  $("#pending-empty").hidden = pending.length > 0;

  $("#pending").innerHTML = pending.map((t, i) => {
    const pct = Math.round((t.hours_done / t.effort_hours) * 100);
    return `<li class="task ${t.type}" data-id="${t.id}">
      <input type="checkbox" aria-label="Mark ${esc(t.title)} as complete" data-act="complete">
      <div><div class="t">${rank[t.id] !== undefined ? `${rank[t.id] + 1}. ` : ""}${esc(t.title)}</div>${taskMeta(t, sched[t.id])}
        <div class="prog"><span>Done</span><input type="number" min="0" max="${t.effort_hours}" step="0.25" value="${t.hours_done}" data-act="progress" aria-label="Hours done on ${esc(t.title)}"><span>of ${fmtH(t.effort_hours)}</span><div class="bar"><i style="width:${pct}%"></i></div></div></div>
      <div class="acts"><button data-act="edit">Edit</button><button data-act="delete" class="danger">Delete</button></div></li>`;
  }).join("");

  $("#done").innerHTML = done.map((t) => `<li class="task ${t.type} is-done" data-id="${t.id}">
      <input type="checkbox" checked aria-label="Reopen ${esc(t.title)}" data-act="reopen">
      <div><div class="t">${esc(t.title)}</div><div class="meta"><span>${esc(t.type)}</span><span>due ${fmtDate(t.deadline)}</span></div></div>
      <div class="acts"><button data-act="delete" class="danger">Delete</button></div></li>`).join("") || `<li class="empty">No completed tasks yet.</li>`;
}

function renderPlan() {
  const p = state.plan;
  if (!p || p.error || !p.days.length) {
    $("#today").innerHTML = `<p class="empty">${p && p.error ? esc(p.error) : "Add tasks to get your plan."}</p>`; $("#days").innerHTML = ""; return;
  }
  const slots = (d) => d.items.map((it) => `<div class="slot"><span class="time">${it.start}-${it.end}</span>
      <div><b>${esc(it.title)}</b>${it.reasons.filter((r) => /Overdue|pressure/.test(r)).map((r) => `<span class="chip">${r}</span>`).join("")}
      <div class="why">${esc(it.reasons.filter((r) => !/Overdue|pressure/.test(r)).join(". "))} (demand ${it.demand}/10)</div></div>
      <span class="hrs">${fmtH(it.hours)}</span></div>`).join("");
  const first = p.days[0];
  $("#today").innerHTML = `<div class="hint">${first.date === todayISO() ? "Today" : "First working day"}, ${fmtDate(first.date)}: ${fmtH(first.total_hours)} planned</div>${slots(first)}`;

  const cap = p.summary.max_cognitive;
  $("#days").innerHTML = p.days.map((d) => `<div class="day"><div class="day-h"><b>${fmtDate(d.date)}</b>
      <span class="hint">${fmtH(d.total_hours)} · avg demand ${d.avg_demand}/10 · ${d.load_status}</span></div>
      <div class="gauge ${d.load_status}" title="Average mental demand ${d.avg_demand} of 10"><i style="width:${d.avg_demand * 10}%"></i><u style="left:${cap * 10}%"></u></div>
      <ul>${d.items.map((it) => `<li><b>${esc(it.title)}</b> ${fmtH(it.hours)}</li>`).join("")}</ul></div>`).join("");
}

function renderDeps() {
  const sel = $("#task-form").depends_on, keep = new Set([...sel.selectedOptions].map((o) => o.value));
  sel.innerHTML = state.tasks.filter((t) => !t.completed && t.id !== state.editing)
    .map((t) => `<option value="${t.id}" ${keep.has(String(t.id)) ? "selected" : ""}>${esc(t.title)}</option>`).join("");
}

function renderSettings() {
  const f = $("#settings-form");
  if (document.activeElement && f.contains(document.activeElement)) return;
  f.hours_per_day.value = state.settings.hours_per_day; f.max_cognitive.value = state.settings.max_cognitive;
  f.max_session_hours.value = state.settings.max_session_hours; f.skip_weekends.checked = !!state.settings.skip_weekends;
  $("#o-cap").textContent = state.settings.max_cognitive + "/10";
}

// ------------------------------------------------------------------ events
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]"); if (!btn || btn.tagName === "INPUT") return;
  const id = Number(btn.closest(".task").dataset.id), task = state.tasks.find((t) => t.id === id);
  try {
    if (btn.dataset.act === "delete") { if (confirm(`Delete "${task.title}"?`)) { await api("DELETE", `/tasks/${id}`); await refresh(); } }
    if (btn.dataset.act === "edit") startEdit(task);
  } catch (err) { flash(err.message); }
});

document.addEventListener("change", async (e) => {
  const el = e.target, act = el.dataset && el.dataset.act; if (!act) return;
  const id = Number(el.closest(".task").dataset.id);
  try {
    if (act === "complete") await api("POST", `/tasks/${id}/complete`, { completed: true });
    if (act === "reopen") await api("POST", `/tasks/${id}/complete`, { completed: false });
    if (act === "progress") await api("POST", `/tasks/${id}/progress`, { hours_done: el.value });
    await refresh();
  } catch (err) { flash(err.message); await refresh(); }
});

function flash(msg, ok = false) { const m = $("#form-msg"); m.textContent = msg; m.className = "msg" + (ok ? " ok" : ""); }

function startEdit(t) {
  const f = $("#task-form"); state.editing = t.id; renderDeps();
  f.title.value = t.title; f.type.value = t.type; f.deadline.value = t.deadline; f.effort_hours.value = t.effort_hours;
  f.complexity.value = t.complexity; f.cognitive_load.value = t.cognitive_load;
  [...f.depends_on.options].forEach((o) => (o.selected = t.depends_on.includes(Number(o.value))));
  syncOutputs(); $("#form-title").textContent = "Edit task"; $("#btn-save").textContent = "Save changes"; $("#btn-cancel").hidden = false;
  f.scrollIntoView({ behavior: "smooth", block: "center" }); f.title.focus();
}

function resetForm() {
  const f = $("#task-form"); state.editing = null; f.reset(); f.deadline.value = defaultDeadline(); syncOutputs();
  $("#form-title").textContent = "Add a task"; $("#btn-save").textContent = "Add task"; $("#btn-cancel").hidden = true; renderDeps();
}
function defaultDeadline() { const d = new Date(); d.setDate(d.getDate() + 7); return d.toISOString().slice(0, 10); }
function syncOutputs() { const f = $("#task-form"); $("#o-cx").textContent = f.complexity.value; $("#o-cog").textContent = f.cognitive_load.value; }

$("#task-form").addEventListener("input", syncOutputs);
$("#btn-cancel").addEventListener("click", () => { resetForm(); flash(""); });
$("#task-form").addEventListener("submit", async (e) => {
  e.preventDefault(); const f = e.target;
  const body = { title: f.title.value, type: f.type.value, deadline: f.deadline.value, effort_hours: Number(f.effort_hours.value),
    complexity: Number(f.complexity.value), cognitive_load: Number(f.cognitive_load.value),
    depends_on: [...f.depends_on.selectedOptions].map((o) => Number(o.value)) };
  try {
    const editing = state.editing !== null;
    if (editing) await api("PUT", `/tasks/${state.editing}`, body); else await api("POST", "/tasks", body);
    resetForm(); await refresh(); flash(editing ? "Task updated and plan rebuilt." : "Task added and plan rebuilt.", true);
  } catch (err) { flash(err.message); }
});

$("#settings-form").addEventListener("input", (e) => { if (e.target.name === "max_cognitive") $("#o-cap").textContent = e.target.value + "/10"; });
$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault(); const f = e.target;
  try {
    await api("PUT", "/settings", { hours_per_day: f.hours_per_day.value, max_cognitive: f.max_cognitive.value,
      max_session_hours: f.max_session_hours.value, skip_weekends: f.skip_weekends.checked });
    f.querySelector("button").blur(); await refresh();
  } catch (err) { $("#alert").hidden = false; $("#alert").textContent = err.message; }
});

$("#btn-seed").addEventListener("click", async () => { if (!state.tasks.length || confirm("Replace your tasks with the sample data?")) { await api("POST", "/seed"); await refresh(); } });
$("#btn-reset").addEventListener("click", async () => { if (confirm("Delete ALL tasks?")) { await api("POST", "/reset"); await refresh(); } });

resetForm();
refresh().catch((err) => { $("#alert").hidden = false; $("#alert").textContent = "Cannot reach the server: " + err.message; });
