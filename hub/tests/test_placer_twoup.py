"""The Bet Placer's 2-Up reader, stage 1 (puntmore_hub/placement/twoup.py): Render's 2-Up feed
read, and in dry-run what would have been placed recorded in the bets table. No network: every
call to Render goes through a fake opener."""
import json
import logging
import os
import subprocess
import sys
import threading
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from puntmore_hub import cli, daemon, inbox
from puntmore_hub.placement import twoup
from puntmore_hub.placement.adapters.base import PlacedResult, PlacementAdapter
from puntmore_hub.placement.engine import ClientEngine
from puntmore_hub.schemas import Bet, BetLeg
from puntmore_hub.store import Store

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def row(event_id="ev1", side="home", value=True, priced_ago=60, kickoff_in=180, price=2.5):
    return {"event_id": event_id, "event": "Home v Away", "home": "Home", "away": "Away",
            "kickoff": iso(NOW + timedelta(minutes=kickoff_in)), "competition": "EPL",
            "selection": "Home" if side == "home" else "Away", "side": side, "market": "Match Result",
            "price": price, "p_win": 0.42, "rescue": 0.05, "fair_price": 2.38, "edge": 0.07, "ev": 0.17,
            "value": value,
            "priced_at": None if priced_ago is None else iso(NOW - timedelta(seconds=priced_ago))}


def feed(rows=(), available=True, **status):
    st = {"provider": "oddspapi", "degraded": False, "cannot_price": [], "stale": False,
          "served_at": iso(NOW), "not_yet_refreshed": False, "last_pass": {"priced_fixtures": 3},
          "killed": False, **status}
    return {"generated_at": iso(NOW), "available": available, "status": st, "candidates": list(rows)}


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = json.dumps(body).encode()
        self.headers = {"Content-Type": "application/json"}

    def read(self):
        return self._body


def serve(body, status=200):
    """An opener answering every call with `body`; the requests it saw are on `.seen`."""
    def opener(req, timeout):
        opener.seen.append((req.full_url, req.get_header("Authorization")))
        return _Resp(status, body)
    opener.seen = []
    return opener


def never(req, timeout):
    pytest.fail(f"Render was called: {req.full_url}")


@pytest.fixture
def pcfg(cfg):
    cfg.sub_clients_file.write_text("sub_clients:\n  - account_id: test-2up\n    bookmaker: bet365\n"
                                    "    device: none\n    enabled: true\n")
    return cfg.with_overrides(render_http="https://puntmore.example/", render_api_key="k",
                              placer_2up_account="test-2up")


def bets(store):
    return [dict(r) for r in store.conn.execute("SELECT * FROM bets ORDER BY bet_id").fetchall()]


def run(pcfg, store, body, status=200):
    return twoup.read_once(pcfg, store, opener=serve(body, status), now=NOW)


# ---- the feed, the key -----------------------------------------------------------------------

def test_a_value_row_is_recorded_as_a_dry_run_single(pcfg, store):
    opener = serve(feed([row()]))
    r = twoup.read_once(pcfg, store, opener=opener, now=NOW)
    assert opener.seen == [("https://puntmore.example/api/2up/feed?value_only=true", "Bearer k")]
    assert (r["outcome"], r["board"], r["seen"], r["recorded"]) == ("done", "ok", 1, 1)
    assert r["statuses"] == {"placed": 1} and r["rows"][0]["fate"] == "placed"
    [b] = bets(store)
    assert (b["bet_id"], b["account_id"], b["status"], b["dry_run"], b["actual_price"]) == (
        "2up:ev1:home", "test-2up", "placed", 1, 2.5)
    body = Bet.from_dict(json.loads(b["body"]))
    assert body.legs == [BetLeg("ev1", "Match Result", "Home", 2.5)]
    assert (body.stake, body.expected_price, body.bet_type) == (10.0, 2.5, "single")
    assert body.not_after == (NOW + timedelta(minutes=180)).isoformat()
    assert store.open_escalations() == []


def test_the_flat_stake_is_a_setting(pcfg, store):
    run(pcfg.with_overrides(placer_flat_stake=2.0), store, feed([row()]))
    assert json.loads(bets(store)[0]["body"])["stake"] == 2.0


