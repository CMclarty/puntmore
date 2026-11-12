// Hub Console. Plain JS, no build, no CDN.
// Everything shown here that came from the Hub's data (scraped rows, tips, escalation details, log
// lines) is untrusted: it is only ever put on the page with textContent, never innerHTML.
"use strict";

const COMPONENTS = [
  ["scraper", "Scraper"], ["daemon", "Daemon"], ["placement", "Placement"], ["escalations", "Escalations"],
  ["tips", "Tips"], ["model", "Model"], ["hermes", "HubAI"], ["render", "Render"],
];
const POLL_MS = 5000;
const STALE_MS = 20000;
// A Scraper target's own state (scraper/runner.py `status`) -> the colour it gets.
const TARGET_STATE = { running: "up", paused: "warn", blocked: "down", retired: "off", finished: "off" };

const feeds = {};          // name -> {lastOk: ms, data, skew: server now - client now (ms)}
let active = null;
let overview = null;

// ---- DOM helpers (textContent only) ---------------------------------------------------------------
function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = String(v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const show = (v) => (v === null || v === undefined || v === "") ? "—"
  : (typeof v === "object" ? JSON.stringify(v) : String(v));
const num = (v, dp) => (v === null || v === undefined) ? "—" : Number(v).toFixed(dp);
const pct = (v) => (v === null || v === undefined) ? "—" : (100 * v).toFixed(1) + "%";

function ago(isoStr, skew) {
  if (!isoStr) return "never";
  const t = Date.parse(isoStr);
  if (Number.isNaN(t)) return show(isoStr);
  const s = Math.max(0, Math.round((Date.now() + (skew || 0) - t) / 1000));
  if (s < 90) return s + "s ago";
  if (s < 5400) return Math.round(s / 60) + "m ago";
  if (s < 172800) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}
function when(isoStr, skew) {
  return isoStr ? el("time", { datetime: isoStr, title: isoStr, text: ago(isoStr, skew) }) : "—";
}

// cols: [label, key | (row) => Node|string, className?]; opts: {cls: table class, rowClass: (row) => class}
// Every cell carries its column's label (data-label), which a `table.cards` shows on a phone.
function table(cols, rows, empty, opts) {
  if (!rows || !rows.length) return el("p", { class: "empty", text: empty || "nothing yet" });
  const o = opts || {};
  const head = el("tr", {}, cols.map(([label]) => el("th", { text: label })));
  const body = rows.map((r) => el("tr", { class: o.rowClass ? o.rowClass(r) : null }, cols.map(([label, key, cls]) => {
    const v = typeof key === "function" ? key(r) : show(r[key]);
    return el("td", { class: cls, "data-label": label }, v instanceof Node ? v : String(v));
  })));
  return el("table", { class: o.cls }, el("thead", {}, head), el("tbody", {}, body));
}
function kv(pairs) {
  return el("dl", { class: "kv" }, pairs.map(([k, v]) => [el("dt", { text: k }),
    el("dd", {}, v instanceof Node ? v : show(v))]));
}
function stateTag(s) { return el("span", { class: "st st-" + (s || "unknown"), text: s || "unknown" }); }
function fill(section, slot, ...nodes) {
  const box = section.querySelector(`[data-slot="${slot}"]`);
  if (box) box.replaceChildren(...nodes);
}
function lines(section, slot, arr, emptyText) {
  const box = section.querySelector(`[data-slot="${slot}"]`);
  if (!box) return;
  const atEnd = !box.dataset.filled || box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
  box.textContent = arr && arr.length ? arr.join("\n") : emptyText;
  box.dataset.filled = "1";
  if (atEnd) box.scrollTop = box.scrollHeight;     // newest last: keep following the tail
}

// ---- fetching -----------------------------------------------------------------------------------
async function getJSON(url, timeoutMs) {
  const opts = { cache: "no-store", headers: { Accept: "application/json" } };
  if (timeoutMs) opts.signal = AbortSignal.timeout(timeoutMs);   // a request that never settles must not hold its caller
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

async function pollOverview() {
  try {
    const d = await getJSON("/api/overview");
    overview = d;
    feeds.overview = { lastOk: Date.now(), skew: Date.parse(d.now) - Date.now() };
    renderOverview();
  } catch (e) { /* the stale marker says it */ }
}

async function pollTab(name) {
  try {
    const d = await getJSON("/api/" + name);
    feeds[name] = { lastOk: Date.now(), data: d, skew: (feeds.overview || {}).skew || 0 };
    if (active === name) RENDER[name](document.getElementById("tab-" + name), d, feeds[name].skew);
  } catch (e) { /* stale marker */ }
}

function tick() {
  const now = Date.now();
  const ov = feeds.overview;
  document.getElementById("overview-stale").hidden = !!(ov && now - ov.lastOk < STALE_MS);
  if (overview) for (const [name] of COMPONENTS) {
    const c = overview.components[name] || {};
    const t = document.querySelector(`#chip-${name} .chip-age`);
    if (t) t.textContent = "checked " + ago(c.checked_at, ov ? ov.skew : 0);
  }
  if (active) {
    const f = feeds[active];
    const s = document.querySelector(`#tab-${active} .feedstate`);
    if (s) {
      const stale = !f || now - f.lastOk > STALE_MS;
      s.className = "feedstate" + (stale ? " stale" : "");
      s.textContent = !f ? "loading…" : (stale ? "stale: last good poll " : "updated ")
        + Math.round((now - f.lastOk) / 1000) + "s ago";
    }
  }
}

// ---- status strip -------------------------------------------------------------------------------
function buildStrip() {
  const strip = document.getElementById("strip");
  for (const [name, label] of COMPONENTS) {
    strip.append(el("button", { id: "chip-" + name, class: "chip st-unknown", type: "button",
      "aria-controls": "tab-" + name, onclick: () => select(name) },
      el("span", { class: "chip-name", text: label }),
      el("span", { class: "chip-state", text: "…" }),
      el("span", { class: "chip-detail", text: "" }),
      el("span", { class: "chip-age", text: "" })));
  }
}

function renderOverview() {
  const d = overview;
  const mode = document.getElementById("mode");
  mode.textContent = d.dry_run ? "dry-run" : "LIVE";
  mode.className = "badge " + (d.dry_run ? "dry" : "live");
  document.getElementById("kill-badge").hidden = !d.kill_switch;
  for (const [name] of COMPONENTS) {
    const c = d.components[name] || {};
    const chip = document.getElementById("chip-" + name);
    chip.className = "chip st-" + (c.state || "unknown") + (active === name ? " active" : "");
    chip.querySelector(".chip-state").textContent = c.state || "unknown";
    chip.querySelector(".chip-detail").textContent = c.detail || "";
    chip.title = c.detail || "";
  }
  for (const b of document.querySelectorAll(".actions-bar button, form button, button[data-act]")) {
    b.disabled = !d.actions_enabled;
    if (!d.actions_enabled) b.title = "Actions are disabled: PUNTMORE_WEB_TOKEN is not set on the Hub";
  }
  for (const b of document.querySelectorAll("form button[type=button]")) b.disabled = false;   // Cancel always works
  tick();
}

function select(name) {
  active = name;
  for (const [n] of COMPONENTS) {
    document.getElementById("tab-" + n).hidden = n !== name;
    const chip = document.getElementById("chip-" + n);
    chip.classList.toggle("active", n === name);
    chip.setAttribute("aria-current", n === name ? "true" : "false");
  }
  try { localStorage.setItem("puntmoreTab", name); } catch (e) { /* private mode */ }
  try { history.replaceState(null, "", "#" + name); } catch (e) { /* */ }
  if (feeds[name] && feeds[name].data) RENDER[name](document.getElementById("tab-" + name), feeds[name].data, feeds[name].skew);
  pollTab(name);
  tick();
}

// ---- tabs ---------------------------------------------------------------------------------------
const RENDER = {
  scraper(sec, d, skew) {
    fill(sec, "summary", kv([
      ["state", stateTag(d.state)], ["detail", d.detail],
      ["heartbeat", d.heartbeat_age_s === null || d.heartbeat_age_s === undefined ? "no heartbeat file" : d.heartbeat_age_s + "s ago"],
      ["status.json written", when(d.status_at, skew)], ["data dir", d.data_dir],
    ]));
    renderTargets(sec, d, skew);
    fill(sec, "sites", table([
      ["site", "site"], ["loads this hour", (r) => `${show(r.loads_hour)} / ${show(r.per_hour)}`, "n"],
      ["loads today", (r) => `${show(r.loads_day)} / ${show(r.per_day)}`, "n"],
      ["cool-off", "cooloff", "wrap"], ["left (h)", "cooloff_left_h", "n"],
      ["needs resume", (r) => r.needs_resume ? el("span", { class: "st st-down", text: "YES" }) : "no"],
      ["to resume, run on the Hub", (r) => r.needs_resume ? el("code", { class: "cmd", text: show(r.resume_command) }) : "—", "wrap"],
    ], d.sites, "no site budgets in status.json", { cls: "cards" }));
    lines(sec, "alerts", d.alerts, "alerts.log is empty or missing");
    renderBehaviour(sec, d.behaviour);
  },

  daemon(sec, d, skew) {
    const drops = d.inbox_drops || {};
    const li = d.last_inbox || {};
    const m = d.last_mail || {};
    fill(sec, "summary", kv([
      ["state", stateTag(d.state)], ["detail", d.detail],
      ["puntmore-link.service (puntmore daemon)", d.service === null ? "systemctl not available" : d.service],
      ["last inbox pass", li.at ? `${when(li.at, skew)}${li.error ? " — failed: " + li.error : ""}` : "none recorded"],
      ["last inbox counts", li.counts ? Object.entries(li.counts).filter(([, v]) => v !== null).map(([k, v]) => `${k} ${v}`).join(", ") : "—"],
      ["last mail poll", show(m.detail)],
      ["drop files waiting", `tips ${show(drops.tips)}, events ${show(drops.events)}, prices ${show(drops.prices)}`], ["inbox dir", d.inbox_dir],
    ]));
  },

  placement(sec, d, skew) {
    fill(sec, "summary", kv([
      ["state", stateTag(d.state)], ["mode", d.dry_run ? "dry-run" : "LIVE (real money)"],
      ["kill switch", d.kill_switch ? el("strong", { class: "st st-down", text: "ON" }) : "off"],
      ["kill switch file", d.kill_switch_file], ["set", d.kill_switch_note], ["failed in 24h", d.failed_24h],
    ]));
    document.getElementById("kill-off-form").hidden = !d.kill_switch;
    fill(sec, "accounts", table([
      ["account", "account"], ["open exposure", (r) => num(r.open_exposure, 2), "n"], ["latest balance", (r) => num(r.balance, 2), "n"],
    ], d.accounts, "no accounts seen yet"));
    fill(sec, "bets", table([
      ["bet_id", "bet_id", "mono"], ["account", "account"], ["status", "status"], ["stake", (r) => num(r.stake, 2), "n"],
      ["price", (r) => num(r.price, 2), "n"], ["actual", (r) => num(r.actual_price, 2), "n"], ["updated", (r) => when(r.updated, skew)],
    ], d.bets, "no bets yet"));
  },

  escalations(sec, d, skew) {
    fill(sec, "open", table([
      ["#", "id", "n"], ["kind", "kind"], ["ref", "ref", "wrap"], ["created", (r) => when(r.created, skew)],
      ["detail", "detail", "wrap pre"],
      ["", (r) => el("button", { type: "button", class: "small", text: "resolve", onclick: () => {
        document.getElementById("resolve-id").value = r.id;
        document.getElementById("resolve-outcome").focus();
      } })],
    ], d.open, "none open"));
    fill(sec, "resolved", table([
      ["#", "id", "n"], ["kind", "kind"], ["ref", "ref", "wrap"], ["created", (r) => when(r.created, skew)],
      ["resolved", (r) => when(r.resolved, skew)], ["outcome", "outcome", "wrap pre"], ["detail", "detail", "wrap pre"],
    ], d.resolved, "none resolved yet"));
  },

  tips(sec, d, skew) {
    fill(sec, "summary", kv([["state", stateTag(d.state)], ["detail", d.detail], ["last tip", when(d.last_tip, skew)], ["in 24h", d.tips_24h]]));
    fill(sec, "sources", table([
      ["source", "source"], ["settled", "settled", "n"], ["strike rate", (r) => pct(r.strike_rate), "n"], ["ROI", (r) => pct(r.roi), "n"],
    ], d.sources, "no sources yet"));
    fill(sec, "tips", table([
      ["source", "source_id"], ["event", "event", "wrap"], ["market", "market", "wrap"], ["selection", "selection", "wrap"],
      ["advised", (r) => num(r.advised_price, 2), "n"], ["tip time", "tip_time", "mono"], ["outcome", "outcome"],
    ], d.tips, "no tips yet"));
  },

  model(sec, d) {
    const p = d.props || {};
    const h = d.health || {};
    fill(sec, "summary", kv([
      ["state", stateTag(d.state)], ["detail", d.detail], ["url", d.url],
      ["/health", h.status_code ? `HTTP ${h.status_code} ${show(h.body)}` : show(h.error)],
      ["model alias", p.model_alias], ["model file", p.model], ["context (per slot)", p.n_ctx], ["total slots", p.total_slots],
      ["/props", p.unavailable ? "unavailable: " + p.unavailable : "ok"],
    ]));
    if (Array.isArray(d.slots)) {
      fill(sec, "slots", table([["slot", "id", "n"], ["context", "n_ctx", "n"], ["busy", (r) => r.busy ? el("span", { class: "st st-warn", text: "busy" }) : "idle"], ["task", "id_task", "n"]], d.slots, "no slots"));
    } else {
      fill(sec, "slots", el("p", { class: "empty", text: d.slots ? "/slots unavailable: " + show(d.slots.unavailable) : "not reachable" }));
    }
  },

  hermes(sec, d) {
    fill(sec, "summary", kv([
      ["state", stateTag(d.state)], ["detail", d.detail],
      ["hermes-gateway.service", d.service === null ? "systemctl not available" : d.service],
      ["profiles", (d.profiles || []).join(", ") || "none found"],
    ]));
    lines(sec, "journal", d.journal, d.journal_error || "no journal lines");
  },

  render(sec, d, skew) {
    const r = d.ready || {};
    fill(sec, "summary", kv([["state", stateTag(d.state)], ["detail", d.detail], ["url", d.url || "not configured (PUNTMORE_RENDER_HTTP)"]]));
    const box = sec.querySelector('[data-slot="ready"]');
    box.textContent = !d.url ? "not configured" : (r.body !== null && r.body !== undefined
      ? `HTTP ${show(r.status_code)} in ${show(r.ms)} ms\n` + JSON.stringify(r.body, null, 2)
      : `HTTP ${show(r.status_code)}: ${show(r.error)}`);
  },
};

// ---- the Scraper's jobs (targets.toml through scraper/targets.py) ----------------------------------
const EDIT_FIELDS = ["every_min", "stale_min", "near_min", "near_hours", "priority", "window", "days", "until", "min_rows", "trust_weight", "wait_for", "url"];
const NUMERIC = new Set(["every_min", "stale_min", "near_min", "near_hours", "priority", "min_rows", "trust_weight"]);
const LOG_ALL = 200;         // lines "all lines" asks for
let editing = null;          // {name, original: {field: value}}
let removing = null;         // name
// job name -> {lines, error} for a tile whose full log is open ("all lines"; "fewer lines" closes it).
// Kept across re-renders like an open edit form, re-read on every poll of the tab (refreshLog),
// dropped when the job leaves the file.
const fullLogs = new Map();
const openJobs = new Set();  // job names whose row is opened ("details")
const logBusy = new Set();   // job names with a full-log request in flight
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function canAct() { return !!(overview && overview.actions_enabled); }

// A log line as "HH:MM:SS msg", local time, or "Sat HH:MM:SS msg" when it is not from today; a line
// with no time (damaged) is just its message.
function logText(lines) {
  if (!lines || !lines.length) return "no log lines";
  const today = new Date().toDateString();
  return lines.map((l) => {
    const t = new Date(l && l.at);
    if (!l || !l.at || Number.isNaN(t.getTime())) return show(l && l.msg);
    const hms = [t.getHours(), t.getMinutes(), t.getSeconds()].map((x) => String(x).padStart(2, "0")).join(":");
    const day = t.toDateString() === today ? "" : WEEKDAYS[t.getDay()] + " ";
    return `${day}${hms} ${show(l.msg)}`;
  }).join("\n");
}

// One row per job: its name, state and what it is doing now, with when it last read rows and how
// many it has sent. "details" opens the rest (site, url, schedule, log, actions) and stays open across
// re-renders (openJobs); a full log, an edit or a remove of that job keeps it open too.
function jobTile(r, skew, off) {
  const v = r.view || {};
  const tag = v.state ? el("span", { class: "st st-" + (TARGET_STATE[v.state] || "warn"), text: v.state })
    : (r.enabled ? el("span", { class: "st st-unknown", text: "not reported" }) : el("span", { class: "st st-off", text: "disabled" }));
  const full = fullLogs.get(r.name);
  const open = openJobs.has(r.name) || !!full;
  return el("article", { class: "job" + (r.enabled ? "" : " muted") },
    el("header", {},
      el("button", { class: "link", type: "button", text: show(r.name), onclick: () => showLatest(r.name) }), tag,
      el("p", { class: "job-doing", text: v.detail || (r.enabled ? "" : "not running") }),
      el("span", { class: "job-read" }, "last read ", when(v.last_ok, skew), " · rows sent ", show(v.rows_posted)),
      el("button", { type: "button", class: "small", text: "details", "aria-expanded": open ? "true" : "false",
        onclick: () => { if (openJobs.has(r.name)) openJobs.delete(r.name); else openJobs.add(r.name); redrawTargets(); } })),
    el("div", { class: "job-more", hidden: !open },
      el("p", { text: `${show(r.site)} · ${show(r.url)}` }),
      el("p", {}, show(v.schedule), " · last load ", when(v.last_load, skew)),
      el("pre", { class: "log", "data-job": r.name, text: logText(full && full.lines ? full.lines : v.log) }),
      el("span", { class: "row-actions" },
        el("button", { type: "button", class: "small", text: full ? "fewer lines" : "all lines",
          "aria-expanded": full ? "true" : "false", onclick: () => toggleLog(r.name) }),
        el("button", { type: "button", class: "small", disabled: off, text: r.enabled ? "disable" : "enable",
          onclick: () => targetAct(`${encodeURIComponent(r.name)}/${r.enabled ? "disable" : "enable"}`, {},
            `${r.enabled ? "Disable" : "Enable"} ${r.name}`) }),
        el("button", { type: "button", class: "small", disabled: off, text: "edit", onclick: () => openEdit(r) }),
        el("button", { type: "button", class: "small", disabled: off, text: "remove", onclick: () => openRemove(r.name) }))));
}

function renderTargets(sec, d, skew) {
  const c = d.config || {};
  fill(sec, "targets-file", c.file || "targets.toml");
  const rows = c.targets || [];
  let empty = "no jobs in " + (c.file || "targets.toml");
  if (c.error) empty = "targets.toml could not be read: " + c.error;
  else if (c.exists === false) empty = "No targets file at " + (c.file || "targets.toml") + " yet. Add the first job below.";
  const named = new Set(rows.map((r) => r.name));
  for (const name of fullLogs.keys()) if (!named.has(name)) fullLogs.delete(name);
  for (const name of openJobs) if (!named.has(name)) openJobs.delete(name);
  for (const name of fullLogs.keys()) refreshLog(name);
  // Re-rendering replaces each log pre: keep where each one was scrolled to.
  const scrolled = new Map([...sec.querySelectorAll('[data-slot="config"] pre[data-job]')].map((p) => [p.dataset.job, p.scrollTop]));
  fill(sec, "config", rows.length ? el("div", { class: "jobs" }, rows.map((r) => jobTile(r, skew, !canAct())))
    : el("p", { class: "empty", text: empty }));
  for (const p of sec.querySelectorAll('[data-slot="config"] pre[data-job]')) {
    if (scrolled.has(p.dataset.job)) p.scrollTop = scrolled.get(p.dataset.job);
  }
  // A target the Scraper still reports but the file no longer has (removed; "retired" until a restart).
  const orphans = c.error ? [] : (d.targets || []).filter((t) => !named.has(t.name));
  fill(sec, "orphans", orphans.length ? el("p", { class: "hint",
    text: "Reported by the Scraper but not in the file (it drops them within 60 s): " + orphans.map((t) => `${t.name} (${show(t.state)})`).join(", ") }) : "");
}

function redrawTargets() {
  const f = feeds.scraper;
  if (f && f.data) renderTargets(document.getElementById("tab-scraper"), f.data, f.skew);
}

// "all lines" opens the tile's full log (the newest LOG_ALL lines); "fewer lines" closes it back to
// the 5-line tail the tab already has.
function toggleLog(name) {
  if (fullLogs.has(name)) {
    fullLogs.delete(name);
    redrawTargets();
  } else {
    fullLogs.set(name, { lines: null, error: null });
    redrawTargets();               // the button now reads "fewer lines"; renderTargets asks for the lines
  }
}

// Read an open tile's full log again; at most one request per tile at a time (logBusy). A failed read
// leaves the last lines shown and says why in targets-result; a tile that never got its lines closes.
async function refreshLog(name) {
  if (!fullLogs.has(name) || logBusy.has(name)) return;
  logBusy.add(name);
  const out = document.getElementById("targets-result");
  try {
    const d = await getJSON("/api/scraper/log/" + encodeURIComponent(name) + "?n=" + LOG_ALL, 10000);
    const full = fullLogs.get(name);
    if (!full) return;                                       // closed meanwhile
    const first = !full.lines;
    full.lines = d.lines || [];
    for (const p of document.querySelectorAll('#tab-scraper pre[data-job]')) {
      if (p.dataset.job !== name) continue;
      const atEnd = p.scrollTop + p.clientHeight >= p.scrollHeight - 8;
      p.textContent = logText(full.lines);
      if (first || atEnd) p.scrollTop = p.scrollHeight;     // newest last: keep following the tail
    }
    if (full.error && out.textContent === full.error) out.hidden = true;
    full.error = null;
  } catch (e) {
    const full = fullLogs.get(name);
    if (!full) return;
    const msg = `All lines for ${name}: ${e.message}`;
    if (full.error !== msg) {                                // say it once: a repeat must not wipe a write's result
      full.error = msg;
      out.hidden = false;
      out.className = "action-result bad";
      out.textContent = msg;
    }
    if (!full.lines) { fullLogs.delete(name); redrawTargets(); }
  } finally {
    logBusy.delete(name);
  }
}

function clearErrors(form) { for (const e of form.querySelectorAll(".field-error")) e.textContent = ""; }
function showError(form, d, status) {
  const field = d && d.field;
  const box = (field && form.querySelector(`[data-err="${CSS.escape(field)}"]`)) || form.querySelector('[data-err="_"]');
  const msg = (d && (d.message || d.detail)) || ("HTTP " + status);
  if (box) box.textContent = msg + (d && d.retry ? " Try again in a few seconds." : "");
}

// One field's value to send. The days checkboxes come as a group (form.elements.days): the ticked
// ones in week order (the form's order), or null when none is ticked.
function fieldValue(input) {
  if (typeof RadioNodeList !== "undefined" && input instanceof RadioNodeList) {
    const on = [...input].filter((i) => i.checked).map((i) => i.value);
    return on.length ? on : null;
  }
  if (input.type === "checkbox") return input.checked;
  const raw = input.value.trim();
  if (raw === "") return null;
  return NUMERIC.has(input.name) ? Number(raw) : raw;
}

function openEdit(r) {
  const form = document.getElementById("target-edit-form");
  clearErrors(form);
  editing = { name: r.name, original: {} };
  document.getElementById("target-edit-name").textContent = r.name;
  for (const f of EDIT_FIELDS) {
    let v = r[f];
    if (f === "days") {
      v = Array.isArray(v) && v.length ? v : null;          // [] is every day: nothing ticked
      for (const box of form.elements.days) box.checked = !!v && v.includes(box.value);
    } else {
      form.elements[f].value = v === null || v === undefined ? "" : String(v);
    }
    editing.original[f] = v === undefined ? null : v;
  }
  document.getElementById("target-remove-form").hidden = true;
  form.hidden = false;
  form.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function openRemove(name) {
  const form = document.getElementById("target-remove-form");
  clearErrors(form);
  removing = name;
  document.getElementById("target-remove-name").textContent = name;
  form.elements.confirm.value = "";
  form.elements.confirm.placeholder = name;
  document.getElementById("target-edit-form").hidden = true;
  form.hidden = false;
  form.elements.confirm.focus();
}

// One write: POST, show the outcome above the table, re-read the tab at once.
async function targetAct(path, body, label, form) {
  const out = document.getElementById("targets-result");
  out.hidden = false;
  out.className = "action-result";
  out.textContent = label + "…";
  if (form) clearErrors(form);
  const res = await post("scraper/targets/" + path, body);
  const msg = (res.d && (res.d.message || res.d.detail)) || res.error || ("HTTP " + res.status);
  out.className = "action-result " + (res.ok ? "ok" : "bad");
  out.textContent = res.ok ? `${label}: ${msg}`
    : `${label}: refused (HTTP ${res.status}): ${msg}${res.d && res.d.retry ? " Try again in a few seconds." : ""}`;
  if (!res.ok && form) showError(form, res.d, res.status);
  pollOverview();
  pollTab("scraper");
  return res.ok;
}

function wireTargets() {
  const add = document.getElementById("target-add-form");
  add.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = {};
    for (const input of add.querySelectorAll("input, select")) {
      if (input.name === "days") continue;
      const v = fieldValue(input);
      if (v !== null && v !== "") body[input.name] = v;
    }
    body.days = fieldValue(add.elements.days);
    if (await targetAct("add", body, "Add " + (body.name || "?"), add)) {
      add.reset();
      showAdd(false);
    }
  });
  // The add form is closed until asked for.
  const addOpen = document.getElementById("target-add-open");
  const showAdd = (on) => {
    add.hidden = !on;
    addOpen.setAttribute("aria-expanded", on ? "true" : "false");
    if (on) { add.scrollIntoView({ behavior: "smooth", block: "nearest" }); add.elements.name.focus(); }
  };
  addOpen.addEventListener("click", () => showAdd(add.hidden));
  document.getElementById("target-add-cancel").addEventListener("click", () => { clearErrors(add); showAdd(false); });
  const edit = document.getElementById("target-edit-form");
  edit.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!editing) return;
    const body = {};
    for (const f of EDIT_FIELDS) {
      const now = fieldValue(edit.elements[f]);
      const was = editing.original[f];
      if (now !== was && !(now !== null && was !== null && String(now) === String(was))) body[f] = now;
    }
    if (!Object.keys(body).length) { edit.querySelector('[data-err="_"]').textContent = "nothing changed"; return; }
    if (await targetAct(encodeURIComponent(editing.name) + "/set", body, "Edit " + editing.name, edit)) {
      edit.hidden = true; editing = null;
    }
  });
  document.getElementById("target-edit-cancel").addEventListener("click", () => { edit.hidden = true; editing = null; });
  const rm = document.getElementById("target-remove-form");
  rm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!removing) return;
    const typed = rm.elements.confirm.value.trim();
    if (typed !== removing) {
      rm.querySelector('[data-err="confirm"]').textContent = `type ${removing} exactly to remove it`;
      rm.elements.confirm.focus();
      return;
    }
    if (await targetAct(encodeURIComponent(removing) + "/remove", { confirm: typed }, "Remove " + removing, rm)) {
      rm.hidden = true; removing = null;
    }
  });
  document.getElementById("target-remove-cancel").addEventListener("click", () => { rm.hidden = true; removing = null; });
}

