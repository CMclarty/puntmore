"""scraper/targets.py: the only way targets.toml is changed (the CLI, the Hub
Console, HubAI). Round-trip, atomic save, the lock, each operation, the
refusals, the status.json join and the CLI's JSON. No browser needed."""
import json
import os
import threading
import time

import pytest

from scraper import config
from scraper import targets as T
from scraper.__main__ import main
from scraper.config import Matches, Target

FULL = [
    Target(name="pp", site="paddypower", url="https://www.paddypower.com/football/epl",
           every_min=15.0, stale_min=30.0, parser="pp_most", post="/api/paddy/most-prices",
           matches=Matches(link_re=r"-v-\d", max_tabs=4, market_tabs=("Corners", 'Cards "x"'),
                           open_gap_min=5.0, tab_max_hours=6.0, relist_min=45.0),
           mode="keep", enabled=True, min_rows=3, trust_weight=0.75,
           wait_for="div.market", priority=5, window="07:00-23:30"),
    Target(name="tips", site="generic", url="https://tips.example.org/today",
           every_min=None, stale_min=20.0, parser="example_tipster", post="hub:tips",
           mode="fetch", enabled=False, min_rows=2, trust_weight=0.5, priority=-1,
           window="22:00-02:00"),
    Target(name="bb-saves", site="bookiebashing", url="https://www.bookiebashing.net/saves"),
]

TIPS = {"name": "new-tips", "site": "generic", "url": "https://tips.example.net/a", "every_min": 60}


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "targets.toml"
    T.save(p, FULL)
    return p


def test_every_field_round_trips(path):
    assert T.load(path) == FULL
    # and the Scraper reads the same file, disabled targets left out
    assert config.load_targets(path) == [FULL[0], FULL[2]]
    # saving what was loaded is byte-identical: no drift between saves
    text = path.read_text()
    T.save(path, T.load(path))
    assert path.read_text() == text


def test_saved_file_has_header_and_keeps_order(path):
    text = path.read_text()
    assert text.startswith(T.HEADER) and "comments are not kept" in text
    assert [t.name for t in T.load(path)] == ["pp", "tips", "bb-saves"]


def test_first_save_keeps_the_hand_written_file_once(tmp_path):
    p = tmp_path / "targets.toml"
    hand = '# my notes\n[[target]]\nname="one"\nsite="paddypower"\nurl="https://www.paddypower.com/one"\n'
    p.write_text(hand)
    T.set(p, "one", every_min=10)
    bak = tmp_path / "targets.toml.bak"
    assert bak.read_text() == hand
    assert "# my notes" not in p.read_text()
    T.set(p, "one", every_min=12)
    assert bak.read_text() == hand          # one-time: not overwritten by our own file


def test_save_keeps_the_files_mode(path):
    os.chmod(path, 0o644)
    T.set(path, "pp", every_min=20)
    assert path.stat().st_mode & 0o777 == 0o644


def test_failed_write_leaves_the_old_file_and_no_temp(path, monkeypatch):
    before = path.read_text()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        T.add(path, TIPS)
    assert path.read_text() == before
    assert [p.name for p in path.parent.iterdir() if p.suffix == ".tmp"] == []


def test_add_set_enable_disable_remove(path):
    t = T.add(path, TIPS)
    assert t.mode == "fetch" and t.every_min == 60.0
    assert T.load(path)[-1] == t
    t = T.set(path, "new-tips", every_min=15, priority=3, window="06:00-12:00", trust_weight=0.4)
    assert (t.every_min, t.priority, t.window, t.trust_weight) == (15.0, 3, "06:00-12:00", 0.4)
    assert T.set(path, "new-tips", window=None).window is None      # None clears
    assert T.disable(path, "new-tips").enabled is False
    assert "new-tips" not in [x.name for x in config.load_targets(path)]
    assert T.enable(path, "new-tips").enabled is True
    left = T.remove(path, "new-tips")
    assert [x.name for x in left] == ["pp", "tips", "bb-saves"] == [x.name for x in T.load(path)]


def test_set_on_a_missing_file_or_name(path, tmp_path):
    with pytest.raises(T.TargetsError, match="no target named"):
        T.set(path, "nope", every_min=10)
    with pytest.raises(T.TargetsError, match="no target named"):
        T.remove(tmp_path / "absent.toml", "x")


