"""A job's own alerts (standing.py; Callum, 12 Nov 2026): one when its parser
has failed the same way for `alerts.failing_min`, one when it reads again;
one when a running job with pages open has handed over no rows for
`alerts.no_rows_min`, one when it delivers again. Each once, kept across the
service's restart in data/alerted.json. A stand-in browser, no site."""
import json
import random
import time
from types import SimpleNamespace

import pytest

from scraper import behaviour, config, joblog, rest, runner, standing
from scraper.alerts import Alerts
from scraper.guard import Guard
from scraper.runner import Runner

PP = "https://www.paddypower.com/football"
MIN = 60


@pytest.fixture(autouse=True)
def _defaults(tmp_path_factory):
    behaviour.load(tmp_path_factory.mktemp("no-overrides"))
    yield
    behaviour.load(tmp_path_factory.mktemp("no-overrides"))


class _Parser:
    """The job's parser: raises `fail` when it is set, else returns `rows`."""
    def __init__(self):
        self.fail, self.rows, self.rest = None, [], None

    def __call__(self, page, capture):
        if self.rest is not None:
            raise rest.Rest(self.rest, "nothing to read")
        if self.fail is not None:
            raise ValueError(self.fail + "\nTraceback line two")
        return list(self.rows)


@pytest.fixture
def parser(monkeypatch):
    p = _Parser()
    monkeypatch.setitem(runner.PARSERS, "fake", p)
    return p


def _job(**kw):
    return config.Target(name="pp", site="paddypower", url=PP + "/a-v-b", parser="fake",
                         post="/api/paddy/most-prices", stale_min=10_000, **kw)


def _runner(tmp_path, targets, ctx=None):
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, capture_mb=1, targets=targets)
    guard = Guard(tmp_path / "g.sqlite3")
    guard.quiet = (0, 0)
    return Runner(s, ctx, guard, Alerts(tmp_path / "alerts.log", None), tmp_path / "samples")


def _tab(t):
    return SimpleNamespace(target=t, page=None, capture=None, next_parse=0.0, url=t.url, market_tabs={})


def _alerts(tmp_path):
    p = tmp_path / "alerts.log"
    return [line.split("  ", 1)[1] for line in p.read_text().splitlines()] if p.exists() else []


def _log(tmp_path, name="pp"):
    return [x["msg"] for x in joblog.read(tmp_path, name, n=500)]


def _parse_each_minute(r, tab, start, minutes):
    for i in range(minutes + 1):
        r._parse(tab, start + i * MIN)


# --- failing the same way --------------------------------------------------------
def test_the_same_failure_alerts_once_at_15_min(tmp_path, parser):
    t0 = time.time()
    r, t = _runner(tmp_path, [_job()]), _job()
    tab = _tab(t)
    parser.fail = "no Corners market on the page"
    _parse_each_minute(r, tab, t0, 14)
    assert _alerts(tmp_path) == []
    r._parse(tab, t0 + 15 * MIN)
    want = "failing for 15 min: no Corners market on the page"
    assert _alerts(tmp_path) == [f"pp: {want}"]
    assert want in _log(tmp_path)
    assert r.status(t0 + 15 * MIN)["targets"][0]["alert"] == want
    _parse_each_minute(r, tab, t0 + 16 * MIN, 24)                      # to 40 min
    assert _alerts(tmp_path) == [f"pp: {want}"]


def test_a_different_failure_restarts_the_clock(tmp_path, parser):
    t0 = time.time()
    r, t = _runner(tmp_path, [_job()]), _job()
    tab = _tab(t)
    parser.fail = "first way"
    _parse_each_minute(r, tab, t0, 10)
    parser.fail = "second way"
    _parse_each_minute(r, tab, t0 + 11 * MIN, 13)                      # to 24 min: 13 min of it
    assert _alerts(tmp_path) == []
    r._parse(tab, t0 + 26 * MIN)
    assert _alerts(tmp_path) == ["pp: failing for 15 min: second way"]


def test_a_parse_that_works_clears_it_with_one_line(tmp_path, parser):
    t0 = time.time()
    r, t = _runner(tmp_path, [_job()]), _job()
    tab = _tab(t)
    parser.fail = "boom"
    _parse_each_minute(r, tab, t0, 15)
    parser.fail = None
    _parse_each_minute(r, tab, t0 + 16 * MIN, 3)
    assert _alerts(tmp_path) == ["pp: failing for 15 min: boom", "pp: reading again"]
    assert _log(tmp_path).count("reading again") == 1
    assert r.status(t0 + 20 * MIN)["targets"][0]["alert"] is None
    # a failure that never reached the threshold clears with nothing said
    parser.fail = "brief"
    _parse_each_minute(r, tab, t0 + 30 * MIN, 5)
    parser.fail = None
    r._parse(tab, t0 + 40 * MIN)
    assert len(_alerts(tmp_path)) == 2


