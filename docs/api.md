# Puntmore API

Every Puntmore tool is reachable by a machine through one key-authenticated
API: the same routes the site uses, under `/api`, each tool behind its own
grant. A bet-placing service can ask one question — what value is on offer
right now, across every tool — at `GET /api/value`, and record what it placed
on the AW 26 ledger. **Nothing in this API places a bet**, and nothing
automates a bookmaker account: read, record, report.

The live, typed reference is **`/api/docs`** (Swagger UI) and
**`/api/openapi.json`**, behind auth like everything else. This page is the
guide; the schema is the contract.

## Auth

Two ways in, one permission model:

| Caller | How | Grants |
|---|---|---|
| A person | log in on the site (`POST /api/auth/login`), session cookie | `User.permissions` (`AUTH_USERS`) |
| A machine | `Authorization: Bearer <key>` (preferred) or `X-API-Key: <key>` | the API client's grant |

Grants use one grammar: `*` (everything) or names joined by `+`. Tool grants:
`coupons`, `accabonus` (365 acca), `cards` (Paddy), `goalscorers` (365
scorers), `pnl`, `kelly`, `boost`, `shop`, `aw26`, `scraper`. Machine grants:
`value` (every source on `/api/value`), `state` (`/api/state`), `ingest` (the
scraper / Bookie Bashing write paths).

- A key resolves through the same seam as a session (`security.user_permissions`),
  so every route that admits a user with grant X admits a key with grant X.
- When a key is sent it IS the identity: a bad or revoked key is `401` even
  next to a valid session cookie. A valid key without the grant is `403`.
- Where a route records who did something (an AW 26 slip, a P&L bet, a shop
  visit), a key acts as `api:<name>` — its own ledger, never a person's.
- `GET /api/auth/me` tells a key its name and grants.
- The two static env keys keep working exactly as before:
  `STATE_API_KEY` is the `state` grant and `SAVES_INGEST_KEY` the `ingest`
  grant (same `X-API-Key` header; Bearer works too). Those routes stay
  key-only: a session does not open them. Unset static key and no client with
  the grant → `503`, as before.
- Keys are stored only as a sha256. They are never logged or echoed; the CLI
  prints one exactly once, at creation.

## Keys — the CLI

```
python -m app.apikeys create <name> --tools paddy,aw26   # prints the key ONCE
python -m app.apikeys list                              # names, grants, last used — never keys
python -m app.apikeys revoke <name>                     # disables at once; row kept
```

`--tools` takes grant names or `*`, comma- or plus-separated; the page names
work too (`paddy` = `cards`, `365` = `accabonus+goalscorers`, `scorers` =
`goalscorers`). Names are unique, including revoked ones. To rotate: create a
new client, switch the caller over, revoke the old one.

**In production** run it in the Render service's **Shell** tab:
`cd /app && python -m app.apikeys create bet-placer --tools value,aw26`. The
shell has the service's env (`DATABASE_URL` → the `/data` disk), so the key
lands in the live database. Copy the printed key into the caller's own secret
store; it cannot be shown again. There is no env-var way to create an API
client — that would put a plaintext key in the dashboard; the static env keys
above are the only env-configured ones.

## The value feed

```
GET /api/value?min_edge=&kickoff_within_hours=&bookmaker=
GET /api/value/{source}            # paddy | 365 | scorers | coupon
```

Grant `value` (or `*`) sees every source; a tool grant sees that tool's
source only (`cards` → paddy, `accabonus` → 365, `goalscorers` → scorers,
`coupons` → coupon); none of them → `403`. A source whose tool is switched
off on the server (`ENABLE_*`) is not listed.

| Param | Meaning |
|---|---|
| `min_edge` | rows with `edge >= min_edge` (may be negative, down to -0.5). Omitted: `edge > 0` |
| `kickoff_within_hours` | only selections kicking off between now and now + N h |
| `bookmaker` | `paddypower` \| `bet365` \| `williamhill` |

Response (`ValueFeedOut`):

```json
{
  "generated_at": "2026-10-26T15:00:00Z",
  "sources": ["paddy", "365", "scorers", "coupon"],
  "errors": {"365": "RuntimeError: ..."},
  "selections": [ValueSelection, ...]
}
```

`errors` names a source that FAILED, as opposed to one with no value — an
empty list is never ambiguous. Selections are sorted by edge, best first.

`ValueSelection` — one shape for every tool:

| Field | |
|---|---|
| `source` | `paddy` \| `365` \| `scorers` \| `coupon` |
| `bookmaker` | `paddypower` \| `bet365` \| `williamhill` |
| `event_id` | the source's own fixture id, or null |
| `event`, `home`, `away` | the fixture as the source names it |
| `kickoff` | ISO 8601 UTC, or null |
| `sport` | league / sport key (`soccer_epl`, …) |
| `market` | `corners`, `cards`, `goals`, `saves`, `team_corners`, `team_cards`, `most_corners`, `most_cards`, `h2h`, `first_goalscorer`, or a coupon type code (`TTS2`, `WO25`, …) |
| `selection` | what to back, as the tool prints it |
| `line` | null where the market has none; otherwise always an x.5 |
| `side` | `over` \| `under` \| `home` \| `away` \| `draw` \| `tie`, or null |
| `odds` | the bookmaker's price |
| `fair` | the de-vigged fair price |
| `edge` | `odds / fair - 1` |
| `basis` | how the fair was reached (`ladder`, `hybrid`, `saves`, `devig`, `grid`, …) |
| `url` | null unless known |
| `seen_at` | when the price was read or the row computed (UTC) |