// ---- the Scraper's behaviour (scraper/behaviour.py through the console) ---------------------------
// One table per group, built once and then updated in place on each poll of the tab, so an open pane
// and what a person is typing in it survive the refresh: a row someone has typed in (dirty) keeps its
// boxes until it is saved or reset. Every check of a value is the library's; the page sends the text.
const beh = { sig: "", rows: new Map() };    // key -> {tr, kind, inputs, dirty, tag, save, reset, err}
const behShow = (v) => Array.isArray(v) ? v.map(String).join("–")
  : (typeof v === "boolean" ? (v ? "on" : "off") : show(v));

function behSetInputs(row, v) {
  if (row.kind === "bool") row.inputs[0].checked = !!v;
  else if (row.inputs.length === 2) { row.inputs[0].value = String(v[0]); row.inputs[1].value = String(v[1]); }
  else row.inputs[0].value = String(v);
}

function behValue(row) {
  if (row.kind === "bool") return row.inputs[0].checked;
  if (row.inputs.length === 2) return row.inputs.map((i) => i.value.trim());
  return row.inputs[0].value.trim();
}

function behaviourRow(s) {
  const row = { kind: s.kind, dirty: false, inputs: [] };
  const typed = () => { row.dirty = true; row.err.textContent = ""; };
  const box = (label) => {
    const i = el("input", s.kind === "bool" ? { type: "checkbox", "aria-label": label, onchange: typed }
      : { type: "text", inputmode: "decimal", autocomplete: "off", spellcheck: "false", "aria-label": label, oninput: typed,
        onkeydown: (ev) => { if (ev.key === "Enter") { ev.preventDefault(); behSave(row, s.key); } } });
    row.inputs.push(i);
    return i;
  };
  const pair = s.kind === "range" || s.kind === "hours";
  const value = pair ? el("span", { class: "pair" }, box(s.key + " low"), el("span", { text: "–", "aria-hidden": "true" }), box(s.key + " high"))
    : box(s.key);
  row.tag = el("span", { class: "st st-warn", text: "changed", hidden: true });
  row.save = el("button", { type: "button", class: "small", text: "save", "data-act": true, onclick: () => behSave(row, s.key) });
  row.reset = el("button", { type: "button", class: "small", text: "reset", "data-act": true, hidden: true,
    onclick: () => behaviourAct("reset", { key: s.key }, "Reset " + s.key, row) });
  row.err = el("span", { class: "field-error", role: "alert" });
  row.def = el("td", { "data-label": "default", class: "n" });
  row.tr = el("tr", {},
    el("td", { "data-label": "", class: "setting" }, el("span", { class: "says", text: s.says }), " ", row.tag,
      el("small", { class: "key", text: s.key })),
    el("td", { "data-label": "value", class: "value" }, value),
    el("td", { "data-label": "unit", class: "unit", text: s.unit || "" }),
    row.def,
    el("td", { "data-label": "", class: "acts" }, row.save, row.reset, row.err));
  behSetInputs(row, s.value);
  return row;
}