def test_it_survives_a_restart(tmp_path, parser):
    t0 = time.time()
    tab = _tab(_job())
    parser.fail = "boom"
    r = _runner(tmp_path, [_job()])
    _parse_each_minute(r, tab, t0, 10)                                 # 10 min, then a restart
    r = _runner(tmp_path, [_job()])
    _parse_each_minute(r, tab, t0 + 11 * MIN, 3)
    assert _alerts(tmp_path) == []
    r._parse(tab, t0 + 15 * MIN)                                       # the clock was kept
    assert _alerts(tmp_path) == ["pp: failing for 15 min: boom"]
    r = _runner(tmp_path, [_job()])                                    # and again
    assert r.status(t0 + 16 * MIN)["targets"][0]["alert"] == "failing for 15 min: boom"
    _parse_each_minute(r, tab, t0 + 16 * MIN, 20)
    assert len(_alerts(tmp_path)) == 1                                 # not said again
    parser.fail = None
    r._parse(tab, t0 + 40 * MIN)
    assert _alerts(tmp_path)[-1] == "pp: reading again"


def test_a_rest_clears_it(tmp_path, parser):
    t0 = time.time()
    r, t = _runner(tmp_path, [_job()]), _job()
    tab = _tab(t)
    parser.fail = "boom"
    _parse_each_minute(r, tab, t0, 15)
    parser.rest = t0 + 3600
    r._parse(tab, t0 + 16 * MIN)
    assert _alerts(tmp_path) == ["pp: failing for 15 min: boom", "pp: reading again"]
    assert "pp" in r.resting


def test_failing_0_is_off(tmp_path, parser):
    behaviour.set_(tmp_path, "alerts.failing_min", 0)
    behaviour.load(tmp_path)
    t0 = time.time()
    r, t = _runner(tmp_path, [_job()]), _job()
    parser.fail = "boom"
    _parse_each_minute(r, _tab(t), t0, 120)
    assert _alerts(tmp_path) == []


def test_a_live_change_of_the_failing_threshold_applies(tmp_path, parser):
    t0 = time.time()
    r, t = _runner(tmp_path, [_job()]), _job()
    tab = _tab(t)
    parser.fail = "boom"
    _parse_each_minute(r, tab, t0, 5)
    assert _alerts(tmp_path) == []
    behaviour.set_(tmp_path, "alerts.failing_min", 5)
    behaviour.load(tmp_path)
    r._parse(tab, t0 + 6 * MIN)
    assert _alerts(tmp_path) == ["pp: failing for 5 min: boom"]


def test_the_state_file_is_atomic_and_an_unreadable_one_is_empty(tmp_path):
    state = {"failing": {"pp": {"first": "x", "since": 1.0, "alerted": True, "text": "t"}},
             "no_rows": {"pp": {"ran_s": 60.0, "text": None}}}
    assert standing.save(tmp_path, state)
    assert standing.load(tmp_path) == state
    assert not list(tmp_path.glob("*.tmp"))
    (tmp_path / "alerted.json").write_text("{half")
    assert standing.load(tmp_path) == {"failing": {}, "no_rows": {}}
    (tmp_path / "alerted.json").write_text('{"failing": [1], "no_rows": {"pp": {"ran_s": "x"}}}')
    assert standing.load(tmp_path) == {"failing": {}, "no_rows": {}}
    assert not standing.save(tmp_path / "missing" / "deeper", state)      # said, never raised


# --- a running job handing over no rows ------------------------------------------
from scraper.tests.test_pacing import _Ctx, _Page  # noqa: E402  (a stand-in browser)


class _NoLinks(_Ctx):
    def new_page(self):
        self.n += 1
        return _Page([])


def _league(**kw):
    # within_h=0: the stand-in listing's links open without a kick-off read
    return config.Target(name="pp", site="paddypower", url=PP + "/epl", parser="fake",
                         post="/api/paddy/most-prices", stale_min=10_000, within_h=0,
                         matches=config.Matches(max_tabs=2, open_gap_min=2, tab_max_hours=24), **kw)


def _run(r, start, minutes):
    """15 s ticks from start for that many minutes; returns the time after."""
    for i in range(int(minutes * 4)):
        r.tick(start + 15 * i)
    return start + 15 * int(minutes * 4)


