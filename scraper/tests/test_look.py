"""`python -m scraper look <job> '<steps>'`: plain steps on a job's open page,
then the page saved - against test_bb_saves.py's fake builder and stand-ins.
No real site, no headed browser."""
import json
import time
from types import SimpleNamespace

import pytest

from scraper import config, joblog, look
from scraper.tests.test_bb_saves import FAKE, page  # noqa: F401  (the fixture)
from scraper.tests.test_pacing import _loads, _runner


# --- the steps' shape: checked before anything is written or run -------------------
def test_good_steps_are_taken_as_given():
    steps = [{"click": "Update Data"}, {"wait_ms": 500},
             {"select": {"field": "Competition", "option": "English Premier League", "prefix": True}},
             {"select": {"field": "Player", "option": "Hull", "nth": 1}},
             {"select": {"has": "BB xG", "option": "BB Player/Team Stats"}},
             {"type": {"field": "saves", "text": "4.5"}}, {"dump": "a line typed"}]
    assert look.parse_steps(json.dumps(steps)) == steps
    assert look.parse_steps("[]") == []                     # nothing to do: the page is saved


@pytest.mark.parametrize("raw, why", [
    ("{not json", "not JSON"),
    ('{"click": "x"}', "a JSON list"),
    ('[{"click": "x", "wait_ms": 1}]', "step 1: each step is an object with one key"),
    ('[{"press": "x"}]', "step 1: unknown step 'press'"),
    ('[{"click": ""}]', "step 1: click needs the text"),
    ('[{"wait_ms": 5001}]', "step 1: wait_ms is at most 5000"),
    ('[{"wait_ms": -1}]', "step 1: wait_ms"),
    ('[{"wait_ms": true}]', "step 1: wait_ms"),
    ('[{"select": {"field": "Competition"}}]', "step 1: select needs an option"),
    ('[{"select": {"option": "x"}}]', "step 1: select needs a field or has"),
    ('[{"select": {"field": "a", "has": "b", "option": "x"}}]', "step 1: select needs a field or has"),
    ('[{"select": {"field": "a", "option": "x", "nth": "1"}}]', "step 1: nth"),
    ('[{"select": {"field": "a", "option": "x", "first": true}}]', "step 1: select does not take 'first'"),
    ('[{"type": {"field": "saves"}}]', "step 1: type needs a field and text"),
    ('[{"dump": 3}]', "step 1: dump needs a note"),
    ('[{"wait_ms": 1}, "click"]', "step 2: each step is an object with one key"),
])
def test_bad_steps_are_refused_with_the_reason(raw, why):
    with pytest.raises(look.LookError, match=None) as e:
        look.parse_steps(raw)
    assert why in str(e.value)


def test_at_most_12_steps():
    assert len(look.parse_steps(json.dumps([{"wait_ms": 1}] * 12))) == 12
    with pytest.raises(look.LookError, match="at most 12 steps"):
        look.parse_steps(json.dumps([{"wait_ms": 1}] * 13))


# --- each step on the fake builder ----------------------------------------------
def _run(page, steps, tmp_path, capture=None):  # noqa: F811
    said, loads = [], []
    ok = look.run(page, look.parse_steps(json.dumps(steps)), tmp_path / "out", said.append,
                  capture=capture, on_load=loads.append)
    return ok, said, loads


