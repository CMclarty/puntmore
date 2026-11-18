"""Bookie Bashing's Early Payout Tracker, read from the real page (18 Nov
2026, signed in; cut into fixtures/bb_2up/tracker.html: 24 of its 194 rows,
the first 12, the 101st and 102nd and the last 10). The parser reads the
page's HTML and nothing else: the stand-in page below answers `content()`
only and fails on anything else, so a click (or any other call on the page)
fails the test. No test loads a Bookie Bashing page."""
import pathlib
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scraper.parsers import PARSERS
from scraper.parsers.bb_2up import _kickoff, bb_2up, read, updated_s

PAGE = (pathlib.Path(__file__).parent / "fixtures" / "bb_2up" / "tracker.html").read_text(encoding="utf-8")
NOW = datetime(2026, 11, 18, 12, 10, 39, tzinfo=timezone.utc).timestamp()

FIRST = {"bookmaker": "bet365",
         "event": "Real Madrid v PSV", "home": "Real Madrid", "away": "PSV",
         "selection": "PSV", "side": "away",
         "book_odds": 13.0, "lay": 15.0, "rating": 108.86, "n_odds": 11.95,
         "kickoff": "2026-11-24T20:00:00Z",
         "updated_s": 60,
         "seen_at": "2026-11-18T12:10:39Z"}
FIRST_ROW_CELLS = ('<td data-v-01949c02="">Real Madrid v PSV</td><td data-v-01949c02="">PSV</td>'
                   '<td data-v-01949c02="" class="backPrice">13.00</td>')


class _Page:
    """content() only: any other call (a click, a locator) fails."""

    def __init__(self, html):
        self._html = html

    def content(self):
        return self._html

    def __getattr__(self, name):
        raise AssertionError(f"the parser touched page.{name}")


def _edit(old, new, html=PAGE, count=1):
    assert html.count(old) >= count, old
    return html.replace(old, new, count)


def test_registered_and_reads_the_real_page_without_touching_it():
    assert PARSERS["bb_2up"] is bb_2up
    capture = SimpleNamespace(last_activity=0.0)
    rows = bb_2up(_Page(PAGE), capture)
    assert len(rows) == 24


def test_the_header_is_found_and_every_row_is_read():
    rows = read(PAGE, NOW)
    assert len(rows) == 24
    assert {r["bookmaker"] for r in rows} == {"bet365"}
    assert all(r["updated_s"] == 60 for r in rows)


def test_the_first_row_exactly():
    assert read(PAGE, NOW)[0] == FIRST


def test_the_last_row_a_negative_ev():
    last = read(PAGE, NOW)[-1]
    assert last == {**FIRST, "event": "Paris FC v Angers", "home": "Paris FC", "away": "Angers",
                    "selection": "Angers", "side": "away", "book_odds": 8.0, "lay": 12.0,
                    "rating": 80.32, "n_odds": 9.97, "kickoff": "2026-11-22T14:00:00Z"}


def test_a_home_selection():
    rows = read(PAGE, NOW)
    parma = next(r for r in rows if r["event"] == "Parma v Roma")
    assert (parma["selection"], parma["side"], parma["home"], parma["away"]) == ("Parma", "home", "Parma", "Roma")


def test_kickoff_is_london_time_converted_to_utc():
    # GMT in November: the page's 20:00 is 20:00 UTC (the fixture's first row).
    assert read(PAGE, NOW)[0]["kickoff"] == "2026-11-24T20:00:00Z"
    # BST, a date built by hand: London 20:00 on 15 Jul is 19:00 UTC.
    html = _edit("24/11/2026, 20:00:00", "15/07/2026, 20:00:00")
    assert read(html, NOW)[0]["kickoff"] == "2026-07-15T19:00:00Z"
    # The night the clocks go back (25 Oct 2026): 00:30 is still BST, 12:30 is GMT.
    assert _kickoff("25/10/2026, 00:30:00") == "2026-10-24T23:30:00Z"
    assert _kickoff("25/10/2026, 12:30:00") == "2026-10-25T12:30:00Z"


@pytest.mark.parametrize("text, want", [
    ("1m ago", 60), ("25s ago", 25), ("2h ago", 7200), ("0s ago", 0), ("12m ago", 720),
    ("just now", None), ("", None), ("1d ago", None), ("m ago", None), ("5 minutes ago", None)])
def test_updated_s(text, want):
    assert updated_s(text) == want


def test_unknown_last_update_text_is_null_not_a_skip():
    html = _edit(">1m ago<", ">a while back<")
    rows = read(html, NOW)
    assert len(rows) == 24 and rows[0]["updated_s"] is None


