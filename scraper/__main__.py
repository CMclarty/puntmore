"""python -m scraper <command>

  run              The service: one Chrome, every target kept open. Targets are
                   data/targets.toml on the Hub; an edit applies without a
                   restart. This is what the service runs.
  status           Budget spent, cool-offs, each target's state, heartbeat age.
  resume <site>    End a cool-off early-ish: it still needs its hours to pass.
                   It also forgets a failed sign-in (login.py).
  dump <target>    Ask the RUNNING service to save that target's open page(s)
                   (HTML + screenshot) to data/dumps/ - no extra page load.
  wake <target>    Ask the RUNNING service to end that job's rest (a parser
                   that found nothing to do rested it: rest.py) - it runs on
                   its next tick.
  check            Load targets.toml and print the targets.
  targets ...      The only way targets.toml is changed: list, show <name>,
                   add --name --site --url [...], set <name> --every-min 15
                   [...], enable/disable/remove <name>; --days fri,sat and
                   --until 2026-11-30 limit a target (a job) to those days and
                   that last day ("none" clears either); log <name> [-n 50]
                   prints the job's own log (data/logs/<name>.log); --json for
                   machine output, --file for another file. `targets -h` for
                   more.
  behaviour ...    How the Scraper behaves: every limit, timing and click
                   (scraper/behaviour.py). `behaviour` lists them (group,
                   key, value, default; * marks a changed one); `behaviour
                   set <key> <value>` changes one ("20-45" for a range,
                   true/false, "1-7" for hours), refused outside its bounds;
                   `behaviour reset <key>` / `reset --all` puts it back to
                   its default. Changes go to data/behaviour.toml; the
                   running service takes them up within 60 s, no restart,
                   and logs each (data/logs/behaviour.log). --json for
                   machine output.
  selfcheck        What a site sees of this browser: navigator.webdriver,
                   userAgent, language, timezone, window, and that Chrome
                   runs without --enable-automation. Asks the running service
                   (it holds the profile); if none, opens the profile itself
                   on about:blank. No network.
"""
import os
import pathlib
import random
import signal
import sys
import threading
import time
from datetime import datetime

from . import behaviour
from . import config
from . import login
from . import standing
from .alerts import Alerts
from .guard import Guard
from .sites import SITES

# A tick that has not come back in this long is stuck: a Playwright call on a
# page whose renderer died (it froze on 27 Oct 2026 when the container ran out
# of memory, and sat there for 33 h). The service then exits, and Docker's
# restart policy brings it back up on a fresh Chrome. Ticks normally take
# seconds; the image's healthcheck already calls 5 min stale.
WATCHDOG_S = 600
# The whole service is restarted (a clean exit; the service manager brings it
# back) every `restart.hours` (behaviour.py, 1.5-2 h). Swapping pages
# (`recycle.hours`) frees the Python side, but the Playwright driver still grew
# ~300 MB an hour on 29 Oct with Paddy's match pages open, toward the
# container's 3 GB. Only a new driver frees that. At 2.5-3.5 h the Bookie
# Bashing renderer still crashed once (2 h 47 min in).


class Watchdog:
    """Exit the process when last_tick[0] is older than limit_s. os._exit, not
    sys.exit: the stuck call holds the main thread, so nothing else would run.

    It says so too (standing.py): once when the loop has made no progress for
    `alerts.stuck_s` (read at each check; 0: off), once if it ticks again
    before the exit, and before the exit it writes data/stalled.json for the
    next start to say. The alerts go on a thread of their own and nothing
    here touches the browser, so a hanging webhook cannot hold up the exit."""

    def __init__(self, last_tick: list, limit_s: float = WATCHDOG_S, exit=os._exit,
                 alerts=None, data_dir=None, clock=time.monotonic):
        self.last_tick, self.limit_s, self.exit = last_tick, limit_s, exit
        self.alerts, self.data_dir, self.clock = alerts, data_dir, clock
        self.stalled_at: float | None = None    # the tick a stall alert was sent for

    def check(self):
        tick = self.last_tick[0]
        stuck = self.clock() - tick
        if stuck > self.limit_s:
            try:
                print(f"watchdog: no tick for {int(stuck)} s - exiting so the "
                      "service restarts", flush=True)
                standing.write_stalled(self.data_dir, stuck)
            finally:
                self.exit(1)
            return
        if self.stalled_at is not None and tick != self.stalled_at:
            self._say(f"running again after a {standing.span(tick - self.stalled_at)} stall")
            self.stalled_at = None
        limit = behaviour.get("alerts.stuck_s")
        if limit and stuck >= limit and self.stalled_at is None:
            self.stalled_at = tick
            self._say(f"stalled: no progress for {standing.span(stuck)} - the watchdog "
                      f"restarts it at {standing.span(self.limit_s)}")

    def _say(self, message):
        if self.alerts is None:
            print(f"watchdog: {message}", flush=True)
        else:
            standing.send_aside(self.alerts, message)