def test_each_kind_of_step_on_the_builder(page, tmp_path):  # noqa: F811
    ok, said, loads = _run(page, [
        {"click": "Update Data"},
        {"wait_ms": 600},
        {"select": {"has": "BB xG", "option": "BB Player/Team Stats"}},
        {"select": {"field": "Competition", "option": "English Football League Cup", "prefix": True}},
        {"select": {"field": "Game(s)", "option": "Bradford v Peterborough - 19:45 tomorrow [C] [S]"}},
        {"dump": "a game chosen"},
        {"type": {"field": "saves", "text": "4.5"}},
    ], tmp_path)
    assert ok and loads == []
    assert page.evaluate("UPDATES") == 1                     # clicked once, with the mouse
    assert page.locator("#stat").input_value() == "BB Player/Team Stats"
    assert page.locator(".comp").input_value() == "English Football League Cup (8/8)"
    assert "Bradford v Peterborough - 19:45 tomorrow [C] [S]" in page.evaluate("VISITED")
    assert page.locator("#line").input_value() == "4.5"
    out = tmp_path / "out"
    assert sorted(p.name for p in out.iterdir()) == [
        "1-a-game-chosen.html", "1-a-game-chosen.png", "1-a-game-chosen.url",
        "2-end.html", "2-end.png", "2-end.url"]
    assert "Bradford" in (out / "1-a-game-chosen.html").read_text()
    assert (out / "2-end.png").stat().st_size > 0
    assert said == ['clicked "Update Data"',
                    "waited 600 ms",
                    'set the select holding "BB xG" to "BB Player/Team Stats"',
                    'set "Competition" to "English Football League Cup (8/8)"',
                    'set "Game(s)" to "Bradford v Peterborough - 19:45 tomorrow [C] [S]"',
                    f'saved "a game chosen" as {out / "1-a-game-chosen"}.*',
                    'typed "4.5" in "saves"',
                    f"saved {out}"]


def test_the_second_block_is_nth_1(page, tmp_path):  # noqa: F811
    ok, said, _ = _run(page, [
        {"click": "Update Data"}, {"wait_ms": 600},
        {"select": {"field": "Competition", "option": "English Football League Cup (8/8)"}},
        {"select": {"field": "Game(s)", "option": "Bradford v Peterborough", "prefix": True}},
        {"select": {"field": "Player", "option": "Bradford"}},
        {"click": "Combine Players/Games"},
        {"select": {"field": "Competition", "option": "English Football League Cup (8/8)", "nth": 1}},
    ], tmp_path)
    assert ok, said
    assert page.locator(".comp").nth(1).input_value() == "English Football League Cup (8/8)"


def test_a_step_that_cannot_be_done_stops_the_run_is_said_and_the_page_is_still_saved(page, tmp_path):  # noqa: F811
    t0 = time.time()
    ok, said, _ = _run(page, [{"click": "Update Data"}, {"click": "Show/Hide Presets"},
                              {"wait_ms": 1}], tmp_path)
    assert not ok
    assert time.time() - t0 < 20
    assert said[0] == 'clicked "Update Data"'
    assert said[1] == ('step 2 of 3 ({"click": "Show/Hide Presets"}) failed: no visible element '
                       'whose text is "Show/Hide Presets" - stopped')
    assert said[2] == f"saved {tmp_path / 'out'}"            # step 3 never ran
    assert (tmp_path / "out" / "1-stopped.html").exists()


def test_an_option_that_is_not_there_is_said_with_what_is(page, tmp_path):  # noqa: F811
    ok, said, _ = _run(page, [{"select": {"has": "BB xG", "option": "BB xNothing"}}], tmp_path)
    assert not ok
    assert 'no option "BB xNothing" (options: BB xG, BB Player/Team Stats)' in said[0]


def test_a_field_that_is_not_there_is_said(page, tmp_path):  # noqa: F811
    ok, said, _ = _run(page, [{"type": {"field": "goals", "text": "1"}}], tmp_path)
    assert not ok and 'no input in the field "goals"' in said[0]


NAV = """<html><body><a href="#moved">Next page</a><button>Stay</button></body></html>"""


def test_a_step_that_moves_the_page_stops_and_is_counted_as_a_load(page, tmp_path):  # noqa: F811
    page.set_content(NAV)
    ok, said, loads = _run(page, [{"click": "Stay"}, {"click": "Next page"}, {"click": "Stay"}], tmp_path)
    assert not ok
    assert loads == ["about:blank#moved"]
    assert said[:2] == ['clicked "Stay"',
                        'step 2 of 3 ({"click": "Next page"}) changed the page (now about:blank#moved) '
                        '- stopped; counted as a load']
    assert (tmp_path / "out" / "1-stopped.html").exists()


