"""The one tip id. hub/tests/test_ids.py pins the SAME vectors against the
Hub's copy (hub/puntmore_hub/ids.py): change both or neither."""
import json
import time

from scraper import config
from scraper.ids import tip_id_for, with_tip_ids
from scraper.sinks import deliver_to_hub

# Computed with hub/puntmore_hub/store.py:tip_id_for as it was before the move.
VECTORS = [
    (("example-tipster", "A v B", "Match Result", "A", "2026-11-05T08:00:00+00:00"), "1461ee403d77791e"),
    (("src", "  Arsenal v Spurs ", "OVER/UNDER 2.5", "Over 2.5", "2026-11-05"), "4f9aa031f4c90cf3"),
]


def test_the_id_is_pinned():
    for args, want in VECTORS:
        assert tip_id_for(*args) == want


def test_the_id_normalises_case_and_space_and_keeps_only_the_day():
    a = tip_id_for("s", "A v B", "Match Result", "A", "2026-11-05T08:00:00+00:00")
    assert a == tip_id_for("s", " a V b ", "MATCH RESULT", "a ", "2026-11-05T23:59:00+00:00")
    assert a != tip_id_for("s", "A v B", "Match Result", "A", "2026-11-06T08:00:00+00:00")
    assert a != tip_id_for("t", "A v B", "Match Result", "A", "2026-11-05T08:00:00+00:00")


def test_every_row_in_a_hub_drop_carries_its_tip_id(tmp_path):
    t = config._target({"name": "tips-a", "site": "generic", "url": "https://a.example.com",
                        "parser": "example_tipster", "post": "hub:tips"})
    rows = [{"event": "A v B", "market": "Match Result", "selection": "A",
             "tip_time": "2026-11-05T08:00:00+00:00"},
            {"event": "C v D", "market": "Match Result", "selection": "D"},   # no tip_time: the drop's day
            {"event": "", "market": "x", "selection": "y"}]                   # the Hub rejects it: no id
    now = time.time()
    assert deliver_to_hub(tmp_path, t, rows, now)
    got = json.loads(next((tmp_path / "hub" / "tips").glob("*.json")).read_text())["rows"]
    assert got[0]["tip_id"] == tip_id_for("tips-a", "A v B", "Match Result", "A", "2026-11-05")
    day = time.strftime("%Y-%m-%d", time.gmtime(now))                      # UTC, as the Hub reads `at`
    assert got[1]["tip_id"] == tip_id_for("tips-a", "C v D", "Match Result", "D", day)
    assert "tip_id" not in got[2]
    assert "tip_id" not in rows[0]                                          # the parser's rows are untouched
    assert with_tip_ids("tips-a", rows, now) == got
