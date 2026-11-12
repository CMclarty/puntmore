"""A listing job reads only games kicking off within its window (games.within_h,
or the job's own within_h; Callum, 12 Nov 2026), with a stand-in browser and
the listing's kick-offs given in place of pp_listing's DOM read (that read is
tested on the real page's fixture in test_pp_listing.py)."""
import dataclasses
import random
import time

import pytest

from scraper import behaviour, config, joblog, rest, runner
from scraper.alerts import Alerts
from scraper.guard import Guard
from scraper.parsers.pp_listing import Fixture
from scraper.runner import Runner
from scraper.tests.test_pacing import _Nothing

PP = "https://www.paddypower.com/football"
UNREAD = "kick-off times could not be read from the listing - nothing opened"
H = 3600


class _Page(_Nothing):
    """A page that remembers what it was sent to; its links are its listing's."""
    def __init__(self, ctx):
        self.ctx, self.at, self.closed = ctx, None, False

    def goto(self, url, **kw):
        self.at = url
        return _Nothing()

    def eval_on_selector_all(self, *a):
        return [u for u, *_ in self.ctx.listings.get(self.at, ())]

    def close(self):
        self.closed = True


class _Ctx:
    """listings: listing url -> [(match url, name, kickoff or None)]."""
    def __init__(self):
        self.listings = {}
        self.pages = []

    def new_page(self):
        self.pages.append(_Page(self))
        return self.pages[-1]


@pytest.fixture
def read(monkeypatch):
    """pp_listing.fixtures as the listing would give it; counts its calls."""
    calls = []

    def fixtures(page, now):
        calls.append(page.at)
        return {u: Fixture(u, name, k) for u, name, k in page.ctx.listings.get(page.at, ())}
    monkeypatch.setattr(runner.pp_listing, "fixtures", fixtures)
    return calls


@pytest.fixture(autouse=True)
def _defaults(tmp_path_factory):
    behaviour.load(tmp_path_factory.mktemp("no-overrides"))
    yield
    behaviour.load(tmp_path_factory.mktemp("no-overrides"))


def _job(**kw):
    # A tab's age is real time (Tab.opened) and these ticks run hours ahead:
    # the longest tab life keeps a match tab from retiring of age here.
    return config.Target(name="pp", site="paddypower", url=PP + "/epl", stale_min=10_000,
                         matches=config.Matches(max_tabs=3, open_gap_min=2, tab_max_hours=24), **kw)


def _runner(tmp_path, targets, ctx):
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, capture_mb=1, targets=targets)
    guard = Guard(tmp_path / "g.sqlite3")
    guard.quiet = (0, 0)
    return Runner(s, ctx, guard, Alerts(tmp_path / "alerts.log", None), tmp_path / "samples")


def _run(r, start, minutes):
    """15 s ticks from start for that many minutes; returns the time after."""
    for i in range(int(minutes * 4)):
        r.tick(start + 15 * i)
    return start + 15 * int(minutes * 4)


def _opened(r):
    return sorted(k.split(":m:")[1] for k in r.tabs if ":m:" in k)


def _msgs(tmp_path):
    return [x["msg"] for x in joblog.read(tmp_path, "pp", n=500)]


def _m(n):
    return f"{PP}/epl/a{n}-v-b{n}"


def test_only_matches_kicking_off_within_the_window_are_opened(tmp_path, read):
    random.seed(1)
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 + 10 * H),       # in
                                 (_m(2), "A2 v B2", t0 + 60 * H),       # out: 60 h away
                                 (_m(3), "A3 v B3", None),              # no readable kick-off
                                 (_m(4), "A4 v B4", t0 - 600),          # kicked off
                                 (_m(5), "A5 v B5", t0 + 47 * H)]       # in
    r = _runner(tmp_path, [_job()], ctx)
    _run(r, t0, 60)
    assert _opened(r) == [_m(1), _m(5)]
    assert "pp" not in r.resting and UNREAD not in _msgs(tmp_path)
    assert r.status(t0 + 3600)["targets"][0]["state"] == "running"