@pytest.mark.parametrize("change, why", [
    ({"url": "https://www.paddypower.com/x"}, "not on an allowed host"),     # bookmaker as generic
    ({"url": "https://www.bookiebashing.net/x"}, "not on an allowed host"),
    ({"every_min": 2}, "every_min = 2 is outside"),                           # below the 5 min floor
    ({"stale_min": 5}, "stale_min = 5 is outside"),
    ({"trust_weight": 1.5}, "trust_weight = 1.5 is outside"),
    ({"window": "7:00-23:00"}, "window must be"),
    ({"window": "07:00-24:00"}, "window must be"),
    ({"window": "08:00-08:00"}, "is empty"),
    ({"priority": 1.5}, "priority must be a whole number"),
    ({"priority": "high"}, "priority must be a number"),
    ({"enabled": "false"}, "enabled must be true or false"),
    ({"mode": "poll"}, "mode must be keep or fetch"),
    ({"site": "betfair"}, "unknown site"),
    ({"evry_min": 10}, "unknown field"),
    ({"matches": {"max_tabs": 2}}, "generic target cannot have"),
])
def test_refusals_write_nothing(path, change, why):
    T.add(path, TIPS)
    before = path.read_text()
    with pytest.raises(T.TargetsError, match=why):
        T.set(path, "new-tips", **change)
    assert path.read_text() == before


def test_refusals_on_add(path):
    with pytest.raises(T.TargetsError, match="already exists"):
        T.add(path, {**TIPS, "name": "tips"})          # a disabled target still owns its name
    with pytest.raises(T.TargetsError, match="url is required"):
        T.add(path, {"name": "x", "site": "generic"})
    with pytest.raises(T.TargetsError, match=r"matches.max_tabs = 9 is outside"):
        T.add(path, {"name": "m", "site": "paddypower", "url": "https://www.paddypower.com/m",
                     "matches": {"max_tabs": 9}})
    with pytest.raises(T.TargetsError, match="cannot be renamed"):
        T.set(path, "pp", name="pp2")


def test_lock_contention_times_out_then_proceeds(path):
    held, release = threading.Event(), threading.Event()

    def holder():
        with T.locked(path):
            held.set()
            release.wait(5)
    th = threading.Thread(target=holder)
    th.start()
    held.wait(5)
    before = path.read_text()
    with pytest.raises(T.TargetsError, match="being changed by something else"):
        T.add(path, TIPS, timeout=0.2)
    assert path.read_text() == before
    release.set()
    th.join()
    assert T.add(path, TIPS, timeout=1).name == "new-tips"


def test_concurrent_writers_do_not_lose_changes(path):
    errors = []

    def one(i):
        try:
            T.add(path, {**TIPS, "name": f"src-{i}"})
        except Exception as exc:                   # pragma: no cover - the failure we test for
            errors.append(exc)
    threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert sorted(t.name for t in T.load(path) if t.name.startswith("src-")) == [f"src-{i}" for i in range(8)]


def test_summary_joins_status_json(path, tmp_path):
    now = time.time()
    (tmp_path / "status.json").write_text(json.dumps({
        "at": now, "targets_file": str(path),
        "targets": [{"name": "pp", "site": "paddypower", "url": FULL[0].url, "state": "running",
                     "mode": "keep", "detail": "2 tabs open", "last_load": now - 60,
                     "last_parse": now - 30, "rows_posted": 42},
                    {"name": "gone", "site": "generic", "url": "https://x.org", "state": "retired",
                     "mode": "fetch", "detail": "removed from targets.toml", "last_load": None,
                     "last_parse": None, "rows_posted": 0}],
        "sites": []}))
    rows = T.summary(path)
    assert [r["name"] for r in rows] == ["pp", "tips", "bb-saves"]       # the file's targets only
    pp = rows[0]
    assert pp["status"] == {"state": "running", "detail": "2 tabs open", "last_load": now - 60,
                            "last_parse": now - 30, "rows_posted": 42, "reported_at": now}
    assert pp["every_min"] == 15.0 and pp["priority"] == 5 and pp["window"] == "07:00-23:30"
    assert pp["matches"]["market_tabs"] == ["Corners", 'Cards "x"']
    assert rows[1]["status"] is None and rows[1]["enabled"] is False
    assert set(pp) == {*T.FIELDS, "status"}
    json.dumps(rows)                                   # what the console is sent


def test_summary_without_status_json(path):
    assert all(r["status"] is None for r in T.summary(path, path.parent / "missing.json"))


# --- the CLI ---------------------------------------------------------------
@pytest.fixture
def cli(path, monkeypatch, capsys):
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(path.parent))
    monkeypatch.delenv("SCRAPER_TARGETS", raising=False)

    def run(*argv):
        code = 0
        try:
            main(["targets", *argv])
        except SystemExit as exc:
            code = exc.code
        out = capsys.readouterr()
        return code, out.out, out.err
    return run


def test_cli_list_json_is_summary(cli, path):
    code, out, _ = cli("list", "--json")
    assert code == 0 and json.loads(out) == T.summary(path)