def test_the_bet_id_is_one_per_selection_and_a_second_pass_records_nothing(pcfg, store):
    body = feed([row(side="home"), row(side="away")])
    first, second = run(pcfg, store, body), run(pcfg, store, body)
    assert first["recorded"] == 2 and second["recorded"] == 0
    assert second["skipped"] == {"already_recorded": 2} and second["statuses"] == {}
    assert [b["bet_id"] for b in bets(store)] == ["2up:ev1:away", "2up:ev1:home"]
    assert twoup.bet_id_for("ev1", "home") == "2up:ev1:home"


@pytest.mark.parametrize("status, says", [
    (403, "the Hub's key is not granted accabonus"),
    (401, "did not accept the Hub's key"),
    (404, "no 2-Up feed"),
    (500, "HTTP 500"),
])
def test_a_refused_key_is_said_plainly_not_raised(pcfg, store, status, says):
    r = run(pcfg, store, {"detail": "nope"}, status)
    assert r["outcome"] == "refused" and says in r["detail"]
    assert bets(store) == [] and store.open_escalations() == []


def test_an_unreachable_render_is_said_plainly_not_raised(pcfg, store):
    def down(req, timeout):
        raise urllib.error.URLError("connection refused")
    r = twoup.read_once(pcfg, store, opener=down, now=NOW)
    assert r["outcome"] == "unreachable" and "connection refused" in r["detail"]
    assert bets(store) == []


def test_without_render_configured_nothing_is_called(pcfg, store):
    r = twoup.read_once(pcfg.with_overrides(render_api_key=""), store, opener=never, now=NOW)
    assert r["outcome"] == "not configured"


# ---- the board -------------------------------------------------------------------------------

@pytest.mark.parametrize("body, says", [
    (feed([row()], available=False, cannot_price=["ALL — ODDSPAPI_API_KEY unset"]), "available: false"),
    (feed([], killed=True), "killed"),
    (feed([], not_yet_refreshed=True, served_at=None), "not_yet_refreshed"),
    (feed([row()], stale=True, degraded=True,
          cannot_price=["ALL — every OddsPapi odds request failed"]), "stale"),
    (feed([], degraded=True, cannot_price=["ALL — OddsPapi reference data failed"],
          last_pass={"priced_fixtures": 0}), "cannot_price: ALL"),
    (feed([], degraded=True, cannot_price=["bet365 1X2 missing on all 4 fixture(s)"],
          last_pass={"priced_fixtures": 0}), "cannot_price: bet365"),
    (feed([row()], degraded=True, cannot_price=["rescue table unreadable: /x.json"]),
     "cannot_price: rescue table unreadable"),
    (feed([row()], degraded=True), "degraded"),
])
def test_a_no_bet_board_records_nothing_and_says_which(pcfg, store, body, says):
    r = run(pcfg, store, body)
    assert (r["outcome"], r["board"]) == ("no-bet", "no-bet")
    assert r["detail"].startswith(says)
    assert r["recorded"] == 0 and bets(store) == []


def test_a_partly_priced_board_keeps_its_priced_rows(pcfg, store):
    body = feed([row()], degraded=True, cannot_price=["bet365 chunk [17, 8]: HTTP 500"])
    r = run(pcfg, store, body)
    assert (r["outcome"], r["board"], r["recorded"]) == ("done", "partly priced", 1)
    assert "bet365 chunk" in r["detail"]


# ---- Bookie Bashing's tracker as bet365's source (Render's `bb` source, 18 Nov 2026) ----------

def bb_row(event_id="bb:arsenal|chelsea|2026-11-18", side="home", sources=("bb",), **kw):
    """A row as Render serves one only the tracker priced: no Pinnacle model fields."""
    out = {**row(event_id=event_id, side=side, **kw), "sources": list(sources),
           "bb_rating": 104.0, "bb_fair": 2.4}
    if "pinnacle" not in sources:
        out.update({"p_win": None, "rescue": None, "fair_price": 2.4, "edge": 0.016, "ev": 0.04})
    return out


def bb_feed(rows=(), **status):
    return feed(rows, provider="bookiebashing+oddspapi", bet365_source="bb", **status)