def _no_rows(tmp_path):
    return [a for a in _alerts(tmp_path) if "no rows" in a]


def test_a_page_job_with_no_rows_alerts_once_at_60_min(tmp_path, parser):
    random.seed(1)
    t0 = time.time()
    r = _runner(tmp_path, [_job()], _Ctx())
    t = _run(r, t0, 59)
    assert "pp" in r.tabs and _alerts(tmp_path) == []
    t = _run(r, t, 3)
    assert _alerts(tmp_path) == ["pp: no rows for 60 min (1 tab open)"]
    assert "no rows for 60 min (1 tab open)" in _log(tmp_path)
    assert r.status(t)["targets"][0]["alert"] == "no rows for 60 min (1 tab open)"
    t = _run(r, t, 60)
    assert len(_alerts(tmp_path)) == 1                                 # once
    parser.rows = [{"event": "A v B", "market": "most_corners", "selection": "A", "price": 2.0}]
    t = _run(r, t, 3)
    assert _alerts(tmp_path) == ["pp: no rows for 60 min (1 tab open)", "pp: delivering again"]
    assert _log(tmp_path).count("delivering again") == 1
    assert r.status(t)["targets"][0]["alert"] is None
    parser.rows = []
    _run(r, t, 59)                                                     # the clock started again
    assert len(_alerts(tmp_path)) == 2


def test_a_listing_job_counts_only_with_a_match_tab_open(tmp_path, parser):
    random.seed(1)
    t0 = time.time()
    r = _runner(tmp_path, [_league()], _NoLinks())
    _run(r, t0, 180)
    assert set(r.tabs) == {"pp:list"} and _alerts(tmp_path) == []      # listing tabs alone: never
    tmp2 = tmp_path / "with-matches"
    tmp2.mkdir()
    r = _runner(tmp2, [_league()], _Ctx())
    _run(r, t0, 75)
    assert any(":m:" in k for k in r.tabs)
    assert [a.split(" (")[0] for a in _no_rows(tmp2)] == ["pp: no rows for 60 min"]


def test_no_clock_while_resting(tmp_path, parser):
    random.seed(1)
    t0 = time.time()
    parser.rest = t0 + 10 * 3600
    r = _runner(tmp_path, [_job()], _Ctx())
    _run(r, t0, 180)
    assert "pp" in r.resting and _alerts(tmp_path) == []


def test_no_clock_outside_the_window(tmp_path, parser):
    from datetime import datetime
    random.seed(1)
    t0 = time.time()
    h = datetime.fromtimestamp(t0).hour
    window = f"{(h + 6) % 24:02d}:00-{(h + 7) % 24:02d}:00"           # hours away all run long
    r = _runner(tmp_path, [_job(window=window)], _Ctx())
    _run(r, t0, 180)
    assert r.tabs == {} and _alerts(tmp_path) == []


def test_no_clock_while_the_site_is_paused(tmp_path, parser):
    random.seed(1)
    t0 = time.time()
    r = _runner(tmp_path, [_job()], _Ctx())
    t = _run(r, t0, 30)                                                # 30 min with the page open
    r.guard.start_cooloff("paddypower", "test", t)
    t = _run(r, t, 180)
    assert _no_rows(tmp_path) == []
    # the half hour before the pause was kept, not lost: paused, not reset
    assert 25 * MIN <= r.job_alerts.no_rows["pp"]["ran_s"] <= 31 * MIN


def test_not_sent_while_a_failing_alert_stands(tmp_path, parser):
    random.seed(1)
    t0 = time.time()
    parser.fail = "boom"
    r = _runner(tmp_path, [_job()], _Ctx())
    t = _run(r, t0, 90)
    assert _alerts(tmp_path) == ["pp: failing for 15 min: boom"]
    parser.fail = None                                                 # reads again, still no rows
    _run(r, t, 2)
    assert _alerts(tmp_path)[1:] == ["pp: reading again", "pp: no rows for 60 min (1 tab open)"]


def test_the_no_rows_clock_survives_a_restart(tmp_path, parser):
    random.seed(1)
    t0 = time.time()
    r = _runner(tmp_path, [_job()], _Ctx())
    t = _run(r, t0, 40)
    r = _runner(tmp_path, [_job()], _Ctx())                            # the service's own restart
    t = _run(r, t + 30, 30)
    assert _no_rows(tmp_path) == ["pp: no rows for 60 min (1 tab open)"]
    r = _runner(tmp_path, [_job()], _Ctx())                            # and again: not said again
    _run(r, t + 30, 30)
    assert len(_no_rows(tmp_path)) == 1
    assert json.loads((tmp_path / "alerted.json").read_text())["no_rows"]["pp"]["text"]


