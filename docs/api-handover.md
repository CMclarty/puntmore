# Puntmore as an API — handover (26 Oct 2026)

> Historical (Oct 2026). AW 26 has since been retired (`docs/retired.md`); its
> routes, package and client methods below no longer exist on main.

For the agent doing this build. Verified against the repo on 26 Oct 2026;
nothing here is a guess. Worktree `/home/callum/bettingtools-api`, branch
`api-modular` off `main` (d405531). **Every push to `main` is a production
deploy** — the agent does NOT push; it commits and reports. Run
`/home/callum/bettingtools/api/.venv/bin/python -m pytest -q` from `api/`
(791 pass) and `cd frontend && npm run lint && npm run test && npm run build`
before reporting. CLAUDE.md rules apply (build only what is asked; no
unrequested UI).

## What Callum asked for (26 Oct, his words)

> There may be an automated bet placing service in future. Ensure the whole
> Puntmore project is available as an API in a modular fashion.

So: a machine can do everything a person can do on the site, through one
consistent, documented, key-authenticated API, tool by tool — and a future
bet-placer can ask ONE question, "what value is on offer right now, across
every tool", and get one answer in one shape. Nothing here places bets.

## What exists today (verified)

- FastAPI app `api/app/main.py` (`app = FastAPI(title="Puntmore API")`),
  mounted under `/api` by `app/asgi.py` (`root = FastAPI(docs_url=None,
  redoc_url=None)`), which also serves the built frontend. 55 routes across
  `app/routers/` (accabonus 7, auth 3, aw26 7, aw26_ingest 4, cards 4,
  coupons 9, diagnostics 3, goalscorers 1, pnl 4, saves_ingest 2, scraper 4,
  shop 4, state 1, upload 2). Only 26 declare a `response_model`.
- Auth: a signed session cookie (`security.py`); per-tool grants on the
  `User.permissions` string (`"*"` or `coupons+aw26+…`); routers are gated at
  include time with `dependencies=[Depends(require_tool("x"))]`
  (`main.py:283-331`). `user_permissions` (security.py:176) is the single
  seam every guard resolves through — tests override it.
- Three ad-hoc machine keys, each a static env value compared with
  `secrets.compare_digest`: `STATE_API_KEY` (`routers/state.py`, the whole-app
  snapshot for bots), `SAVES_INGEST_KEY` (`routers/saves_ingest.py`
  `require_ingest_key`; also `aw26_ingest.py` and `routers/scraper.py`'s
  worker routes). All sent as `X-API-Key`.
- Domain code is already partly modular: `app/aw26/` (service, offers,
  results, snapshots, marry, attribution), `app/cards/` (laddered, split,
  most, saves, health), `app/pricing/`, `app/accabonus/arbs.py`,
  `app/goalscorers/service.py`. But the value FEEDS are built inside routers:
  `routers/cards.py build_feed(client, min_edge)` → `CardFeedItem`
  (`schemas.py`, with `CardFeedBet`: pick, odds, fair, value, market, line,
  side, basis) and `routers/accabonus.py build_feed(client, min_edge)` →
  `AccaBonusFeedItem`. Coupons/tracker value lives in `routers/coupons.py` +
  `pricing/legs.py`. AW26 snapshots (`aw26/snapshots.py`, `ValueSnapshot` /
  `ValueLine` tables) already record "value on offer" per sweep for Paddy.
- Scheduler jobs (`scheduler.py`) hold the live state the API reads from:
  cards sweep (2 min), odds refresh (10 min), accabonus alerts, aw26 daily.

## Build (in this order, committing as you go)

### 1. API clients (machine auth), one mechanism
- Table `ApiClient`: `name` (unique), `key_hash` (sha256 of the key; show the
  key once at creation), `permissions` (same grammar as `User.permissions`,
  reuse `parse_permissions`/`has_permission`), `enabled`, `created_at`,
  `last_used_at`.
