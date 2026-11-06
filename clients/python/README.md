# puntmore — Python client for the Puntmore API

A thin typed client (httpx) for the routes in `/api/docs`. It reads value.
**It places nothing**: placing a bet is outside this API.

```
pip install ./clients/python        # or: PYTHONPATH=clients/python
```

## Keys

A key is made on the server, once, and printed once:

```
python -m app.apikeys create value-reader --tools value
```

`value` reads every source on `/api/value`, the cross-tool view. Give a key
only the grants it needs; the Bet Placer's key is in `docs/placer-spec.md`.
See `docs/api.md`.

## Surface

```python
from puntmore import Puntmore, PuntmoreError

pm = Puntmore("https://puntmore.com", key)      # or AsyncPuntmore, same methods

pm.whoami()                                      # {"username": "api:value-reader", "tools": [...]}
feed = pm.value(min_edge=0.03,                   # ValueFeed
                kickoff_within_hours=6,
                bookmaker="paddypower",          # optional
                source=None)                     # or "paddy" | "scorers" | "coupon" | "2up"
feed.selections                                  # list[ValueSelection], best edge first
feed.errors                                      # {source: reason} for a source that FAILED
pm.paddy.feed(min_edge=None)                     # the Paddy page's feed (cards grant)
pm.state()                                       # whole-app snapshot (state grant)
pm.get("/coupons/types"); pm.post(path, json)    # anything else in /api/docs
```

`ValueSelection`: `source, bookmaker, event_id, event, home, away, kickoff,
sport, market, selection, line, side, odds, fair, edge, basis, url, seen_at`
(`edge = odds / fair - 1`; times are aware UTC datetimes; `line` is always an
x.5 when present). A non-2xx answer raises `PuntmoreError(status, detail)`.

## Reading value by hand

`/api/value` is a cross-tool view: one shape across the tools, a failed source
named in `errors`. It is not the Bet Placer's source: the Bet Placer reads each
board's own feed (`/api/paddy/feed`, `/api/2up/feed`, `/api/finder/feed`), which
carries the full status (`docs/placer-spec.md`). A quick look:

```python
from puntmore import Puntmore

pm = Puntmore("https://puntmore.com", KEY)
feed = pm.value(min_edge=0.04, kickoff_within_hours=3)
if feed.errors:
    print("sources down:", feed.errors)          # an empty list is not "no value"
for row in feed.selections:
    print(row.source, row.event, row.selection, row.odds, row.fair, row.edge)
```

The feeds read the server's caches, so polling every couple of minutes costs no
vendor quota; polling faster returns the same prices.

## Tests

`render/api/tests/test_python_client.py` runs this client against the real app
in-process over `httpx.ASGITransport` (from `render/api/`: `python -m pytest -q`).
