"""The fit caches across a restart (cards/fit_store.py)."""
import pickle
import time

import pytest

from app.cards import fit_store, laddered
from app.ttl_cache import MISS, TTLCache


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    caches = (laddered._FITS, laddered._PRICES, laddered._SPLITS)
    for c in caches:
        c.clear()
    monkeypatch.setattr(fit_store, "PATH", str(tmp_path / "fit_cache.pkl"))
    monkeypatch.setattr(fit_store, "_saved_writes", None)
    yield
    for c in caches:
        c.clear()


def _restart():
    for c in (laddered._FITS, laddered._PRICES, laddered._SPLITS):
        c.clear()


def test_the_caches_come_back_after_a_restart():
    laddered._FITS.set(("e1", "corners"), {"lam": 9.4})
    laddered._FITS.set(("e2", "corners"), laddered._REFUSED)
    laddered._PRICES.set((("e1", "corners"), 9.5), (0.52, 0.48))
    laddered._SPLITS.set(("e1", "corners", "x"), None)
    assert fit_store.save() is True
    _restart()
    assert fit_store.load() == 4
    assert laddered._FITS.get(("e1", "corners")) == {"lam": 9.4}
    assert laddered._FITS.get(("e2", "corners")) is laddered._REFUSED   # the same marker
    assert laddered._PRICES.get((("e1", "corners"), 9.5)) == (0.52, 0.48)
    assert laddered._SPLITS.get(("e1", "corners", "x")) is None


def test_nothing_is_written_when_nothing_changed():
    laddered._FITS.set("k", 1)
    assert fit_store.save() is True
    assert fit_store.save() is False
    laddered._FITS.set("k2", 2)
    assert fit_store.save() is True
    _restart()
    fit_store.load()
    assert fit_store.save() is False                 # just loaded: nothing new to write


def test_no_path_means_no_file(monkeypatch):
    monkeypatch.setattr(fit_store, "PATH", None)
    laddered._FITS.set("k", 1)
    assert fit_store.save() is False
    assert fit_store.load() == 0


def test_a_file_from_other_pricing_code_is_not_loaded(monkeypatch):
    laddered._FITS.set("k", 1)
    fit_store.save()
    _restart()
    monkeypatch.setattr(fit_store, "_version", lambda: "another")
    assert fit_store.load() == 0
    assert laddered._FITS.get("k") is MISS


def test_a_broken_or_missing_file_is_ignored():
    assert fit_store.load() == 0                     # no file yet
    with open(fit_store.PATH, "wb") as fh:
        fh.write(b"not a pickle")
    assert fit_store.load() == 0
    with open(fit_store.PATH, "wb") as fh:
        pickle.dump({"version": fit_store._version()}, fh)      # no caches
    assert fit_store.load() == 0


def test_an_entry_keeps_its_age_and_the_time_on_disk_counts(monkeypatch):
    laddered._FITS.set("k", 1)
    fit_store.save()
    _restart()
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 7 * 3600)   # read 7 h later
    assert fit_store.load() == 0                                   # past the 6 h TTL
    assert laddered._FITS.get("k") is MISS


def test_a_cache_reloads_entries_at_their_age():
    c = TTLCache(ttl=100, maxsize=10)
    c.set("held", "mine")
    assert c.load([("a", 10.0, 1), ("old", 100.0, 2), ("held", 1.0, "theirs")]) == 1
    assert c.get("a") == 1 and c.get("old") is MISS and c.get("held") == "mine"
    assert c.get("a", ttl=5) is MISS                 # ten seconds old already
    assert c.writes == 1


def test_the_diagnostics_say_what_boot_read_back():
    from app.cards import feed
    laddered._FITS.set("k", 1)
    fit_store.save()
    _restart()
    fit_store.load()
    got = feed.diagnose(None)["fit_cache"]
    assert got["kept"] is True and got["loaded_at_boot"] == 1
    assert got["held"] == {"fits": 1, "prices": 0, "splits": 0}
    assert got["last_saved"] is not None