def watchdog(last_tick: list, limit_s: float = WATCHDOG_S, every_s: float = 30,
             exit=os._exit, alerts=None, data_dir=None) -> Watchdog:
    """A Watchdog checked every every_s on a daemon thread of its own."""
    dog = Watchdog(last_tick, limit_s, exit, alerts, data_dir)

    def loop():
        while True:
            time.sleep(every_s)
            try:
                dog.check()
            except Exception as exc:                # the next check still runs
                print(f"watchdog: {exc.__class__.__name__}: {exc}", flush=True)
    threading.Thread(target=loop, name="watchdog", daemon=True).start()
    return dog


def _guard(s):
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return Guard(s.data_dir / "guard.sqlite3")      # its limits: behaviour.py


def run(s):
    from playwright.sync_api import sync_playwright
    from . import browser
    from .runner import Runner

    if not s.targets_file.exists():
        sys.exit(f"No targets - put a targets.toml in {s.data_dir} "
                 f"(see targets.example.toml).")
    stop = []

    def on_signal(signum, _frame):
        print(f"stopping ({signal.Signals(signum).name})", flush=True)
        stop.append(signum)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    run_dir = s.data_dir / "samples" / datetime.now().strftime("%Y%m%d-%H%M%S")
    _prune(s.data_dir / "samples", keep=5)
    guard, alerts = _guard(s), Alerts(s.data_dir / "alerts.log", s.alert_webhook)
    standing.restarted(s.data_dir, alerts)      # the watchdog ended the last run: said once
    with sync_playwright() as p:
        ctx = browser.launch(p, s)
        # A tab that is never closed: the loop waits on it, which is what lets
        # the other tabs' events (responses, websocket frames) be delivered.
        idle = ctx.pages[0] if ctx.pages else ctx.new_page()
        runner = Runner(s, ctx, guard, alerts, run_dir)
        print(f"running {len(s.targets)} target(s) from {s.targets_file}: "
              + ", ".join(f"{t.name} ({t.site})" for t in s.targets), flush=True)
        last_tick = [time.monotonic()]
        watchdog(last_tick, alerts=alerts, data_dir=s.data_dir)
        restart_at = time.monotonic() + random.uniform(*behaviour.get("restart.hours")) * 3600
        while not stop:
            last_tick[0] = time.monotonic()
            if last_tick[0] >= restart_at:
                print("scheduled restart - exiting so the container starts afresh",
                      flush=True)
                break
            try:
                runner.tick()
            except Exception as exc:              # one bad tick never ends the service
                print(f"tick failed: {exc.__class__.__name__}: {exc}", flush=True)
            for _ in range(6):                    # ~15 s, in slices so a stop is quick
                if stop:
                    break
                idle.wait_for_timeout(2500)
        runner.close_all()
        ctx.close()


def _prune(root, keep):
    if not root.exists():
        return
    import shutil
    for old in sorted(p for p in root.iterdir() if p.is_dir())[:-keep or None]:
        shutil.rmtree(old, ignore_errors=True)


def status(s):
    """Per site: budget and cool-off. Per target: the running service's last
    report (data/status.json) - the same file the Hub reads."""
    from .status import site_status
    for st in site_status(_guard(s), time.time()):
        line = (f"{st['site']:14} loads {st['loads_hour']}/{st['per_hour']} this hour, "
                f"{st['loads_day']}/{st['per_day']} today")
        if st["cooloff"]:
            line += (f"  COOLING OFF ({st['cooloff']}), {st['cooloff_left_h']:.1f} h left, "
                     f"{'needs resume' if st['needs_resume'] else 'resumed'}")
        print(line)
    rep = s.data_dir / "status.json"
    if rep.exists():
        import json
        body = json.loads(rep.read_text())
        print(f"targets        from {body.get('targets_file')}, reported "
              f"{int(time.time() - body['at'])} s ago")
        for t in body["targets"]:
            ago = lambda ts: "never" if ts is None else f"{int(time.time() - ts)} s ago"
            print(f"  {t['name']:28} {t['state']:8} load {ago(t['last_load'])}, "
                  f"parse {ago(t['last_parse'])}, posted {t['rows_posted']}"
                  + (f"  ({t['detail']})" if t.get("detail") else ""))
    hb = s.data_dir / "heartbeat"
    print(f"heartbeat      {int(time.time() - int(hb.read_text()))} s ago" if hb.exists()
          else "heartbeat      none")