Each source is the tool's own feed builder (`app/cards/feed.py`,
`app/accabonus/feed.py`, the stored goalscorer rows, the coupon tracker's
priced legs still to kick off), so the feed can never disagree with the pages.
It reads the same caches as the pages: polling costs no vendor quota beyond
what the pages cost, and prices refresh every ~2 minutes. Integer and quarter
lines are never listed (`pricing/counts/lines.is_half_line`). Ladder and
bundle coupons are not listed — their value belongs to a combination, not a
selection.

## Endpoints by tool

All paths are under `/api`. "Grant" is what a key (or user) needs.

| Tool (grant) | Method & path | Returns |
|---|---|---|
| any | `GET /auth/me` | who is calling, grants |
| — | `POST /auth/login`, `POST /auth/logout` | session (browsers) |
| value (`value` or a tool) | `GET /value`, `GET /value/{source}` | `ValueFeedOut` |
| state (`state`) | `GET /state` | whole-app snapshot |
| coupons (`coupons`) | `GET /coupons/types`, `/coupons/types/{type}/selections`, `/coupons/types/{type}/bundles`, `/coupons/periods`, `/coupons/upload-options`, `/coupons`, `/coupons/{id}/legs` | tracker reads |
| coupons (`coupons`) | `POST /coupons/{id}/refresh`, `PATCH /coupons/{id}/legs/{leg}`, `POST /upload`, `POST /upload/{id}/rescan` | re-price, correct, upload a photo |
| 365 acca (`accabonus`) | `GET /accabonus/feed`, `/accabonus/arbs`, `/accabonus/diagnostics`, `/accabonus/eligible-keys`, `/accabonus/probe-books` (costs credits) | |
| 365 scorers (`goalscorers`) | `GET /goalscorers/feed` | |
| Paddy (`cards`) | `GET /paddy/feed`, `/paddy/diagnostics`, `/paddy/shadow`, `/paddy/oddspapi-check` | |
| Paddy ingest (`ingest`) | `POST /paddy/saves-fair`, `POST /paddy/most-prices` | scraper writes |
| AW 26 (`aw26`) | `GET /aw26/slips`, `/aw26/snapshots`, `/aw26/slips/{id}/image`, `/aw26/results-check` | ledger reads |
| AW 26 (`aw26`) | `POST /aw26/slips/record` | **record a placed bet** (structured legs) |
| AW 26 (`aw26`) | `POST /aw26/slips` (photo), `PATCH /aw26/slips/{id}`, `DELETE /aw26/slips/{id}` | upload, correct, delete |
| AW 26 ingest (`ingest`) | `POST /aw26/fairs`, `/aw26/slips/ingest`, `/aw26/results`, `GET /aw26/open-leagues` | desktop scripts |
| P&L (`pnl`) | `GET/POST /pnl/{dogs\|virtuals}/bets`, `GET /pnl/{c}/bets.csv`, `DELETE /pnl/{c}/bets/{id}` | per-caller ledger |
| shop (`shop`) | `GET /shop`, `POST /shop/visits`, `DELETE /shop/visits[/{id}]` | per-caller route |
| scraper (`scraper`) | `GET /scraper`, `PUT /scraper/leagues/{id}`, `/scraper/markets/{key}`, `/scraper/timings/{site}` | control page |
| scraper box (`ingest`) | `GET /scraper/config`, `POST /scraper/status` | |
| diagnostics (`*`, or `state`) | `GET/DELETE /diagnostics/errors` | error ring buffer |
| — | `GET /health`, `GET /ready` | probes, unauthenticated |

`kelly` and `boost` are client-side calculators with no routes. The legacy
aliases (`/cards/*`, `/tennis/*`) still answer and are hidden from the schema.

`POST /aw26/slips/record` body (`AwSlipRecordIn`):

```json
{"receipt": "R-123", "placed_at": "2026-10-26T14:02:00Z", "offer": "refund",
 "stake": 50, "legs": [{"event": "Arsenal v Chelsea", "market": "Total Corners",
 "selection": "Over 9.5", "odds": 1.9, "kickoff": "2026-10-26T15:00:00Z",
 "fair_odds": 1.75}]}
```

`offer` is `refund` | `boost` | `other`. It is priced and settled exactly like
a photographed slip; `fair_odds` per leg is optional (missing ones are
matched against the day's Bookie Bashing fairs). A receipt already on the
ledger is `409`, so a retried record never double-counts.

## Python client

`clients/python/puntmore` — `Puntmore(url, key)` / `AsyncPuntmore`, with
`.value()`, `.paddy.feed()`, `.aw26.slips()`, `.aw26.record(...)`, `.state()`,
`.whoami()` and `.get()` / `.post()` for the rest. See its README for a
bet-placer's polling loop.

## Versioning

The API is version **1** (`info.version` in the schema: `1.0.0`, plus the
deploy's commit). Within a version, changes are **additive only**: new routes,
new optional fields, new enum values in documented open sets (a new `source`,
`market` or `basis`). Clients must ignore fields they do not know — the
Python client does. Nothing is renamed, retyped or removed in place: a
breaking change ships beside the old one under `/api/v2/...`, and `/api/...`
keeps its v1 behaviour until every caller has moved.

## Code map

| | |
|---|---|
| auth, grants, key resolution | `api/app/security.py` |
| API clients table / CLI | `api/app/models.py::ApiClient`, `api/app/apikeys.py` |
| value feed | `api/app/value/service.py`, `api/app/routers/value.py` |
| per-tool services | `api/app/cards/feed.py`, `api/app/accabonus/feed.py`, `api/app/coupons/service.py`, `api/app/pnl/service.py`, `api/app/shop/service.py`, `api/app/aw26/record.py` (+ the existing `aw26/`, `cards/`, `goalscorers/` packages) |
| response models | `api/app/schemas.py` |
| route walk test | `api/tests/test_openapi.py` |
