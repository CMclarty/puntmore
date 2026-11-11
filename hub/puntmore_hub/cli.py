"""``puntmore`` command line. Every operation an agent or a human needs, with plain text output.

Scraping is the Scraper's (`python -m scraper ...` in `scraper/`); this CLI reads what the
Scraper drops and reports, polls the tipster mailbox, runs the daemon, hands tips to Finder on
Render for HubAI, and owns placement and escalations.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from . import __version__, alerts, aw26, escalation, finder, inbox, killswitch, mail
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
    print(f"dry_run={settings.dry_run} kill_switch={settings.kill_switch_on()}")
    print(f"scraper data: {settings.scraper_data_dir} ({'present' if settings.scraper_data_dir.exists() else 'not found'})")


def cmd_status(a):
    st = _store()
    s = st.summary_since(utcnow() - timedelta(hours=24))
    print(f"puntmore-hub {__version__}  dry_run={settings.dry_run}  "
          f"kill_switch={'ON' if settings.kill_switch_on() else 'off'}")
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
        print(f"escalation #{a.id} resolved")


def cmd_tips(a):
    rows = [dict(r) for r in _store().conn.execute(
        "SELECT tip_id, source_id, event, market, selection, advised_price, tip_time, outcome FROM tips ORDER BY first_seen DESC LIMIT ?",
        (a.limit,)).fetchall()]
    _print_rows(rows, ["tip_id", "source_id", "event", "market", "selection", "advised_price", "tip_time", "outcome"])


def cmd_heartbeat(a):
    print(alerts.heartbeat(_store(), scraper=inbox.scraper_status(settings)))


def cmd_daemon(a):
    """Run the Hub daemon: the inbox pass over the Scraper's drop files every 30 s (the price
    drops every 10 s) and the tipster mailbox on its own thread (daemon.py). `puntmore link` is the same command."""
    from . import daemon
    daemon.run(settings)


def cmd_finder(a):
    """Finder (docs/finder-spec.md): the new tips HubAI judges, the rows it posts to Render,
    the tips it skips, and the Bet Placer's placed|gone."""
    if a.which == "new":
        for tip in finder.new_tips(_store(), limit=a.limit):
            print(json.dumps(tip))
    elif a.which == "post":
        raw = sys.stdin.read() if a.file == "-" else open(a.file).read()
        rows = json.loads(raw)
        if not isinstance(rows, list):
            sys.exit("finder post: the file must hold a JSON list of rows")
        print(json.dumps(finder.post_rows(settings, rows, store=_store())))
    elif a.which == "skip":
        try:
            print(json.dumps(finder.skip(_store(), a.tip_id, a.reason)))
        except ValueError as exc:
            sys.exit(f"finder skip: {exc}")
    else:
        at = utcnow().isoformat(timespec="seconds")
        print(json.dumps(finder.post_status(settings, a.tip_id, a.status, at, store=_store())))


def _reading_photo(slip: dict, reading: str) -> dict:
    """The slip of the photo this run is reading (PUNTMORE_AW26_PHOTO_ID): its `photo_id` filled
    in when it has none; another photo's id is a ValueError naming both."""
    photo_id = int(reading)
    if slip.get("photo_id") is None:
        return {**slip, "photo_id": photo_id}
    if str(slip["photo_id"]).strip() != str(photo_id):
        raise ValueError(f"this run is reading photo {photo_id}, not photo {slip['photo_id']}")
    return slip


