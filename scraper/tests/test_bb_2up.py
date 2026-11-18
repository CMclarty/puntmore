"""Bookie Bashing's Early Payout Tracker, read from the real page and the
ratings payload it was drawn from (18 Nov 2026, signed in; cut into
fixtures/bb_2up/: tracker.html, 24 of the table's 194 rows, and
ratings.json, the same 24 records). The parser reads the page's HTML and
the capture's payloads and nothing else: the stand-in page below answers
`content()` only and fails on anything else, so a click (or any other call
on the page) fails the test. No test loads a Bookie Bashing page."""
import copy
import json
import pathlib
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scraper import behaviour
from scraper.parsers import PARSERS
from scraper.parsers.bb_2up import bb_2up, latest_ratings, read
from scraper.rest import Rest

FIX = pathlib.Path(__file__).parent / "fixtures" / "bb_2up"
PAGE = (FIX / "tracker.html").read_text(encoding="utf-8")
PAYLOAD = json.loads((FIX / "ratings.json").read_text(encoding="utf-8"))
ARRIVED = datetime(2026, 11, 18, 12, 9, 47, tzinfo=timezone.utc).timestamp()   # the payload, as captured
NOW = datetime(2026, 11, 18, 12, 10, 39, tzinfo=timezone.utc).timestamp()      # the page, as dumped

FIRST = {"bookmaker": "bet365",
         "event": "Real Madrid v PSV", "home": "Real Madrid", "away": "PSV",
         "selection": "PSV", "side": "away",
         "book_odds": 13.0, "lay": 15.0, "rating": 108.86, "n_odds": 11.95,
         "kickoff": "2026-11-24T20:00:00Z",
         "updated_s": 137,                      # 12:10:39 less `changed`, 12:08:22
         "seen_at": "2026-11-18T12:10:39Z"}
FIRST_ROW_CELLS = '<td data-v-01949c02="">Real Madrid v PSV</td><td data-v-01949c02="">PSV</td>'


class _Page:
    """content() only: any other call (a click, a locator) fails."""

    def __init__(self, html):
        self._html = html

    def content(self):
        return self._html

    def __getattr__(self, name):
        raise AssertionError(f"the parser touched page.{name}")


def _payload():
    return copy.deepcopy(PAYLOAD)


def _capture(*payloads):
    """A capture holding (arrived, body) payloads as the page received them."""
    ring = deque((ts, "http", "https://example.invalid/x", b if isinstance(b, str) else json.dumps(b))
                 for ts, b in payloads)
    return SimpleNamespace(recent=ring, pinned=deque(), last_activity=0.0)


def _read(html=PAGE, payload=None, now=NOW, within_h=0, arrived=ARRIVED):
    return read(html, (arrived, payload if payload is not None else _payload()), now, within_h)


def _edit(old, new, html=PAGE, count=1):
    assert html.count(old) >= count, old
    return html.replace(old, new, count)


@pytest.fixture
def no_window(tmp_path):
    behaviour.set_(tmp_path, "games.within_h", 0)
    behaviour.load(tmp_path)
    yield
    behaviour.load(tmp_path / "none")


# --- reading ----------------------------------------------------------------
def test_registered_and_reads_without_touching_the_page_or_the_capture(no_window):
    assert PARSERS["bb_2up"] is bb_2up
    capture = _capture((ARRIVED, _payload()))
    rows = bb_2up(_Page(PAGE), capture, now=NOW)
    assert len(rows) == 24 and rows.said is None and rows[0] == FIRST
    assert capture.last_activity == 0.0          # each payload arriving is the page's activity


def test_the_first_row_exactly():
    rows = _read()
    assert len(rows) == 24 and rows.said is None
    assert rows[0] == FIRST