- A CLI: `python -m app.apikeys create <name> --tools paddy,aw26` (prints the
  key once), `list`, `revoke <name>`. Document in CLAUDE.md.
- Accept the key as `Authorization: Bearer <key>` (preferred) or `X-API-Key`.
  Resolve it in `user_permissions` (security.py) so **every existing
  `require_tool` guard admits a client with that grant** — one seam, no
  per-router changes. Session cookie behaviour unchanged.
- Keep `STATE_API_KEY` and `SAVES_INGEST_KEY` working exactly as now (the
  scraper box and PC scripts use them); internally they become the grants
  `state` and `ingest`. Do not remove them.

### 2. One value feed for a bet-placer
`app/value/` (service + router), `GET /api/value` (grant `value`, or any
tool grant filters to that tool's rows), `GET /api/value/{source}`, with
`?min_edge=&kickoff_within_hours=&bookmaker=`. One row shape,
`ValueSelection` in `schemas.py`:
`source` (paddy | 365 | coupon …), `bookmaker`, `event_id`, `event`,
`home`, `away`, `kickoff` (ISO UTC), `sport`, `market`, `selection`,
`line`, `side`, `odds`, `fair`, `edge`, `basis`, `url` (null unless known),
`seen_at`. Built by calling the existing feed builders (move
`routers/cards.py build_feed` → `app/cards/feed.py`, `routers/accabonus.py
build_feed` → `app/accabonus/feed.py`; the routers import them — same
behaviour, now importable without FastAPI). Coupon-tracker value legs join
the same shape from `pricing/legs.py`. Half-point lines only, as everywhere
(`cards/lines.py is_half_line`). Sorted by edge desc.

### 3. Every route documented and typed
- Every route gets a `response_model` (add Pydantic models in `schemas.py`
  where a dict is returned today) and a `tags=[...]` per tool.
- OpenAPI on: `openapi_url="/openapi.json"`, `docs_url="/docs"` on the
  mounted API app (so `/api/docs`, `/api/openapi.json`) — gated by a
  dependency that accepts a session OR an API client (any grant), never
  public. `title`, `version` (from git describe or a constant), and a
  description that points to this doc.
- A test that walks `app.routes` and fails on any route under `/api` without
  a response_model or tag (allow-list the few that stream files/images).

### 4. Modular layout (no behaviour change)
Each tool a package with `service.py` (pure, no FastAPI) and the thin router
in `routers/`. Move what is not already there: coupons' feed/value logic out
of `routers/coupons.py` into `app/coupons/`, pnl computations into
`app/pnl/`, shop into `app/shop/`. Routers call services; tests may call
services directly. Keep import paths that tests/skills use working, or
update them in the same commit.

### 5. Python client
`clients/python/puntmore/` — a thin typed client (httpx): `Puntmore(url,
key)`, `.value(min_edge=…)`, `.paddy.feed()`, `.aw26.slips()`,
`.aw26.record(...)`, `.state()`, generated-by-hand from the OpenAPI, with a
README showing a bet-placer's loop (poll `/value`, decide, record the slip to
AW26 — recording only; placing is out of scope). Tests against the FastAPI
app with `httpx.ASGITransport`.

### 6. Docs
`docs/api.md`: auth, the value feed, per-tool endpoints (one table), the
CLI, versioning policy (additive changes only; a breaking change bumps the
path to `/api/v2/...`). CLAUDE.md entry.

## Constraints
- No bet placing, no bookmaker account automation of any kind. Read, record,
  report.
- Do not touch `api/app/cards/laddered.py`, `split.py`, `pricing/` maths, or
  `frontend/src/pages/CardsPage.jsx`. The frontend keeps working unchanged
  (same paths, same shapes — additive only).
- Never log or echo a key. Keys hashed at rest.
- Commit on `api-modular`; **never push**. Report: commits, test counts,
  the `ValueSelection` shape, the client's surface, what was NOT done and
  why, and anything Callum must do (expected: create a key with the CLI on
  Render's shell, or set one via env — say which).
