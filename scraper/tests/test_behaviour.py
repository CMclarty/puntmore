"""The Scraper's behaviour catalogue (scraper/behaviour.py): every default is
the constant it replaced, values are checked against their kind and bounds,
the overrides file round-trips, a bad file never stops anything, and the
modules really read the catalogue. No browser."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scraper import behaviour
from scraper import browser, login, schedule
from scraper.guard import Guard
from scraper.sites import SITES

# Pinned from the constants as they were on this branch before the catalogue
# (schedule.py, sites.py, guard.py's defaults, browser.py's click / read_down /
# settle / dismiss_consent / wait_for_feed, login.py, __main__.py,
# parsers/bb_saves.py, runner.py's back-off jitter). Written out by hand on
# purpose: a test that imported the old values could not catch a changed one.
OLD = {
    # sites.SITES
    "paddypower.per_hour": 15, "paddypower.per_day": 120,
    "paddypower.load_gap_s": 90.0, "paddypower.quiet_hours": True,
    "bookiebashing.per_hour": 8, "bookiebashing.per_day": 120,
    "bookiebashing.load_gap_s": 60.0, "bookiebashing.quiet_hours": False,
    "generic.per_hour": 60, "generic.per_day": 400,
    "generic.load_gap_s": 20.0, "generic.quiet_hours": False,
    # guard.Guard's defaults; login.MAX_SIGNINS, SIGNIN_WINDOW_H; sites.BACKOFF_MIN;
    # runner's random.uniform(0.9, 1.2) on a back-off
    "quiet.hours": (1, 7), "cooloff.hours": 24.0,
    "login.max_signins": 3, "login.window_h": 6,
    "backoff.min": (15, 120), "backoff.jitter_x": (0.9, 1.2),
    # schedule.py; __main__.RESTART_H
    "load.any_gap_s": (20, 45), "load.site_gap_x": (1.0, 1.8),
    "reload.jitter_x": (0.75, 1.35), "relist.jitter_x": (0.9, 1.3),
    "open_gap.jitter_x": (1.0, 1.6), "recycle.hours": 0.5, "recycle.jitter_x": (0.85, 1.2),
    "fetch.default_min": 60, "fetch.retry_s": 300, "restart.hours": (1.5, 2.0),
    # browser.settle, browser.dismiss_consent
    "arrive.wait_ms": (2500, 5000), "arrive.passes": (1, 3), "arrive.move_steps": (8, 25),
    "arrive.scroll_px": (250, 700), "arrive.pause_ms": (700, 2200),
    "arrive.back_up_px": (800, 2000), "arrive.after_consent_ms": (600, 1500),
    # schedule.CLICK_EVERY_S, CLICK_JITTER; browser.click, browser.wait_for_feed
    "click.every_s": 600, "click.every_jitter_x": (0.8, 1.4),
    "click.before_move_ms": (300, 900), "click.move_steps": (12, 30),
    "click.before_press_ms": (250, 900), "click.press_ms": (50, 140),
    "click.aim_x": (0.3, 0.7), "click.aim_y": (0.35, 0.65),
    "click.after_feed_ms": (1500, 3000),
    # browser.read_down
    "read.passes": (2, 4), "read.move_steps": (8, 25), "read.scroll_px": (300, 700),
    "read.pause_ms": (900, 2500), "read.back_up_px": (2500, 4000),
    "read.back_up_pause_ms": (500, 1200),
    # parsers/bb_saves.py: its 48-hour window and day's rest are now the
    # "Games" pair, read by every job that reads games (12 Nov 2026)
    "games.within_h": 48, "games.look_again_s": 86400,
    "bb.call_budget_s": 40.0, "bb.update_every_s": 1800, "bb.update_tries": 3,
    "bb.xsaves_retries": 3, "bb.settle_s": 0.25,
    # new with the Scraper's own alerts (12 Nov 2026, Callum); no constant before
    "alerts.stuck_s": 180, "alerts.failing_min": 15, "alerts.no_rows_min": 60,
}


@pytest.fixture(autouse=True)
def _defaults(tmp_path_factory):
    """Every test starts and ends on the defaults (the live values are module state)."""
    empty = tmp_path_factory.mktemp("no-overrides")
    behaviour.load(empty)
    yield
    behaviour.load(empty)


def _types(v):
    return tuple(type(x) for x in v) if isinstance(v, tuple) else type(v)


# --- the catalogue -------------------------------------------------------------
def test_every_default_is_the_constant_it_replaced():
    built = {s.key: s.default for s in behaviour.SETTINGS}
    assert built == OLD
    for key, value in OLD.items():
        assert behaviour.get(key) == value, key
        assert _types(behaviour.get(key)) == _types(value), key     # 90.0 stays a float, 15 an int


def test_the_catalogue_reads_as_a_description():
    keys = [s.key for s in behaviour.SETTINGS]
    assert len(keys) == len(set(keys))
    groups = list(dict.fromkeys(s.group for s in behaviour.SETTINGS))
    assert groups == ["Games", "Limits, per site", "Limits, all sites", "Spacing of page loads",
                      "Arriving on a page", "Clicks", "Reading down a page",
                      "Bookie Bashing bet builder", "Alerts"]
    for s in behaviour.SETTINGS:
        assert s.kind in ("int", "float", "bool", "range", "hours"), s.key
        assert s.says and s.says[0].isupper(), s.key
        if s.kind != "bool":
            assert s.unit, s.key
            assert s.lo <= s.hi, s.key
            for x in (s.default if isinstance(s.default, tuple) else (s.default,)):
                assert s.lo <= x <= s.hi, s.key
    assert behaviour.__doc__ and "behaviour.toml" in behaviour.__doc__


def test_the_per_site_settings_are_the_sites_in_code():
    per_site = {s.key.split(".")[0] for s in behaviour.SETTINGS if s.group == "Limits, per site"}
    assert per_site == set(SITES)


def test_an_unknown_key_is_an_error_naming_it():
    with pytest.raises(KeyError, match="no.such"):
        behaviour.get("no.such")


# --- kinds, bounds and refusals -------------------------------------------------
@pytest.mark.parametrize("key, text, want", [
    ("paddypower.per_hour", "12", 12),
    ("paddypower.per_hour", 0, 0),
    ("paddypower.per_hour", "20.0", 20),
    ("cooloff.hours", "12", 12.0),
    ("cooloff.hours", 1.5, 1.5),
    ("paddypower.quiet_hours", "false", False),
    ("paddypower.quiet_hours", "True", True),
    ("generic.quiet_hours", True, True),
    ("load.any_gap_s", "30-60", (30, 60)),
    ("load.any_gap_s", " 30 - 60 ", (30, 60)),
    ("load.any_gap_s", [30, 60], (30, 60)),
    ("load.any_gap_s", "[30, 60]", (30, 60)),
    ("reload.jitter_x", "0.8-1.2", (0.8, 1.2)),
    ("reload.jitter_x", [1, 1], (1.0, 1.0)),
    ("quiet.hours", "1-7", (1, 7)),
    ("quiet.hours", "23-6", (23, 6)),          # past midnight
    ("quiet.hours", "0-0", (0, 0)),            # none
])
def test_values_parse_from_text_and_from_values(key, text, want):
    got = behaviour.parse(key, text)
    assert got == want
    assert _types(got) == _types(want)


@pytest.mark.parametrize("key, value, says", [
    ("paddypower.per_hour", -1, "between"),
    ("paddypower.per_hour", 601, "between"),
    ("paddypower.per_hour", "1.5", "whole number"),
    ("paddypower.per_hour", "lots", "number"),
    ("paddypower.per_hour", True, "number"),
    ("paddypower.load_gap_s", 7 * 86400, "between"),
    ("paddypower.quiet_hours", "maybe", "true or false"),
    ("paddypower.quiet_hours", 1, "true or false"),
    ("load.any_gap_s", "45-20", "low"),
    ("load.any_gap_s", "20", "pair"),
    ("load.any_gap_s", [1, 2, 3], "pair"),
    ("load.any_gap_s", "-5-20", "pair"),
    ("load.any_gap_s", "20-99999", "between"),
    ("read.pause_ms", "900.5-2500", "whole number"),
    ("quiet.hours", "1-25", "between"),
    ("quiet.hours", "7", "pair"),
])
def test_a_wrong_value_is_refused_with_a_message_naming_the_setting(key, value, says):
    with pytest.raises(behaviour.BehaviourError) as err:
        behaviour.parse(key, value)
    assert key in str(err.value) and says in str(err.value)


def test_an_unknown_setting_is_refused():
    with pytest.raises(behaviour.BehaviourError, match="no.such"):
        behaviour.parse("no.such", 1)


# --- the overrides file ---------------------------------------------------------
def test_set_and_reset_round_trip_through_the_file_keeping_only_changes(tmp_path):
    f = tmp_path / "behaviour.toml"
    assert behaviour.set_(tmp_path, "paddypower.per_hour", "10") == 10
    assert behaviour.set_(tmp_path, "load.any_gap_s", "30-60") == (30, 60)
    assert behaviour.set_(tmp_path, "generic.quiet_hours", "true") is True
    assert behaviour.get("paddypower.per_hour") == 15          # the file, not this process
    assert behaviour.load(tmp_path) == []
    assert behaviour.get("paddypower.per_hour") == 10
    assert behaviour.get("load.any_gap_s") == (30, 60)
    assert behaviour.get("generic.quiet_hours") is True
    assert behaviour.changed() == {"paddypower.per_hour": 10, "load.any_gap_s": (30, 60),
                                   "generic.quiet_hours": True}
    import tomllib
    assert tomllib.loads(f.read_text()) == {"paddypower.per_hour": 10, "load.any_gap_s": [30, 60],
                                            "generic.quiet_hours": True}
    # Setting a default back removes it from the file.
    behaviour.set_(tmp_path, "paddypower.per_hour", 15)
    assert "paddypower.per_hour" not in tomllib.loads(f.read_text())
    behaviour.reset(tmp_path, "load.any_gap_s")
    assert tomllib.loads(f.read_text()) == {"generic.quiet_hours": True}
    assert behaviour.load(tmp_path) == []
    assert behaviour.get("load.any_gap_s") == (20, 45)          # back to the default
    assert behaviour.get("paddypower.per_hour") == 15
    behaviour.reset(tmp_path, None)
    assert tomllib.loads(f.read_text()) == {}
    behaviour.load(tmp_path)
    assert behaviour.changed() == {}


def test_a_refused_set_writes_nothing(tmp_path):
    behaviour.set_(tmp_path, "games.within_h", 24)
    before = (tmp_path / "behaviour.toml").read_text()
    with pytest.raises(behaviour.BehaviourError, match="paddypower.per_hour"):
        behaviour.set_(tmp_path, "paddypower.per_hour", -3)
    with pytest.raises(behaviour.BehaviourError, match="no.such"):
        behaviour.set_(tmp_path, "no.such", 1)
    with pytest.raises(behaviour.BehaviourError, match="no.such"):
        behaviour.reset(tmp_path, "no.such")
    assert (tmp_path / "behaviour.toml").read_text() == before
    assert not list(tmp_path.glob(".behaviour.toml.*"))          # no temp file left


def test_the_file_is_flat_and_says_what_each_line_is(tmp_path):
    behaviour.set_(tmp_path, "click.every_s", 900)
    text = (tmp_path / "behaviour.toml").read_text()
    assert '"click.every_s" = 900' in text
    assert "default 600" in text and "behaviour.py" in text


def test_a_hand_written_dotted_key_reads_too(tmp_path):
    (tmp_path / "behaviour.toml").write_text("[paddypower]\nper_hour = 9\n")
    assert behaviour.load(tmp_path) == []
    assert behaviour.get("paddypower.per_hour") == 9


def test_an_unreadable_file_keeps_the_running_values_and_is_reported(tmp_path):
    behaviour.set_(tmp_path, "click.every_s", 900)
    behaviour.load(tmp_path)
    (tmp_path / "behaviour.toml").write_text('"click.every_s" = [oops\n')
    problems = behaviour.load(tmp_path)
    assert len(problems) == 1 and "behaviour.toml" in problems[0]
    assert behaviour.get("click.every_s") == 900                # kept, not reset to default
    # set_ refuses to build on a file it cannot read; reset --all mends it.
    with pytest.raises(behaviour.BehaviourError, match="does not load"):
        behaviour.set_(tmp_path, "click.every_s", 700)
    behaviour.reset(tmp_path, None)
    assert behaviour.load(tmp_path) == [] and behaviour.get("click.every_s") == 600


def test_an_unknown_key_or_a_bad_value_in_the_file_keeps_that_value_and_is_reported(tmp_path):
    behaviour.set_(tmp_path, "read.passes", "3-5")
    behaviour.load(tmp_path)
    (tmp_path / "behaviour.toml").write_text(
        '"no.such" = 1\n"read.passes" = [9, 2]\n"games.within_h" = 24\n')
    problems = behaviour.load(tmp_path)
    assert len(problems) == 2
    assert any("no.such" in p for p in problems) and any("read.passes" in p for p in problems)
    assert behaviour.get("read.passes") == (3, 5)               # its running value
    assert behaviour.get("games.within_h") == 24                # the good line applies
    # reset of a key the catalogue does not know but the file holds: removes it
    behaviour.reset(tmp_path, "no.such")
    assert "no.such" not in (tmp_path / "behaviour.toml").read_text()


def test_a_missing_file_is_every_default(tmp_path):
    assert behaviour.load(tmp_path / "nowhere") == []
    assert {s.key: behaviour.get(s.key) for s in behaviour.SETTINGS} == OLD


def test_a_busy_lock_is_an_error_not_a_hang(tmp_path):
    import fcntl
    with open(tmp_path / "behaviour.toml.lock", "a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        with pytest.raises(behaviour.BehaviourError, match="being changed"):
            behaviour.set_(tmp_path, "click.every_s", 900, timeout=0.2)


def test_listing_shows_value_default_and_changed(tmp_path):
    behaviour.set_(tmp_path, "games.within_h", 24)
    behaviour.load(tmp_path)
    rows = {r["key"]: r for r in behaviour.listing()}
    assert list(rows) == [s.key for s in behaviour.SETTINGS]
    r = rows["games.within_h"]
    assert (r["value"], r["default"], r["changed"], r["group"], r["kind"]) == (
        24, 48, True, "Games", "int")
    assert rows["load.any_gap_s"]["value"] == [20, 45] and not rows["load.any_gap_s"]["changed"]
    json.dumps(list(rows.values()))                             # JSON-ready


# --- the "Games" pair: how far ahead a game is read (Callum, 12 Nov 2026) ----------
def test_games_is_the_first_group_and_holds_the_window_and_the_look_again():
    first = [s for s in behaviour.SETTINGS if s.group == "Games"]
    assert behaviour.SETTINGS[:2] == tuple(first)
    w, a = first
    assert (w.key, w.kind, w.default, w.lo, w.hi, w.unit) == ("games.within_h", "int", 48, 0, 720, "h")
    assert w.says == "Only games kicking off within this many hours are read (0: no limit)"
    assert (a.key, a.kind, a.default, a.lo, a.hi) == ("games.look_again_s", "int", 86400, 600, 604800)
    assert a.says == ("With no game that near, a job rests at most this long before looking at "
                      "the fixtures again")
    assert behaviour.parse("games.within_h", "0") == 0          # no limit
    with pytest.raises(behaviour.BehaviourError, match="games.within_h"):
        behaviour.parse("games.within_h", 721)
    with pytest.raises(behaviour.BehaviourError, match="games.look_again_s"):
        behaviour.parse("games.look_again_s", 599)


def test_the_bb_window_keys_are_gone_and_an_old_file_naming_them_is_reported_and_ignored(tmp_path):
    keys = {s.key for s in behaviour.SETTINGS}
    assert "bb.within_h" not in keys and "bb.look_again_s" not in keys
    (tmp_path / "behaviour.toml").write_text('"bb.within_h" = 24\n"bb.look_again_s" = 600\n'
                                             '"click.every_s" = 900\n')
    problems = behaviour.load(tmp_path)
    assert problems == [f"{tmp_path / 'behaviour.toml'}: bb.within_h: no such setting - ignored",
                        f"{tmp_path / 'behaviour.toml'}: bb.look_again_s: no such setting - ignored"]
    assert behaviour.get("games.within_h") == 48 and behaviour.get("games.look_again_s") == 86400
    assert behaviour.get("click.every_s") == 900                # the rest of the file applies
    with pytest.raises(behaviour.BehaviourError, match="bb.within_h: no such setting"):
        behaviour.set_(tmp_path, "bb.within_h", 24)


# --- the modules read the catalogue ---------------------------------------------
def _use(tmp_path, **values):
    for key, value in values.items():
        behaviour.set_(tmp_path, key.replace("__", "."), value)
    assert behaviour.load(tmp_path) == []


def test_the_schedule_reads_the_catalogue(tmp_path):
    _use(tmp_path, click__every_s=100, click__every_jitter_x="1-1",
         reload__jitter_x="2-2", relist__jitter_x="3-3", open_gap__jitter_x="0.5-0.5",
         recycle__hours=2, recycle__jitter_x="1-1", fetch__default_min=7,
         paddypower__load_gap_s=5, load__site_gap_x="1-1", load__any_gap_s="0-0")
    assert schedule.next_clicks_at(1000) == 1100
    assert schedule.reload_at(0, 10) == 1200
    assert schedule.relist_at(0, 10) == 1800
    assert schedule.next_open_at(0, 10) == 300
    assert schedule.recycle_at(0) == 7200
    assert schedule.next_fetch_at(0, None) == 7 * 60 * 2
    p = schedule.Pacer()
    p.spaced("paddypower", 1000)
    assert p.next_load_at["paddypower"] == 1005 and p.next_any_load == 1000


class _Mouse:
    def __init__(self, said):
        self.said = said

    def move(self, x, y, steps):
        self.said.append(("move", x, y, steps))

    def wheel(self, dx, dy):
        self.said.append(("wheel", dy))

    def click(self, x, y, delay):
        self.said.append(("click", x, y, delay))


class _Page:
    def __init__(self):
        self.said = []
        self.mouse = _Mouse(self.said)

    def wait_for_timeout(self, ms):
        self.said.append(("wait", ms))


class _Loc:
    def scroll_into_view_if_needed(self, timeout):
        pass

    def bounding_box(self):
        return {"x": 100, "y": 200, "width": 40, "height": 20}


def test_the_click_helper_reads_the_catalogue(tmp_path):
    _use(tmp_path, click__before_move_ms="7-7", click__move_steps="3-3",
         click__before_press_ms="8-8", click__press_ms="9-9",
         click__aim_x="0.25-0.25", click__aim_y="0.5-0.5")
    page = _Page()
    browser.click(page, _Loc())
    assert page.said == [("wait", 7), ("move", 110.0, 210.0, 3), ("wait", 8), ("click", 110.0, 210.0, 9)]


def test_reading_down_and_arriving_read_the_catalogue(tmp_path):
    _use(tmp_path, read__passes="1-1", read__move_steps="4-4", read__scroll_px="5-5",
         read__pause_ms="6-6", read__back_up_px="7-7", read__back_up_pause_ms="8-8",
         arrive__wait_ms="1-1", arrive__passes="1-1", arrive__move_steps="2-2",
         arrive__scroll_px="3-3", arrive__pause_ms="4-4", arrive__back_up_px="5-5")
    page = _Page()
    browser.read_down(page)
    assert [s for s in page.said if s[0] != "move"] == [("wheel", 5), ("wait", 6), ("wheel", -7), ("wait", 8)]
    assert [s[3] for s in page.said if s[0] == "move"] == [4]
    page = _Page()
    browser.settle(page)
    assert [s for s in page.said if s[0] != "move"] == [("wait", 1), ("wheel", 3), ("wait", 4), ("wheel", -5)]
    assert [s[3] for s in page.said if s[0] == "move"] == [2]


def test_bb_saves_reads_the_catalogue(tmp_path):
    import importlib
    bb_saves = importlib.import_module("scraper.parsers.bb_saves")   # the package exports the function
    _use(tmp_path, games__within_h=2, games__look_again_s=7200)
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    soon = (now + timedelta(hours=3), "A v B")
    r = bb_saves._rest(now, soon)
    assert r.until == (now + timedelta(hours=1)).timestamp() and "within 2 hours" in r.why
    far = (now + timedelta(days=3), "C v D")
    assert bb_saves._rest(now, far).until == now.timestamp() + 7200
    _use(tmp_path, games__within_h=0)                           # no limit: nothing to wait for
    assert bb_saves._rest(now, None).why == "no games listed under the configured competitions"


def test_login_reads_the_catalogue(tmp_path):
    login.record_sent(tmp_path, "bookiebashing", 1000.0)
    assert login.sent(tmp_path, "bookiebashing", 1000.0 + 2 * 3600) == [1000.0]
    _use(tmp_path, login__window_h=1)
    assert login.sent(tmp_path, "bookiebashing", 1000.0 + 2 * 3600) == []


def test_the_guard_is_built_from_the_catalogue(tmp_path):
    g = Guard(tmp_path / "a.sqlite3")
    assert g.limit("paddypower") == (15, 120) and g.quiet == (1, 7) and g.cooloff_hours == 24.0
    _use(tmp_path, paddypower__per_hour=2, quiet__hours="2-5", cooloff__hours=6)
    g = Guard(tmp_path / "b.sqlite3")
    assert g.limit("paddypower") == (2, 120) and g.quiet == (2, 5) and g.cooloff_hours == 6.0
    now = datetime(2026, 11, 12, 12).timestamp()
    g.record_load("paddypower", "u", now)
    g.record_load("paddypower", "u", now)
    assert g.can_load("paddypower", now) == (False, "hourly budget (2) spent")


def test_a_site_with_no_budget_loads_nothing(tmp_path):
    _use(tmp_path, generic__per_day=0)
    g = Guard(tmp_path / "g.sqlite3")
    assert g.can_load("generic", datetime(2026, 11, 12, 12).timestamp())[0] is False


# --- the command line -----------------------------------------------------------
@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    from scraper.__main__ import main
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))

    def run(*argv):
        code = 0
        try:
            main(["behaviour", *argv])
        except SystemExit as exc:
            code = exc.code
        out = capsys.readouterr()
        return code, out.out, out.err
    return run


def test_cli_lists_sets_and_resets(cli, tmp_path):
    code, out, _ = cli()
    assert code == 0 and "paddypower.per_hour" in out and "Clicks" in out
    code, out, _ = cli("set", "paddypower.per_hour", "10")
    assert code == 0 and "10" in out
    code, out, _ = cli("set", "load.any_gap_s", "30-60")
    assert code == 0
    code, out, _ = cli()
    line = next(x for x in out.splitlines() if "paddypower.per_hour" in x)
    assert "10" in line and "15" in line and "*" in line         # value, default, changed mark
    assert "2 setting(s) changed" in out
    code, out, _ = cli("--json")
    body = json.loads(out)
    rows = {r["key"]: r for r in body["settings"]}
    assert rows["paddypower.per_hour"]["value"] == 10 and rows["load.any_gap_s"]["value"] == [30, 60]
    assert body["changed"] == 2 and body["problems"] == []
    code, out, _ = cli("reset", "paddypower.per_hour", "--json")
    assert code == 0 and json.loads(out)["value"] == 15
    code, out, _ = cli("reset", "--all")
    assert code == 0
    code, out, _ = cli("--json")
    assert json.loads(out)["changed"] == 0


def test_cli_refuses_with_the_setting_named(cli, tmp_path):
    code, _, err = cli("set", "paddypower.per_hour", "-4")
    assert code == 2 and "paddypower.per_hour" in err
    code, out, _ = cli("set", "no.such", "1", "--json")
    assert code == 2 and "no.such" in json.loads(out)["error"]
    assert not (tmp_path / "behaviour.toml").exists()


def test_cli_reports_a_bad_file_and_still_lists(cli, tmp_path):
    (tmp_path / "behaviour.toml").write_text("not toml [\n")
    code, out, err = cli()
    assert code == 0 and "behaviour.toml" in err and "paddypower.per_hour" in out
    code, out, _ = cli("--json")
    assert json.loads(out)["problems"]


def test_start_loads_the_overrides_once_and_prints_a_problem(tmp_path, monkeypatch, capsys):
    from scraper import __main__ as m
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    (tmp_path / "targets.toml").write_text("")
    behaviour.set_(tmp_path, "click.every_s", 1200)
    m.main(["check"])
    assert behaviour.get("click.every_s") == 1200
    (tmp_path / "behaviour.toml").write_text("oops = [\n")
    m.main(["check"])
    assert "behaviour.toml" in capsys.readouterr().out
    assert behaviour.get("click.every_s") == 1200                # kept


# --- live: the running service follows the file -----------------------------------
def _live(tmp_path):
    """A runner on a stand-in browser (test_pacing's), with no targets."""
    from scraper.tests.test_pacing import _runner
    return _runner(tmp_path, [])


def _moved(p):
    """Move a hand-written file's mtime on, as a later edit would."""
    import os
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def _log(tmp_path):
    from scraper import joblog
    return [x["msg"] for x in joblog.read(tmp_path, "behaviour")]


def test_a_change_is_picked_up_without_a_restart_and_logged_once(tmp_path, capsys):
    from scraper.runner import TARGETS_CHECK_S
    r = _live(tmp_path)
    now = datetime(2026, 11, 12, 12).timestamp()
    r.tick(now)                                                 # nothing changed: nothing said
    assert _log(tmp_path) == [] and "behaviour" not in capsys.readouterr().out
    behaviour.set_(tmp_path, "paddypower.per_hour", 4)
    behaviour.set_(tmp_path, "load.any_gap_s", "1-2")
    behaviour.set_(tmp_path, "click.press_ms", "10-20")
    r.tick(now + 1)                                             # not due yet: still the old values
    assert behaviour.get("paddypower.per_hour") == 15
    r.tick(now + TARGETS_CHECK_S + 1)
    assert behaviour.get("paddypower.per_hour") == 4
    assert behaviour.get("load.any_gap_s") == (1, 2) and behaviour.get("click.press_ms") == (10, 20)
    assert r.guard.limit("paddypower") == (4, 120)              # the guard follows
    want = ["paddypower.per_hour: 15 -> 4", "load.any_gap_s: 20-45 -> 1-2", "click.press_ms: 50-140 -> 10-20"]
    assert _log(tmp_path) == want
    out = capsys.readouterr().out
    assert all(f"behaviour: {line}" in out for line in want)
    r.tick(now + 2 * TARGETS_CHECK_S + 2)                       # the file has not moved: said once
    r.tick(now + 3 * TARGETS_CHECK_S + 3)
    assert _log(tmp_path) == want and "behaviour" not in capsys.readouterr().out


def test_the_guard_enforces_the_new_budget_and_quiet_hours(tmp_path):
    r = _live(tmp_path)
    noon = datetime(2026, 11, 12, 12).timestamp()
    r.guard.record_load("paddypower", "u", noon - 60)
    r.guard.record_load("paddypower", "u", noon - 30)
    assert r.guard.can_load("paddypower", noon) == (True, "")
    behaviour.set_(tmp_path, "paddypower.per_hour", 2)
    r._reload_behaviour(noon)
    assert r.guard.can_load("paddypower", noon) == (False, "hourly budget (2) spent")
    behaviour.set_(tmp_path, "paddypower.per_hour", 15)
    behaviour.set_(tmp_path, "quiet.hours", "11-13")
    behaviour.set_(tmp_path, "cooloff.hours", 6)
    r._reload_behaviour(noon + 3600)
    assert r.guard.can_load("paddypower", noon) == (False, "quiet hours")
    assert r._paused("paddypower", noon) == "quiet hours"
    assert r._paused("generic", noon) is None                  # its quiet_hours is off
    assert r.guard.cooloff_hours == 6.0


def test_a_broken_file_keeps_the_values_and_is_said_once(tmp_path, capsys):
    r = _live(tmp_path)
    now = datetime(2026, 11, 12, 12).timestamp()
    behaviour.set_(tmp_path, "paddypower.per_hour", 4)
    r._reload_behaviour(now)
    capsys.readouterr()
    p = behaviour.path(tmp_path)
    p.write_text("oops = [\n")
    _moved(p)
    for i in range(1, 4):                                       # three checks, one edit
        r._reload_behaviour(now + i * 61)
    assert behaviour.get("paddypower.per_hour") == 4 and r.guard.limit("paddypower") == (4, 120)
    out = capsys.readouterr().out
    assert out.count("does not load") == 1 and "keeping the values in use" in out
    assert sum("does not load" in x for x in _log(tmp_path)) == 1
    assert r.status(now)["behaviour"]["problems"] and r.status(now)["behaviour"]["changed"] == 1
    p.write_text('"paddypower.per_hour" = -3\n"click.every_s" = 900\n')       # one line refused
    _moved(p)
    for i in range(4, 7):
        r._reload_behaviour(now + i * 61)
    assert behaviour.get("paddypower.per_hour") == 4 and behaviour.get("click.every_s") == 900
    out = capsys.readouterr().out
    assert out.count("paddypower.per_hour: -3 is not between") == 1
    assert "behaviour: click.every_s: 600 -> 900" in out
    assert any("paddypower.per_hour: -3" in x for x in _log(tmp_path))       # both said in the log
    assert "click.every_s: 600 -> 900" in _log(tmp_path)
    behaviour.set_(tmp_path, "paddypower.per_hour", 5)          # mended: the problem is gone
    r._reload_behaviour(now + 8 * 61)
    assert r.status(now)["behaviour"] == {"changed": 2, "problems": []}


def test_deleting_the_file_puts_every_setting_back_to_its_default(tmp_path):
    r = _live(tmp_path)
    now = datetime(2026, 11, 12, 12).timestamp()
    behaviour.set_(tmp_path, "paddypower.per_hour", 4)
    behaviour.set_(tmp_path, "quiet.hours", "2-5")
    r._reload_behaviour(now)
    assert r.guard.quiet == (2, 5)
    behaviour.path(tmp_path).unlink()
    r._reload_behaviour(now + 61)
    assert behaviour.changed() == {}
    assert r.guard.limit("paddypower") == (15, 120) and r.guard.quiet == (1, 7)
    assert _log(tmp_path)[-2:] == ["paddypower.per_hour: 4 -> 15", "quiet.hours: 2-5 -> 1-7"]


def test_status_carries_the_count_of_changed_settings(tmp_path):
    r = _live(tmp_path)
    now = datetime(2026, 11, 12, 12).timestamp()
    assert r.status(now)["behaviour"] == {"changed": 0, "problems": []}
    behaviour.set_(tmp_path, "paddypower.per_hour", 4)
    behaviour.set_(tmp_path, "generic.quiet_hours", True)
    r._reload_behaviour(now)
    r._report(now)
    body = json.loads((tmp_path / "status.json").read_text())
    assert body["behaviour"] == {"changed": 2, "problems": []}
    assert {s["site"] for s in body["sites"]} == set(SITES)    # the rest as it was


def test_a_runner_started_on_a_changed_file_says_nothing_new(tmp_path, capsys):
    behaviour.set_(tmp_path, "click.every_s", 900)
    behaviour.load(tmp_path)                                    # what start does (__main__)
    capsys.readouterr()
    r = _live(tmp_path)
    r.tick(datetime(2026, 11, 12, 12).timestamp())
    assert behaviour.get("click.every_s") == 900 and _log(tmp_path) == []
    assert r.status(0)["behaviour"]["changed"] == 1


# --- the final review's findings (12 Nov 2026) --------------------------------------
@pytest.mark.parametrize("line", [
    '"paddypower.per_hour" = ' + "9" * 400,
    '"load.any_gap_s" = "[' + "9" * 400 + ', 5]"',
    '"load.any_gap_s" = "' + "[" * 5000 + '"',
])
def test_load_never_raises_on_a_value_it_cannot_take(tmp_path, line):
    """A number too big for a float, or a string nested past the parser's depth,
    is a refused line like any other: said, and the value in use kept."""
    behaviour.path(tmp_path).write_text(line + "\n")
    try:
        problems = behaviour.load(tmp_path)
        assert len(problems) == 1 and "keeping" in problems[0]
        assert behaviour.get("paddypower.per_hour") == 15 and behaviour.get("load.any_gap_s") == (20, 45)
    finally:
        behaviour.path(tmp_path).unlink()
        behaviour.load(tmp_path)


@pytest.mark.parametrize("value", ["9" * 400, "[" * 5000])
def test_set_refuses_such_a_value_with_a_message(tmp_path, value):
    with pytest.raises(behaviour.BehaviourError, match="paddypower.per_hour"):
        behaviour.set_(tmp_path, "paddypower.per_hour", value)
    assert not behaviour.path(tmp_path).exists()


def test_set_says_so_when_the_file_holds_something_it_cannot_write_back(tmp_path):
    behaviour.path(tmp_path).write_text('"paddypower.per_hour" = 10\nwhen = 2026-11-12\n')
    with pytest.raises(behaviour.BehaviourError, match="reset --all"):
        behaviour.set_(tmp_path, "paddypower.per_day", 100)
    assert "when = 2026-11-12" in behaviour.path(tmp_path).read_text()      # untouched
    behaviour.reset(tmp_path, None)
    behaviour.set_(tmp_path, "paddypower.per_day", 100)                     # mended


def test_bounds_keep_one_tick_inside_the_watchdog():
    """The pauses a single click may add up to stay well under the 600 s after
    which the service's watchdog restarts it."""
    for key in ("arrive.passes", "read.passes"):
        assert behaviour.setting(key).hi <= 6
    for s in behaviour.SETTINGS:
        if s.unit == "ms":
            assert s.hi <= 5000, s.key
    assert behaviour.setting("bb.settle_s").hi <= 1.0


def test_the_change_log_is_not_a_name_a_job_can_take(tmp_path):
    from scraper import runner, targets
    with pytest.raises(targets.TargetsError, match="behaviour"):
        targets.add(tmp_path / "targets.toml", {"name": runner.BEHAVIOUR_LOG, "site": "generic",
                                                "url": "https://example.com/a", "post": "hub:tips"})


# --- "Alerts": when the Scraper says it is not working (Callum, 12 Nov 2026) --------
def test_alerts_is_the_last_group_and_holds_the_three_thresholds():
    last = [s for s in behaviour.SETTINGS if s.group == "Alerts"]
    assert behaviour.SETTINGS[-3:] == tuple(last)
    stuck, failing, no_rows = last
    assert (stuck.key, stuck.kind, stuck.default, stuck.lo, stuck.hi, stuck.unit) == \
        ("alerts.stuck_s", "int", 180, 0, 590, "s")
    assert stuck.says == ("Alert when the loop has made no progress for this long (0: off); "
                          "the watchdog restarts it at 600 s")
    assert (failing.key, failing.kind, failing.default, failing.lo, failing.hi, failing.unit) == \
        ("alerts.failing_min", "int", 15, 0, 1440, "min")
    assert failing.says == "Alert when a job's parser has failed the same way for this long (0: off)"
    assert (no_rows.key, no_rows.kind, no_rows.default, no_rows.lo, no_rows.hi, no_rows.unit) == \
        ("alerts.no_rows_min", "int", 60, 0, 1440, "min")
    assert no_rows.says == ("Alert when a running job with pages open has handed over no rows "
                            "for this long (0: off)")
    for key in ("alerts.stuck_s", "alerts.failing_min", "alerts.no_rows_min"):
        assert behaviour.parse(key, "0") == 0                   # off
    with pytest.raises(behaviour.BehaviourError, match="alerts.stuck_s"):
        behaviour.parse("alerts.stuck_s", 591)                  # the watchdog's 600 s would come first