def test_the_last_row_and_a_home_selection():
    rows = _read()
    assert rows[-1] == {**FIRST, "event": "Paris FC v Angers", "home": "Paris FC", "away": "Angers",
                        "selection": "Angers", "book_odds": 8.0, "lay": 12.0, "rating": 80.32,
                        "n_odds": 9.97, "kickoff": "2026-11-22T14:00:00Z"}
    parma = next(r for r in rows if r["event"] == "Parma v Roma")
    assert (parma["selection"], parma["side"], parma["home"], parma["away"]) == ("Parma", "home", "Parma", "Roma")


def test_book_odds_are_the_payloads_price_not_the_tables_rounding():
    hibs = next(r for r in _read() if r["event"] == "Hibernian v Dundee")
    assert hibs["book_odds"] == 1.833 and 'class="backPrice">1.83<' in PAGE     # the table rounds it


def test_kickoff_is_the_payloads_instant_which_the_page_shows_in_london_time():
    london = ZoneInfo("Europe/London")
    for rec in PAYLOAD["records"]:
        shown = datetime.fromtimestamp(rec["ko"], london).strftime("%d/%m/%Y, %H:%M:%S")
        assert f">{shown}<" in PAGE
    # A summer kick-off: 20:00 in London on 15 Jul is 19:00 UTC.
    p = _payload()
    p["records"][0]["ko"] = int(datetime(2026, 7, 15, 20, 0, tzinfo=london).timestamp())
    assert _read(payload=p)[0]["kickoff"] == "2026-07-15T19:00:00Z"


def test_updated_s_is_now_less_changed_not_the_frozen_text():
    assert {r["updated_s"] for r in _read()} == {137}
    assert {r["updated_s"] for r in _read(now=NOW + 60)} == {197}       # the table still says "1m ago"
    p = _payload()
    del p["records"][0]["changed"]
    p["records"][1]["changed"] = int(NOW) + 5                          # a clock a little ahead: 0, not -5
    rows = _read(payload=p)
    assert rows[0]["updated_s"] is None and rows[1]["updated_s"] == 0


def test_names_with_an_ampersand_and_accents():
    event, sel = "Brighton & Hove Albion v Atlético Madrid", "Atlético Madrid"
    html = _edit(FIRST_ROW_CELLS, '<td data-v-01949c02="">Brighton &amp; Hove Albion v Atl&eacute;tico Madrid</td>'
                                  '<td data-v-01949c02="">Atlético Madrid</td>')
    p = _payload()
    p["records"][0]["eventName"] = "Brighton & Hove Albion v Atlético Madrid"    # decomposed é
    p["records"][0]["selectionName"] = "Atlético Madrid"
    rows = _read(html, p)
    assert len(rows) == 24 and rows.said is None
    assert (rows[0]["event"], rows[0]["home"], rows[0]["away"], rows[0]["selection"], rows[0]["side"]) == (
        event, "Brighton & Hove Albion", "Atlético Madrid", sel, "away")


# --- the payload in the capture ----------------------------------------------
def test_the_newest_ratings_payload_is_found_by_its_shape():
    books = {"records": [{"id": 58, "name": "Bet365"}, {"id": 4, "name": "William Hill"}]}
    prefs = {"prefs": {"filters": {"competition": "all"}}, "updatedAt": "2026-11-18T12:08:08.000Z"}
    older = _payload()
    older["records"][0]["bookPrice"] = 99.0
    capture = _capture((ARRIVED - 60, older), (ARRIVED - 1, books), (ARRIVED, _payload()),
                       (ARRIVED + 1, prefs), (ARRIVED + 2, '{"records": "not json'))
    at, body = latest_ratings(capture)
    assert at == ARRIVED and body["records"][0]["bookPrice"] == 13
    assert latest_ratings(_capture((ARRIVED, books), (ARRIVED, prefs))) is None


@pytest.mark.parametrize("capture", [None, _capture()])
def test_no_payload_yet_posts_nothing(capture):
    rows = bb_2up(_Page(PAGE), capture, now=NOW)
    assert rows == [] and rows.said.startswith("no ratings payload received yet")


