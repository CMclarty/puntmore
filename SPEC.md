# Puntmore — Build Specification

A self-hosted suite of three betting tools sharing one FastAPI backend and one
React frontend. Single household/syndicate (≤4 trusted users), not a SaaS —
keep it simple.

## 1. The three tools

### Coupon Tracker
Track football accumulator coupons and identify value legs.
1. Upload an image of a coupon (matches + offered odds) with a coupon type and
   match day.
2. OCR (Claude or Gemini vision) extracts each row: match, pick, offered odds.
3. User reviews/corrects the extraction (OCR is never trusted blind — §7).
4. On confirm the coupon goes **live**: fair odds are fetched from TheOddsApi
   (Pinnacle as the sharp reference) and value computed per leg (offered vs fair).
5. The tracker screen shows the active coupon's legs ranked by value, with
   tiered value badges. A picker sheet switches between coupons.

Scale: ~100–150 images/month. Football only.

### AccaBonus Arbs (tennis-arbs)
Find events where Bet365 is at or near fair value vs de-vigged Pinnacle, across
all Bet365 AccaBonus sports (tennis, basketball, baseball, NFL, NHL, cricket,
rugby — everything except soccer). For each event with both Bet365 and Pinnacle
h2h prices: de-vig Pinnacle → fair odds, measure Bet365's edge, flag genuine
cross-book arbs. Filterable by minimum edge.

### Cards & Corners
Find value in soccer cards/corners over-under markets where Paddy Power's price
beats de-vigged Pinnacle. Same de-vig methodology as arbs, applied to
`alternate_totals` lines. Filterable by minimum edge.

## 2. Architecture & stack
- **Frontend**: a single React app (`frontend/`, Vite), built to static assets,
  served by its own nginx container. React Router serves all three tools as
  routes (`/`, `/tennis`, `/cards`) plus `/upload` and `/login`.
- **Backend**: one FastAPI app (`api/`). Routers namespaced per tool. Python
  chosen for mature image/HTTP tooling; with Claude/Gemini doing OCR the backend
  stays light (no Tesseract/OpenCV).
- **Storage**: SQLite on a named Docker volume (`db_data`). Correct for this
  scale; Postgres would be overkill. Images on a named volume (`uploads`).
- **Scheduler**: APScheduler inside the API process (NOT host cron) so the
  container is self-contained.
- **Reverse proxy**: a shared nginx container routes `/api/` → api and `/` →
  frontend. In front of that, Caddy terminates TLS (Cloudflare DNS challenge)
  for `puntmore.com`.
- **Secrets**: `.env` only (never committed; provide `.env.example`). Holds the
  Odds API key, OCR key, Discord webhook, and the auth user list + session key.

```
bettingtools/
├── api/              ← shared FastAPI backend (all three tools)
│   └── app/
│       ├── odds/         ← shared TheOddsApiClient (HTTP + cache) + devig util
│       ├── providers/    ← OCRProvider + OddsProvider seams + impls
│       ├── notifications/ ← NotificationProvider seam + Discord/Null impls
│       ├── tennis/       ← arbs compute logic
│       ├── cards/        ← cards client + value compute logic
│       └── routers/      ← coupons, upload, tennis, cards, auth
├── frontend/         ← single React app (all three tools)
├── nginx/            ← shared reverse proxy
└── docker-compose.yml
```

## 3. Domain concepts (coupon tracker)
- **Coupon**: one uploaded accumulator — a named bet slip with N legs, e.g.
  "Saturday Acca". Has a `type_code` (the offer it came from, e.g. "TTS2") and
  a `match_day` (the date its legs play). Status: **pending** (uploaded, OCR
  rows awaiting review) → **live** (confirmed; refresh loop tracks its value).
- **Leg**: one parsed row of a coupon — match name, pick (selection text),
  your odds, fair odds, value. Displayed ranked by value.
- A coupon's name is optional on upload; it defaults to
  `"{type_code} · {match_day}"`.

## 4. Data model (SQLite)
- `coupons`: id, name, type_code, match_day, status (pending | live),
  image_path, uploaded_by, created_at.
- `legs`: id, coupon_id (FK ON DELETE CASCADE), match_name, pick, odds,
  fair_odds, value, fair_odds_updated_at, external_event_id (nullable), rank.
- `users`: id, username (unique), password_hash, created_at.

Deletion cascades: deleting a `coupon` deletes its `legs` (FK cascade) AND its
image file (in application code, same boundary).

Migration: `security.ensure_schema()` detects the pre-v3 schema
(sets/versions/matches) on startup, drops it, and recreates the new tables.
No Alembic — guarded DDL only.

## 5. Odds data layer — TheOddsApi
All three tools read odds through one shared client
(`app/odds/client.py::TheOddsApiClient`).
- Base URL `https://api.the-odds-api.com`, v4 endpoints.
  `GET /v4/sports/{sport_key}/odds` returns events with
  `bookmakers[].markets[].outcomes[]`.
- **Free tier: 500 credits/month.** Each sport/odds call costs ~2 credits.
  Responses are cached in-memory keyed by `(sport_key, markets, bookmakers)`.
  Cache TTL is `Settings.odds_cache_ttl` (default 86400 = 24 h). **Do not lower
  the default** — a 1 h cache burns ~336 credits/day on a single sport. A paid
  tier upgrade is a single `.env` change (`ODDS_CACHE_TTL`).
- Off-season sport keys return 404 (free, no quota) and are cached as empty —
  safe to keep all sport keys listed year-round.
- **Fair odds via de-vig**: `app/odds/utils.py::devigd(price_a, price_b)` removes
  the bookmaker margin. `total_impl = 1/a + 1/b`; `fair = price × total_impl`;
  `margin = total_impl − 1`. Pinnacle is the de-vig reference everywhere (sharpest
  market). Used identically by tennis arbs and cards.