def test_no_rows_0_is_off_and_a_live_change_applies(tmp_path, parser):
    random.seed(1)
    behaviour.set_(tmp_path, "alerts.no_rows_min", 0)
    behaviour.load(tmp_path)
    t0 = time.time()
    r = _runner(tmp_path, [_job()], _Ctx())
    t = _run(r, t0, 120)
    assert _alerts(tmp_path) == []
    behaviour.set_(tmp_path, "alerts.no_rows_min", 90)
    behaviour.load(tmp_path)
    _run(r, t, 1)
    assert _alerts(tmp_path) == ["pp: no rows for 90 min (1 tab open)"]


def test_a_fetch_job_is_left_to_min_rows(tmp_path, parser):
    t = config.Target(name="tips", site="generic", url="https://tips.example/a", parser="fake",
                      post="hub:tips", mode="fetch", every_min=5)
    r = _runner(tmp_path, [t], _Ctx())
    random.seed(1)
    _run(r, time.time(), 180)
    assert _no_rows(tmp_path) == []


# --- the betslip tripwire (12 Nov 2026) ---------------------------------------------
class _Seen:
    """A tab's capture holding what its page received."""
    def __init__(self, rows):
        self.rows = rows

    def since(self, ts):
        return [r for r in self.rows if r[0] >= ts]


SLIP = "https://www.paddypower.com/www/sports/fixedodds/transactional/v1/implyBets"


def test_a_selection_in_paddys_betslip_is_alerted_once(tmp_path):
    """Paddy's page prices its betslip at every load (implyBets). A slip with
    a selection in it is something the Scraper never means to have: said once
    per run, in the alerts and the job's log."""
    t = _job()
    r = _runner(tmp_path, [t])
    tab = SimpleNamespace(target=t, url=t.url, capture=_Seen([
        (10.0, "http", SLIP, json.dumps({"betCombinations": []})),                     # an empty slip: nothing
        (11.0, "http", PP + "/other", "{}"),
    ]))
    r.tabs["pp"] = tab
    r._watch_betslip(20.0)
    assert not (tmp_path / "alerts.log").exists()
    tab.capture.rows.append((30.0, "http", SLIP, json.dumps({"betCombinations": [{"betType": "SINGLE"}]})))
    r._watch_betslip(40.0)
    r._watch_betslip(50.0)
    tab.capture.rows.append((60.0, "http", SLIP, json.dumps({"betCombinations": [{"betType": "SINGLE"}]})))
    r._watch_betslip(70.0)
    lines = (tmp_path / "alerts.log").read_text().splitlines()
    assert len(lines) == 1 and "paddypower: the betslip is not empty" in lines[0]
    assert "betslip is not empty" in joblog.read(tmp_path, "pp", 5)[-1]["msg"]


def test_the_betslip_watch_never_raises_on_a_payload_it_cannot_read(tmp_path):
    t = _job()
    r = _runner(tmp_path, [t])
    r.tabs["pp"] = SimpleNamespace(target=t, url=t.url, capture=_Seen([(1.0, "http", SLIP, "not json"), (2.0, "http", SLIP, "[1, 2]")]))
    r._watch_betslip(5.0)
    assert not (tmp_path / "alerts.log").exists()


def test_the_pages_own_featured_bet_builders_are_not_the_betslip(tmp_path):
    """A Paddy match page prices its "Most Popular Bet Builders" cards with the
    same call (implyBets), marked pricePolicy=SUGGESTED: that is not the
    betslip, and it alerted falsely on 12 Nov 2026 (England v Croatia)."""
    t = _job()
    r = _runner(tmp_path, [t])
    body = json.dumps({"betCombinations": [{"betType": "FIVEFOLD", "isSGM": True}] * 5})
    r.tabs["pp"] = SimpleNamespace(target=t, url=t.url, capture=_Seen([
        (1.0, "http", SLIP + "?scope=MULTIPLES&pricePolicy=SUGGESTED&_ak=x", body)]))
    r._watch_betslip(5.0)
    assert not (tmp_path / "alerts.log").exists()
    r.tabs["pp"].capture.rows.append((6.0, "http", SLIP + "?_ak=x", json.dumps({"betCombinations": [{"betType": "SINGLE"}]})))
    r._watch_betslip(9.0)
    assert "the betslip is not empty" in (tmp_path / "alerts.log").read_text()
