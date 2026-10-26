# puntmore — Python client for the Puntmore API

A thin typed client (httpx) for the routes in `/api/docs`. It reads value and
records slips. **It places nothing**: placing a bet is outside this API.

```
pip install ./clients/python        # or: PYTHONPATH=clients/python
```

## Keys

A key is made on the server, once, and printed once:

```
python -m app.apikeys create bet-placer --tools value,aw26
```

`value` reads every source on `/api/value`; `aw26` lets it record slips. Give
a key only the grants it needs. See `docs/api.md`.

## Surface

```python
from puntmore import Puntmore, PuntmoreError

pm = Puntmore("https://puntmore.com", key)      # or AsyncPuntmore, same methods

pm.whoami()                                      # {"username": "api:bet-placer", "tools": [...]}
feed = pm.value(min_edge=0.03,                   # ValueFeed
                kickoff_within_hours=6,
                bookmaker="paddypower",          # optional
                source=None)                     # or "paddy" | "365" | "scorers" | "coupon"
feed.selections                                  # list[ValueSelection], best edge first
feed.errors                                      # {source: reason} for a source that FAILED
pm.paddy.feed(min_edge=None)                     # the Paddy page's feed (cards grant)
pm.aw26.slips()                                  # {"summary": ..., "slips": [...]}
pm.aw26.record(legs, offer="refund", stake=50, receipt="...", placed_at=None)
pm.state()                                       # whole-app snapshot (state grant)
pm.get("/coupons/types"); pm.post(path, json)    # anything else in /api/docs
```

`ValueSelection`: `source, bookmaker, event_id, event, home, away, kickoff,
sport, market, selection, line, side, odds, fair, edge, basis, url, seen_at`
(`edge = odds / fair - 1`; times are aware UTC datetimes; `line` is always an
x.5 when present). A non-2xx answer raises `PuntmoreError(status, detail)`.

## A bet-placer's loop

Poll the feed, decide, and — after the bet is placed by whatever does the
placing — record the slip on the AW 26 ledger. Recording is idempotent on
`receipt`: a second record of the same receipt is a 409, so a retry after a
timeout can never count a bet twice.

```python
import time
from puntmore import Puntmore, PuntmoreError

pm = Puntmore("https://puntmore.com", KEY)
seen = set()

while True:
    feed = pm.value(min_edge=0.04, kickoff_within_hours=3)
    if feed.errors:
        print("sources down:", feed.errors)          # an empty list is not "no value"
    for row in feed.selections:
        k = (row.source, row.event_id, row.market, row.selection, row.line)
        if k in seen:
            continue
        seen.add(k)
        if not decide(row):                           # your rules: stake, limits, exposure
            continue
        receipt = place_elsewhere(row)                # NOT part of this API
        try:
            pm.aw26.record(
                [{"event": row.event, "market": row.market, "selection": row.selection,
                  "odds": row.odds, "kickoff": row.kickoff.isoformat() if row.kickoff else None,
                  "fair_odds": row.fair}],
                offer="other", stake=STAKE, receipt=receipt,
            )
        except PuntmoreError as exc:
            if exc.status != 409:                     # 409 = already recorded
                raise
    time.sleep(120)                                   # the feeds refresh every 2 min
```

The feeds read the server's caches, so polling every couple of minutes costs no
vendor quota; polling faster returns the same prices.

## Tests

`api/tests/test_python_client.py` runs this client against the real app
in-process over `httpx.ASGITransport` (from `api/`: `python -m pytest -q`).