def test_a_hung_page_posts_nothing_and_is_not_marked_active():
    capture = _capture((NOW - 181, _payload()))
    rows = bb_2up(_Page(PAGE), capture, now=NOW)
    assert rows == [] and capture.last_activity == 0.0
    assert rows.said.startswith("the newest ratings payload is 3 min old: the page has stopped refreshing")


def test_a_payload_just_inside_three_minutes_is_read():
    assert len(_read(arrived=NOW - 179)) == 24


# --- never a short board -----------------------------------------------------
def test_a_payload_cut_short_by_its_limit_posts_nothing():
    p = _payload()
    p["count"], p["limit"] = 30, 24
    rows = _read(payload=p)
    assert rows == [] and rows.said == "the tracker has 30 selections but the page fetched 24 (its limit is 24); nothing posted"


def test_a_table_short_of_the_payload_posts_nothing():
    p = _payload()
    p["records"].append(copy.deepcopy(p["records"][0]))
    p["count"] = 25
    rows = _read(payload=p)
    assert rows == [] and "its table has 24 rows and the newest payload 25 records" in rows.said


def test_a_shown_count_that_differs_posts_nothing():
    rows = _read(_edit("24/24 shown", "25/25 shown"))
    assert rows == [] and rows.said.startswith("the page shows 25, its table has 24 rows")
    rows = _read(_edit("24/24 shown", "24/24 rows"))
    assert rows == [] and "shown\" line is missing" in rows.said


def test_a_table_not_drawn_from_the_newest_payload_posts_nothing():
    p = _payload()
    p["records"][3]["eventName"] = "West Ham v Burnley"
    p["records"][3]["selectionName"] = "Burnley"
    rows = _read(payload=p)
    assert rows == [] and rows.said.startswith("the table is not the newest payload's selections")


def test_a_few_unreadable_records_are_skipped_and_counted():
    p = _payload()
    p["records"][0]["bookPrice"] = "13.00"
    p["records"][5]["ko"] = None
    rows = _read(payload=p)
    assert len(rows) == 22 and rows[0]["event"] == "Chesterfield v Shrewsbury"
    assert rows.said == ("2 of 24 records skipped: 1 with no kick-off, "
                         "1 with odds that are not a number above 1")


def test_more_than_a_tenth_unreadable_posts_nothing():
    p = _payload()
    for i in (0, 5, 9):
        p["records"][i]["fairOdds"] = 0
    rows = _read(payload=p)
    assert rows == [] and rows.said == ("3 of 24 records skipped: 3 with odds that are not a number above 1"
                                        " - over 10%, nothing posted")


@pytest.mark.parametrize("field, value, why", [
    ("score", float("nan"), "with no EV"),
    ("nOdds", 1.0, "with odds that are not a number above 1"),
    ("homeAway", True, "where homeAway disagrees with the names"),
    ("eventName", "Real Madrid - PSV", "with an event that is not 'Home v Away'"),
])
def test_a_field_that_cannot_be_read_skips_the_record(field, value, why):
    p = _payload()
    p["records"][0][field] = value
    html = PAGE if field != "eventName" else _edit(">Real Madrid v PSV<", ">Real Madrid - PSV<")
    rows = _read(html, p)
    assert len(rows) == 23 and rows.said == f"1 of 24 records skipped: 1 {why}"


def test_a_selection_that_is_neither_team_is_skipped_and_quoted():
    p = _payload()
    p["records"][0]["selectionName"] = "Draw"
    html = _edit(FIRST_ROW_CELLS, FIRST_ROW_CELLS.replace(">PSV<", ">Draw<"))
    rows = _read(html, p)
    assert len(rows) == 23 and rows.said == "1 of 24 records skipped: 1 selection is neither team (first: 'Draw')"


def _empty_tracker():
    start, end = PAGE.index("<tbody"), PAGE.index("</tbody>")
    html = PAGE[:PAGE.index(">", start) + 1] + PAGE[end:]
    return _edit("24/24 shown", "0/0 shown", html), {**_payload(), "count": 0, "records": []}


