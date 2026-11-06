"""The one tip id. scraper/tests/test_ids.py pins the SAME vectors against the
Scraper's copy (scraper/ids.py), which mints it: change both or neither."""
import json
import time

from puntmore_hub import finder, inbox
from puntmore_hub.ids import tip_id_for
from puntmore_hub.store import tip_id_for as store_tip_id_for

VECTORS = [
    (("example-tipster", "A v B", "Match Result", "A", "2026-11-05T08:00:00+00:00"), "1461ee403d77791e"),
    (("src", "  Arsenal v Spurs ", "OVER/UNDER 2.5", "Over 2.5", "2026-11-05"), "4f9aa031f4c90cf3"),
]


def test_the_id_is_pinned():
    for args, want in VECTORS:
        assert tip_id_for(*args) == want
    assert store_tip_id_for is tip_id_for


def _drop(cfg, rows):
    d = cfg.inbox_dir / "tips"
    d.mkdir(parents=True, exist_ok=True)
    body = {"source": "tips-a", "at": time.time(), "url": "https://a.example.com", "channel": "tips",
            "trust_weight": 1.0, "rows": rows}
    (d / f"{int(time.time() * 1000)}-tips-a.json").write_text(json.dumps(body))


def test_the_inbox_keeps_the_scrapers_tip_id(cfg, store):
    _drop(cfg, [{"event": "A v B", "market": "Match Result", "selection": "A", "tip_id": "0123456789abcdef"}])
    inbox.ingest(store, cfg, notify=False)
    queued = finder.new_tips(store)[0]
    assert queued["tip_id"] == "0123456789abcdef"            # handed to HubAI as given, not re-minted
    assert store.conn.execute("SELECT tip_id FROM tips").fetchone()[0] == "0123456789abcdef"


def test_an_old_drop_without_tip_id_is_minted_here(cfg, store):
    _drop(cfg, [{"event": "A v B", "market": "Match Result", "selection": "A",
                 "tip_time": "2026-11-05T08:00:00+00:00"},
                {"event": "C v D", "market": "Match Result", "selection": "C", "tip_id": "not an id"}])
    inbox.ingest(store, cfg, notify=False)
    ids = [t["tip_id"] for t in finder.new_tips(store)]
    assert ids[0] == tip_id_for("tips-a", "A v B", "Match Result", "A", "2026-11-05")
    assert ids[1] != "not an id" and len(ids[1]) == 16