def test_a_wait_is_capped_at_5_s_even_if_the_check_was_skipped(page, tmp_path, monkeypatch):  # noqa: F811
    waited = []
    monkeypatch.setattr(page, "wait_for_timeout", lambda ms: waited.append(ms))
    look.run(page, [{"wait_ms": 60_000}], tmp_path / "out", lambda m: None)
    assert waited == [look.MAX_WAIT_MS] == [5000]


def test_every_playwright_call_in_a_look_has_at_most_8_s(page, tmp_path, monkeypatch):  # noqa: F811
    set_to, shots = [], []
    real = page.set_default_timeout
    monkeypatch.setattr(page, "set_default_timeout", lambda ms: (set_to.append(ms), real(ms)))
    from scraper import browser
    monkeypatch.setattr(browser, "screenshot",
                        lambda pg, path, full_page=False, timeout_ms=None: shots.append((full_page, timeout_ms)))
    look.run(page, [], tmp_path / "out", lambda m: None)
    assert set_to == [8000] and shots == [(True, 8000)]


# --- the runner answers look-<job> --------------------------------------------------
def _bb_job(name="bb"):
    return config.Target(name=name, site="bookiebashing", stale_min=10_000,
                         url="https://www.bookiebashing.net/tools/betbuilder/", parser="bb_builder")


def _pp_job(name="pp"):
    return config.Target(name=name, site="paddypower", stale_min=10_000,
                         url="https://www.paddypower.com/football/x/a-v-b")


def _ask(tmp_path, name, steps):
    (tmp_path / "requests" / f"look-{name}").write_text(steps if isinstance(steps, str) else json.dumps(steps))


def _msgs(tmp_path, name):
    return [x["msg"] for x in joblog.read(tmp_path, name, n=100)]


def test_the_runner_runs_a_look_on_the_jobs_open_page(tmp_path, page):  # noqa: F811
    t = _bb_job()
    r = _runner(tmp_path, [t])
    r.tabs["bb"] = SimpleNamespace(target=t, page=page, capture=None, url=t.url)
    _ask(tmp_path, "bb", [{"click": "Update Data"}])
    r._requests()
    assert not (tmp_path / "requests" / "look-bb").exists()
    msgs = _msgs(tmp_path, "bb")
    assert msgs[0] == 'look: clicked "Update Data"'
    assert msgs[1].startswith(f"look: saved {tmp_path / 'dumps' / 'bb-'}")
    saved = next((tmp_path / "dumps").iterdir())
    assert (saved / "1-end.html").exists() and (saved / "1-end.url").exists()


def test_the_runner_counts_a_look_that_moved_the_page_as_a_load(tmp_path, page):  # noqa: F811
    t = _bb_job()
    r = _runner(tmp_path, [t])
    page.set_content(NAV)
    r.tabs["bb"] = SimpleNamespace(target=t, page=page, capture=None, url=t.url)
    _ask(tmp_path, "bb", [{"click": "Next page"}])
    r._requests(now=1_000.0)
    assert _loads(r) == [("bookiebashing", 1_000.0)]
    assert "changed the page" in _msgs(tmp_path, "bb")[0]


def test_the_runner_refuses_a_look_at_a_paddy_job(tmp_path, monkeypatch):
    t = _pp_job()
    r = _runner(tmp_path, [t])
    ran = []
    r.tabs["pp"] = SimpleNamespace(target=t, page=SimpleNamespace(url=t.url), capture=None, url=t.url)
    monkeypatch.setattr(look, "run", lambda *a, **k: ran.append(a))
    _ask(tmp_path, "pp", [{"click": "Corners"}])
    r._requests()
    assert ran == []
    assert not (tmp_path / "dumps").exists()
    assert _msgs(tmp_path, "pp") == ["look: refused - look is not for Paddy Power pages: the Scraper "
                                     "clicks nothing there but a cookie banner and market tabs"]


def test_a_look_on_a_job_with_no_open_page_says_so(tmp_path, capsys):
    r = _runner(tmp_path, [_bb_job()])
    _ask(tmp_path, "bb", [{"click": "Update Data"}])
    _ask(tmp_path, "ghost", [{"click": "Update Data"}])
    r._requests()
    assert _msgs(tmp_path, "bb") == ["look: no open page for that target"]
    assert "look ghost: no such target" in capsys.readouterr().out
    assert not joblog.path(tmp_path, "ghost").exists()