def cmd_aw26(a):
    """AW 26 (aw26.py): the slip-photo queue on Render, Paddy's board at a moment, and the slips
    HubAI reads. JSON to stdout. An unreadable time or a slip with no receipt exits 2 before
    anything is sent; any other refusal exits 1; a slip already in (or a photo already a slip)
    is success. In a reading run (PUNTMORE_AW26_PHOTO_ID set, aw26_loop.py) `upload` exits 2,
    and `record` fills in that `photo_id` or exits 2 on another one, `--dry-run` alike."""
    reading = os.environ.get(aw26.PHOTO_ID_ENV, "").strip()
    try:
        if a.which == "upload" and reading:
            raise ValueError(f"this run is reading photo {reading} and must not upload")
        if a.which == "photos":
            out = aw26.photos(settings)
        elif a.which == "take":
            out = {"id": a.id, "path": str(aw26.take(settings, a.id, settings.data_dir / "aw26"))}
        elif a.which == "fail":
            out = aw26.fail(settings, a.id, a.reason)
        elif a.which == "upload":
            out = aw26.upload(settings, Path(a.file), source=a.source)
        elif a.which == "board":
            out = aw26.board(settings, a.at, match=a.match)
        else:
            slip = json.loads(sys.stdin.read() if a.file == "-" else open(a.file).read())
            if not isinstance(slip, dict):
                sys.exit("aw26 record: the file must hold one JSON slip")
            if reading:
                slip = _reading_photo(slip, reading)
            out = aw26.record(settings, slip, dry_run=a.dry_run)
    except ValueError as exc:
        print(f"aw26 {a.which}: {exc}", file=sys.stderr)
        sys.exit(2)
    except RuntimeError as exc:          # a RenderError, or no Render configured
        sys.exit(f"aw26 {a.which}: {exc}")
    print(json.dumps(out))


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
    ib = sub.add_parser("inbox", help="one pass over the Scraper's drop folders (the daemon does this every 30 s, the prices every 10 s)")
    ib.add_argument("--no-notify", action="store_true", help="record escalations without waking HubAI"); ib.set_defaults(fn=cmd_inbox)
    ml = sub.add_parser("mail", help="one poll of the tipster mailbox (the daemon does this every PUNTMORE_IMAP_EVERY_MIN)")
    ml.add_argument("--dry-run", action="store_true", help="show what the parsers read; store nothing, mark nothing seen")
    ml.add_argument("--no-notify", action="store_true", help="record escalations without waking HubAI"); ml.set_defaults(fn=cmd_mail)
    sub.add_parser("health", help="exit 1 if anything needs attention").set_defaults(fn=cmd_health)
    k = sub.add_parser("kill", help="kill switch"); k.add_argument("action", choices=["on", "off", "status"]); k.set_defaults(fn=cmd_kill)

    es = sub.add_parser("escalation", help="escalations to HubAI").add_subparsers(dest="which", required=True)
    es.add_parser("list").set_defaults(fn=cmd_escalation)
    sh = es.add_parser("show"); sh.add_argument("id", type=int); sh.set_defaults(fn=cmd_escalation)
    rs = es.add_parser("resolve"); rs.add_argument("id", type=int); rs.add_argument("--outcome", required=True); rs.set_defaults(fn=cmd_escalation)

    t = sub.add_parser("tips", help="recent tips from the Scraper's sources and the mailbox"); t.add_argument("--limit", type=int, default=30); t.set_defaults(fn=cmd_tips)
    fd = sub.add_parser("finder", help="Finder: new tips for HubAI, its rows to Render, skips, the Bet Placer's status").add_subparsers(dest="which", required=True)
    fn = fd.add_parser("new", help="tips not yet posted to Finder, one JSON object per line")
    fn.add_argument("--limit", type=int, default=50); fn.set_defaults(fn=cmd_finder)
    fp = fd.add_parser("post", help="POST a JSON list of rows (a file, or - for stdin) to /api/finder/rows")
    fp.add_argument("file"); fp.set_defaults(fn=cmd_finder)
    fk = fd.add_parser("skip", help="a tip HubAI cannot post: marked skipped, so `finder new` moves on")
    fk.add_argument("tip_id"); fk.add_argument("--reason"); fk.set_defaults(fn=cmd_finder)
    fs = fd.add_parser("status", help="the Bet Placer's report on an offered row")
    fs.add_argument("tip_id"); fs.add_argument("status", choices=["placed", "gone"]); fs.set_defaults(fn=cmd_finder)
    aw = sub.add_parser("aw26", help="AW 26: slip photos for HubAI to read, Paddy's board at a moment, slips to record").add_subparsers(dest="which", required=True)
    aw.add_parser("photos", help="the photos on Render's queue not done yet").set_defaults(fn=cmd_aw26)
    at = aw.add_parser("take", help="take a waiting photo and save its image under data/aw26/")
    at.add_argument("id", type=int); at.set_defaults(fn=cmd_aw26)
    af = aw.add_parser("fail", help="a photo HubAI cannot make a slip of")
    af.add_argument("id", type=int); af.add_argument("--reason", required=True); af.set_defaults(fn=cmd_aw26)
    au = aw.add_parser("upload", help="put a photo on the queue")
    au.add_argument("file"); au.add_argument("--source", choices=["page", "telegram"], default="telegram"); au.set_defaults(fn=cmd_aw26)
    ab = aw.add_parser("board", help="Paddy's board as it stood at a moment (printed or ISO)")
    ab.add_argument("--at", required=True); ab.add_argument("--match"); ab.set_defaults(fn=cmd_aw26)
    ar = aw.add_parser("record", help="record a slip (a JSON file, or - for stdin)")
    ar.add_argument("file"); ar.add_argument("--dry-run", action="store_true", help="print the body; send nothing"); ar.set_defaults(fn=cmd_aw26)
    sub.add_parser("heartbeat", help="send the daily summary").set_defaults(fn=cmd_heartbeat)
    sub.add_parser("daemon", help="run the daemon: inbox every 30 s, prices every 10 s, tipster mailbox on its own thread").set_defaults(fn=cmd_daemon)
    sub.add_parser("link", help="the same as `daemon` (the name puntmore-link.service runs)").set_defaults(fn=cmd_daemon)
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