def test_a_bb_only_board_is_bet_while_pinnacle_is_down(pcfg, store):
    body = bb_feed([bb_row(), bb_row(event_id="bb:leeds|hull|2026-11-18", side="away")],
                   degraded=True, last_pass={"priced_fixtures": 2},
                   cannot_price=["pinnacle mode: every OddsPapi odds request for Pinnacle failed"])
    r = run(pcfg, store, body)
    assert (r["outcome"], r["board"], r["recorded"]) == ("done", "partly priced", 2)
    assert "pinnacle mode" in r["detail"]
    assert [b["bet_id"] for b in bets(store)] == ["2up:bb:arsenal|chelsea|2026-11-18:home",
                                                  "2up:bb:leeds|hull|2026-11-18:away"]
    # The same selection in the next pass, now joined to Pinnacle: the same bet id, not a second bet.
    again = run(pcfg, store, bb_feed([bb_row(sources=("bb", "pinnacle"))]))
    assert again["recorded"] == 0 and again["skipped"] == {"already_recorded": 1}


@pytest.mark.parametrize("body, says", [
    (bb_feed([], not_yet_refreshed=True, degraded=True,
             cannot_price=["ALL — no Bookie Bashing tracker snapshot since boot (POST /api/2up/bb)"]),
     "not_yet_refreshed"),
    (bb_feed([], degraded=True, last_pass={"priced_fixtures": 2},
             cannot_price=["ALL — the Bookie Bashing tracker snapshot is 400s old (limit 300s)"]),
     "cannot_price: ALL — the Bookie Bashing tracker snapshot"),
])
def test_a_bb_board_with_no_usable_tracker_is_no_bet(pcfg, store, body, says):
    r = run(pcfg, store, body)
    assert (r["outcome"], r["board"]) == ("no-bet", "no-bet")
    assert r["detail"].startswith(says) and bets(store) == []


def test_an_unreadable_rescue_table_leaves_only_the_rows_bb_flags(pcfg, store):
    body = bb_feed([bb_row(), bb_row(event_id="bb:leeds|hull|2026-11-18", sources=("bb", "pinnacle")),
                    {**row(event_id="bb:wigan|bolton|2026-11-18"), "sources": ["pinnacle"]}],
                   degraded=True, cannot_price=["rescue table unreadable: /x.json"])
    r = run(pcfg, store, body)
    assert (r["outcome"], r["board"], r["recorded"]) == ("done", "partly priced", 2)
    assert r["skipped"] == {"needs_rescue_table": 1}
    assert [b["bet_id"] for b in bets(store)] == ["2up:bb:arsenal|chelsea|2026-11-18:home",
                                                  "2up:bb:leeds|hull|2026-11-18:home"]


def test_an_unreadable_rescue_table_with_no_bb_row_is_still_no_bet(pcfg, store):
    body = bb_feed([{**row(), "sources": ["pinnacle"]}], degraded=True,
                   cannot_price=["rescue table unreadable: /x.json"])
    r = run(pcfg, store, body)
    assert (r["outcome"], r["board"]) == ("no-bet", "no-bet")
    assert r["detail"].startswith("cannot_price: rescue table unreadable")


# ---- the rows --------------------------------------------------------------------------------

@pytest.mark.parametrize("bad, reason", [
    (row(value=False), "not_value"),
    (row(priced_ago=None), "no_priced_at"),
    (row(priced_ago=3901), "stale_price"),
    (row(kickoff_in=-1), "kicked_off"),
    ({**row(), "event_id": ""}, "bad_row"),
])
def test_a_row_that_cannot_be_bet_is_skipped_by_reason(pcfg, store, bad, reason):
    r = run(pcfg, store, feed([bad, row(event_id="ok")]))
    assert r["skipped"] == {reason: 1} and r["recorded"] == 1
    assert [b["bet_id"] for b in bets(store)] == ["2up:ok:home"]


def test_the_price_age_limit_is_a_setting(pcfg, store):
    r = run(pcfg.with_overrides(placer_2up_max_age_s=30), store, feed([row(priced_ago=60)]))
    assert r["skipped"] == {"stale_price": 1} and bets(store) == []


def test_an_already_recorded_bet_never_reaches_the_engine(pcfg, store):
    run(pcfg, store, feed([row()]))
    calls = []

    class Counting(ClientEngine):
        def handle_bets(self, b):
            calls.append(b)
            return super().handle_bets(b)
    eng = Counting(store, pcfg, notify=False)
    twoup.read_once(pcfg, store, opener=serve(feed([row()])), now=NOW, engine=eng)
    assert calls == []


# ---- the account and the kill switch ---------------------------------------------------------