def test_a_bad_request_file_is_refused_in_the_log(tmp_path):
    r = _runner(tmp_path, [_bb_job()])
    _ask(tmp_path, "bb", "[{\"wait_ms\": 9000}]")
    r._requests()
    assert _msgs(tmp_path, "bb") == ["look: refused - step 1: wait_ms is at most 5000 (whole ms, 0 or more)"]


def test_nothing_from_a_look_escapes_the_requests(tmp_path, monkeypatch, capsys):
    t = _bb_job()
    r = _runner(tmp_path, [t])
    r.tabs["bb"] = SimpleNamespace(target=t, page=SimpleNamespace(url=t.url), capture=None, url=t.url)

    def boom(*a, **k):
        raise RuntimeError("the renderer died")
    monkeypatch.setattr(look, "run", boom)
    _ask(tmp_path, "bb", [{"click": "Update Data"}])
    (tmp_path / "requests" / "wake-bb").touch()
    r._requests()                                            # must not raise
    assert "look: failed: RuntimeError: the renderer died" in _msgs(tmp_path, "bb")
    assert "wake: not resting" in _msgs(tmp_path, "bb")      # the other requests are still answered


# --- the command ------------------------------------------------------------------
@pytest.fixture
def cli(tmp_path, monkeypatch):
    (tmp_path / "targets.toml").write_text(
        '[[target]]\nname = "bb-look"\nsite = "bookiebashing"\n'
        'url = "https://www.bookiebashing.net/tools/betbuilder/"\nparser = "bb_builder"\n\n'
        '[[target]]\nname = "pp"\nsite = "paddypower"\nurl = "https://www.paddypower.com/football"\n')
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCRAPER_TARGETS", str(tmp_path / "targets.toml"))
    from scraper.__main__ import main
    return main


def test_the_command_writes_the_request_and_says_where_the_dump_will_be(cli, tmp_path, capsys):
    steps = [{"click": "Update Data"}, {"wait_ms": 3000}]
    cli(["look", "bb-look", json.dumps(steps)])
    assert json.loads((tmp_path / "requests" / "look-bb-look").read_text()) == steps
    assert [p.name for p in (tmp_path / "requests").iterdir()] == ["look-bb-look"]   # no temp file left
    out = capsys.readouterr().out
    assert f"{tmp_path / 'dumps' / 'bb-look-'}" in out and "2 step(s)" in out


def test_the_command_json(cli, tmp_path, capsys):
    cli(["look", "bb-look", '[{"dump": "as it is"}]', "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["job"] == "bb-look" and body["steps"] == [{"dump": "as it is"}]
    assert body["request"] == str(tmp_path / "requests" / "look-bb-look")
    assert body["dumps"].startswith(str(tmp_path / "dumps" / "bb-look-"))


@pytest.mark.parametrize("argv, why", [
    (["look", "bb-look", "[{click: 1}]"], "not JSON"),
    (["look", "bb-look", '[{"wait_ms": 6000}]'], "wait_ms is at most 5000"),
    (["look", "pp", '[{"click": "Corners"}]'], "look is not for Paddy Power pages"),
    (["look", "nope", "[]"], "no such target"),
    (["look", "bb-look"], "the following arguments are required"),
])
def test_the_command_refuses_with_a_message(cli, tmp_path, capsys, argv, why):
    with pytest.raises(SystemExit) as e:
        cli(argv)
    assert e.value.code == 2
    assert why in capsys.readouterr().err
    assert not (tmp_path / "requests").exists() or not list((tmp_path / "requests").iterdir())


def test_the_command_refuses_in_json_too(cli, capsys):
    with pytest.raises(SystemExit):
        cli(["look", "pp", "[]", "--json"])
    assert "look is not for Paddy Power pages" in json.loads(capsys.readouterr().out)["error"]


def test_the_module_docstring_lists_look(cli):
    doc = cli.__globals__["__doc__"]
    assert "look <job>" in doc and "dump <target>" in doc
