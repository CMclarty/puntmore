# Puntmore — project rules

Read `SPEC.md` for design detail and `PLAN.md` for current work. This file is
guardrails for the whole repo.

## What this is
A self-hosted betting-tools suite: three tools share one FastAPI backend and one
React frontend, behind a shared nginx layer.

> **Current status (PLAN.md v5, July 2026):** only the **coupon tracker** is
> surfaced. AccaBonus and Cards/Corners are hidden — their routers are gated on
> `ENABLE_ACCABONUS` / `ENABLE_CARDS` (default False) and their nav tabs/routes
> are suppressed. Code, tests, and the seams are all kept; this is a visibility
> gate, not a removal. The disabled routers / coupon-only UI are intentional —
> do not "fix" them. The coupon tracker currently prices via **TheOddsApi**
> (`COUPON_ODDS_SOURCE=theoddsapi`); bettingiscool remains the planned switch.

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

## Two odds sources (deliberate — do not merge them)
- **Coupon tracker** → `BettingIsCoolProvider` (Pinnacle-native, exposes team
  totals + raw prices). This is what makes in-house devig and the derived
  Win-and-Over model possible; TheOddsApi cannot (no team totals).
- **AccaBonus + Cards** → the shared `TheOddsApiClient` (free tier). Unchanged.

Both live behind their seams. Core logic never calls a vendor API directly.

## Hard rules (do not violate)
- NEVER commit secrets. All credentials (bettingiscool key, Odds API key, OCR
  key, Discord webhook, auth users + session key) live in `.env` only.
  `.env` is git-ignored; only `.env.example` is committed.
- The provider seams MUST stay behind their interfaces (see Provider guide
  below). Never call external APIs directly from core logic.
- **Coupon fair odds are computed IN-HOUSE by de-vigging raw Pinnacle prices**
  (power method, `odds/utils.py`). Vendor-supplied devigged fields
  (bettingiscool `todds`) are for cross-check tests ONLY — the pricing path must
  not depend on them, so providers stay swappable. (This reverses the old R10
  raw-price exception — see PLAN.md.)
- **Win-and-Over markets have NO direct Pinnacle price.** They MUST be derived
  via the calibrated score-grid model (SPEC §8). NEVER price them as
  moneyline × over — result and total goals are correlated and naive
  multiplication overstates fair odds. (The old `is_approx` h2h×totals path is
  retired.)
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
Interface: `get_market_prices(queries) -> list[RawPrices]` — returns RAW prices
(1X2, totals ladder, team totals) so core logic devigs (§7) and derives (§8).
| Class | File | Trigger |
|---|---|---|
| `BettingIsCoolProvider` | `providers/bettingiscool.py` | `BETTINGISCOOL_API_KEY` set |
| `MockOddsProvider` | `providers/mock_odds.py` | no key (dev/test) |

Auth: `X-API-Key` header. KNOWN RISK (accepted): one-person vendor. The seam is
the mitigation — a replacement feed is one new class, not a rebuild. To add a
new coupon odds provider, implement `OddsProvider` and add an `elif` in the
`main.py` lifespan. Refresh cadence is 10 min (§8) — inside the vendor quota.

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
  coupon tracker devigs the same way, now over bettingiscool prices.
- TheOddsApi returns the totals line as `"point"` (numeric) on each outcome.
  Do not confuse with `"description"` — that field does not exist in this API.

Consumers:
| Consumer | File | Source | Markets | Bookmakers |
|---|---|---|---|---|
| coupons | `providers/bettingiscool.py` | bettingiscool | 1X2, totals, team totals | Pinnacle |
| arbs router | `routers/accabonus.py` + `accabonus/arbs.py` | TheOddsApi | `h2h` | bet365 vs Pinnacle |
| `CardsApiClient` | `cards/client.py` + `cards/value.py` | TheOddsApi | `alternate_totals` | Pinnacle, Paddy Power |

`BettingIsCoolProvider` satisfies the `OddsProvider` interface (used by the
refresh loop). AccaBonus and cards consume the `TheOddsApiClient` directly via
`app.state.arbs_client` / `app.state.cards_client` because they query bespoke
market/bookmaker combos.

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
- Work in phases per `PLAN.md`. Complete and verify one phase before the next.
- Tests/verification steps are part of each phase, not an afterthought.
- Decisions locked in PLAN.md's header are settled — do not re-litigate them.
- **The coupon realignment (PLAN v4) BEGINS with the bettingiscool provider
  verification calls (SPEC §13.1).** Do not build devig/derived logic until the
  provider has passed them and `docs/provider-verification.md` is committed.

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
