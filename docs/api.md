# Puntmore API

Every Puntmore tool is reachable by a machine through one key-authenticated
API: the same routes the site uses, under `/api`, each tool behind its own
grant. The three boards each serve a one-way feed (`GET /api/paddy/feed`,
`/api/2up/feed`, `/api/finder/feed`); the Bet Placer reads those
(`docs/placer-spec.md`). `GET /api/value` is a cross-tool view of the same
value in one shape. **Nothing in this API places a bet**, and nothing automates
a bookmaker account: read and report. Every route answers on both hosts, the
main domain and `me` (`me` once `ME_HOST` is set; `GET /api/auth/site` says
which host it is, `""` while there is none).

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
scorers), `hills`, `pnl`, `kelly`, `boost`, `shop`, `finder` (Finder,
Callum's tips on `me`, or the main domain while `ME_HOST` is blank: Callum's
account and his Bet Placer's key only). `*`
includes `finder`, so a key or account issued to anyone but Callum never
carries `*`, only named grants. Machine grants: `value` (every source on
`/api/value`), `state` (`/api/state`), `ingest` (the write paths of
Scraper control's posts of the Scraper's prices, the Bookie Bashing script
and HubAI, and the Bet Placer's Finder status). A key holding a grant name
that no longer exists is granted nothing for it.

**The Bet Placer's key** (register, 6 Nov 2026) carries `accabonus`, `cards`,
`finder` and `ingest`; **a third party's** carries `accabonus` only.

- A key resolves through the same seam as a session (`security.user_permissions`),
  so every route that admits a user with grant X admits a key with grant X.
- When a key is sent it IS the identity: a bad or revoked key is `401` even
  next to a valid session cookie. A valid key without the grant is `403`.
- Where a route records who did something (a P&L bet, a shop
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
python -m app.apikeys create <name> --tools paddy,value  # prints the key ONCE
python -m app.apikeys grant <name> --tools aw26,cards    # adds to its grant; same key, not printed
python -m app.apikeys list                              # names, grants, last used — never keys
python -m app.apikeys revoke <name>                     # disables at once; row kept
```

`--tools` takes grant names or `*`, comma- or plus-separated; the page names
work too (`paddy` = `cards`, `365` = `accabonus+goalscorers`, `scorers` =
`goalscorers`). Names are unique, including revoked ones. To rotate: create a
new client, switch the caller over, revoke the old one.

**In production** run it in the Render service's **Shell** tab:
`cd /app && python -m app.apikeys create bet-placer --tools accabonus,cards,finder,ingest`. The
shell has the service's env (`DATABASE_URL` → the `/data` disk), so the key
lands in the live database. Copy the printed key into the caller's own secret
store; it cannot be shown again. There is no env-var way to create an API
client — that would put a plaintext key in the dashboard; the static env keys
above are the only env-configured ones.

## The cross-tool value view

```
GET /api/value?min_edge=&kickoff_within_hours=&bookmaker=
GET /api/value/{source}            # paddy | scorers | coupon | 2up | finder
```

Grant `value` (or `*`) sees every source; a tool grant sees that tool's
source only (`cards` → paddy, `goalscorers` → scorers (parked),
`coupons` → coupon, `accabonus` → 2up); none of them → `403`. A source whose
tool is switched off on the server (`ENABLE_*`; 2up has `ENABLE_TWOUP`) is not
listed. On `2up` rows `fair` is the 2-up-adjusted fair price
1 / (P(win) + P(rescue)), so `edge` keeps its meaning (odds / fair − 1).

`finder` is a **private** source: only the `finder` grant reads it, and only
at `GET /api/value/finder`. It is never part of the combined `GET /api/value`
(for `*` neither) and the `value` grant does not reach it. Its rows are
Finder's `offered` rows: `odds` and `fair` are the prices the tip quotes
(`basis: "tip"`), `bookmaker` is the tip's own, `sport` is `football` or
`horse`, and with no `min_edge` every offered row is served.

| Param | Meaning |
|---|---|
| `min_edge` | rows with `edge >= min_edge` (may be negative, down to -0.5). Omitted: `edge > 0` |
| `kickoff_within_hours` | only selections kicking off between now and now + N h |
| `bookmaker` | `paddypower` \| `bet365` \| `williamhill` |

Response (`ValueFeedOut`):

```json
{
  "generated_at": "2026-10-26T15:00:00Z",
  "sources": ["paddy", "coupon", "2up"],
  "errors": {"2up": "RuntimeError: ..."},
  "selections": [ValueSelection, ...]
}
```

`errors` names a source that FAILED, as opposed to one with no value — an
empty list is never ambiguous. It also names a source answering but not
fresh: `2up` is `"killed: TWOUP_KILL_SWITCH is on"` (no rows), `"not yet
refreshed"` (no rows) or `"stale: prices from HH:MM UTC — OddsPapi not
answering"` (HH:MM the oldest price behind the board) beside the last good
board's rows, each with `seen_at` = when its prices were fetched. `"cannot
price: <reason>"` (no rows) is a deploy where every pass since boot failed or
raised; `"partly priced: <gap>; <gap>"` sits beside the rows of a board where
part of the pass failed. Selections are sorted by edge, best first.

`ValueSelection` — one shape for every tool:

| Field | |
|---|---|
| `source` | `paddy` \| `scorers` \| `coupon` \| `2up` \| `finder` |
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
| `seen_at` | when the price was read or the row computed (UTC); on `2up` rows the row's `priced_at` |

Each source is the tool's own feed builder (`app/cards/feed.py`,
`app/twoup/feed.py`, the stored goalscorer rows (parked), the coupon tracker's
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
| — | `GET /auth/site` | `{"me_host": …}`: `me`'s host, `""` while `ME_HOST` is blank (public; the shell reads it at boot) |
| value (`value` or a tool) | `GET /value`, `GET /value/{source}` | `ValueFeedOut` |
| state (`state`) | `GET /state` | whole-app snapshot |
| coupons (`coupons`) | `GET /coupons/types`, `/coupons/types/{type}/selections`, `/coupons/types/{type}/bundles`, `/coupons/periods`, `/coupons/upload-options`, `/coupons`, `/coupons/{id}/legs` | tracker reads |
| coupons (`coupons`) | `POST /coupons/{id}/refresh`, `PATCH /coupons/{id}/legs/{leg}`, `POST /upload`, `POST /upload/{id}/rescan` | re-price, correct, upload a photo |
| 365 scorers (`goalscorers`) | `GET /goalscorers/feed` — PARKED, not mounted (`ENABLE_GOALSCORERS=false`) | |
| 365 2-Up (`accabonus`) | `GET /2up/feed` (`?value_only=`), `GET /2up/diagnostics` | the served board of priced rows; what the feed last saw, the rescue table and the dials |
| Paddy (`cards`) | `GET /paddy/feed`, `/paddy/diagnostics`, `/paddy/shadow`, `/paddy/oddspapi-check`, `/paddy/board-at?at=` | each feed item carries `priced_at`, when the board's OddsPapi prices were fetched (the oldest chunk; null when unknown; added 8 Nov 2026). `board-at` (10 Nov 2026) is the board as it stood at `at` (ISO 8601, UTC unless it says otherwise), in the feed's shape: each line's Paddy price and fair then, or its first price after when it had none yet, for every game not kicked off at `at`, soonest first; `priced_at` is when its newest line was seen. Every priced line, no value floor (down to −50%), kept 14 days after kick-off (`cards/history.py`). Paddy only, not Hills |
| Paddy ingest (`ingest`) | `POST /paddy/saves-fair`, `POST /paddy/most-prices` | Scraper control (the Hub's inbox), posting the Scraper service's prices |
| Finder writes (`ingest`, key only) | `POST /finder/rows`, `POST /finder/rows/{tip_id}/status` | HubAI's rows; the Bet Placer's `placed`\|`gone` |
| Finder reads (`finder`) | `GET /finder/feed`, `GET /finder/board` (`?days=3`), `GET /value/finder` | the offered rows; the board on `me` |
| P&L (`pnl`) | `GET/POST /pnl/{dogs\|virtuals}/bets`, `GET /pnl/{c}/bets.csv`, `DELETE /pnl/{c}/bets/{id}` | per-caller ledger |
| AW 26 (`aw26`) | `GET /aw26/summary`, `GET /aw26/slips`, `POST /aw26/slips/record`, `PATCH /aw26/slips/{id}`, `DELETE /aw26/slips/{id}`, `GET /aw26/slips/{id}/image`, `POST /aw26/selections/{leg_id}/result` | Callum's slips: a day's figures and selections, a day's slips, record one, correct one or set its legs' results, delete one, a slip's photo, one result for a selection on every slip |
| AW 26 photos (`aw26`) | `POST /aw26/photos`, `GET /aw26/photos`, `POST /aw26/photos/{id}/take`, `/fail`, `/resend`, `GET /aw26/photos/{id}/image`, `DELETE /aw26/photos/{id}` | the queue of slip photos waiting for the Hub |
| shop (`shop`) | `GET /shop`, `POST /shop/visits`, `DELETE /shop/visits[/{id}]` | per-caller route |
| diagnostics (`*`, or `state`) | `GET/DELETE /diagnostics/errors` | error ring buffer |
| — | `GET /health`, `GET /ready` | probes, unauthenticated |

### 365 2-Up feed (`GET /api/2up/feed`)

A one-way feed: the module takes nothing back and keeps no account
(`docs/2up-engine-spec.md`). Every reader gets
the same rows. The board is priced by the scheduler job `twoup_refresh` every
`TWOUP_FEED_TTL` seconds (5 min by default) and the route only reads it; a
request never fetches. `value_only=true` drops rows outside the price band or
under the edge floor (each row also carries the `value` flag).

```
{
  "generated_at": "...",        // when the served board was priced; now if there is none
  "available": true,            // false only with no OddsPapi key
  "status": {
    "provider": "oddspapi",
    "degraded": false,          // something could not be priced (see cannot_price)
    "cannot_price": [],
    "stale": false,             // the last pass failed; an older board is being served
    "served_at": "...",         // ISO time of the served board, null before the first pass
    "not_yet_refreshed": false, // no pass since boot: the list is empty
    "last_pass": {...},         // counts, errors and cannot_price of the last pass
    "killed": false             // TWOUP_KILL_SWITCH: candidates is empty
  },
  "candidates": [{
    "event_id", "event", "home", "away", "kickoff", "competition",
    "selection", "side",        // "home" | "away"; event_id + side identify a row
    "market", "price",          // bet365's decimal price at priced_at
    "p_win", "rescue", "fair_price", "edge", "ev", "value",
    "priced_at"                 // when the prices behind this row were fetched
  }]
}
```

On a failed pass — every request failed, reference data down, no configured
tournament resolved, or the pass itself raised — the last good board is kept
with its old `priced_at` and `status.stale` is true. A pass where one chunk's
request failed but another priced replaces the board and names the failed
chunk in `cannot_price` (`degraded: true`, `stale: false`). Before the first
pass and with the kill switch on the list is empty. `served_at: null` with
`not_yet_refreshed: false` means no pass has priced yet; see `cannot_price`.
With no OddsPapi key `status` is just `degraded: true`, `killed` and
`cannot_price: ["ALL — ODDSPAPI_API_KEY unset"]`. The kill switch also empties
the `2up` source of `GET /api/value` (and names it in its `errors`).

`GET /api/2up/diagnostics` returns `{feed, rescue_table, global_kill_switch,
settings}`: `feed` is the same `status` block as above (without `killed`),
`rescue_table` the path, `loaded`, `default`, `key` and the buckets in use,
`global_kill_switch` the flag, and `settings` the dials (`twoup_min_edge`,
`twoup_min_price`, `twoup_max_price`, `twoup_min_lead_minutes`,
`twoup_tournament_ids`, `twoup_feed_ttl`, `twoup_horizon_hours`). No secrets.

### Finder (`/api/finder/*`, Nov 2026)

Callum's tips (`docs/finder-spec.md`). HubAI on the Hub judges
each tip and posts the verdict; Render keeps, serves and expires it, and never
prices a tip or loads a page.

- `POST /finder/rows` — ingest key (HubAI). Body `{"rows": [FinderRowIn, …]}`
  (at most 500): `tip_id, origin, sport (football|horse), bookmaker, event,
  home?, away?, event_id?, kickoff?, market, selection, line?, side?, odds?,
  fair?, advised_stake?, reason?, status (asked|offered|rejected), expiry?`.
  An `offered` row must carry `kickoff`, `odds` and `fair`, or the whole post
  is a `422`; an `asked` or `rejected` row may leave all three out.
  `event_id` is the canonical fixture key (`football:<YYYY-MM-DD>:<home>-v-<away>`
  or `horse:<YYYY-MM-DD>:<course>:<HH:MM>`, defined in `hub/README.md`, "Finder").
  Upsert on `tip_id`. Answers `{stored, updated, refused: [[tip_id, reason]],
  duplicates: [tip_id], rows: [{tip_id, status}]}`: `refused` is a post
  against a final row (`final`) or a move the status machine lacks
  (`offered → asked`; `duplicate of <tip_id>` for an offered row re-posted
  onto another live or placed bet), the row left unchanged; `duplicates` are
  rows for a bet already `asked`, `offered` or `placed`, stored `rejected`
  with reason `duplicate of <tip_id>`; `rows` is every posted row, in order,
  with the status it holds after the post (null when there is no row), which
  is what the Hub records. Re-posting an `offered` row as `rejected`
  withdraws it; a `placed` row cannot be withdrawn (`final`).
- `POST /finder/rows/{tip_id}/status` — ingest key (the Bet Placer). Body
  `{"status": "placed"|"gone", "at": <ISO>}` → the row; `404` unknown tip,
  `409` a final row or one never offered, `422` any other word.
- `GET /finder/feed` — `finder` grant. `{generated_at, rows}`: `offered`
  rows not yet expired, soonest kick-off first.
- `GET /finder/board?days=3` — `finder` grant (`days` 1–30). Every row
  created in the window, all statuses, newest first.

A row (`FinderRowOut`) carries every column: the tip fields above (`kickoff`
and `odds` null on an asked row that has none), `edge` (`odds / fair - 1`,
null without both), `status`, `expires_at` (the earlier of `kickoff` and
`expiry`; with neither, `seen_at` + 48 h), `asked_at`, `answered_at`,
`seen_at` (HubAI's latest post), `created_at`, `updated_at`, all UTC.
Status words: `asked → offered | rejected | expired`, `offered → placed |
gone | rejected | expired`; `placed`, `gone`, `rejected` and `expired` are
final. Render expires rows every minute; rows are kept 30 days. One bet per
(event_id or event, market, line, selection), held by an `asked`, `offered`
or `placed` row. `ENABLE_FINDER=false` removes the routes.

### AW 26 (`/api/aw26/*`, back 9 Nov 2026)

Callum's slips from the Paddy terminal, on `me`. The page and HubAI on the Hub
call the same routes; HubAI uses an API client key granted `aw26`, and `cards`
to read the Paddy board the fairs come from (`python -m app.apikeys grant
<name> --tools aw26,cards` adds both to an existing key). A photo uploaded on
the page waits on Render's queue until the Hub collects it. Render reads no
photo, looks nothing up and runs nothing in the background for AW 26; it does
the sums when a request arrives (`aw26/offers.py`). Days are London days. A
slip placed before 9 Nov 2026, the first day, stays stored and counts nowhere:
it is in no figure, no list and no mark.

- `GET /aw26/summary?day=` — the figures for one day (default today): `day`,
  `today`, `first_day`, `days` (how many days a slip was placed on, a count),
  `targets` (`ev_per_day` £1,000, `profit` £100,000), `chosen`, `to_date` and
  `selections`. `chosen` and `to_date` each carry `ev`, `ev_target`,
  `profit`, `staked`, `slips` and `open`. `chosen` is the slips placed on the
  day, its profit the slips decided on it; `to_date` is every slip, against
  £1,000 EV for each of the `days`. `selections` are the day's selections in
  aggregate, soonest kick-off first: a leg is on its kick-off's day, or its
  slip's when it has none, and today lists today and every later day. Each
  has `leg_id`, `key`, `game` (the leg's `fixture_id`, or else the event's
  words on one London day: the kick-off's, or the day placed when the leg has
  no kick-off), `event`,
  `market`, `selection`, `kickoff`, `day`, `slips` (how many carry it),
  `odds` (their mean), `result` (`pending` until every leg has the same one)
  and `fair_assumed` (any leg's). `400` for a day before `first_day` or after
  today.
- `GET /aw26/slips?day=` — `{slips}`: the slips placed on the day (default
  today), newest first, with their legs.
- `POST /aw26/slips/record` — body `AwSlipRecordIn`: `receipt`, `placed_at`
  (as printed, London, or ISO 8601), `offer` (`refund` | `boost` | `other` |
  `free`, a free bet), `stake` (blank on a refund, boost or free bet slip
  means £50), `photo_id` (the queued
  photo it was read from, or none), `legs` (1–40, each `event`, `market`,
  `selection`, `odds`, `kickoff`, `fair_odds`, `kind` (`corners` | `cards` |
  `goals` | `saves` | `other`), `line`, `side` (`over` | `under`),
  `fixture_id` and `fair_assumed`). A leg with `fair_assumed` has no fair: its
  odds are its fair and any `fair_odds` sent is ignored. The legs are kept as
  sent; Render reads nothing off their words. The slip is filed under the
  caller (`api:<name>` for a key). `409` when the receipt is already in, so a
  slip sent twice is counted once. With a `photo_id`: `404` for no such photo,
  `409` for one already a slip; recorded, the photo is `done` and is the
  slip's photo (`409` when another record made it a slip first); a receipt
  already in makes it `duplicate`; no receipt is a `400`, and a photo
  `waiting` or `reading` goes back to `waiting` with the reason "no receipt
  number" while it has had fewer than 3 tries, else `failed`.
- `PATCH /aw26/slips/{id}` — any of `offer`, `stake`, `placed_at`, `receipt`,
  `legs`. `legs` **replaces the whole list**: send every leg, with its `id`,
  `odds`, `fair_odds` and `result` (`pending` | `won` | `lost` | `void`). A
  leg sent without its `fair_odds` loses it, and the slip's EV with it. A leg
  whose fair is assumed keeps following its odds only when its `fair_odds` is
  sent back as stored; any other value, or none, becomes the leg's fair and
  the leg is no longer assumed. A leg whose `market` or `selection` changes
  loses its `kind`, `line` and `side`; one whose `event` changes loses its
  `fixture_id`. `409` for a receipt already on another slip.
- `POST /aw26/selections/{leg_id}/result` — body `{"result": …}` (`pending` |
  `won` | `lost` | `void`) → `{legs, slips}`, how many legs were set and
  slips re-settled. Sets the result on every leg of that leg's selection (the
  same game, by `fixture_id` or else the event's words on the same London
  day, and the same market and selection) on every slip, and re-settles each.
  Two matches between the same teams on different days are two games. A mark
  is not a correction: the EV, the fairs and `edited` stand. `400` for any
  other word, `404` for no such leg or one on a slip from before the first
  day.
- `DELETE /aw26/slips/{id}`, with its photo; `GET /aw26/slips/{id}/image`, the
  photo it was read from (`404` when it has none).

The EV is fixed once every leg has its odds and a fair price; until then it
is null and `ev_note` says how many legs are waiting. A slip is never valued
off the legs that did price. The slip's status and returns follow from its
legs' results under its offer's terms.

The figures are cash (Callum, 10 Nov 2026). A refunded slip returns £0: its
stake comes back as a free bet, not cash, and its EV is that of the cash bet,
with nothing for the bonus. The free bet is its own slip (`offer: free`) when
it is placed: EV the lower of `stake × P(win) × (odds − 1)` and `stake ×
(odds − 1) / odds` (its value at its own odds as if fair), a win returns `stake × (odds −
1)`, a loss or a void £0, and its stake is not cash staked, so `staked` leaves
it out and its profit is its returns.

**The photo queue.** A photo (`AwPhotoOut`: `id`, `created_at`, `source`,
`state`, `tries`, `reason`, `slip_id`) is one of `waiting | reading | failed |
duplicate | done` (`aw26/photos.py`). Render stores it and changes its state
when asked; nothing reads it on Render. Each change of state is refused with
`409` when another request changed the photo first, so a photo is handed out
once. Every route on one photo answers `404` for no such photo.

- `POST /aw26/photos` — multipart `files` (1–20; JPEG, PNG, GIF or WebP, each
  up to `MAX_UPLOAD_MB`) and `source` (`page` by default, or `telegram`) → the
  stored photos, `waiting`. The `source`, the count and every file's declared
  type are checked before any is stored: `400` for another `source`, more
  than 20 files or an unsupported type. The files are then stored one at a
  time, and the batch is still all or nothing: a file refused then (`400`
  empty, `413` too large) or a store that fails keeps no photo of the batch.
- `GET /aw26/photos` — every photo not `done`, oldest first. A photo
  `reading` for more than 15 minutes is put back first, with the reason "Qwen
  did not finish": to `waiting` while it has had fewer than 3 tries, else
  `failed`.
- `POST /aw26/photos/{id}/take` — `waiting` → `reading`, one more try; `409`
  from any other state, or when another request took it first.
- `GET /aw26/photos/{id}/image` — the stored file; `404` when it is missing.
- `POST /aw26/photos/{id}/fail` — body `{"reason": …}` (1–200 characters):
  from `waiting` or `reading`, back to `waiting` while it has had fewer than 3
  tries, else `failed`; `409` from any other state.
- `POST /aw26/photos/{id}/resend` — `failed` → `waiting`, tries back to 0;
  `409` from any other state.
- `DELETE /aw26/photos/{id}` — the row and its file; `409` for a `done`
  photo, whose file is its slip's (delete the slip).

`kelly` and `boost` are client-side calculators with no routes. The legacy
aliases (`/cards/*`, `/tennis/*`) still answer and are hidden from the schema.

## Python client

`clients/python/puntmore` — `Puntmore(url, key)` / `AsyncPuntmore`, with
`.value()`, `.paddy.feed()`, `.state()`,
`.whoami()` and `.get()` / `.post()` for the rest. See its README.

## Versioning

The API is version **1** (`info.version` in the schema: `1.0.0`, plus the
deploy's commit). Within a version, changes are **additive only**: new routes,
new optional fields, new enum values in documented open sets (a new `source`,
`market` or `basis`). Clients must ignore fields they do not know — the
Python client does. Nothing is renamed, retyped or removed in place: a
breaking change ships beside the old one under `/api/v2/...`, and `/api/...`
keeps its v1 behaviour until every caller has moved, unless the register
decides otherwise.

## Code map

| | |
|---|---|
| auth, grants, key resolution | `render/api/app/security.py` |
| API clients table / CLI | `render/api/app/models.py::ApiClient`, `render/api/app/apikeys.py` |
| value feed | `render/api/app/value/service.py`, `render/api/app/routers/value.py` |
| Finder | `render/api/app/finder/rows.py`, `render/api/app/routers/finder.py` |
| AW 26 | `render/api/app/aw26/` (`offers.py`, `photos.py`, `record.py`, `selections.py`, `service.py`), `render/api/app/routers/aw26.py` |
| per-tool services | `render/api/app/cards/feed.py`, `render/api/app/twoup/feed.py`, `render/api/app/coupons/service.py`, `render/api/app/pnl/service.py`, `render/api/app/shop/service.py` (+ the existing `cards/`, `goalscorers/` packages) |
| response models | `render/api/app/schemas.py` |
| route walk test | `render/api/tests/test_openapi.py` |
