"""python -m scraper <command>

  run              The service: one Chrome, every target kept open. Targets come
                   from Puntmore's /scraper page (targets.toml offline). This
                   is what the container runs.
  status           Budget spent, cool-offs, each target's state, heartbeat age.
  resume <site>    End a cool-off early-ish: it still needs its hours to pass.
  dump <target>    Ask the RUNNING service to save that target's open page(s)
                   (HTML + screenshot) to data/dumps/ - no extra page load.
  check            Load the targets (server, cache or toml) and print them.
  selfcheck        What a site sees of this browser: navigator.webdriver,
                   userAgent, language, timezone, window, and that Chrome
                   runs without --enable-automation. Asks the running service
                   (it holds the profile); if none, opens the profile itself
                   on about:blank. No network.
"""
import os
import signal
import sys
import time
from datetime import datetime

from . import config
from .alerts import Alerts
from .guard import Guard
from .sites import SITES


def _guard(s):
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return Guard(s.data_dir / "guard.sqlite3",
                 {name: (r.per_hour, r.per_day) for name, r in SITES.items()})


def run(s):
    from playwright.sync_api import sync_playwright
    from . import browser
    from .runner import Runner

    if not s.targets and not s.from_server:
        sys.exit(f"No targets - set PUNTMORE_URL and SAVES_INGEST_KEY, or put a targets.toml "
                 f"in {s.data_dir} (see targets.example.toml).")
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
        print(f"running {len(s.targets)} target(s) from {s.targets_source}: "
              + ", ".join(f"{t.name} ({t.site})" for t in s.targets), flush=True)
        while not stop:
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
    report (data/status.json) - the same thing it posts to Puntmore."""
    from .runner import site_status
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
        print(f"targets        from {body.get('source')}, reported "
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
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
        print(f"targets from {s.targets_source}")
        for t in s.targets:
            print(f"{t.name}: {t.site} {t.url}"
                  + (f"  matches(max {t.matches.max_tabs}, tabs {list(t.matches.market_tabs)})" if t.matches else "")
                  + (f"  parser={t.parser}" if t.parser else "") + (f"  post={t.post}" if t.post else ""))
        print(f"{len(s.targets)} target(s) OK")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
