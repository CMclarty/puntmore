"""``puntmore`` command line. Every operation an agent or a human needs, with plain text output."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import timedelta
from pathlib import Path

from . import __version__, alerts, escalation
from .config import settings
from .store import Store, parse_iso, utcnow


def _store() -> Store:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return Store(settings.db_path)


def _print_rows(rows, cols):
    widths = [max(len(c), *(len(str(r[c] if r[c] is not None else "")) for r in rows)) if rows else len(c) for c in cols]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  ".join(str(r[c] if r[c] is not None else "").ljust(w) for c, w in zip(cols, widths)))


# ---- commands ---------------------------------------------------------------------------------

def cmd_init(a):
    for d in (settings.data_dir, settings.raw_dir, settings.chrome_profile_dir, settings.data_dir / "escalations"):
        d.mkdir(parents=True, exist_ok=True)
    _store().close()
    print(f"initialised {settings.data_dir} (db {settings.db_path})")
    print(f"dry_run={settings.dry_run} kill_switch={settings.kill_switch_on()} link_configured={settings.link_configured}")


def cmd_status(a):
    st = _store()
    s = st.summary_since(utcnow() - timedelta(hours=24))
    print(f"puntmore-home {__version__}  client={settings.client_id}  dry_run={settings.dry_run}  "
          f"kill_switch={'ON' if settings.kill_switch_on() else 'off'}  link={'configured' if settings.link_configured else 'OFFLINE'}")
    print("last 24h: " + ", ".join(f"{k}={v}" for k, v in s.items()))
    from .scraper.budget import RequestBudget
    b = RequestBudget(st, settings.pages_per_hour, settings.pages_per_day)
    print(f"request budget remaining: {b.remaining()[0]}/h, {b.remaining()[1]}/day")
    states = st.all_source_states()
    if states:
        print("sources:")
        _print_rows(states, ["source", "healthy", "last_ok", "last_run", "cooloff_until", "note"])


def cmd_sources(a):
    from .scraper.sources import load_sources
    rows = []
    st = _store()
    for s in load_sources(settings.sources_dir).values():
        state = st.source_state(s.name)
        rows.append({"name": s.name, "enabled": s.enabled, "kind": s.kind, "parser": s.parser,
                     "schedule": s.schedule.every or ",".join(s.schedule.at) or "manual",
                     "healthy": state["healthy"], "last_ok": state["last_ok"]})
    _print_rows(rows, ["name", "enabled", "kind", "parser", "schedule", "healthy", "last_ok"])


def cmd_scrape(a):
    from .scraper.runner import run_due, run_source
    from .scraper.sources import load_sources
    st = _store()
    if a.which == "due":
        results = run_due(st, notify=not a.no_notify)
        if not results:
            print("nothing due")
    else:
        srcs = load_sources(settings.sources_dir)
        if a.name not in srcs:
            sys.exit(f"unknown source {a.name!r}; known: {', '.join(srcs) or 'none'}")
        results = [run_source(srcs[a.name], st, force=a.force, notify=not a.no_notify)]
    for r in results:
        print(f"{r.source}: {r.status} items={r.items} new={r.new_tips}" + (f" error={r.error}" if r.error else "")
              + (f" raw={r.raw_path}" if r.raw_path else ""))
    if any(r.status not in ("ok", "skipped") for r in results):
        sys.exit(1)


def cmd_runs(a):
    rows = [dict(r) for r in _store().recent_runs(a.limit)]
    _print_rows(rows, ["id", "source", "started", "status", "items", "error"])


def cmd_raw(a):
    d = settings.raw_dir / a.source
    files = sorted(d.glob("*.html")) if d.exists() else []
    if not files:
        print(f"no raw payloads for {a.source}")
        return
    for f in files[-a.limit:]:
        print(f"{f}  {f.stat().st_size} bytes")


def cmd_parser_test(a):
    """Run a parser against a saved page (or a fixture) and print what it would emit."""
    from .scraper.browser import Fetched
    from .scraper.parsers import load_parser
    from .scraper.runner import validate
    from .scraper.sources import Source, load_sources
    html = Path(a.file).read_text()
    srcs = load_sources(settings.sources_dir)
    source = srcs.get(a.source) if a.source else next((s for s in srcs.values() if s.parser == a.parser), None)
    if source is None:
        source = Source(name=a.parser, parser=a.parser)
    fetched = Fetched(url=f"file://{a.file}", status=200, html=html)
    items = load_parser(a.parser, settings.parsers_dir)(fetched, source)
    good, problems = validate(items, source)
    print(f"{len(items)} parsed, {len(good)} valid, {len(problems)} rejected (min_items {source.health.min_items})")
    for it in good[: a.limit]:
        print(json.dumps(it, default=str))
    for p in problems[:5]:
        print("REJECTED:", p)
    if len(good) < source.health.min_items:
        sys.exit(1)


def cmd_health(a):
    st = _store()
    bad = [s for s in st.all_source_states() if not s.get("healthy", 1)]
    open_esc = st.open_escalations()
    stale_h = a.stale_hours
    stale = []
    for s in st.all_source_states():
        ok = parse_iso(s.get("last_ok"))
        if ok and utcnow() - ok > timedelta(hours=stale_h):
            stale.append(s["source"])
    print(f"unhealthy sources: {', '.join(s['source'] for s in bad) or 'none'}")
    print(f"stale sources (> {stale_h}h since last ok): {', '.join(stale) or 'none'}")
    print(f"open escalations: {len(open_esc)}")
    for e in open_esc:
        print(f"  #{e['id']} {e['kind']} {e['ref']} ({e['created']}): {e['detail'][:160]}")
    if settings.kill_switch_on():
        print("KILL SWITCH IS ON")
    if bad or open_esc or stale:
        sys.exit(1)


def cmd_kill(a):
    f = settings.kill_switch_file
    if a.action == "on":
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"set {utcnow().isoformat()}\n")
        escalation.raise_escalation(_store(), "kill_switch", "global", "kill switch turned ON from the CLI", notify=False)
        print("kill switch ON: no scrapes, no placements until `puntmore kill off`")
    elif a.action == "off":
        f.unlink(missing_ok=True)
        print("kill switch off")
    else:
        print("ON" if f.exists() else "off")


def cmd_escalation(a):
    st = _store()
    if a.which == "list":
        rows = [dict(r) for r in st.open_escalations()]
        _print_rows(rows, ["id", "kind", "ref", "created", "detail"])
    elif a.which == "show":
        print(escalation.brief_for(st, a.id))
    elif a.which == "resolve":
        escalation.resolve(st, a.id, a.outcome)
        print(f"escalation #{a.id} resolved; outcome queued for Render")


def cmd_tips(a):
    rows = [dict(r) for r in _store().conn.execute(
        "SELECT tip_id, source_id, event, market, selection, advised_price, tip_time, outcome FROM tips ORDER BY first_seen DESC LIMIT ?",
        (a.limit,)).fetchall()]
    _print_rows(rows, ["tip_id", "source_id", "event", "market", "selection", "advised_price", "tip_time", "outcome"])


def cmd_heartbeat(a):
    print(alerts.heartbeat(_store()))


def cmd_link(a):
    """Run the Render link daemon: bet lists -> client engine, scrape jobs -> runner."""
    from .link.client import RenderLink, bets_from, job_from
    from .placement.engine import ClientEngine
    from .scraper.runner import run_source
    from .scraper.sources import load_sources

    st = _store()
    engine = ClientEngine(st)
    link = RenderLink(st)

    def on_bet_list(env):
        statuses = engine.handle_bet_list(bets_from(env))
        logging.getLogger("puntmore.link").info("bet_list %s -> %s", env.msg_id, statuses)

    def on_scrape_job(env):
        job = job_from(env)
        srcs = load_sources(settings.sources_dir)
        if job.source not in srcs:
            link.enqueue("scrape_result", {"job_id": job.job_id, "status": "error", "error": f"unknown source {job.source}"})
            return
        r = run_source(srcs[job.source], st, force=bool(job.params.get("force")))
        link.enqueue("scrape_result", {"job_id": job.job_id, "status": r.status, "items": r.items, "error": r.error})

    def on_kill(env):
        on = bool(env.body.get("on", True))
        if on:
            settings.kill_switch_file.write_text("set by Render\n")
        else:
            settings.kill_switch_file.unlink(missing_ok=True)
        escalation.raise_escalation(st, "kill_switch", "render", f"kill switch set {'ON' if on else 'off'} by Render")

    link.handlers.update({"bet_list": on_bet_list, "scrape_job": on_scrape_job, "kill_switch": on_kill})
    alerts.startup(st, "link")
    try:
        asyncio.run(link.run())
    except KeyboardInterrupt:
        pass


def cmd_link_check(a):
    """Handshake with a Render build and report. Exit 1 on failure."""
    from .link.client import check
    url = a.url or settings.render_ws_url
    key = a.key or settings.render_api_key
    if not url or not key:
        sys.exit("need a URL and key: pass them or set PUNTMORE_RENDER_WS / PUNTMORE_RENDER_API_KEY")
    ok, detail = asyncio.run(check(url, key, settings.client_id))
    print(("OK " if ok else "FAIL ") + detail)
    if not ok:
        sys.exit(1)


# ---- parser -----------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="puntmore", description="Puntmore home box")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create data dirs and the database").set_defaults(fn=cmd_init)
    sub.add_parser("status", help="one-screen status").set_defaults(fn=cmd_status)
    sub.add_parser("sources", help="list configured sources").set_defaults(fn=cmd_sources)

    sc = sub.add_parser("scrape", help="run scrapers").add_subparsers(dest="which", required=True)
    r = sc.add_parser("run", help="run one source now"); r.add_argument("name"); r.add_argument("--force", action="store_true")
    r.add_argument("--no-notify", action="store_true", help="record escalations without waking the local AI"); r.set_defaults(fn=cmd_scrape)
    d = sc.add_parser("due", help="run every source that is due (timer entry point)"); d.add_argument("--no-notify", action="store_true"); d.set_defaults(fn=cmd_scrape)

    rr = sub.add_parser("runs", help="recent scraper runs"); rr.add_argument("--limit", type=int, default=30); rr.set_defaults(fn=cmd_runs)
    rw = sub.add_parser("raw", help="list archived raw payloads for a source"); rw.add_argument("source"); rw.add_argument("--limit", type=int, default=10); rw.set_defaults(fn=cmd_raw)

    pt = sub.add_parser("parser-test", help="run a parser on a saved page or fixture")
    pt.add_argument("parser"); pt.add_argument("file"); pt.add_argument("--source", help="source name whose health rules to apply")
    pt.add_argument("--limit", type=int, default=20); pt.set_defaults(fn=cmd_parser_test)

    h = sub.add_parser("health", help="exit 1 if anything needs attention"); h.add_argument("--stale-hours", type=float, default=24); h.set_defaults(fn=cmd_health)
    k = sub.add_parser("kill", help="kill switch"); k.add_argument("action", choices=["on", "off", "status"]); k.set_defaults(fn=cmd_kill)

    es = sub.add_parser("escalation", help="escalations to the local AI").add_subparsers(dest="which", required=True)
    es.add_parser("list").set_defaults(fn=cmd_escalation)
    sh = es.add_parser("show"); sh.add_argument("id", type=int); sh.set_defaults(fn=cmd_escalation)
    rs = es.add_parser("resolve"); rs.add_argument("id", type=int); rs.add_argument("--outcome", required=True); rs.set_defaults(fn=cmd_escalation)

    t = sub.add_parser("tips", help="recent candidate tips"); t.add_argument("--limit", type=int, default=30); t.set_defaults(fn=cmd_tips)
    sub.add_parser("heartbeat", help="send the daily summary").set_defaults(fn=cmd_heartbeat)
    sub.add_parser("link", help="run the Render link daemon").set_defaults(fn=cmd_link)
    lc = sub.add_parser("link-check", help="handshake with a Render build (hello -> hello_ack)")
    lc.add_argument("url", nargs="?"); lc.add_argument("--key"); lc.set_defaults(fn=cmd_link_check)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.fn(args)


if __name__ == "__main__":
    main()
