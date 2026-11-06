"""python -m scraper <command>

  run              The service: one Chrome, every target kept open. Targets are
                   data/targets.toml on the Hub; an edit applies without a
                   restart. This is what the service runs.
  status           Budget spent, cool-offs, each target's state, heartbeat age.
  resume <site>    End a cool-off early-ish: it still needs its hours to pass.
  dump <target>    Ask the RUNNING service to save that target's open page(s)
                   (HTML + screenshot) to data/dumps/ - no extra page load.
  check            Load targets.toml and print the targets.
  targets ...      The only way targets.toml is changed: list, show <name>,
                   add --name --site --url [...], set <name> --every-min 15
                   [...], enable/disable/remove <name>; --json for machine
                   output, --file for another file. `targets -h` for more.
  selfcheck        What a site sees of this browser: navigator.webdriver,
                   userAgent, language, timezone, window, and that Chrome
                   runs without --enable-automation. Asks the running service
                   (it holds the profile); if none, opens the profile itself
                   on about:blank. No network.
"""
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime

from . import config
from .alerts import Alerts
from .guard import Guard
from .sites import SITES

# A tick that has not come back in this long is stuck: a Playwright call on a
# page whose renderer died (it froze on 27 Oct 2026 when the container ran out
# of memory, and sat there for 33 h). The service then exits, and Docker's
# restart policy brings it back up on a fresh Chrome. Ticks normally take
# seconds; the image's healthcheck already calls 5 min stale.
WATCHDOG_S = 600
# The whole service is restarted (a clean exit; Docker brings it back) about
# this often. Swapping pages (runner.RECYCLE_H) frees the Python side, but the
# Playwright driver still grew ~300 MB an hour on 29 Oct with Paddy's match
# pages open, toward the container's 3 GB. Only a new driver frees that. At
# 2.5-3.5 h the Bookie Bashing renderer still crashed once (2 h 47 min in).
RESTART_H = (1.5, 2.0)


def watchdog(last_tick: list, limit_s: float = WATCHDOG_S, every_s: float = 30,
             exit=os._exit):
    """Exit the process when last_tick[0] is older than limit_s. os._exit, not
    sys.exit: the stuck call holds the main thread, so nothing else would run."""
    def loop():
        while True:
            time.sleep(every_s)
            stuck = time.monotonic() - last_tick[0]
            if stuck > limit_s:
                print(f"watchdog: no tick for {int(stuck)} s - exiting so the "
                      "container restarts", flush=True)
                exit(1)
    threading.Thread(target=loop, name="watchdog", daemon=True).start()


def _guard(s):
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return Guard(s.data_dir / "guard.sqlite3",
                 {name: (r.per_hour, r.per_day) for name, r in SITES.items()})


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
    with sync_playwright() as p:
        ctx = browser.launch(p, s)
        # A tab that is never closed: the loop waits on it, which is what lets
        # the other tabs' events (responses, websocket frames) be delivered.
        idle = ctx.pages[0] if ctx.pages else ctx.new_page()
        runner = Runner(s, ctx, guard, alerts, run_dir)
        print(f"running {len(s.targets)} target(s) from {s.targets_file}: "
              + ", ".join(f"{t.name} ({t.site})" for t in s.targets), flush=True)
        last_tick = [time.monotonic()]
        watchdog(last_tick)
        restart_at = time.monotonic() + random.uniform(*RESTART_H) * 3600
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


# (option, field, type) for `targets add` and `targets set`
_TARGET_OPTS = (
    ("--site", "site", str), ("--url", "url", str),
    ("--parser", "parser", _none_or(str)), ("--post", "post", _none_or(str)),
    ("--every-min", "every_min", _none_or(float)), ("--stale-min", "stale_min", float),
    ("--min-rows", "min_rows", int), ("--trust-weight", "trust_weight", float),
    ("--wait-for", "wait_for", _none_or(str)), ("--mode", "mode", str),
    ("--priority", "priority", int), ("--window", "window", _none_or(str)),
    ("--matches", "matches", _matches_json),
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
    remove, through scraper/targets.py. --json prints machine output (the
    console and Hermes); a refused change exits 2 with the reason."""
    import json
    import pathlib
    from . import targets as T
    args = vars(_targets_parser().parse_args(argv))
    op, as_json = args.pop("op"), args.pop("json")
    path = pathlib.Path(args.pop("file") or T.default_path())
    status_json = pathlib.Path(os.environ.get("SCRAPER_DATA_DIR", config.HERE / "data")) / "status.json"

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
        else:                                   # remove
            T.remove(path, args["name"])
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
    else:
        print(_target_line(out))
        for k, v in out.items():
            if k != "status" and v is not None:
                print(f"  {k:13} {v}")
        if op != "show":
            print("saved; the running Scraper picks it up within 60 s.")


def _target_line(d):
    st = d["status"]
    state = "disabled" if not d["enabled"] else (st["state"] if st else "not reported")
    every = f"every {d['every_min']:g} min" if d["every_min"] else f"when quiet {d['stale_min']:g} min"
    extra = "".join((f"  priority {d['priority']}" if d["priority"] else "",
                     f"  window {d['window']}" if d["window"] else ""))
    return (f"{d['name']:28} {d['site']:13} {state:12} {every}{extra}"
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
    s = config.load()
    if cmd == "run":
        run(s)
    elif cmd == "status":
        status(s)
    elif cmd == "resume":
        if not rest or rest[0] not in SITES:
            sys.exit(f"resume which site? {', '.join(SITES)}")
        _guard(s).resume(rest[0])
        print(f"{rest[0]}: resume recorded - it restarts once the cool-off hours have passed.")
    elif cmd == "dump":
        if not rest or rest[0] not in {t.name for t in s.targets}:
            sys.exit(f"dump which target? {', '.join(t.name for t in s.targets)}")
        req = s.data_dir / "requests"
        req.mkdir(parents=True, exist_ok=True)
        (req / f"dump-{rest[0]}").touch()
        print(f"asked the service to dump {rest[0]} - see data/dumps/ within ~15 s")
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
