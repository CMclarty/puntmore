// Hub Console. Plain JS, no build, no CDN.
// Everything shown here that came from the Hub's data (scraped rows, tips, escalation details, log
// lines) is untrusted: it is only ever put on the page with textContent, never innerHTML.
"use strict";

const COMPONENTS = [
  ["scraper", "Scraper"], ["link", "Link"], ["placement", "Placement"], ["escalations", "Escalations"],
  ["tips", "Tips"], ["model", "Model"], ["hermes", "Hermes"], ["render", "Render"],
];
const POLL_MS = 5000;
const STALE_MS = 20000;
// A Scraper target's own state (scraper/runner.py `status`) -> the colour it gets.
const TARGET_STATE = { running: "up", paused: "warn", blocked: "down", retired: "off" };

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

// cols: [label, key | (row) => Node|string, className?]
function table(cols, rows, empty) {
  if (!rows || !rows.length) return el("p", { class: "empty", text: empty || "nothing yet" });
  const head = el("tr", {}, cols.map(([label]) => el("th", { text: label })));
  const body = rows.map((r) => el("tr", {}, cols.map(([, key, cls]) => {
    const v = typeof key === "function" ? key(r) : show(r[key]);
    return el("td", { class: cls }, v instanceof Node ? v : String(v));
  })));
  return el("table", {}, el("thead", {}, head), el("tbody", {}, body));
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
async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store", headers: { Accept: "application/json" } });
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
  for (const b of document.querySelectorAll(".actions-bar button, form button")) {
    b.disabled = !d.actions_enabled;
    if (!d.actions_enabled) b.title = "Actions are disabled: PUNTMORE_WEB_TOKEN is not set on the Hub";
  }
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
    fill(sec, "targets", table([
      ["name", (r) => el("button", { class: "link", type: "button", text: show(r.name), onclick: () => showLatest(r.name) })],
      ["site", "site"], ["mode", "mode"], ["state", (r) => el("span", { class: "st st-" + (TARGET_STATE[r.state] || "warn"), text: show(r.state) })],
      ["last ok", (r) => when(r.last_ok, skew)], ["last load", (r) => when(r.last_load, skew)],
      ["rows last parsed", "rows_last_parsed", "n"], ["rows posted", "rows_posted", "n"], ["detail", "detail", "wrap"],
    ], d.targets, "no targets in status.json"));
    fill(sec, "sites", table([
      ["site", "site"], ["loads/h", (r) => `${show(r.loads_hour)} / ${show(r.per_hour)}`, "n"],
      ["loads/day", (r) => `${show(r.loads_day)} / ${show(r.per_day)}`, "n"],
      ["cool-off", "cooloff", "wrap"], ["left (h)", "cooloff_left_h", "n"], ["needs resume", (r) => r.needs_resume ? "YES" : "no"],
    ], d.sites, "no site budgets in status.json"));
    lines(sec, "alerts", d.alerts, "alerts.log is empty or missing");
  },

  link(sec, d, skew) {
    const drops = d.inbox_drops || {};
    const ob = d.outbox || {};
    fill(sec, "summary", kv([
      ["state", stateTag(d.state)], ["detail", d.detail],
      ["puntmore-link.service", d.service === null ? "systemctl not available" : d.service],
      ["configured", d.configured ? "yes" : "no (PUNTMORE_RENDER_WS / PUNTMORE_RENDER_API_KEY)"],
      ["outbox pending (not yet sent)", ob.pending], ["outbox unacked by Render", ob.unacked],
      ["oldest unacked", d.oldest_unacked_age_s === null ? "—" : d.oldest_unacked_age_s + "s"],
      ["inbox drops waiting", `tips ${show(drops.tips)}, events ${show(drops.events)}`], ["inbox dir", d.inbox_dir],
    ]));
    fill(sec, "outbox", table([
      ["#", "id", "n"], ["type", "type"], ["created", (r) => when(r.created, skew)],
      ["sent", (r) => when(r.sent_at, skew)], ["acked", (r) => r.acked_at ? when(r.acked_at, skew) : el("span", { class: "st st-warn", text: "not yet" })],
      ["msg_id", "msg_id", "mono"],
    ], d.outbox_rows, "outbox is empty"));
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
    const lc = d.last_link_check;
    sec.querySelector('[data-slot="linkcheck"]').textContent = lc
      ? `${lc.ok ? "OK" : "FAIL"} ${show(lc.message)}\n${show(lc.url)}\n${show(lc.at)} (${ago(lc.at, skew)})`
      : "no link-check run from the console yet";
  },
};

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

async function act(path, body, label) {
  const out = document.getElementById("action-result");
  out.hidden = false;
  out.className = "action-result";
  out.textContent = label + "…";
  try {
    const r = await fetch("/api/actions/" + path, {
      method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Puntmore-Token": token() },
      body: JSON.stringify(body || {}),
    });
    let d = {};
    try { d = await r.json(); } catch (e) { /* not JSON */ }
    const msg = d.message || d.detail || ("HTTP " + r.status);
    out.className = "action-result " + (r.ok && d.ok !== false ? "ok" : "bad");
    out.textContent = `${label}: ${r.ok && d.ok !== false ? "" : "refused/failed: "}${msg}`;
    pollOverview();
    if (active) pollTab(active);
    return r.ok && d.ok !== false;
  } catch (e) {
    out.className = "action-result bad";
    out.textContent = `${label}: ${e.message}`;
    return false;
  }
}

function wireActions() {
  const tok = document.getElementById("token");
  try { tok.value = localStorage.getItem("puntmoreToken") || ""; } catch (e) { /* private mode */ }
  tok.addEventListener("change", () => { try { localStorage.setItem("puntmoreToken", tok.value.trim()); } catch (e) { /* */ } });
  document.getElementById("btn-kill-on").addEventListener("click", () => act("kill-on", {}, "Kill switch ON"));
  document.getElementById("btn-inbox").addEventListener("click", () => act("inbox-now", {}, "Inbox now"));
  document.getElementById("btn-heartbeat").addEventListener("click", () => act("heartbeat-now", {}, "Heartbeat now"));
  document.getElementById("btn-link-check").addEventListener("click", () => act("link-check", {}, "Link-check"));
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
