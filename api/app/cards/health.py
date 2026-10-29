"""Paddy health: a rolling day of sweep tallies, posted with the morning message.

`laddered.LAST_RUN` says what the LAST tick did. This remembers the day, so
the morning post can answer the questions that otherwise need someone to open
/paddy/diagnostics: is the sweep running, is the team split throwing fixtures
away (`split_disagreement` — a systematic count means the handicap sign
convention is wrong), and is the desktop scraper feeding saves and "most"
prices.

`record` is called once per sweep; `report` builds the lines and starts the
next day. Both are cheap and never raise into the sweep.
"""
import threading

_LOCK = threading.Lock()

# Hybrid rows this fraction (or more) of the split's attempts, AND at least
# this many, is a pattern rather than a stale quote on one fixture.
DISAGREE_WARN_FRACTION = 0.25
DISAGREE_WARN_MIN = 5


def _fresh() -> dict:
    return {"sweeps": 0, "errors": 0, "rows": 0, "events": 0,
            "hybrid": 0, "split_disagreement": 0, "by_market": {}}


_DAY = _fresh()


def record(last_run: dict | None, rows: int, *, error: bool = False) -> None:
    """Add one sweep. `last_run` is laddered.LAST_RUN (its by_market tallies)."""
    with _LOCK:
        _DAY["sweeps"] += 1
        if error:
            _DAY["errors"] += 1
            return
        _DAY["rows"] += rows
        _DAY["events"] = max(_DAY["events"], int((last_run or {}).get("events", 0)))
        for market, tally in ((last_run or {}).get("by_market") or {}).items():
            _DAY["hybrid"] += tally.get("hybrid", 0)
            _DAY["split_disagreement"] += tally.get("split_disagreement", 0)
            _DAY["by_market"][market] = _DAY["by_market"].get(market, 0) + tally.get("rows", 0)


def report(*, saves: int = 0, most: int = 0) -> list[str]:
    """The day's lines, then start the next day. `saves` and `most` are the
    scraper stores' sizes."""
    global _DAY
    with _LOCK:
        d, _DAY = _DAY, _fresh()
    lines = []
    if d["sweeps"] == 0:
        lines.append("Paddy: ⚠ no sweep ran")
    else:
        markets = " · ".join(f"{m} {n}" for m, n in sorted(d["by_market"].items()) if n)
        lines.append(f"Paddy: {d['sweeps']} sweeps · {d['events']} fixtures · "
                     f"{d['rows']} value rows" + (f" ({markets})" if markets else "")
                     + (f" · ⚠ {d['errors']} failed" if d["errors"] else ""))
    attempts = d["hybrid"] + d["split_disagreement"]
    split = f"Team split: {d['hybrid']} hybrid rows · {d['split_disagreement']} disagreements"
    if (d["split_disagreement"] >= DISAGREE_WARN_MIN
            and d["split_disagreement"] >= DISAGREE_WARN_FRACTION * attempts):
        split += " ⚠ check the handicap sign"
    lines.append(split)
    lines.append(f"Scraper: saves {saves} · most {most}"
                 + ("" if saves or most else " ⚠ nothing posted"))
    return lines
