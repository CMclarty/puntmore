# Puntmore — project rules

Read `SPEC.md` for design detail and `PLAN.md` for current work. This file is
guardrails for the whole repo.

## What this is
A self-hosted betting-tools suite: three tools share one FastAPI backend and one
React frontend, behind a shared nginx layer.

```
bettingtools/
├── api/              ← shared FastAPI backend (all three tools)
├── frontend/         ← single React app (Coupons / Tennis Arbs / Cards), Vite
├── nginx/            ← shared reverse proxy (routes /api/ → api, / → frontend)
└── docker-compose.yml
```

The three tools are routes in one React app (`/`, `/tennis`, `/cards`), not
separate builds. The backend serves them all under namespaced routers.

## Hard rules (do not violate)
- NEVER commit secrets. All credentials live in `.env` only.
  `.env` is git-ignored; only `.env.example` is committed.
- The provider seams MUST stay behind their interfaces (see Provider guide
  below). Never call external APIs directly from core logic.
- The Odds API free tier is 500 credits/month. The 24-hour cache
  (`Settings.odds_cache_ttl`, applied in `app/odds/client.py`) is what keeps this
  safe — do not lower the default. A paid-tier upgrade is an `.env` change only.
- The refresh loop runs every 10 minutes; it recomputes value locally and reads
  through the odds cache, so it never increases API call frequency. Never make it
  bypass the cache.
- OCR output is NEVER trusted as-is. Every upload passes through the
  manual-correction step before values are saved (SPEC §7).
- The 30-day cleanup deletes BOTH database rows AND stored image files. Never
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

### Odds data layer (`app/odds/client.py::TheOddsApiClient`)
All three tools read TheOddsApi through this one shared client (HTTP + cache).
`fetch(sport_key, markets, bookmakers=None) -> list[dict]`, cached in-memory
keyed by `(sport_key, markets, bookmakers)`.

- Free tier: 500 credits/month. Each `/v4/sports/{sport}/odds` call costs ~2
  credits. Cache TTL is `Settings.odds_cache_ttl` (default 86400 = 24 h). Do not
  lower the default: a 1-hour cache burns ~336 credits/day on a single sport.
- Off-season sport keys return 404 (free, no quota) and are cached empty — safe
  to keep all keys listed year-round.
- Fair odds come from de-vigging Pinnacle: `app/odds/utils.py::devigd`. Pinnacle
  is the reference everywhere.
- TheOddsApi returns the totals line as `"point"` (numeric) on each outcome.
  Do not confuse with `"description"` — that field does not exist in this API.

Consumers:
| Consumer | File | Markets | Bookmakers |
|---|---|---|---|
| `TheOddsApiProvider` (coupons) | `providers/odds_api.py` | `h2h,totals` | all (prefers Pinnacle) |
| arbs router | `routers/tennis.py` + `tennis/arbs.py` | `h2h` | bet365 vs Pinnacle |
| `CardsApiClient` | `cards/client.py` + `cards/value.py` | `alternate_totals` | Pinnacle, Paddy Power |

`TheOddsApiProvider` satisfies the `OddsProvider` interface (used by the refresh
loop). Tennis and cards consume the client directly via `app.state.arbs_client` /
`app.state.cards_client` because they query bespoke market/bookmaker combos.

### Notification provider (`app/notifications/`)
`send(message)`. `DiscordNotificationProvider` (`DISCORD_WEBHOOK_URL`) →
`NullNotificationProvider` (no webhook).

## Workflow
- Work in phases per `PLAN.md`. Complete and verify one phase before the next.
- Tests/verification steps are part of each phase, not an afterthought.
- Decisions locked in PLAN.md's header are settled — do not re-litigate them.

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

# Install a new Python package into the venv (no pip in venv — use uv):
uv pip install <package> --python api/.venv/bin/python3
```