def test_a_jobs_own_within_h_wins(tmp_path, read):
    random.seed(1)
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 + 10 * H), (_m(2), "A2 v B2", t0 + 60 * H)]
    r = _runner(tmp_path, [_job(within_h=72)], ctx)
    _run(r, t0, 60)
    assert _opened(r) == [_m(1), _m(2)]


def test_the_main_setting_is_read_live(tmp_path, read):
    random.seed(1)
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 + 10 * H), (_m(2), "A2 v B2", t0 + 60 * H)]
    behaviour.set_(tmp_path, "games.within_h", 72)
    r = _runner(tmp_path, [_job()], ctx)
    _run(r, t0, 60)
    assert _opened(r) == [_m(1), _m(2)]


def test_no_readable_kickoff_is_said_once_shown_and_opens_nothing(tmp_path, read):
    random.seed(1)
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", None), (_m(2), "A2 v B2", None)]
    r = _runner(tmp_path, [_job()], ctx)
    end = _run(r, t0, 60)
    assert _opened(r) == [] and "pp:list" in r.tabs           # the listing stays open
    assert _msgs(tmp_path).count(UNREAD) == 1
    assert "pp" not in r.resting                              # not a rest: Paddy's layout may have moved
    st = r.status(end)["targets"][0]
    assert st["state"] == "running" and UNREAD in st["detail"]
    # readable again: the line goes from the detail, the match opens, and a
    # later miss is said again
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", end + 3 * H)]
    end = _run(r, end, 15)
    assert _opened(r) == [_m(1)] and UNREAD not in r.status(end)["targets"][0]["detail"]
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", None), (_m(2), "A2 v B2", None)]
    _run(r, end, 5)
    assert _msgs(tmp_path).count(UNREAD) == 2


def test_none_in_range_rests_until_the_window_reaches_the_next_game(tmp_path, read):
    random.seed(1)
    t0 = time.time()
    ctx = _Ctx()
    first = t0 + 60 * H                                        # 12 h past the window
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 + 80 * H), (_m(2), "Man City v Fulham", first),
                                 (_m(3), "A3 v B3", None)]
    r = _runner(tmp_path, [_job()], ctx)
    r.tick(t0)                                                 # the listing loads
    assert "pp:list" in r.tabs and "pp" not in r.resting
    r.tick(t0 + 15)                                            # and is read: nothing near
    why = f"no games within 48 hours - next is Man City v Fulham, {rest.when(first)}"
    assert r.resting["pp"] == {"until": first - 48 * H, "why": why}
    assert rest.load(tmp_path) == r.resting                   # kept across a restart
    assert not r.tabs and all(p.closed for p in ctx.pages)    # its pages closed
    said = f"{why} - resting until {rest.when(first - 48 * H)}"
    assert _msgs(tmp_path)[-1] == said
    st = r.status(t0 + 60)["targets"][0]
    assert (st["state"], st["detail"]) == ("paused", said)
    loads = r.guard.spent("paddypower", t0 + 60)[1]
    _run(r, t0 + 30, 120)
    assert r.guard.spent("paddypower", t0 + 2 * H)[1] == loads == 1      # nothing loads while it rests
    # the rest ends as the game comes into range: the listing loads again and it opens
    _run(r, first - 48 * H + 1, 20)
    assert "rest over - running" in _msgs(tmp_path)
    assert _opened(r) == [_m(2)] and "pp" not in r.resting


def test_a_far_off_next_game_rests_at_most_look_again(tmp_path, read):
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 + 9 * 24 * H)]
    r = _runner(tmp_path, [_job()], ctx)
    r.tick(t0)
    r.tick(t0 + 15)
    assert r.resting["pp"]["until"] == t0 + 15 + 86400
    data = tmp_path / "other"
    behaviour.set_(data, "games.look_again_s", 3600)
    r = _runner(data, [_job()], ctx)                       # takes data/behaviour.toml up
    r.tick(t0)
    r.tick(t0 + 15)
    assert r.resting["pp"]["until"] == t0 + 15 + 3600


