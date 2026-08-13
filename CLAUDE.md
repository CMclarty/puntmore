# Puntmore — project rules

Read `SPEC.md` for design detail and `PLAN.md` for current work. This file is
guardrails for the whole repo.

## What this is
A self-hosted betting-tools suite: three tools share one FastAPI backend and one
React frontend, behind a shared nginx layer.

> **Current status (July 2026):** only the **coupon tracker** is surfaced.
> AccaBonus and Cards/Corners are hidden — their routers are gated on
> `ENABLE_ACCABONUS` / `ENABLE_CARDS` (default False) and their nav tabs/routes
> are suppressed. Code, tests, and the seams are all kept; this is a visibility
> gate, not a removal. The disabled routers / coupon-only UI are intentional —
> do not "fix" them. The coupon tracker prices via **bettingiscool**
> (`COUPON_ODDS_SOURCE=bettingiscool`), de-vigs in-house, and derives
> Win-and-Over off the fitted score grid. OCR **auto-confirms**: rows save and
> price on upload, with inline edit and re-scan.

```
bettingtools/
├── api/              ← FastAPI backend; api/Dockerfile ALSO builds the frontend
│                       into this one image, and app/asgi.py serves both
├── frontend/         ← React app (Vite); built into the api image, not deployed alone
└── render.yaml       ← Render blueprint: ONE Docker web service, deploy on push to main
```