@pytest.mark.parametrize("old, new, why", [
    ('class="backPrice">13.00<', 'class="backPrice">-<', "with odds that are not a number above 1"),
    ('class="layPrice">15.00<', 'class="layPrice">0.00<', "with odds that are not a number above 1"),
    ('yellow2">108.86<', 'yellow2">n/a<', "with EV not 100 + EV £(100)"),
    ('yellow2">108.86<', 'yellow2">8.86<', "with EV not 100 + EV £(100)"),
    ("24/11/2026, 20:00:00", "31/11/2026, 20:00:00", "with a KO that is not a date"),
    ("24/11/2026, 20:00:00", "Tue 20:00", "with a KO that is not a date"),
    ('title="bet365" style', 'title="" style', "with no bookmaker"),
    (">Real Madrid v PSV<", ">Real Madrid - PSV<", "with an event that is not 'Home v Away'"),
])
def test_a_cell_that_cannot_be_read_skips_the_row_and_is_counted(old, new, why):
    rows = read(_edit(old, new), NOW)
    assert len(rows) == 23 and rows[0]["event"] == "Chesterfield v Shrewsbury"
    assert f"1 of 24 rows skipped: 1 {why}" in rows.said


def test_a_selection_that_is_neither_team_is_skipped_counted_and_quoted():
    html = _edit(FIRST_ROW_CELLS, FIRST_ROW_CELLS.replace(">PSV</td>", ">Draw</td>"))
    rows = read(html, NOW)
    assert len(rows) == 23
    assert "1 of 24 rows skipped: 1 selection is neither team (first: 'Draw')" in rows.said


def test_a_team_named_with_a_v_is_resolved_by_the_selection():
    html = _edit(FIRST_ROW_CELLS, FIRST_ROW_CELLS.replace("Real Madrid v PSV", "Real Madrid v PSV v B"
                                                          ).replace(">PSV</td>", ">PSV v B</td>"))
    row = read(html, NOW)[0]
    assert (row["home"], row["away"], row["side"]) == ("Real Madrid", "PSV v B", "away")


def test_another_books_badge_is_its_own_bookmaker():
    html = _edit('title="bet365" style="background-color: rgb(112, 88, 166); color: rgb(255, 255, 255);">B365<',
                 'title="William Hill" style="background-color: rgb(0, 0, 0); color: rgb(255, 255, 255);">WH<')
    rows = read(html, NOW)
    assert len(rows) == 24
    assert rows[0] == {**FIRST, "bookmaker": "william hill"}
    assert {r["bookmaker"] for r in rows[1:]} == {"bet365"}


def test_a_header_that_is_not_the_trackers_gives_no_rows_and_says_so():
    html = _edit('<th data-v-01949c02="">Lay</th>', '<th data-v-01949c02="">Back</th>')
    rows = read(html, NOW)
    assert rows == []
    assert rows.said.startswith("the table's header is not the Early Payout Tracker's: ")
    assert "| Back |" in rows.said


def test_a_page_with_no_table_says_logged_out_or_loading():
    rows = read("<html><body><p>Please log in</p></body></html>", NOW)
    assert rows == [] and "logged out of Bookie Bashing" in rows.said
    assert read("", NOW).said == rows.said


def test_the_pages_shown_count_against_the_rows_read():
    # The fixture keeps the page's own "194/194 shown" over 24 rows.
    assert read(PAGE, NOW).said == "the page says 194 shown, 24 rows in the table"
    assert read(_edit("194/194 shown", "24/194 shown"), NOW).said is None


def test_fresh_rows_mark_the_capture_active():
    capture = SimpleNamespace(last_activity=0.0)
    before = time.time()
    bb_2up(_Page(PAGE), capture)
    assert capture.last_activity >= before


def test_rows_all_older_than_five_minutes_leave_the_page_to_the_stale_reload():
    html = _edit("194/194 shown", "24/194 shown")
    html = html.replace(">1m ago<", ">6m ago<")
    capture = SimpleNamespace(last_activity=0.0)
    rows = bb_2up(_Page(html), capture)
    assert len(rows) == 24 and capture.last_activity == 0.0
    assert rows.said == "no row updated in the last 5 minutes: the page is left for the runner's stale reload"
    # One row fresh is enough.
    html = html.replace(">6m ago<", ">5m ago<", 1)
    rows = bb_2up(_Page(html), capture)
    assert capture.last_activity > 0 and rows.said is None