def selfcheck(s):
    import json
    from . import browser
    hb = s.data_dir / "heartbeat"
    running = hb.exists() and time.time() - int(hb.read_text()) < 120
    if running:
        out = s.data_dir / "selfcheck.json"
        out.unlink(missing_ok=True)
        req = s.data_dir / "requests"
        req.mkdir(parents=True, exist_ok=True)
        (req / "selfcheck").touch()
        for _ in range(60):                     # the loop ticks every ~15 s
            if out.exists():
                break
            time.sleep(1)
        else:
            sys.exit("the running service did not answer within 60 s")
        seen, source = json.loads(out.read_text()), "the running service's browser"
    else:
        from playwright.sync_api import sync_playwright
        headless = os.environ.get("SCRAPER_HEADLESS") == "1"
        with sync_playwright() as p:
            ctx = browser.launch(p, s, headless=headless)
            try:
                seen = browser.selfcheck(ctx)
            finally:
                ctx.close()
        source = "a fresh launch of the profile" + (", headless" if headless else "")
    print("\n".join(browser.selfcheck_lines(seen, source)))


def _none_or(kind):
    """An option value; "none" clears an optional field (`targets set`)."""
    def parse(v):
        if v.lower() in ("none", "null", ""):
            return None
        try:
            return kind(v)
        except ValueError:
            import argparse
            raise argparse.ArgumentTypeError(f"not a {kind.__name__}: {v!r}") from None
    parse.__name__ = kind.__name__
    return parse


def _days(v):
    """`--days fri,sat` (any case) -> ["fri", "sat"]; "none" clears. The
    names are checked by config._target."""
    if v.lower() in ("none", "null", ""):
        return None
    return [d.strip().lower() for d in v.split(",")]


def _matches_json(v):
    import argparse
    import json
    if v.lower() in ("none", "null"):
        return None
    try:
        m = json.loads(v)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--matches is not JSON: {exc}") from None
    if not isinstance(m, dict):
        raise argparse.ArgumentTypeError("--matches must be a JSON object")
    return m


def _listings(v):
    """--listings url,url: a matches job's further listing pages; "none" clears them."""
    return [] if v.strip().lower() in ("", "none") else [u.strip() for u in v.split(",") if u.strip()]


# (option, field, type) for `targets add` and `targets set`
_TARGET_OPTS = (
    ("--site", "site", str), ("--url", "url", str),
    ("--parser", "parser", _none_or(str)), ("--post", "post", _none_or(str)),
    ("--every-min", "every_min", _none_or(float)), ("--stale-min", "stale_min", float),
    ("--min-rows", "min_rows", int), ("--trust-weight", "trust_weight", float),
    ("--wait-for", "wait_for", _none_or(str)), ("--mode", "mode", str),
    ("--priority", "priority", int), ("--window", "window", _none_or(str)),
    ("--days", "days", _days), ("--until", "until", _none_or(str)),
    ("--matches", "matches", _matches_json),
    ("--listings", "listings", _listings),
    ("--within-h", "within_h", _none_or(float)),
)


def _targets_parser():
    import argparse
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", help="targets.toml (default: SCRAPER_TARGETS, "
                                       "else SCRAPER_DATA_DIR/targets.toml)")
    common.add_argument("--json", action="store_true", help="machine output")
    p = argparse.ArgumentParser(prog="python -m scraper targets",
                                description="Change targets.toml - the only way it is changed.")
    sub = p.add_subparsers(dest="op", required=True)
    sub.add_parser("list", parents=[common], help="every target, joined to status.json")
    for op in ("show", "enable", "disable", "remove"):
        sub.add_parser(op, parents=[common]).add_argument("name")
    lg = sub.add_parser("log", parents=[common], help="the newest lines of a job's own log")
    lg.add_argument("name")
    lg.add_argument("-n", type=int, default=50, help="how many lines (default 50)")
    a = sub.add_parser("add", parents=[common], help="add a target")
    a.add_argument("--name", required=True)
    s = sub.add_parser("set", parents=[common], help="change fields of a target; 'none' clears one")
    s.add_argument("name")
    for opt, field, kind in _TARGET_OPTS:
        a.add_argument(opt, dest=field, type=kind, default=argparse.SUPPRESS,
                       required=field in ("site", "url"))
        s.add_argument(opt, dest=field, type=kind, default=argparse.SUPPRESS)
    a.add_argument("--disabled", dest="enabled", action="store_false", default=argparse.SUPPRESS)
    en = s.add_mutually_exclusive_group()
    en.add_argument("--enabled", dest="enabled", action="store_true", default=argparse.SUPPRESS)
    en.add_argument("--disabled", dest="enabled", action="store_false", default=argparse.SUPPRESS)
    return p


