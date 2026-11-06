"""``puntmore`` command line. Every operation an agent or a human needs, with plain text output.

Scraping is the Scraper's (`python -m scraper ...` in `scraper/`); this CLI reads what the
Scraper drops and reports, polls the tipster mailbox, and owns the link to Render, placement,
and escalations.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
from datetime import timedelta

from . import __version__, alerts, escalation, inbox, killswitch, mail
from .config import settings
from .store import Store, utcnow


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
    for d in (settings.data_dir, settings.data_dir / "escalations"):
        d.mkdir(parents=True, exist_ok=True)
    _store().close()
    print(f"initialised {settings.data_dir} (db {settings.db_path})")
    print(f"dry_run={settings.dry_run} kill_switch={settings.kill_switch_on()} link_configured={settings.link_configured}")
    print(f"scraper data: {settings.scraper_data_dir} ({'present' if settings.scraper_data_dir.exists() else 'not found'})")


def cmd_status(a):
    st = _store()
    s = st.summary_since(utcnow() - timedelta(hours=24))
    print(f"puntmore-hub {__version__}  client={settings.client_id}  dry_run={settings.dry_run}  "
          f"kill_switch={'ON' if settings.kill_switch_on() else 'off'}  link={'configured' if settings.link_configured else 'OFFLINE'}")
    print("last 24h: " + ", ".join(f"{k}={v}" for k, v in s.items()))
    w = inbox.scraper_status(settings)
    age = w["heartbeat_age_s"]
    print(f"scraper: {'running' if w['running'] else 'NOT RUNNING'}"
          + (f" (heartbeat {age}s ago)" if age is not None else " (no heartbeat file)")
          + f", {len(w['targets'])} target(s), blocked={w['blocked'] or 'none'}, unhealthy={w['unhealthy'] or 'none'}")
    if w["targets"]:
        _print_rows(w["targets"], ["name", "site", "mode", "state", "detail", "rows_posted"])
    print(f"mail: {mail.status(settings)['detail']}")


def cmd_inbox(a):
    counts = inbox.ingest(_store(), settings, notify=not a.no_notify)
    print(", ".join(f"{k}={v}" for k, v in counts.items()))


def cmd_mail(a):
    """One poll of the tipster mailbox. --dry-run shows what the parsers read and changes nothing."""
    r = mail.poll(_store(), settings, dry_run=a.dry_run, notify=not a.no_notify)
    if not r["configured"]:
        print(r["error"])
        return
    if a.dry_run:
        for row in r["rows"]:
            print(f"  {row['source_id']}  {row['tip_id']}  {row['event']} | {row['market']} | {row['selection']}"
                  f" | {row['advised_price'] if row['advised_price'] is not None else '-'}  ({row['tip_time']})")
        for p in r["problems"]:
            print(f"  ! {p}")
    print(("dry-run: " if a.dry_run else "") + ", ".join(f"{k}={r[k]}" for k in mail.COUNT_KEYS))
    if r["error"]:
        sys.exit(f"error: {r['error']}")


def cmd_health(a):
    st = _store()
    w = inbox.scraper_status(settings)
    open_esc = st.open_escalations()
    bad = []
    if not w["running"]:
        bad.append("scraper not running (stale or missing heartbeat)")
    if w["blocked"]:
        bad.append("blocked: " + ", ".join(w["blocked"]))
    if w["unhealthy"]:
        bad.append("unhealthy sources: " + ", ".join(w["unhealthy"]))
    print(f"scraper: {'running' if w['running'] else 'NOT RUNNING'}; blocked: {', '.join(w['blocked']) or 'none'}; "
          f"unhealthy: {', '.join(w['unhealthy']) or 'none'}")
    m = mail.status(settings)
    bad.extend(m["problems"])
    print(f"mail: {m['detail']}")
    print(f"open escalations: {len(open_esc)}")
    for e in open_esc:
        print(f"  #{e['id']} {e['kind']} {e['ref']} ({e['created']}): {e['detail'][:160]}")
    if settings.kill_switch_on():
        print("KILL SWITCH IS ON")
    if bad or open_esc:
        sys.exit(1)


def cmd_kill(a):
    if a.action == "on":
        killswitch.turn_on(_store(), settings, by="the CLI")
        print("kill switch ON: no placements until `puntmore kill off`")
    elif a.action == "off":
        killswitch.turn_off(settings)
        print("kill switch off")
    else:
        print("ON" if settings.kill_switch_on() else "off")


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
    print(alerts.heartbeat(_store(), scraper=inbox.scraper_status(settings)))


def cmd_link(a):
    """Run the Render link daemon: bet lists -> client engine, Scraper drops and tipster emails -> tips
    and escalations."""
    from .link.client import RenderLink, bets_from
    from .placement.engine import ClientEngine

    st = _store()
    engine = ClientEngine(st)
    link = RenderLink(st)
    log = logging.getLogger("puntmore.link")

    def on_bet_list(env):
        statuses = engine.handle_bet_list(bets_from(env))
        log.info("bet_list %s -> %s", env.msg_id, statuses)

    def on_scrape_job(env):
        # Scraping is the Scraper's: a job from Render is noted, not run here.
        link.enqueue("scrape_result", {"job_id": env.body.get("job_id"), "status": "skipped",
                                       "error": "scrapes run on the Scraper's own schedule (scraper/targets.toml)"})

    def on_kill(env):
        # ON only: Render never turns the file off (killswitch.from_render).
        killswitch.from_render(st, env.body, settings)

    link.handlers.update({"bet_list": on_bet_list, "scrape_job": on_scrape_job, "kill_switch": on_kill})

    def ingest_loop():
        import time
        while True:
            try:
                counts = inbox.ingest(Store(settings.db_path), settings)
                if counts["tip_files"] or counts["events"]:
                    log.info("inbox: %s", counts)
            except Exception as exc:  # noqa: BLE001
                log.exception("inbox pass failed: %s", exc)
            time.sleep(30)

    def mail_loop():
        # The tipster mailbox, every PUNTMORE_IMAP_EVERY_MIN; a failed poll is its own alert.
        import time
        while True:
            try:
                counts = mail.poll(Store(settings.db_path), settings)
                log.info("mail: %s", {k: counts[k] for k in mail.COUNT_KEYS} | {"error": counts["error"]})
            except Exception as exc:  # noqa: BLE001
                log.exception("mail poll failed: %s", exc)
            time.sleep(max(1, settings.imap_every_min) * 60)

    threading.Thread(target=ingest_loop, name="inbox", daemon=True).start()
    if settings.mail_configured:
        threading.Thread(target=mail_loop, name="mail", daemon=True).start()
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


def cmd_web(a):
    """Run the Hub Console: what every Hub component is doing, live, and the kill switch."""
    from .web.app import serve
    serve(settings, bind=a.bind)
def cmd_update(a):
    """Fast-forward the checkout to origin/main, reinstall, restart the units (deploy/update.sh)."""
    from . import update
    update.main(a, settings)


# ---- parser -----------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="puntmore", description="Puntmore Hub")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create data dirs and the database").set_defaults(fn=cmd_init)
    sub.add_parser("status", help="one-screen status, including the Scraper").set_defaults(fn=cmd_status)
    ib = sub.add_parser("inbox", help="one pass over the Scraper's drop folders (the link daemon does this every 30 s)")
    ib.add_argument("--no-notify", action="store_true", help="record escalations without waking the local AI"); ib.set_defaults(fn=cmd_inbox)
    ml = sub.add_parser("mail", help="one poll of the tipster mailbox (the link daemon does this every PUNTMORE_IMAP_EVERY_MIN)")
    ml.add_argument("--dry-run", action="store_true", help="show what the parsers read; store nothing, mark nothing seen")
    ml.add_argument("--no-notify", action="store_true", help="record escalations without waking the local AI"); ml.set_defaults(fn=cmd_mail)
    sub.add_parser("health", help="exit 1 if anything needs attention").set_defaults(fn=cmd_health)
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
    w = sub.add_parser("web", help="the Hub Console web page (PUNTMORE_WEB_BIND, default 127.0.0.1:8090)")
    w.add_argument("--bind", help="host:port, overrides PUNTMORE_WEB_BIND"); w.set_defaults(fn=cmd_web)
    up = sub.add_parser("update", help="fast-forward to origin/main, reinstall, restart (puntmore-update.timer)")
    mode = up.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print what it would do; change nothing")
    mode.add_argument("--check", action="store_true", help="exit 0 if up to date, 1 if behind, 2 if it cannot tell")
    up.add_argument("--repo", help="the checkout (default: the one this package is installed from)")
    up.set_defaults(fn=cmd_update)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.fn(args)


if __name__ == "__main__":
    main()
