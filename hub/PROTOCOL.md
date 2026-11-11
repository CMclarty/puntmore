# The Scraper's drop files

The Scraper service hands the Hub its rows and its problems as files on the Hub's own disk; the
files never cross a network. The Hub reads them there, and posts one type, the prices, on to
Render. Each file is one JSON envelope, validated by `schemas/protocol.schema.json`.
A change to the format touches the Scraper (`scraper/sinks.py`) and the Hub (`puntmore_hub/inbox.py`)
in one pull request.

## The envelope

| Field | Meaning |
|---|---|
| `type` | `scraper_tips`, `scraper_event` or `scraper_prices`; nothing else is valid |
| `client_id` | always `scraper` |
| `msg_id` | one per file (hex) |
| `ts` | when the file was written (ISO 8601) |
| `body` | by type, below |

## The three types

| Type | Where | Body |
|---|---|---|
| `scraper_tips` | `<scraper data>/hub/tips/<ms>-<source>.json`, one per run of a target posting to `hub:<channel>` (a Finder source) | `source, at, url, channel, trust_weight, rows[]`; every row has `event, market, selection` and its `tip_id` (`$defs/scraper_tip_row`) |
| `scraper_event` | `<scraper data>/hub/events/<ms>-<ref>.json` | `kind` (`unhealthy_source` or `blocked_source`), `ref`, `detail`, `at` |
| `scraper_prices` | `<scraper data>/hub/prices/<ms>-<source>-<8hex>.json`, one per parse of one tab of a target whose `post` is a Render route; the 8 hex are the start of the `msg_id`, so two tabs parsed in one tick are two files | `source` (the target's name), `at` (the parse), `url` (the target's), `route` (`/api/paddy/most-prices` or `/api/paddy/saves-fair`, nothing else), `rows[]` (at least one object, the parser's rows unchanged) |

`at` is unix seconds. Every file is written atomically (temp file + rename), so the Hub never reads
half a file: it reads only `*.json`, never a `*.json.tmp`. The Hub moves each tips or events file to
`hub/done/` once handled, and each prices file to `hub/done/prices/`.

## Prices: what the Hub does with them

The inbox (`puntmore_hub/inbox.py`, `post_prices`) posts each prices file to Render as
`{"prices": rows}` on the file's `route`, through the Hub's one Render client (`finder.post_json`:
`PUNTMORE_RENDER_HTTP`, `PUNTMORE_RENDER_API_KEY` as a Bearer key, a 30 s timeout, no retry in
place). The daemon runs a price pass every 10 s, so a file is posted within about 10 s of being
written; `puntmore inbox` and the Console's "inbox now" run one too, after the tips and events.

A pass takes a non-blocking lock on `<scraper data>/hub/prices/.lock`. If another pass holds it, this
one posts nothing and counts the files as waiting, so two passes never post the same file. Files go
oldest first, by name. For each file:

| The file | What happens | Count |
|---|---|---|
| Render answers 2xx | moved to `done/prices/`; an info line with Render's answer | `price_files`, `price_rows` |
| Render answers 408, 429, a 5xx, a 1xx or a 3xx; a network error or a timeout; `PUNTMORE_RENDER_HTTP` or the key blank | the pass stops: this file and every later one wait for the next pass, so an older price is never posted after a newer one; one warning | `price_waiting` |
| Render answers any other 4xx (a bad body, a refused key) | moved to `done/prices/`; a warning with the code and the start of the answer | `price_dropped` |
| not JSON, not an object, `at` not a finite number, `route` not a string, `rows` not a non-empty list of objects | moved to `done/prices/`; a warning | `price_dropped` |
| older than 15 min (`PRICE_MAX_AGE_S`, 900 s, from `at`) | moved to `done/prices/` unsent; a warning | `price_dropped` |
| `route` not one this inbox knows (`PRICE_ROUTES`) | left in place for an inbox that knows it, and dropped once 15 min old; a warning | `price_waiting` |

The 15 min cut-off is there because Render stores no read time with a price: it keeps what arrives
for 36 h as if it were current. A backlog posted after an outage would put old Paddy prices on the
board as fresh ones; 15 min is `pp_most`'s own freshness limit.

`done/prices/` keeps its newest 300 files, apart from the tips' and events' `done/`. The rows pass
through unfiltered: the Scraper's sink and the inbox check only the file's shape, and Render's
handler is the rows' validator.

## Rules

- **Never a guessed row.** For tips, the Scraper's sink keeps out any row the Hub would reject (no
  event, market or selection, or a price outside 1.01–1000), so a file it writes always validates;
  the Hub checks again on ingest. The price sink filters nothing, so a prices file's body is what
  the parser read; a parser returns rows only from what the page shows.
- **`tip_id` is minted once**, where the tip is first seen (`scraper/ids.py` = `hub/puntmore_hub/ids.py`),
  and never re-minted downstream (`docs/dataflow.md`, rule 2).
- **A file written before the envelope** (a bare body) is still read as it is, so an update never
  strands what is already on disk.

## Tests

The canonical examples are `schemas/examples/scraper_tips.json`, `scraper_event.json` and
`scraper_prices.json`. `scraper/tests/test_sinks_schema.py` checks what the sinks write,
`hub/tests/test_inbox.py` what the inbox reads and what it posts to Render, and
`hub/tests/test_protocol.py` that the schema holds these three types and no other.