def test_cli_add_set_enable_show_remove(cli, path):
    code, out, _ = cli("add", "--name", "new-tips", "--site", "generic", "--url", TIPS["url"],
                       "--every-min", "60", "--min-rows", "2", "--trust-weight", "0.5",
                       "--priority", "1", "--window", "06:00-22:00", "--disabled", "--json")
    d = json.loads(out)
    assert code == 0 and d["name"] == "new-tips" and d["enabled"] is False
    assert (d["every_min"], d["min_rows"], d["priority"], d["window"]) == (60.0, 2, 1, "06:00-22:00")
    assert d["status"] is None
    code, out, _ = cli("set", "new-tips", "--every-min", "15", "--window", "none", "--json")
    d = json.loads(out)
    assert code == 0 and d["every_min"] == 15.0 and d["window"] is None
    assert json.loads(cli("enable", "new-tips", "--json")[1])["enabled"] is True
    assert json.loads(cli("show", "new-tips", "--json")[1]) == T.summary(path)[-1]
    code, out, _ = cli("set", "pp", "--matches", '{"max_tabs": 2, "market_tabs": ["Goals"]}', "--json")
    assert code == 0 and json.loads(out)["matches"]["max_tabs"] == 2
    code, out, _ = cli("remove", "new-tips", "--json")
    assert code == 0 and json.loads(out) == {"removed": "new-tips", "targets": ["pp", "tips", "bb-saves"]}


def test_cli_errors_exit_2_with_the_reason(cli, path):
    before = path.read_text()
    code, out, _ = cli("set", "pp", "--every-min", "2", "--json")
    assert code == 2 and "every_min = 2.0 is outside" in json.loads(out)["error"]
    code, _, err = cli("add", "--name", "x", "--site", "generic", "--url", "https://www.paddypower.com/x")
    assert code == 2 and "not on an allowed host" in err
    code, _, err = cli("set", "pp")
    assert code == 2 and "nothing to change" in err
    code, _, _ = cli("set", "pp", "--every-min", "often")        # argparse refuses the type
    assert code == 2
    assert path.read_text() == before


def test_cli_human_list(cli):
    code, out, _ = cli("list")
    assert code == 0 and "pp" in out and "priority 5" in out and "disabled" in out


def test_running_scraper_applies_a_save_through_its_re_read(path, monkeypatch, capsys):
    from scraper.alerts import Alerts
    from scraper.guard import Guard
    from scraper.runner import TARGETS_CHECK_S, Runner
    from scraper.sites import SITES
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(path.parent))
    monkeypatch.setenv("SCRAPER_TARGETS", str(path))
    s = config.load()
    guard = Guard(path.parent / "g.sqlite3", {k: (v.per_hour, v.per_day) for k, v in SITES.items()})
    r = Runner(s, ctx=None, guard=guard, alerts=Alerts(path.parent / "alerts.log", None),
               run_dir=path.parent / "run")
    closed = []
    monkeypatch.setattr(r, "_close", lambda key: closed.append(key))
    tab = type("Tab", (), {"target": FULL[0], "due_at": 123.0})()   # an open tab for pp
    r.tabs["pp"] = tab

    def save_and_reload(n, **change):
        T.set(path, "pp", **change)
        st = path.stat()                                        # make sure the mtime moved
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000 * n))
        r._reload_targets(time.time() + n * (TARGETS_CHECK_S + 1))

    # A timing / priority / window / health change is applied in place: the
    # tab stays open, takes the new definition, and its timed reload is
    # redrawn with the new every_min. No load.
    save_and_reload(1, every_min=20, priority=7, window="06:00-23:00", min_rows=1, trust_weight=0.25)
    assert next(t for t in r.targets if t.name == "pp").every_min == 20.0
    assert "changed pp (in place)" in capsys.readouterr().out
    assert closed == [] and r.tabs["pp"] is tab
    assert (tab.target.every_min, tab.target.priority, tab.target.window) == (20.0, 7, "06:00-23:00")
    assert tab.due_at is None
    # A change to the page itself (here its url) still closes and reopens.
    save_and_reload(2, url="https://www.paddypower.com/football/championship")
    assert closed == ["pp"]
    assert "changed pp" in capsys.readouterr().out
    # Disabling closes too (the target is gone from the running list).
    r.tabs["pp"] = type("Tab", (), {"target": next(t for t in r.targets if t.name == "pp")})()
    T.disable(path, "pp")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 6_000_000_000))
    r._reload_targets(time.time() + 3 * (TARGETS_CHECK_S + 1))
    assert closed == ["pp", "pp"] and "pp" not in [t.name for t in r.targets]


def test_cli_file_option(cli, tmp_path):
    other = tmp_path / "elsewhere.toml"
    code, _, _ = cli("add", "--file", str(other), "--name", "a", "--site", "generic",
                     "--url", "https://a.example.org/")
    assert code == 0 and [t.name for t in T.load(other)] == ["a"]
