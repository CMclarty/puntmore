"""python -m scraper <command>

  run              The service: one Chrome, every target in targets.toml kept
                   open. This is what the container runs.
  status           Budget spent, cool-offs, heartbeat age.
  resume <site>    End a cool-off early-ish: it still needs its hours to pass.
  dump <target>    Ask the RUNNING service to save that target's open page(s)
                   (HTML + screenshot) to data/dumps/ - no extra page load.
  check            Validate targets.toml and print what would run.
"""
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

    if not s.targets:
        sys.exit(f"No targets - put a targets.toml in {s.data_dir} (see targets.example.toml).")
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
        print(f"running {len(s.targets)} target(s): "
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
    g = _guard(s)
    for site in SITES:
        h, d = g.spent(site)
        c = g.cooloff(site)
        line = f"{site:14} loads {h}/{SITES[site].per_hour} this hour, {d}/{SITES[site].per_day} today"
        if c and g.cooloff_active(site):
            left = max(0, g.cooloff_hours * 3600 - (time.time() - c["since"])) / 3600
            line += (f"  COOLING OFF ({c['reason']}), {left:.1f} h left, "
                     f"{'resumed' if c['resumed'] else 'needs resume'}")
        print(line)
    hb = s.data_dir / "heartbeat"
    print(f"heartbeat      {int(time.time() - int(hb.read_text()))} s ago" if hb.exists()
          else "heartbeat      none")


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
    elif cmd == "check":
        for t in s.targets:
            print(f"{t.name}: {t.site} {t.url}"
                  + (f"  matches(max {t.matches.max_tabs}, tabs {list(t.matches.market_tabs)})" if t.matches else "")
                  + (f"  parser={t.parser}" if t.parser else "") + (f"  post={t.post}" if t.post else ""))
        print(f"{len(s.targets)} target(s) OK")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