def targets_cmd(argv):
    """`python -m scraper targets ...`: list, show, add, set, enable, disable,
    remove, through scraper/targets.py, and log (joblog.py). --json prints
    machine output (the Console and HubAI); a refused change exits 2 with the
    reason."""
    import json
    from . import joblog
    from . import targets as T
    args = vars(_targets_parser().parse_args(argv))
    op, as_json = args.pop("op"), args.pop("json")
    path = pathlib.Path(args.pop("file") or T.default_path())
    data_dir = _data_dir()
    status_json = data_dir / "status.json"
    _load_behaviour(data_dir, out=sys.stderr)   # schedule_words reads fetch.default_min

    def show(t):
        return next(d for d in T.summary(path, status_json) if d["name"] == t.name)
    try:
        if op == "list":
            out = T.summary(path, status_json)
        elif op == "show":
            out = show(T.get(path, args["name"]))
        elif op == "add":
            out = show(T.add(path, args))
        elif op == "set":
            name = args.pop("name")
            if not args:
                raise T.TargetsError("nothing to change: give at least one --field")
            out = show(T.set(path, name, **args))
        elif op in ("enable", "disable"):
            out = show(getattr(T, op)(path, args["name"]))
        elif op == "log":
            out = {"name": args["name"], "lines": joblog.read(data_dir, args["name"], args["n"])}
        else:                                   # remove
            T.remove(path, args["name"], data_dir=data_dir)
            out = {"removed": args["name"], "targets": [t.name for t in T.load(path)]}
    except T.TargetsError as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    if as_json:
        print(json.dumps(out, indent=1))
    elif op == "list":
        print(f"targets in {path}")
        for d in out:
            print(_target_line(d))
        print(f"{len(out)} target(s), {sum(d['enabled'] for d in out)} enabled. "
              "The running Scraper picks up a change within 60 s.")
    elif op == "remove":
        print(f"removed {out['removed']}; left: {', '.join(out['targets']) or 'none'}")
    elif op == "log":
        for x in out["lines"]:
            print(f"{datetime.fromtimestamp(x['ts']):%Y-%m-%d %H:%M:%S}  {x['msg']}")
        if not out["lines"]:
            print(f"no log lines for {out['name']} in {joblog.path(data_dir, out['name'])}",
                  file=sys.stderr)
    else:
        print(_target_line(out))
        for k, v in out.items():
            if k != "status" and v is not None:
                print(f"  {k:13} {v}")
        if op != "show":
            print("saved; the running Scraper picks it up within 60 s.")


def _data_dir():
    return pathlib.Path(os.environ.get("SCRAPER_DATA_DIR", config.HERE / "data"))


def _load_behaviour(data_dir, out=None) -> list[str]:
    """data/behaviour.toml's values made the ones in use; a problem is said
    (stdout, the journal, unless `out`), and the values in use are kept."""
    problems = behaviour.load(data_dir)
    for p in problems:
        print(f"behaviour: {p}", file=out or sys.stdout, flush=True)
    return problems


def _behaviour_parser():
    import argparse
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine output")
    p = argparse.ArgumentParser(
        prog="python -m scraper behaviour", parents=[common],
        description="How the Scraper behaves (scraper/behaviour.py): list, set, reset.")
    sub = p.add_subparsers(dest="op")
    sub.add_parser("list", parents=[common], help="every setting (the default)")
    s = sub.add_parser("set", parents=[common], help="change one setting")
    s.add_argument("key")
    s.add_argument("value", help='a number, true/false, "20-45" for a range, "1-7" for hours')
    r = sub.add_parser("reset", parents=[common], help="put a setting back to its default")
    which = r.add_mutually_exclusive_group(required=True)
    which.add_argument("key", nargs="?")
    which.add_argument("--all", action="store_true", help="every setting")
    return p