@pytest.mark.parametrize("within_h", [0, 48])
def test_an_empty_tracker_rests_for_look_again_s(within_h):
    html, p = _empty_tracker()
    with pytest.raises(Rest) as exc:
        read(html, (ARRIVED, p), NOW, within_h, look_again_s=86400)
    assert exc.value.until == NOW + 30 * 60 and exc.value.why == "the tracker lists no selections"


# --- the page ----------------------------------------------------------------
def test_a_header_that_is_not_the_trackers_gives_no_rows_and_says_so():
    rows = _read(_edit('<th data-v-01949c02="">Lay</th>', '<th data-v-01949c02="">Back</th>'))
    assert rows == [] and rows.said.startswith("the table's header is not the Early Payout Tracker's: ")
    assert "| Back |" in rows.said


def test_a_page_with_no_table_says_logged_out_or_loading():
    rows = _read("<html><body><p>Please log in</p></body></html>")
    assert rows == [] and "logged out of Bookie Bashing" in rows.said


# --- the settings the rows are read for --------------------------------------
def test_a_metric_other_than_lay_posts_nothing():
    p = _payload()
    p["metric"] = "last_trade"
    rows = _read(payload=p)
    assert rows == [] and rows.said == "the tracker's Metric is 'last_trade', not 'lay'; nothing posted"
    p = _payload()
    p["records"][4]["metric"] = "back"
    assert _read(payload=p).said == "a record's metric is not 'lay'; nothing posted"


def test_another_book_posts_nothing():
    p = _payload()
    p["records"][2]["bookName"] = "William Hill"
    rows = _read(payload=p)
    assert rows == [] and rows.said == ("the Book filter is not bet365 alone (the payload has 'william hill');"
                                        " nothing posted")


def test_the_dynamic_2up_model_posts_nothing():
    p = _payload()
    rec = p["records"][0]
    rec["score"] = rec["ratings"]["dam_lay"]["ev"]
    rows = _read(payload=p)
    assert rows == [] and rows.said == ("the 2-up model is not the fixed trigger (EV is not the lay rating's);"
                                        " nothing posted")


@pytest.mark.parametrize("ratings", [None, {}, {"lay": {}}, {"lay": {"ev": "108.86"}}, "lay"])
def test_a_record_with_no_lay_rating_cannot_confirm_the_model(ratings):
    p = _payload()
    if ratings is None:
        del p["records"][7]["ratings"]
    else:
        p["records"][7]["ratings"] = ratings
    rows = _read(payload=p)
    assert rows == [] and rows.said == "cannot confirm the 2-up model: a record has no lay rating; nothing posted"


# --- the 48-hour window ------------------------------------------------------
def test_only_games_within_the_window_are_rows():
    # 73 hours from 12:10:39 on Wed 18 Nov reaches 13:10:39 on Sat 21 Nov: the 12:30 games only.
    rows = _read(within_h=73)
    assert [r["event"] for r in rows] == ["Norwich v Cardiff", "Reading v Bromley", "Newport County v Swindon"]
    assert rows.said == "21 kicking off outside the next 73 hours left out"


def test_outside_the_window_is_not_a_skip():
    p = _payload()
    p["records"][0]["bookPrice"] = None          # 1 skip in 24: under a tenth
    rows = _read(payload=p, within_h=73)
    assert len(rows) == 3
    assert rows.said == ("1 of 24 records skipped: 1 with odds that are not a number above 1; "
                         "20 kicking off outside the next 73 hours left out")


def test_a_game_under_way_is_out():
    p = _payload()
    p["records"][5]["ko"] = int(NOW) - 60        # Norwich v Cardiff kicked off a minute ago
    rows = _read(payload=p, within_h=73)
    assert [r["event"] for r in rows] == ["Reading v Bromley", "Newport County v Swindon"]