- **Coupon market mapping** (`providers/odds_api.py`): `win` → home h2h;
  `over X.X` / `under X.X` → totals; `win and over X.X` → h2h × totals
  (independence approximation — these legs carry `is_approx: true` and the UI
  shows `fair ≈X.XX`). TheOddsApi returns the totals line as `"point"`
  (numeric) on each outcome.

## 6. The provider seams (must stay abstracted)
Core logic must be provider-agnostic. Swappable seams:

1. **`OCRProvider`** (`providers/base.py`) — `extract(image_bytes, media_type)
   -> list[ExtractedRow]`.
   - `GeminiOCRProvider` (`GOOGLE_API_KEY`, priority) → `ClaudeOCRProvider`
     (`ANTHROPIC_API_KEY`) → `MockOCRProvider` (neither key — dev/test).
   - Model ids: Gemini `gemini-2.0-flash`, Claude `claude-haiku-4-5`. VERIFY
     from docs before changing — do not hardcode from memory.
2. **`OddsProvider`** (`providers/base.py`) — `get_fair_odds(queries)
   -> list[OddsResult]`. `TheOddsApiProvider` (delegates to the shared client) →
   `MockOddsProvider` (no key).
3. **`NotificationProvider`** (`notifications/`) — `send(message)`.
   `DiscordNotificationProvider` (`DISCORD_WEBHOOK_URL`) → `NullNotificationProvider`.

Tennis and cards consume the shared `TheOddsApiClient` directly (set on
`app.state.arbs_client` / `app.state.cards_client`) rather than the `OddsProvider`
interface, because they query bespoke market/bookmaker combinations.

## 7. OCR correction step (required)
OCR output is never saved blind — a misread digit (1.85 vs 1.65) changes the
value calc. After extraction, present parsed rows for review: editable match
name, pick, odds, add/remove rows. Only on confirm are legs persisted and the
coupon set **live**. Coupon `status` tracks this (pending → live).

Team-name normalisation (`app/normalise.py`): OCR'd names won't match TheOddsApi
fixture names exactly ("Man Utd" vs "Manchester United"). A fuzzy-match layer
(difflib ratio, ≥0.6 threshold) sits between extraction and odds lookup. The
matched fixture name is stored on the leg (`matched_fixture`) and shown in the
tracker when it differs from the OCR'd name, so a wrong match is visible rather
than silently producing a wrong fair price; matches scoring < 0.75 are also
logged as warnings.

## 8. Refresh loop (coupons)
- Every 10 minutes APScheduler refreshes fair odds for all legs in live coupons,
  recomputes value, and (if a Discord webhook is set) notifies on positive-value
  legs. Alerts are deduplicated: a leg notifies only when its value tier
  (hi ≥ +6% / mid ≥ +4% / lo > 0) is new or changed since the last alert
  (tracked in `legs.notified_tier`; cleared when value drops non-positive, so a
  leg that recovers re-alerts). `POST /coupons/{id}/refresh` triggers the same
  refresh on demand for one coupon. Both recompute locally and read through the
  odds cache — they never increase TheOddsApi call frequency.

## 9. Auto-deletion (30 days)
- Daily APScheduler job + a run on startup (self-heals after downtime): delete
  `coupons` older than 30 days, cascading to `legs`, AND delete the image
  files from the volume. Never one without the other. In-app only; no host cron.

## 10. Authentication (≤4 users, shared data)
- Syndicate model: all users share the same coupons, uploads, and feeds. Logins
  exist to gate access and to attribute uploads (`coupons.uploaded_by`), not to
  partition data. All users are equal — no roles.
- Minimal in-app auth: a `users` table (bcrypt hashes), a signed session cookie
  (Starlette `SessionMiddleware`), and a login page. Users are seeded from
  `AUTH_USERS` in `.env` on startup. No registration / reset flows — change a
  password by editing `.env` and recreating the user.
- Caddy basic-auth is removed from `puntmore.com` once in-app auth lands (the
  separate `lab.puntmore.com` host keeps its own auth).

## 11. Frontend layout (v3 PWA — see `design/HANDOFF.md`)
Mobile-first PWA, warm paper theme, dense. Single column capped at 480 px,
safe-area insets, bottom tab bar (Coupon / Tennis / C/C). System sans for UI,
mono for all numbers. Value tiers colour the EV% badges (hi ≥ +6 % accent,
mid ink, lo faint).
- **Login** (`/login`): wordmark lockup, "Email or username" + password, LOG IN.
- **Coupon tracker** (`/`): header coupon pill (`COUPON:` name + chevron) +
  LIVE/PENDING chip; legs as value-ranked cards (match / pick + odds / badge).
  Tapping the pill opens the coupon-picker bottom sheet (radio rows + an
  `↑ UPLOAD COUPONS` button).
- **Upload** (`/upload`): coupon type + match-day date picker + image drop-zone,
  PROCESS IMAGE → OCR review table (editable rows) → CONFIRM & SAVE. Below:
  PREVIOUS UPLOADS list with LIVE/PENDING chips.
- **Value tools** (`/tennis`, `/cards`): title + min-edge control + freshness
  line. Tennis = feed of match cards (best side as the pick, ARB chip).
  C/C = match rows expanding to an inline bets accordion (single-open).

## 12. Verification expectations
Each phase ships with proof: pytest exercising API endpoints, the cleanup job
shown deleting both rows and files, OCR round-tripped on a real sample image,
and the auth flow (401 unauthenticated, 200 after login). Frontend phases verify
with a clean `npm run build` and a responsive manual check.