function behUpdate(row, s) {
  row.tr.classList.toggle("changed", !!s.changed);
  row.tag.hidden = !s.changed;
  row.reset.hidden = !s.changed;
  row.def.textContent = behShow(s.default);
  for (const b of [row.save, row.reset]) b.disabled = !canAct();
  if (!row.dirty && !row.inputs.includes(document.activeElement)) behSetInputs(row, s.value);
}

function renderBehaviour(sec, b) {
  if (!b) return;
  const n = b.changed || 0;
  const probs = b.problems || [];
  fill(sec, "behaviour-count", b.error ? "could not read the settings: " + b.error
    : (n ? `${n} changed from default` : "all defaults") + (probs.length ? ` · ${probs.length} problem${probs.length > 1 ? "s" : ""} in behaviour.toml` : ""));
  fill(sec, "behaviour-file", b.file || "behaviour.toml");
  fill(sec, "behaviour-problems", probs.length
    ? el("ul", { class: "problems" }, probs.map((p) => el("li", { text: p }))) : "");
  const settings = b.settings || [];
  const sig = settings.map((s) => s.key + ":" + s.kind).join("|");
  if (sig !== beh.sig) {                 // the catalogue itself changed (or first time): build the tables
    beh.sig = sig;
    beh.rows.clear();
    const groups = [];
    for (const s of settings) {
      if (!groups.length || groups[groups.length - 1].name !== s.group) groups.push({ name: s.group, rows: [] });
      const row = behaviourRow(s);
      beh.rows.set(s.key, row);
      groups[groups.length - 1].rows.push(row.tr);
    }
    document.getElementById("behaviour-groups").replaceChildren(...groups.map((g) => el("section", { class: "beh-group" },
      el("h4", { text: g.name }),
      el("table", { class: "cards beh" },
        el("thead", {}, el("tr", {}, ["setting", "value", "unit", "default", ""].map((h) => el("th", { text: h })))),
        el("tbody", {}, g.rows)))));
  }
  for (const s of settings) behUpdate(beh.rows.get(s.key), s);
}