The three tools are routes in one React app (`/`, `/accabonus`, `/cards`), not
separate builds. The backend serves them all under namespaced routers.
(`/tennis` is the AccaBonus tool's legacy prefix — kept as redirect/API aliases
for stale PWA clients; don't add new code under it.)

## Odds sources
- **Coupon tracker** → `BettingIsCoolProvider` (`providers/bettingiscool.py`).
  Pinnacle-native and exposes team totals, which feed the derived score grid.
  Fair odds are de-vigged in-house from its raw prices.
- **AccaBonus + Cards** → the shared `TheOddsApiClient` (free tier). Unchanged.

`TheOddsApiProvider` (`providers/odds_api.py`) is the fallback and is **no
longer the live path**. It has no score grid of its own, so its compound-leg
pricing is still a naive product — if it is ever made live again, wire it to
`pricing.grid` first rather than shipping that path.

Both live behind their seams. Core logic never calls a vendor API directly.

## Hard rules (do not violate)
- NEVER commit secrets. All credentials (bettingiscool key, Odds API key, OCR
  key, Discord webhook, auth users + session key) live in `.env` only.
  `.env` is git-ignored; only `.env.example` is committed.
- The provider seams MUST stay behind their interfaces (see Provider guide
  below). Never call external APIs directly from core logic.
- **Coupon fair odds are computed IN-HOUSE by de-vigging raw Pinnacle prices**
  (power method, `odds/utils.py`) — enforced in every provider since July 2026.
  Any vendor-supplied devigged field is for cross-check tests ONLY; the pricing
  path must not depend on one, so providers stay swappable.
- **Win-and-Over markets have NO direct Pinnacle price.** They MUST be derived
  via the calibrated score-grid model (SPEC §8, `pricing/grid.py`). NEVER price
  them as moneyline × over — result and total goals are correlated and naive
  multiplication overstates fair odds. The live provider derives them off the
  grid and refuses to price when the fit is too poor or under-constrained;
  keep that refusal, an unpriced leg beats a confidently wrong one.
- Derived-market value thresholds come from MEASURED backtest calibration
  (SPEC §8.4), not assumed. Until measured, use the provisional 8% threshold and
  display the uncertainty band.
- **OCR TRANSCRIBES; the app INTERPRETS.** Coupons are a fixed grid, so nothing
  about their structure is inferred by the model (July 2026):
  - It reads EVERY market block on the sheet into `prices`, keyed by the block
    heading as printed. It is never told to find one block and ignore the rest —
    picking among six look-alikes is a guess that fails invisibly. The app
    matches the heading (`normalise_board_label`) and selects (`_pair_for`).
  - Each block's two prices come back as a positional `["left", "right"]` pair.
    The model never sees the words home or away: **left = home, right = away**,
    matching the fixture's own order, assigned in `claude_ocr._sides_from`.
    Going by the printed "H"/"A" headers is what crossed prices onto the wrong
    team. Never reintroduce a labelled pair or a "pick this block" instruction.
    A response that *does* label the pair is parsed but flagged
    (`ExtractedRow.sides_positional`) and **refused on a two-sided coupon** —
    that pair is the model's judgement about sides, which is the one question
    it is never asked. On a two-sided coupon `row.market` is ignored outright:
    it is one value for a row that yields two legs, so using it filed the away
    price under the home team's selection.
- **THE SHEET IS A RECTANGLE — a coupon is a solved puzzle** (`coupon_grid.py`,
  Aug 2026). R fixtures × B market blocks, the same B on every row, so the cell
  count is knowable whether or not every cell can be READ. Three distinct facts
  that were previously one silent `(None, None)`:
  - **hole** — block reported, both prices illegible. Kept in place. An honest
    coupon and a poor photo; the grid is still solved.
  - **absent** — the row didn't carry a block the sheet has. The read losing
    structure. This is the number that means re-scan.
  - **malformed** — a "pair" that wasn't two items.
  Do NOT go back to dropping unreadable blocks: collapsing hole and absent is
  what made a bad photo indistinguishable from a coupon with fewer markets on
  it. One line per match is enforced too (a repeat is the read losing its
  place, never a coupon printing a fixture twice).
  Heading identity is `coupon_grid.headings_match`: **digits exactly, letters
  fuzzily**. Never a plain similarity score — the six blocks differ by one
  digit and `difflib` rates "TEAM TO SCORE 1…" vs "…2…" at 0.97, so any
  threshold loose enough to survive a typo will also file a pair under the
  neighbouring market.
  The verdict is stored per coupon (`coupons.scan_audit`) and surfaced as ONE
  sheet-level line in the tracker. It is not a per-row badge and must not
  become one — see the auto-confirm rule below.
- **One photo of a sheet fills every market on it.** The uploaded type is just
  the one it opens on; `upload.py::_build_sibling_coupons` creates a coupon per
  block found, each superseding its own previous issue. They SHARE the stored
  image, so `coupon_images.delete_coupon_image` removes the file only with the
  LAST coupon referencing it — both the supersede path and the scheduled
  cleanup go through it.
- **Coupon OCR is not blocking-reviewed** (phone uploads). Rows auto-save and
  price immediately with inline editing (SPEC §7). The per-row anomaly checks
  and ⚠ badges were REMOVED (July 2026, Callum's call — the rows are checked by
  hand anyway); do not reintroduce them. Re-scan preserves human-edited
  rows — never overwrite a correction with a machine re-read.
- **EVERY CACHE MUST BE BOUNDED** (`ttl_cache.py`, Aug 2026). The caches here
  were plain dicts keyed by fixture id with a timestamp beside each value: the
  timestamp decided whether a value could be REUSED and never removed the
  entry, so fed by the 10-minute refresh loop they accumulated every fixture the
  app had ever priced (~110 KB odds + ~14 KB grid each, measured). That is what
  ran the Render instance out of memory. Use `TTLCache` — it sweeps expired
  entries ON WRITE (a fixture stops being queried once it kicks off, so
  read-triggered eviction never fires) and has a hard ceiling for bursts. Never
  reintroduce a bare dict cache, and never "fall back to the stale value" on a
  fetch failure — holding the last good copy for ever is the leak.
  The same rule covers the request path: uploads are capped
  (`MAX_UPLOAD_MB`, the body is held in memory) and images above
  `images.MAX_DECODE_PIXELS` are stored un-rotated rather than decoded, since
  rotating holds two full-size copies. Never "fix" that by resizing the image —
  OCR accuracy on small print depends on the full resolution.
- The Odds API free tier (AccaBonus/Cards) is 500 credits/month. The 24-hour
  cache (`Settings.odds_cache_ttl`, `app/odds/client.py`) keeps this safe — do
  not lower the default. A paid-tier upgrade is an `.env` change only.
- The refresh loop runs every 10 minutes; it recomputes value locally and reads
  through the odds cache, so it never increases API call frequency. Never make
  it bypass the cache.
- The coupon cleanup deletes BOTH database rows AND stored image files. Never
  one without the other. The same holds when an upload SUPERSEDES the previous
  issue of its (type, period) — `upload.py::_supersede_previous`. That runs only
  after the new coupon has read rows: a failed or empty scan must never delete a
  working coupon. Two issues of one type still coexist (GG midweek + weekend);
  the key is type AND period, never type alone.
- **A scanned ladder is CHECKED before it is trusted**
  (`coupon_types.sanitise_ladder`, Aug 2026). The coupon's own reading still
  wins — ladders change between issues — but a rung that pays less than a
  shorter fold, or sits more than `LADDER_MAX_DRIFT` from the type's printed
  board, is a misread and is replaced by the board's. The ladder is what a
  combination is BET on, so one fumbled digit doesn't nudge a price, it invents
  value: a "300/1" rung read as ~7920/1 turned a +34% 8-fold into a "+3300.7%
  10-fold". Sanitised on the way in (upload) AND on the way out
  (`Coupon.ladder`), because a coupon already stored with a bad rung will not
  re-scan itself. Rejected rungs go into `scan_audit`.
- **A ladder combination belongs to an ISSUE, not to a type.** The legs of a
  fold are struck on one sheet, so never price one across two uploads, and never
  show a single type-wide figure when a type has more than one live issue — that
  hid the live coupon's value behind the newest upload's until July 2026.

## Provider guide — how to swap OCR or odds backends

### OCR provider (`app/providers/base.py::OCRProvider`)
Interface: `extract(image_bytes: bytes, media_type: str) -> list[ExtractedRow]`

| Class | File | Trigger |
|---|---|---|
| `GeminiOCRProvider` | `providers/gemini_ocr.py` | `GOOGLE_API_KEY` set in `.env` |
| `ClaudeOCRProvider` | `providers/claude_ocr.py` | `ANTHROPIC_API_KEY` set (fallback) |
| `MockOCRProvider` | `providers/mock_ocr.py` | neither key present (dev/test) |

Selection logic is in `app/main.py` lifespan — `GOOGLE_API_KEY` takes priority.
To add a new OCR provider:
1. Create `providers/<name>_ocr.py` implementing `OCRProvider`.
2. Add its API key to `config.py` (`Settings`) and `.env.example`.
3. Add an `elif settings.<new_key>:` branch in `main.py` lifespan before the
   `MockOCRProvider` fallback.

Current model IDs (verify before changing — do not assume from memory):
- Gemini: `gemini-2.0-flash` (set in `providers/gemini_ocr.py::MODEL`)
- Claude: `claude-haiku-4-5` (set in `providers/claude_ocr.py::MODEL`)

### Coupon odds provider (`app/providers/base.py::OddsProvider`)
Interface: `get_fair_odds(queries) -> list[OddsResult]`. The provider de-vigs
internally and returns FAIR odds plus the fixture it fuzzy-matched.

| Class | File | Trigger |
|---|---|---|
| `BettingIsCoolProvider` | `providers/bettingiscool.py` | `COUPON_ODDS_SOURCE=bettingiscool` (default) |
| `TheOddsApiProvider` | `providers/odds_api.py` | `COUPON_ODDS_SOURCE=theoddsapi` (fallback) |
| `MockOddsProvider` | `providers/mock_odds.py` | no provider key (dev/test) |

Legs are priced through `pricing/legs.py::price_legs` — one implementation
shared by upload, the on-demand refresh and the scheduler. Don't reintroduce a
second pricing path; that duplication caused drift before.

De-vigging happens in `_fair_h2h_price` / `_fair_totals_price` via
`odds/utils.py`. **Both require the complete outcome set** — all of 1X2, or both
sides of a total — because the margin is a property of the market, not of one
selection. Where that isn't available they return `None`. Never "fix" that by
falling back to the raw price: a vigged price returned as fair odds reads as
value that isn't there and fires false Discord alerts. That was a live bug until
July 2026.

Refresh cadence is 10 min, reading through the cache — it never adds vendor
calls. To add a provider, implement `OddsProvider` and add an `elif` on
`settings.coupon_odds_source` in the `main.py` lifespan.

### Odds data layer for value tools (`app/odds/client.py::TheOddsApiClient`)
AccaBonus + Cards read TheOddsApi through this one shared client (HTTP + cache).
`fetch(sport_key, markets, bookmakers=None) -> list[dict]`, cached in-memory
keyed by `(sport_key, markets, bookmakers)`.

- Free tier: 500 credits/month. Each `/v4/sports/{sport}/odds` call costs ~2
  credits. Cache TTL is `Settings.odds_cache_ttl` (default 86400 = 24 h). Do not
  lower the default: a 1-hour cache burns ~336 credits/day on a single sport.
- Off-season sport keys return 404 (free, no quota) and are cached empty — safe
  to keep all keys listed year-round.
- Fair odds come from de-vigging Pinnacle via the **power method**
  (`app/odds/utils.py::devigd` / `devigd_multi` — user decision, June 2026;
  don't revert to multiplicative). Pinnacle is the reference everywhere. The
  coupon tracker devigs the same way, over TheOddsApi prices.
- TheOddsApi returns the totals line as `"point"` (numeric) on each outcome.
  Do not confuse with `"description"` — that field does not exist in this API.

Consumers:
| Consumer | File | Source | Markets | Bookmakers |
|---|---|---|---|---|
| coupons | `providers/odds_api.py` | TheOddsApi | `h2h`, `totals` | Pinnacle (falls back to first listed) |
| arbs router | `routers/accabonus.py` + `accabonus/arbs.py` | TheOddsApi | `h2h` | bet365 vs Pinnacle |
| `CardsApiClient` | `cards/client.py` + `cards/value.py` | TheOddsApi | `alternate_totals` ⚠️ **broken, see below** | Pinnacle, Paddy Power |

⚠️ `CardsApiClient` asks the **bulk** endpoint for `alternate_totals`, which
that endpoint rejects (`INVALID_MARKET`) — so it has never returned a row. The
tool is gated off, so nothing is affected, but re-enabling it means rebuilding
against the per-event endpoint with the real corners/cards market keys. See
`docs/provider-verification.md` and PLAN item 7 before touching it.

`TheOddsApiProvider` satisfies the `OddsProvider` interface (used by the refresh
loop) and reads through the client on its own `COUPON_ODDS_CACHE_TTL`. AccaBonus
and cards consume the `TheOddsApiClient` directly via `app.state.arbs_client` /
`app.state.cards_client` because they query bespoke market/bookmaker combos.

### Notification provider (`app/notifications/`)
`send(message)`. `DiscordNotificationProvider` (`DISCORD_WEBHOOK_URL`) →
`NullNotificationProvider` (no webhook). Value-bet alerts from the refresh loop
are deduped via `legs.notified_tier` (notify only on new/changed tier) — keep
that behaviour when touching the loop, or live coupons re-alert every 10 min.

### State snapshot (`app/routers/state.py`)
`GET /state` returns the whole app state (coupons+legs, AccaBonus feed, cards
feed) for machine consumers. It is NOT session-guarded — it self-guards with
the static `STATE_API_KEY` (`X-API-Key` header, constant-time compare; blank
key = 503/disabled). It reads through the odds cache, so polling it never
increases API call frequency. Keep it read-only — never add mutating
operations behind the API key.

## Workflow
- Work the numbered items in `PLAN.md` → **Open work**, in order. Complete and
  verify one before starting the next.
- Tests/verification steps are part of each item, not an afterthought.
- `PLAN.md` → **Decisions** is settled — do not re-litigate.
- Secrets: a gitleaks pre-commit hook blocks commits containing them. On a fresh
  clone, enable it with `git config core.hooksPath .githooks` (gitleaks must be
  on PATH). Rules are in `.gitleaks.toml` — the tool's defaults miss this repo's
  known leak shape, so don't replace them with a stock config.

## Stack & deployment
- Backend: FastAPI (Python), one app, routers namespaced per tool.
- Frontend: one React (Vite) app. `api/Dockerfile` builds it and copies the
  static bundle into the API image; `app/asgi.py` serves the SPA at `/` and the
  API under `/api` — ONE origin (required: the session cookie is `same_site=lax`
  and the frontend calls relative `/api/...`). CSS modules + vars in `index.css`.
- **Deployment is Render only.** ONE Docker web service (`render.yaml`),
  `autoDeploy: true` → **every push to `main` deploys** (`puntmore.com`, served at
  216.24.57.x). There is NO local docker-compose / nginx stack — it was removed;
  don't reintroduce one. To deploy: push to `main`. Health gate is `/api/ready`.
- Storage: SQLite on the Render persistent disk at `/data` (`DATABASE_URL`);
  uploaded images under `/data/uploads`. Single instance — SQLite has one writer.
- Scheduler: APScheduler (in-process).

## Adding a new tool page
1. Add the compute logic under `api/app/<tool>/` (and a client consumer of
   `TheOddsApiClient` if it needs odds).
2. Add a router under `api/app/routers/<tool>.py`; include it in `main.py`.
3. Add a Pydantic response schema in `schemas.py`.
4. Frontend: add a page under `frontend/src/pages/`, a route in `App.jsx`, and a
   tab in `components/NavTabs.jsx`. Reuse `DataTable.module.css` for tables.

## Fresh clone
Everything except `.env` is in the repo. Verified July 2026 by cloning from
GitHub and building from nothing: the test suite, the frontend build and the
Render Docker image all come up clean with NO credentials present.

```
git clone git@github.com:CMclarty/puntmore.git && cd puntmore
uv venv api/.venv
uv pip install -r api/requirements.txt -r api/requirements-dev.txt \
   --python api/.venv/bin/python3
(cd frontend && npm install)
git config core.hooksPath .githooks     # gitleaks pre-commit (needs gitleaks on PATH)
```

`.env` is the ONLY thing that cannot be cloned — copy it across by hand; every
variable is documented in `.env.example`. Without it you still get the full test
suite (321 tests, hermetic: the OCR and odds providers are mocked), the frontend
build, and deploys. What needs real keys is LIVE verification — running the real
OCR over a coupon photo and pricing it against the live board — which is what
catches the bugs unit tests cannot.

## Commands
```
# Run tests (from api/):
.venv/bin/python -m pytest tests/ -q

# Run API locally (from api/):
.venv/bin/uvicorn app.main:app --reload

# Run the frontend dev server (from frontend/):
npm run dev

# Deploy: push to main — Render autoDeploys (no local stack).
git push origin HEAD:main

# Re-run the derived-market calibration backtest (from api/):
.venv/bin/python tools/backtest_calibration.py

# Install a new Python package into the venv (no pip in venv — use uv):
uv pip install <package> --python api/.venv/bin/python3
```
