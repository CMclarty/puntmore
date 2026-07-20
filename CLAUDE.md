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
> price on upload, with per-leg ⚠ flags, inline edit and re-scan.

```
bettingtools/
├── api/              ← shared FastAPI backend (all three tools)
├── frontend/         ← single React app (Coupons / AccaBonus / Cards), Vite
├── nginx/            ← shared reverse proxy (routes /api/ → api, / → frontend)
└── docker-compose.yml
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
- **Coupon OCR is not blocking-reviewed** (phone uploads). Rows auto-save and
  price immediately with per-row anomaly ⚠ flags and inline editing (SPEC §7).
  Never drop the sanity checks or the ⚠ badges. Re-scan preserves human-edited
  rows — never overwrite a correction with a machine re-read.
- The Odds API free tier (AccaBonus/Cards) is 500 credits/month. The 24-hour
  cache (`Settings.odds_cache_ttl`, `app/odds/client.py`) keeps this safe — do
  not lower the default. A paid-tier upgrade is an `.env` change only.
- The refresh loop runs every 10 minutes; it recomputes value locally and reads
  through the odds cache, so it never increases API call frequency. Never make
  it bypass the cache.
- The coupon cleanup deletes BOTH database rows AND stored image files. Never
  one without the other.

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
shared by upload, the on-demand refresh and the scheduler, which also
recomputes each leg's ⚠ flags (`checks.py`). Don't reintroduce a second
pricing path; that duplication caused drift before.

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

## Stack
- Backend: FastAPI (Python), one app, routers namespaced per tool.
- Frontend: one React (Vite) app, built to static files served by its own nginx
  container. CSS modules + shared CSS variables in `src/index.css`.
- Reverse proxy: shared nginx container routes `/api/` → api, `/` → frontend.
  Caddy sits in front for TLS (`puntmore.com`).
- Storage: SQLite on a named Docker volume (`db_data`). Images on `uploads`.
- Scheduler: APScheduler (in-process).

## Adding a new tool page
1. Add the compute logic under `api/app/<tool>/` (and a client consumer of
   `TheOddsApiClient` if it needs odds).
2. Add a router under `api/app/routers/<tool>.py`; include it in `main.py`.
3. Add a Pydantic response schema in `schemas.py`.
4. Frontend: add a page under `frontend/src/pages/`, a route in `App.jsx`, and a
   tab in `components/NavTabs.jsx`. Reuse `DataTable.module.css` for tables.

## Commands
```
# Run tests (from api/):
.venv/bin/python -m pytest tests/ -q

# Run API locally (from api/):
.venv/bin/uvicorn app.main:app --reload

# Run the frontend dev server (from frontend/):
npm run dev

# Build and run full stack:
docker compose up --build

# Re-run the derived-market calibration backtest (from api/):
.venv/bin/python tools/backtest_calibration.py

# Install a new Python package into the venv (no pip in venv — use uv):
uv pip install <package> --python api/.venv/bin/python3
```