function behSave(row, key) {
  behaviourAct("set", { key, value: behValue(row) }, "Set " + key, row);
}

// One write: POST, the outcome above the tables (and a refusal beside its row), re-read the tab.
async function behaviourAct(path, body, label, row, form) {
  const out = document.getElementById("behaviour-result");
  out.hidden = false;
  out.className = "action-result";
  out.textContent = label + "…";
  if (row) row.err.textContent = "";
  if (form) clearErrors(form);
  const res = await post("scraper/behaviour/" + path, body);
  const msg = (res.d && (res.d.message || res.d.detail)) || res.error || ("HTTP " + res.status);
  const again = res.d && res.d.retry ? " Try again in a few seconds." : "";
  out.className = "action-result " + (res.ok ? "ok" : "bad");
  out.textContent = res.ok ? `${label}: ${msg}` : `${label}: refused (HTTP ${res.status}): ${msg}${again}`;
  if (res.ok) {
    if (row) row.dirty = false;
    else for (const r of beh.rows.values()) r.dirty = false;            // reset all: every row shows the file
    const s = res.d && res.d.setting;
    if (row && s) { behSetInputs(row, s.value); behUpdate(row, s); }
  } else if (row) {
    row.err.textContent = msg + again;
  } else if (form) {
    showError(form, res.d, res.status);
  }
  pollOverview();
  pollTab("scraper");
  return res.ok;
}