@pytest.mark.parametrize("account, says", [("", "no account set"), ("someone-else", "not in sub_clients.yaml")])
def test_with_no_usable_account_a_pass_does_nothing_and_raises_nothing(pcfg, store, account, says):
    c = pcfg.with_overrides(placer_2up_account=account)
    for _ in range(3):
        r = twoup.read_once(c, store, opener=never, now=NOW)
        assert r["outcome"] == "no account" and says in r["detail"]
    assert bets(store) == [] and store.open_escalations() == []


def test_a_disabled_account_is_no_account(pcfg, store):
    pcfg.sub_clients_file.write_text("sub_clients:\n  - account_id: test-2up\n    enabled: false\n")
    r = twoup.read_once(pcfg, store, opener=never, now=NOW)
    assert r["outcome"] == "no account" and "disabled" in r["detail"]
    assert store.open_escalations() == []


def test_with_the_kill_switch_on_nothing_is_read_or_recorded(pcfg, store):
    pcfg.kill_switch_file.write_text("x")
    r = twoup.read_once(pcfg, store, opener=never, now=NOW)
    assert r["outcome"] == "kill switch"
    assert bets(store) == [] and store.open_escalations() == []


# ---- the dry-run mark ------------------------------------------------------------------------

class Live(PlacementAdapter):
    name = "live"

    def place(self, bet, account):
        return PlacedResult(actual_price=bet.expected_price)


def test_the_bets_table_says_whether_a_bet_was_dry_run(pcfg, store):
    one = Bet("dry", "test-2up", [BetLeg("e", "m", "s", 2.0)], 1.0, 2.0, "single")
    two = Bet("live", "test-2up", [BetLeg("e", "m", "s", 2.0)], 1.0, 2.0, "single")
    ClientEngine(store, pcfg, adapter=Live(), notify=False).handle_bet(one)   # dry-run on: Live not used
    ClientEngine(store, pcfg.with_overrides(dry_run=False), adapter=Live(), notify=False).handle_bet(two)
    assert {b["bet_id"]: (b["status"], b["dry_run"]) for b in bets(store)} == {
        "dry": ("placed", 1), "live": ("placed", 0)}


def test_an_old_database_gains_the_dry_run_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE bets(bet_id TEXT PRIMARY KEY, account_id TEXT, body TEXT, received TEXT,"
                " status TEXT, actual_price REAL, updated TEXT)")
    con.execute("INSERT INTO bets(bet_id, status) VALUES('old', 'placed')")
    con.commit(); con.close()
    st = Store(path)
    assert st.conn.execute("SELECT dry_run FROM bets WHERE bet_id='old'").fetchone()[0] is None


# ---- the CLI ---------------------------------------------------------------------------------

def test_puntmore_placer_2up_prints_the_verdict_and_counts(pcfg, monkeypatch, capsys):
    monkeypatch.setattr(cli, "settings", pcfg)
    monkeypatch.setattr(twoup.aw26, "_open", serve(feed([row(), row(side="away", value=False)])))
    monkeypatch.setattr(twoup, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)}))
    cli.main(["placer", "2up", "--show"])
    out = capsys.readouterr().out
    assert "2up: done: board ok" in out
    assert "rows seen 2; skipped 1 (not_value 1); recorded 1 (placed 1); dry_run=True" in out
    assert "2up:ev1:home" in out and "2up:ev1:away" in out


def test_puntmore_placer_2up_exits_1_when_the_feed_is_refused(pcfg, monkeypatch, capsys):
    monkeypatch.setattr(cli, "settings", pcfg)
    monkeypatch.setattr(twoup.aw26, "_open", serve({"detail": "no"}, 403))
    with pytest.raises(SystemExit) as exc:
        cli.main(["placer", "2up"])
    assert exc.value.code == 1 and "not granted accabonus" in capsys.readouterr().out


# ---- the daemon ------------------------------------------------------------------------------