def behaviour_cmd(argv):
    """`python -m scraper behaviour [list|set|reset] [--json]`, through
    scraper/behaviour.py. A refused change exits 2 with the reason (naming
    the setting). The list reads data/behaviour.toml first; a file that does
    not load is reported (stderr; "problems" in --json) and the defaults
    listed."""
    import json
    args = _behaviour_parser().parse_args(argv)
    op = args.op or "list"
    data_dir = _data_dir()
    file = behaviour.path(data_dir)
    try:
        if op == "set":
            behaviour.set_(data_dir, args.key, args.value)
        elif op == "reset":
            gone = behaviour.reset(data_dir, None if args.all else args.key)
    except behaviour.BehaviourError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    problems = behaviour.load(data_dir)
    rows = behaviour.listing()
    if op == "reset" and args.all:
        if args.json:
            print(json.dumps({"reset": gone}, indent=1))
        else:
            print(f"every setting back to its default ({len(gone)} taken out of {file}); "
                  "the running Scraper takes it up within 60 s.")
        return
    if op in ("set", "reset"):
        # A key reset from the file that the catalogue does not know has no row.
        row = next((r for r in rows if r["key"] == args.key), None)
        if args.json:
            print(json.dumps(row or {"reset": gone}, indent=1))
        elif row is None:
            print(f"removed {args.key} from {file}")
        else:
            print(f"{row['key']} = {behaviour.show(row['value'])} {row['unit']}"
                  f"  (default {behaviour.show(row['default'])})")
            print(f"saved to {file}; the running Scraper takes it up within 60 s.")
        return
    changed = sum(r["changed"] for r in rows)
    if args.json:
        print(json.dumps({"file": str(file), "changed": changed, "problems": problems,
                          "settings": rows}, indent=1))
        return
    for p in problems:
        print(f"behaviour: {p}", file=sys.stderr)
    group, width = None, max(len(r["key"]) for r in rows)
    for r in rows:
        if r["group"] != group:
            group = r["group"]
            print(f"\n{group}")
        value = f"{behaviour.show(r['value'])} {r['unit']}".strip()
        print(f"  {'*' if r['changed'] else ' '} {r['key']:{width}}  {value:20} "
              f"default {behaviour.show(r['default'])}")
    print(f"\n{changed} setting(s) changed from default (* above), in {file}. "
          "Each one's meaning: scraper/behaviour.py.")


def _within_words(h) -> str:
    """A job's own within_h for `targets list`; none (the main setting) says nothing."""
    if h is None:
        return ""
    return "  games: no limit" if h == 0 else f"  games within {h:g} h"


def _target_line(d):
    st = d["status"]
    state = "disabled" if not d["enabled"] else (st["state"] if st else "not reported")
    return (f"{d['name']:28} {d['site']:13} {state:12} {d['schedule']}"
            + (f"  priority {d['priority']}" if d["priority"] else "")
            + (_within_words(d.get("within_h")))
            + (f"  ({st['detail']})" if st and st.get("detail") else ""))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "targets":            # not config.load(): it would exit on a file that does not load
        targets_cmd(rest)
        return
    if cmd == "behaviour":          # nor here: the behaviour file is changed without the targets
        behaviour_cmd(rest)
        return
    s = config.load()
    _load_behaviour(s.data_dir)     # once, at start; a problem keeps the defaults
    if cmd == "run":
        run(s)
    elif cmd == "status":
        status(s)
    elif cmd == "resume":
        if not rest or rest[0] not in SITES:
            sys.exit(f"resume which site? {', '.join(SITES)}")
        _guard(s).resume(rest[0])
        login.clear(s.data_dir, rest[0], times=True)
        print(f"{rest[0]}: resume recorded - it restarts once the cool-off hours have passed."
              + (" A failed sign-in is forgotten: it will try once more the next time it "
                 "finds the site logged out." if rest[0] in login.FORMS else ""))
    elif cmd == "dump":
        if not rest or rest[0] not in {t.name for t in s.targets}:
            sys.exit(f"dump which target? {', '.join(t.name for t in s.targets)}")
        req = s.data_dir / "requests"
        req.mkdir(parents=True, exist_ok=True)
        (req / f"dump-{rest[0]}").touch()
        print(f"asked the service to dump {rest[0]} - see data/dumps/ within ~15 s")
    elif cmd == "wake":
        if not rest or rest[0] not in {t.name for t in s.targets}:
            sys.exit(f"wake which target? {', '.join(t.name for t in s.targets)}")
        req = s.data_dir / "requests"
        req.mkdir(parents=True, exist_ok=True)
        (req / f"wake-{rest[0]}").touch()
        print(f"asked the service to wake {rest[0]} - its log says how within ~15 s")
    elif cmd == "selfcheck":
        selfcheck(s)
    elif cmd == "check":
        print(f"targets from {s.targets_file}")
        for t in s.targets:
            print(f"{t.name}: {t.site} {t.url}"
                  + (f"  matches(max {t.matches.max_tabs}, tabs {list(t.matches.market_tabs)})" if t.matches else "")
                  + (f"  parser={t.parser}" if t.parser else "") + (f"  post={t.post}" if t.post else ""))
        print(f"{len(s.targets)} target(s) OK")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