function wireBehaviour() {
  const pane = document.getElementById("behaviour");
  const open = document.getElementById("behaviour-open");
  open.addEventListener("click", () => {
    pane.hidden = !pane.hidden;
    open.setAttribute("aria-expanded", pane.hidden ? "false" : "true");
    if (!pane.hidden) pane.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  const form = document.getElementById("behaviour-reset-form");
  const showForm = (on) => {
    form.hidden = !on;
    clearErrors(form);
    form.elements.confirm.value = "";
    if (on) form.elements.confirm.focus();
  };
  document.getElementById("behaviour-reset-all").addEventListener("click", () => showForm(form.hidden));
  document.getElementById("behaviour-reset-cancel").addEventListener("click", () => showForm(false));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const typed = form.elements.confirm.value.trim();
    if (typed !== "RESET") {
      form.querySelector('[data-err="confirm"]').textContent = "type RESET exactly to put every setting back";
      form.elements.confirm.focus();
      return;
    }
    if (await behaviourAct("reset", { all: true, confirm: typed }, "Reset all", null, form)) showForm(false);
  });
}

async function showLatest(target) {
  const box = document.getElementById("latest");
  box.hidden = false;
  document.getElementById("latest-title").textContent = "latest/" + target + ".json";
  const meta = document.getElementById("latest-meta");
  const rowsBox = document.getElementById("latest-rows");
  meta.textContent = "loading…";
  rowsBox.replaceChildren();
  try {
    const d = await getJSON("/api/scraper/latest/" + encodeURIComponent(target));
    meta.textContent = `${d.count} row(s), parsed ${d.at || "?"}${d.truncated ? " (first 500 shown)" : ""} from ${d.url || "?"}`;
    const keys = [];
    for (const r of d.rows) if (r && typeof r === "object") for (const k of Object.keys(r)) if (!keys.includes(k) && keys.length < 30) keys.push(k);
    rowsBox.replaceChildren(table(keys.map((k) => [k, (r) => show(r && r[k]), "wrap"]), d.rows, "no rows"));
  } catch (e) {
    meta.textContent = "no latest rows for this target (" + e.message + ")";
  }
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---- actions ------------------------------------------------------------------------------------
function token() { return document.getElementById("token").value.trim(); }

// POST an action with the token. -> {ok, status, d (the JSON answer), error (network)}
async function post(path, body) {
  try {
    const r = await fetch("/api/actions/" + path, {
      method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Puntmore-Token": token() },
      body: JSON.stringify(body || {}),
    });
    let d = {};
    try { d = await r.json(); } catch (e) { /* not JSON */ }
    return { ok: r.ok && d.ok !== false, status: r.status, d };
  } catch (e) {
    return { ok: false, status: 0, d: {}, error: e.message };
  }
}

async function act(path, body, label) {
  const out = document.getElementById("action-result");
  out.hidden = false;
  out.className = "action-result";
  out.textContent = label + "…";
  const res = await post(path, body);
  if (res.error) {
    out.className = "action-result bad";
    out.textContent = `${label}: ${res.error}`;
    return false;
  }
  const msg = res.d.message || res.d.detail || ("HTTP " + res.status);
  out.className = "action-result " + (res.ok ? "ok" : "bad");
  out.textContent = `${label}: ${res.ok ? "" : "refused/failed: "}${msg}`;
  pollOverview();
  if (active) pollTab(active);
  return res.ok;
}

function wireActions() {
  const tok = document.getElementById("token");
  try { tok.value = localStorage.getItem("puntmoreToken") || ""; } catch (e) { /* private mode */ }
  tok.addEventListener("change", () => { try { localStorage.setItem("puntmoreToken", tok.value.trim()); } catch (e) { /* */ } });
  document.getElementById("btn-kill-on").addEventListener("click", () => act("kill-on", {}, "Kill switch ON"));
  document.getElementById("btn-inbox").addEventListener("click", () => act("inbox-now", {}, "Inbox now"));
  document.getElementById("btn-heartbeat").addEventListener("click", () => act("heartbeat-now", {}, "Heartbeat now"));
  document.getElementById("kill-off-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const c = document.getElementById("kill-off-confirm");
    if (c.value !== "OFF") { c.focus(); return; }
    if (await act("kill-off", { confirm: c.value }, "Kill switch off")) c.value = "";
  });
  document.getElementById("resolve-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const id = document.getElementById("resolve-id");
    const oc = document.getElementById("resolve-outcome");
    if (!id.value || !oc.value.trim()) return;
    if (await act("escalation-resolve", { id: Number(id.value), outcome: oc.value.trim() }, "Resolve #" + id.value)) {
      id.value = ""; oc.value = "";
    }
  });
}

// ---- start --------------------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  buildStrip();
  wireActions();
  wireTargets();
  wireBehaviour();
  let first = "scraper";
  try { first = localStorage.getItem("puntmoreTab") || first; } catch (e) { /* */ }
  if (location.hash.length > 1) first = location.hash.slice(1);     // #escalations etc., for a bookmark
  if (!COMPONENTS.some(([n]) => n === first)) first = "scraper";
  select(first);
  pollOverview();
  setInterval(pollOverview, POLL_MS);
  setInterval(() => { if (active) pollTab(active); }, POLL_MS);
  setInterval(tick, 1000);
});