def test_only_kicked_off_games_listed_rests_a_look_again(tmp_path, read):
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 - 600)]
    r = _runner(tmp_path, [_job()], ctx)
    r.tick(t0)
    r.tick(t0 + 15)
    assert r.resting["pp"] == {"until": t0 + 15 + 86400, "why": "no games within 48 hours on the listing"}


def test_wake_ends_the_rest_and_the_listing_is_read_again(tmp_path, read):
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 + 60 * H)]
    r = _runner(tmp_path, [_job()], ctx)
    r.tick(t0)
    r.tick(t0 + 15)
    assert "pp" in r.resting
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 + 60 * H), (_m(2), "A2 v B2", t0 + 5 * H)]
    (tmp_path / "requests" / "wake-pp").touch()
    _run(r, t0 + 300, 15)
    assert "woken by hand - running" in _msgs(tmp_path)
    assert _opened(r) == [_m(2)] and "pp" not in r.resting


def test_every_listing_is_read_before_a_rest(tmp_path, read):
    """The rest waits for every listing to be open: a second league may have a
    game this weekend."""
    random.seed(1)
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 + 60 * H)]
    ctx.listings[PP + "/liga"] = [(PP + "/liga/c-v-d", "C v D", t0 + 4 * H)]
    job = _job()
    job = dataclasses.replace(job, matches=dataclasses.replace(job.matches, listings=(PP + "/liga",)))
    r = _runner(tmp_path, [job], ctx)
    _run(r, t0, 30)
    assert "pp" not in r.resting and _opened(r) == [PP + "/liga/c-v-d"]


def test_an_open_match_that_kicks_off_or_leaves_the_window_is_retired(tmp_path, read):
    random.seed(1)
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 + 20 * 60), (_m(2), "A2 v B2", t0 + 30 * H)]
    r = _runner(tmp_path, [_job()], ctx)
    end = _run(r, t0, 15)
    assert _opened(r) == [_m(1), _m(2)]
    end = _run(r, end, 10)                                     # A1 has kicked off
    assert _opened(r) == [_m(2)] and f"pp:m:{_m(1)}: retired" in _msgs(tmp_path)
    behaviour.set_(tmp_path, "games.within_h", 24)            # A2 is now outside the window
    r.next_behaviour_check = 0
    end = _run(r, end, 2)
    assert _opened(r) == [] and f"pp:m:{_m(2)}: retired" in _msgs(tmp_path)


def test_a_window_of_0_is_todays_behaviour(tmp_path, read):
    random.seed(1)
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", None), (_m(2), "A2 v B2", t0 + 60 * H),
                                 (_m(3), "A3 v B3", t0 - 600)]
    r = _runner(tmp_path, [_job(within_h=0)], ctx)
    _run(r, t0, 60)
    assert _opened(r) == [_m(1), _m(2), _m(3)]
    assert read == [] and "pp" not in r.resting               # the listing's times are not even read


def test_a_within_h_change_ends_a_rest_and_applies_in_place(tmp_path, read):
    random.seed(1)
    t0 = time.time()
    ctx = _Ctx()
    ctx.listings[PP + "/epl"] = [(_m(1), "A1 v B1", t0 + 60 * H)]
    job = _job()
    r = _runner(tmp_path, [job], ctx)
    r.tick(t0)
    r.tick(t0 + 15)
    assert "pp" in r.resting
    r.apply_targets([dataclasses.replace(job, within_h=72.0)])
    assert "pp" not in r.resting
    _run(r, t0 + 300, 15)
    assert _opened(r) == [_m(1)]


def test_a_listing_with_no_match_link_opens_nothing_and_does_not_rest(tmp_path, read):
    """A listing not drawn yet looks the same as an empty one: no rest on it."""
    t0 = time.time()
    ctx = _Ctx()
    r = _runner(tmp_path, [_job()], ctx)
    _run(r, t0, 10)
    assert "pp" not in r.resting and "pp:list" in r.tabs and _opened(r) == []
    assert UNREAD not in _msgs(tmp_path)