def test_the_window_is_the_games_setting(tmp_path):
    behaviour.set_(tmp_path, "games.within_h", 73)
    behaviour.load(tmp_path)
    try:
        rows = bb_2up(_Page(PAGE), _capture((ARRIVED, _payload())), now=NOW)
    finally:
        behaviour.load(tmp_path / "none")
    assert len(rows) == 3
    with pytest.raises(Rest):                                                  # the default, 48
        bb_2up(_Page(PAGE), _capture((ARRIVED, _payload())), now=NOW)


# --- the rest ----------------------------------------------------------------
NEXT_KO = datetime(2026, 11, 21, 12, 30, tzinfo=timezone.utc).timestamp()     # Norwich v Cardiff, the earliest


def test_no_game_within_48_hours_rests_until_48_hours_before_the_earliest():
    with pytest.raises(Rest) as exc:
        read(PAGE, (ARRIVED, _payload()), NOW, 48, look_again_s=2 * 86400)
    assert exc.value.until == NEXT_KO - 48 * 3600                # Thu 19 Nov 12:30 UTC, inside two days
    assert exc.value.why == ("no game on the tracker kicks off within 48 hours - "
                             "next is Norwich v Cardiff, Sat 21 Nov 12:30")


def test_the_rest_is_capped_by_look_again_s():
    with pytest.raises(Rest) as exc:
        read(PAGE, (ARRIVED, _payload()), NOW, 24, look_again_s=3600)
    assert exc.value.until == NOW + 3600                         # 48 h before Sat 12:30 is two days off


def test_the_rest_reads_look_again_s_from_the_games_setting(tmp_path):
    behaviour.set_(tmp_path, "games.look_again_s", 7200)
    behaviour.load(tmp_path)
    try:
        with pytest.raises(Rest) as exc:
            bb_2up(_Page(PAGE), _capture((ARRIVED, _payload())), now=NOW)     # games.within_h 48
    finally:
        behaviour.load(tmp_path / "none")
    assert exc.value.until == NOW + 7200


def test_every_game_already_under_way_rests_for_look_again_s():
    p = _payload()
    for rec in p["records"]:
        rec["ko"] = int(NOW) - 600
    with pytest.raises(Rest) as exc:
        read(PAGE, (ARRIVED, p), NOW, 48, look_again_s=86400)
    assert exc.value.until == NOW + 86400 and exc.value.why == "no game on the tracker kicks off within 48 hours"


def test_one_game_inside_the_window_is_no_rest():
    p = _payload()
    p["records"][23]["ko"] = int(NOW) + 3600                     # Paris FC v Angers in an hour
    rows = read(PAGE, (ARRIVED, p), NOW, 48, look_again_s=86400)
    assert [r["event"] for r in rows] == ["Paris FC v Angers"]


def test_no_window_is_no_rest():
    assert len(read(PAGE, (ARRIVED, _payload()), NOW, 0, look_again_s=86400)) == 24


def _unhealthy():
    stale = (PAGE, (NOW - 181, _payload()))
    p = _payload()
    p["metric"] = "last_trade"
    guard = (PAGE, (ARRIVED, p))
    p = _payload()
    p["records"][3]["selectionName"] = "Burnley"
    p["records"][3]["eventName"] = "West Ham v Burnley"
    mismatch = (PAGE, (ARRIVED, p))
    p = _payload()
    p["count"] = 30
    short = (PAGE, (ARRIVED, p))
    p = _payload()
    for i in (0, 5, 9):
        p["records"][i]["fairOdds"] = 0
    unreadable = (PAGE, (ARRIVED, p))
    header = (_edit('<th data-v-01949c02="">Lay</th>', '<th data-v-01949c02="">Back</th>'), (ARRIVED, _payload()))
    return {"stale": stale, "guard": guard, "mismatch": mismatch, "short": short,
            "unreadable": unreadable, "header": header, "no payload": (PAGE, None)}


@pytest.mark.parametrize("case", sorted(_unhealthy()))
def test_an_unhealthy_read_never_rests(case):
    html, ratings = _unhealthy()[case]
    rows = read(html, ratings, NOW, 48, look_again_s=86400)     # nothing within 48 h: healthy would rest
    assert rows == [] and rows.said
