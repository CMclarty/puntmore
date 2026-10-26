"""Pacing, with a stand-in browser: what one person with a few tabs open
would do - no burst at startup, loads spaced, timed reloads jittered."""
import random
import time

from scraper import config
from scraper.alerts import Alerts
from scraper.guard import Guard
from scraper.runner import Runner
from scraper.sites import SITES


class _Nothing:
    """Any attribute, any call: answers with itself; counts as zero/empty."""
    status = 200
    url = "http://127.0.0.1/x"

    def __getattr__(self, name):
        return _Nothing()

    def __call__(self, *a, **k):
        return _Nothing()

    def count(self):
        return 0

    def title(self):
        return "Page"

    def bounding_box(self):
        return None


class _Page(_Nothing):
    def __init__(self, links):
        self.links = links

    def eval_on_selector_all(self, *a):
        return self.links


class _Ctx:
    def __init__(self):
        self.n = 0

    def new_page(self):
        self.n += 1
        return _Page([f"https://www.paddypower.com/football/x/a{i}-v-b{i}" for i in range(9)])


def _runner(tmp_path, targets):
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, puntmore_url="http://x",
                        ingest_key=None, capture_mb=1, targets=targets)
    guard = Guard(tmp_path / "g.sqlite3", {n: (r.per_hour, r.per_day) for n, r in SITES.items()})
    guard.quiet = (0, 0)
    return Runner(s, _Ctx(), guard, Alerts(tmp_path / "alerts.log", None), tmp_path / "samples")


def _loads(r):
    return r.guard.db.execute("SELECT site, ts FROM loads ORDER BY ts").fetchall()


def _league(name):
    return config.Target(name=name, site="paddypower", stale_min=10_000,
                         url=f"https://www.paddypower.com/football/{name}",
                         matches=config.Matches(max_tabs=3, open_gap_min=4))


def test_startup_is_staggered_and_loads_are_spaced(tmp_path):
    random.seed(1)
    bb = config.Target(name="bb", site="bookiebashing", url="https://www.bookiebashing.net/s",
                       every_min=10, stale_min=10_000)
    r = _runner(tmp_path, [_league("epl"), _league("champ"), _league("liga"), bb])
    t0 = time.time()
    r.tick(t0)
    assert len(_loads(r)) == 1                          # one page on the first tick, not four
    for i in range(1, 4 * 60 * 4):                      # 4 hours of 15 s ticks
        r.tick(t0 + 15 * i)
    loads = _loads(r)
    gaps = [b[1] - a[1] for a, b in zip(loads, loads[1:])]
    assert min(gaps) >= 20                              # never two loads at once
    pp = [ts for site, ts in loads if site == "paddypower"]
    assert min(b - a for a, b in zip(pp, pp[1:])) >= SITES["paddypower"].load_gap_s
    for h in range(4):                                  # and the hourly ceiling holds
        assert sum(1 for ts in pp if t0 + 3600 * h <= ts < t0 + 3600 * (h + 1)) <= 15
    # 3 listings + 9 match tabs open in the end, none of them in a burst
    assert sum(1 for k in r.tabs if ":m:" in k) == 9


def test_timed_reloads_are_jittered_around_the_interval(tmp_path):
    """every_min=10 reloads about every 10 min (+-35%), not at the low end
    of the jitter every time."""
    random.seed(2)
    bb = config.Target(name="bb", site="bookiebashing", url="https://www.bookiebashing.net/s",
                       every_min=10, stale_min=10_000)
    r = _runner(tmp_path, [bb])
    t0 = time.time()
    for i in range(6 * 60 * 4):                         # 6 hours
        r.tick(t0 + 15 * i)
    ts = [t for _, t in _loads(r)]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    assert min(gaps) >= 0.75 * 600 - 15 and max(gaps) <= 1.35 * 600 + 15
    assert 9 * 60 < sum(gaps) / len(gaps) < 11.5 * 60