def test_the_2up_reader_is_off_unless_switched_on(tmp_path):
    """PUNTMORE_PLACER_2UP: off by default, on at 1 (a fresh interpreter reads the environment)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PUNTMORE_PLACER")}
    env["PUNTMORE_HUB_ROOT"] = str(tmp_path)          # no .env read
    hub = Path(__file__).resolve().parents[1]
    read = [sys.executable, "-c", "from puntmore_hub.config import Settings; s = Settings(); "
            "print(s.placer_2up, s.placer_2up_every_s, s.placer_flat_stake, s.placer_2up_max_age_s, "
            "repr(s.placer_2up_account), s.dry_run)"]
    out = subprocess.run(read, env={**env, "PYTHONPATH": str(hub)}, cwd=hub, capture_output=True, text=True, check=True)
    assert out.stdout == "False 120 10.0 3900 '' True\n"
    on = {**env, "PYTHONPATH": str(hub), "PUNTMORE_PLACER_2UP": "1"}
    assert subprocess.run(read, env=on, cwd=hub, capture_output=True, text=True, check=True).stdout.startswith("True ")


def _stop_after(n):
    calls = []

    def ingest(store, c):
        calls.append(c)
        if len(calls) == n:
            raise KeyboardInterrupt
        return {"tip_files": 0, "events": 0}
    return ingest


def test_switched_off_the_daemon_starts_no_2up_thread(pcfg, monkeypatch, caplog):
    started = []
    monkeypatch.setattr(inbox, "ingest", _stop_after(2))
    monkeypatch.setattr(twoup, "read_once", lambda *a, **kw: pytest.fail("a 2-Up pass ran"))
    monkeypatch.setattr(threading.Thread, "start", lambda t: started.append(t.name))
    caplog.set_level(logging.INFO, logger="puntmore.daemon")
    clock = [0.0]
    daemon.run(pcfg, sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0])
    assert started == [] and "placer_2up" not in daemon.last_status(pcfg)
    assert caplog.text.count("2-Up reader not started: switched off") == 1


def test_switched_on_the_daemon_reads_2up_on_its_own_thread(pcfg, monkeypatch):
    on = pcfg.with_overrides(placer_2up=True)
    passed, names = threading.Event(), []

    def read_once(c, store):
        names.append(threading.current_thread().name)
        passed.set()
        return {"outcome": "done", "detail": "board ok", "board": "ok", "seen": 1, "skipped": {},
                "recorded": 1, "statuses": {"placed": 1}, "rows": [{"bet_id": "x"}]}

    def ingest(store, c):
        assert passed.wait(5), "the 2-Up thread never ran a pass"
        for _ in range(500):                       # the pass is written just after it returns
            if "placer_2up" in daemon.last_status(on):
                break
            threading.Event().wait(0.01)
        raise KeyboardInterrupt
    monkeypatch.setattr(inbox, "ingest", ingest)
    monkeypatch.setattr(twoup, "read_once", read_once)
    daemon.run(on, sleep=lambda s: None)
    assert names[0] == "placer_2up"
    rec = daemon.last_status(on)["placer_2up"]
    assert rec["error"] is None and rec["at"] and rec["counts"]["recorded"] == 1
    assert "rows" not in rec["counts"]


def test_a_failing_2up_pass_never_stops_the_loop(pcfg, monkeypatch):
    stop, calls = threading.Event(), []

    def read_once(c, store):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        stop.set()
        return {"outcome": "no-bet", "detail": "stale", "board": "no-bet", "seen": 0, "skipped": {},
                "recorded": 0, "statuses": {}, "rows": []}
    monkeypatch.setattr(twoup, "read_once", read_once)
    monkeypatch.setattr(stop, "wait", lambda s: None)
    daemon._placer_2up_loop(pcfg.with_overrides(placer_2up=True), stop)
    assert len(calls) == 2
    assert daemon.last_status(pcfg)["placer_2up"]["counts"]["outcome"] == "no-bet"


# ---- review fixes (18 Nov 2026) --------------------------------------------------------------

from puntmore_hub.placement.adapters.base import PlacementFailed  # noqa: E402
from puntmore_hub.placement.adapters.dry_run import DryRunAdapter  # noqa: E402


def test_a_migration_another_process_already_ran_is_not_an_error(tmp_path, monkeypatch):
    """Two processes opening an old database together: the loser of the ALTER carries on."""
    import sqlite3
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE bets(bet_id TEXT PRIMARY KEY, account_id TEXT, body TEXT, received TEXT,"
                " status TEXT, actual_price REAL, updated TEXT)")
    con.execute("CREATE TABLE finder_posted(tip_id TEXT PRIMARY KEY, status TEXT, ts TEXT)")
    con.commit(); con.close()
    st = Store.__new__(Store)
    st.conn = sqlite3.connect(path)
    st.conn.row_factory = sqlite3.Row
    real = st.conn
    other = sqlite3.connect(path)               # the other process, adding both columns first
    other.execute("ALTER TABLE bets ADD COLUMN dry_run INTEGER")
    other.execute("ALTER TABLE finder_posted ADD COLUMN reason TEXT")
    other.commit(); other.close()

    class Stale:                                 # PRAGMA answered before the other process won
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *a):
            if sql.startswith("PRAGMA"):
                return real.execute("SELECT 'x', 'x' WHERE 0")
            return real.execute(sql, *a)

        def commit(self):
            real.commit()
    st.conn = Stale(real)
    st._migrate()                                # no "duplicate column name"
    cols = {r[1] for r in real.execute("PRAGMA table_info(bets)").fetchall()}
    assert "dry_run" in cols


def test_claiming_a_bet_id_twice_loses_the_second_time(store):
    assert store.claim_bet("b1", "acc", {"stake": 1.0}, dry_run=True) is True
    store.set_bet_status("b1", "placing")
    assert store.claim_bet("b1", "acc", {"stake": 9.0}, dry_run=False) is False
    row = dict(store.conn.execute("SELECT * FROM bets").fetchone())
    assert (row["status"], row["dry_run"], json.loads(row["body"])["stake"]) == ("placing", 1, 1.0)


def test_an_engine_that_loses_the_claim_never_runs_the_adapter(pcfg, store, monkeypatch):
    calls = []

    class Counting(DryRunAdapter):
        def place(self, bet, account):
            calls.append(bet.bet_id)
            return super().place(bet, account)
    b = Bet("x", "test-2up", [BetLeg("e", "m", "s", 2.0)], 1.0, 2.0, "single")
    store.claim_bet("x", "test-2up", b.to_dict(), dry_run=True)      # another caller got there first
    store.set_bet_status("x", "placing")
    monkeypatch.setattr(store, "bet_exists", lambda bid: False)      # it had not when we looked
    eng = ClientEngine(store, pcfg, adapter=Counting(), notify=False)
    assert eng.handle_bet(b) == "duplicate"
    assert calls == [] and bets(store)[0]["status"] == "placing"


class _Flip(DryRunAdapter):
    """Places the first bet, then does `flip` (the kill switch, the account) before the next."""
    def __init__(self, flip):
        self.flip, self.n = flip, 0

    def place(self, bet, account):
        self.n += 1
        if self.n == 1:
            self.flip()
        return super().place(bet, account)


@pytest.mark.parametrize("what", ["kill", "disable", "remove"])
def test_a_mid_pass_flip_stops_the_pass_without_a_failed_row(pcfg, store, what):
    def flip():
        if what == "kill":
            pcfg.kill_switch_file.write_text("x")
        elif what == "disable":
            pcfg.sub_clients_file.write_text("sub_clients:\n  - account_id: test-2up\n    enabled: false\n")
        else:
            pcfg.sub_clients_file.write_text("sub_clients: []\n")
    eng = ClientEngine(store, pcfg, adapter=_Flip(flip), notify=False)
    body = feed([row(event_id="a"), row(event_id="b"), row(event_id="c")])
    r = twoup.read_once(pcfg, store, opener=serve(body), now=NOW, engine=eng)
    assert r["outcome"] == "stopped" and r["recorded"] == 1
    assert [b["status"] for b in bets(store)] == ["placed"]
    assert store.open_escalations() == []
    assert [x["fate"] for x in r["rows"]] == ["placed", "not handed", "not handed"]


def test_read_once_logs_nothing_at_info(pcfg, store, caplog):
    caplog.set_level(logging.INFO, logger="puntmore.placement.twoup")
    run(pcfg, store, feed([row()]))
    run(pcfg, store, feed([], stale=True))
    assert [r for r in caplog.records if r.name == "puntmore.placement.twoup"] == []


def test_the_price_age_limit_defaults_to_the_slowest_tier_plus_the_feed(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PUNTMORE_PLACER")}
    hub = Path(__file__).resolve().parents[1]
    env.update(PUNTMORE_HUB_ROOT=str(tmp_path), PYTHONPATH=str(hub))
    read = [sys.executable, "-c", "from puntmore_hub.config import Settings; print(Settings().placer_2up_max_age_s)"]
    assert subprocess.run(read, env=env, cwd=hub, capture_output=True, text=True, check=True).stdout == "3900\n"


def test_with_dry_run_off_the_reader_still_records_through_the_dry_run_adapter(pcfg, store):
    r = run(pcfg.with_overrides(dry_run=False), store, feed([row()]))
    assert r["dry_run"] is True and r["recorded"] == 1
    assert bets(store)[0]["dry_run"] == 1


def test_a_failed_bet_is_not_retried_on_the_next_pass(pcfg, store):
    class Broken(DryRunAdapter):
        def place(self, bet, account):
            raise PlacementFailed("slip would not open")
    eng = ClientEngine(store, pcfg, adapter=Broken(), notify=False)
    first = twoup.read_once(pcfg, store, opener=serve(feed([row()])), now=NOW, engine=eng)
    assert first["statuses"] == {"failed": 1} and len(store.open_escalations()) == 1
    second = run(pcfg, store, feed([row()]))
    assert second["skipped"] == {"already_recorded": 1} and second["recorded"] == 0
    assert bets(store)[0]["status"] == "failed" and len(store.open_escalations()) == 1


def test_the_same_selection_twice_in_one_feed_is_recorded_once(pcfg, store):
    r = run(pcfg, store, feed([row(), row(price=2.6)]))
    assert r["recorded"] == 1 and r["skipped"] == {"repeated_in_feed": 1}
    assert len(bets(store)) == 1 and r["statuses"] == {"placed": 1}


@pytest.mark.parametrize("fmt", [
    lambda dt: dt.isoformat().replace("+00:00", "Z"),
    lambda dt: dt.replace(tzinfo=None).isoformat(),                                   # naive = UTC
    lambda dt: dt.astimezone(timezone(timedelta(hours=1))).isoformat(),             # +01:00
])
def test_times_with_z_no_offset_or_an_offset_are_read_alike(pcfg, store, fmt):
    fresh = {**row(event_id="fresh"), "priced_at": fmt(NOW - timedelta(seconds=60)),
             "kickoff": fmt(NOW + timedelta(minutes=30))}
    old = {**row(event_id="old"), "priced_at": fmt(NOW - timedelta(seconds=4000))}
    gone = {**row(event_id="gone"), "kickoff": fmt(NOW - timedelta(minutes=1))}
    r = run(pcfg, store, feed([fresh, old, gone]))
    assert r["skipped"] == {"stale_price": 1, "kicked_off": 1}
    assert [b["bet_id"] for b in bets(store)] == ["2up:fresh:home"]


def test_stale_and_partly_priced_together_is_no_bet(pcfg, store):
    body = feed([row()], stale=True, degraded=True, cannot_price=["bet365 chunk [17]: HTTP 500"])
    r = run(pcfg, store, body)
    assert r["outcome"] == "no-bet" and r["detail"].startswith("stale") and bets(store) == []


@pytest.mark.parametrize("status", [None, "ok", ["killed"]])
def test_an_unreadable_status_is_no_bet_not_an_error(pcfg, store, status):
    body = {**feed([row()]), "status": status}
    r = run(pcfg, store, body)
    assert r["outcome"] == "no-bet" and "status" in r["detail"] and bets(store) == []


@pytest.mark.parametrize("text", [
    "sub_clients:\n  - bookmaker: bet365\n",           # no account_id
    "sub_clients: [unclosed\n",                         # not YAML
    "- just a list\n",                                  # not a mapping
])
def test_a_malformed_sub_clients_file_is_a_plain_result(pcfg, store, text):
    pcfg.sub_clients_file.write_text(text)
    for _ in range(2):
        r = twoup.read_once(pcfg, store, opener=never, now=NOW)
        assert r["outcome"] == "no account" and "could not be read" in r["detail"]
    assert bets(store) == [] and store.open_escalations() == []


def test_the_cli_prints_whether_the_pass_was_dry_not_the_setting(pcfg, monkeypatch, capsys):
    live = pcfg.with_overrides(dry_run=False)
    monkeypatch.setattr(cli, "settings", live)
    monkeypatch.setattr(twoup.aw26, "_open", serve(feed([row()])))
    monkeypatch.setattr(twoup, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)}))
    cli.main(["placer", "2up"])
    assert "dry_run=True" in capsys.readouterr().out
