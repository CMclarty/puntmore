# Puntmore — Build Specification

A self-hosted suite of three betting tools sharing one FastAPI backend and one
React frontend. Single household/syndicate (≤4 trusted users), not a SaaS —
keep it simple.

## 1. The three tools

### Coupon Tracker
Track football accumulator coupons and identify value legs.
1. Upload an image of a coupon (matches + offered odds) with a coupon type and
   match day — typically from a phone.
2. OCR (Gemini or Claude vision) extracts each row: match, pick, offered odds.
3. Rows auto-save and price immediately — there is NO blocking review screen
   (§7). Anomalous rows carry a ⚠ badge; every row is editable inline.
4. Fair odds are obtained Pinnacle-referenced via bettingiscool: DIRECTLY (with
   in-house devig) for team-total markets, and via the derived score-grid model
   (§8) for Win-and-Over markets. Value is computed per leg (offered vs fair).
5. The tracker screen shows the active coupon's legs ranked by value, with
   tiered value badges; derived legs show an uncertainty band. A picker sheet
   switches between coupons.

Scale: ~100–150 images/month. Football only.

### AccaBonus Arbs
Find events where Bet365 is at or near fair value vs de-vigged Pinnacle, across
the competitions eligible for Bet365's Acca Boost offer (verified against the
published terms, June 2026): all tennis, MMA, plus NBA/Euroleague basketball,
NFL, MLB/NPB/KBO baseball and NHL ice hockey. Cricket and rugby are NOT
eligible; pre-season/exhibition games are excluded. Soccer IS eligible for the
boost but is deliberately excluded from this tool (user decision, June 2026):
football is covered by the coupon tracker and cards/corners tools — don't
re-add it. For each event with both Bet365 and Pinnacle h2h prices: de-vig
Pinnacle across all priced outcomes (3-way generically when both books price a
draw) → fair odds, measure Bet365's edge, flag genuine cross-book arbs.
Filterable by minimum edge. Eligibility lives in
`accabonus/arbs.py::select_accabonus_keys`.

### Cards & Corners
Find value in soccer cards/corners over-under markets where Paddy Power's price
beats de-vigged Pinnacle. Same de-vig methodology as arbs, applied to
`alternate_totals` lines. Filterable by minimum edge.

## 2. Architecture & stack
- **Frontend**: a single React app (`frontend/`, Vite), built to static assets,
  served by its own nginx container. React Router serves all three tools as
  routes (`/`, `/accabonus`, `/cards`) plus `/upload` and `/login` (`/tennis`
  redirects to `/accabonus` — legacy path).
- **Backend**: one FastAPI app (`api/`). Routers namespaced per tool. Python
  chosen for mature image/HTTP + numerical tooling; with Claude/Gemini doing OCR
  the backend stays light (no Tesseract/OpenCV), and the derived-market grid fit
  uses numpy/scipy only.
- **Storage**: SQLite on a named Docker volume (`db_data`). Correct for this
  scale; Postgres would be overkill. Images on a named volume (`uploads`).
- **Scheduler**: APScheduler inside the API process (NOT host cron) so the
  container is self-contained.
- **Reverse proxy**: a shared nginx container routes `/api/` → api and `/` →
  frontend. In front of that, Caddy terminates TLS (Cloudflare DNS challenge)
  for `puntmore.com`.
- **Secrets**: `.env` only (never committed; provide `.env.example`). Holds the
  bettingiscool key, Odds API key, OCR key, Discord webhook, and the auth user
  list + session key.

```
bettingtools/
├── api/              ← shared FastAPI backend (all three tools)
│   ├── app/
│   │   ├── odds/         ← shared TheOddsApiClient (HTTP + cache) + devig util
│   │   ├── providers/    ← OCRProvider + OddsProvider seams + impls
│   │   ├── pricing/      ← in-house devig pipeline + derived score-grid model
│   │   ├── notifications/ ← NotificationProvider seam + Discord/Null impls
│   │   ├── accabonus/    ← arbs compute logic + league eligibility
│   │   ├── cards/        ← cards client + value compute logic
│   │   └── routers/      ← coupons, upload, accabonus, cards, auth, state
│   └── tools/            ← backtest_calibration.py (re-runnable, committed)
├── frontend/         ← single React app (all three tools)
├── nginx/            ← shared reverse proxy
└── docker-compose.yml
```

## 3. Domain concepts (coupon tracker)
- **Coupon**: one uploaded accumulator — a named bet slip with N legs, e.g.
  "Saturday Acca". Has a `type_code` (the offer it came from, e.g. "TTS2",
  "WO2") and a `match_day` (the date its legs play). Status: **processing** (OCR
  running / pricing) → **confirmed** (priced; refresh loop tracks its value).
  There is no manual-review gate — see §7.
- **Leg**: one parsed row of a coupon — match name, pick (selection text),
  your odds, fair odds, value, and (for derived markets) an uncertainty band.
  Displayed ranked by value. `fair_odds_source` records whether the price came
  from a direct market or the derived model.
- A coupon's name is optional on upload; it defaults to
  `"{type_code} · {match_day}"`.

### Coupon → market mapping (§8.2)
- `Team to Score 2+`  → DIRECT: team total over 1.5 (devigged).
- `Team to Score 3+`  → DIRECT: team total over 2.5 (devigged).
- `Win and Over 2` / `Win and Over 3` → DERIVED via the §8 grid.
  (Confirm exact line semantics from real coupons — "over 2" vs "over 2.5":
  whole-number lines imply push handling; the grid supports both.)

## 4. Data model (SQLite)
- `coupons`: id, name, type_code, match_day, status (processing | confirmed),
  image_path, uploaded_by, created_at.
- `legs`: id, coupon_id (FK ON DELETE CASCADE), match_name, pick, odds,
  fair_odds, value, fair_odds_source (direct | derived_model),
  uncertainty_band (nullable, derived only), fair_odds_updated_at,
  external_event_id (nullable), matched_fixture (nullable), notified_tier
  (nullable), flags (nullable JSON — anomaly ⚠ reasons), edited (bool — human
  correction; re-scan never overwrites), rank.
- `users`: id, username (unique), password_hash, created_at.

Deletion cascades: deleting a `coupon` deletes its `legs` (FK cascade) AND its
image file (in application code, same boundary).

Migration: `security.ensure_schema()` applies guarded DDL on startup (no
Alembic). The v4 realignment adds `fair_odds_source`, `uncertainty_band`,
`flags`, and `edited` via guarded `ALTER TABLE`, and maps the pre-v4
`pending|live` statuses to `processing|confirmed`.

## 5. Coupon odds source — bettingiscool
Pinnacle's public API closed July 2025 (direct access is by application only).
The coupon tracker therefore reads Pinnacle-referenced prices from an
aggregator, `BettingIsCoolProvider` (https://api.bettingiscool.com), chosen
because team totals (TT) are a standard market and raw prices are exposed on
every snapshot — both are required for in-house devig (§7) and the derived
model (§8). (SharpAPI was evaluated and rejected: its soccer schema is 3-way ML
/ AH / totals only — no team totals.)
- Auth: `X-API-Key` header. Tiers: Starter €49 (15-min delay), Pro €149
  (real-time), Enterprise €249 (specials). Token billing ≈ 1 + 1/row.
- KNOWN RISK (accepted): one-person operation. The `OddsProvider` seam is the
  mitigation — a replacement feed is one new class, not a rebuild.
- Staleness: if prices haven't refreshed within 2× the refresh interval, the UI
  shows a banner with the last-updated time.
- Vendor devigged fields (`todds`) are used ONLY as a cross-check in tests —
  the pricing path must not depend on them (§7).

**Value-tool odds — TheOddsApi** (AccaBonus + Cards, unchanged): one shared
client (`app/odds/client.py::TheOddsApiClient`), v4 endpoints, in-memory cache
keyed by `(sport_key, markets, bookmakers)`. Free tier 500 credits/month; each
sport/odds call ≈ 2 credits; cache TTL `Settings.odds_cache_ttl` (default 86400
= 24 h) — do not lower. Off-season keys 404 (free) and cache empty.

## 6. The provider seams (must stay abstracted)
Core logic must be provider-agnostic. Swappable seams:

1. **`OCRProvider`** (`providers/base.py`) — `extract(image_bytes, media_type)
   -> list[ExtractedRow]`.
   - `GeminiOCRProvider` (`GOOGLE_API_KEY`, priority) → `ClaudeOCRProvider`
     (`ANTHROPIC_API_KEY`) → `MockOCRProvider` (neither key — dev/test).
   - Model ids: Gemini `gemini-2.0-flash`, Claude `claude-haiku-4-5`. VERIFY
     from docs before changing — do not hardcode from memory.
2. **`OddsProvider`** (`providers/base.py`) — `get_market_prices(queries)
   -> list[RawPrices]`. Returns RAW prices for 1X2, the totals ladder, and team
   totals (specials if available), so core logic devigs and derives.
   `BettingIsCoolProvider` (`BETTINGISCOOL_API_KEY`) → `MockOddsProvider`
   (no key). One implementation, kept swappable.
3. **`NotificationProvider`** (`notifications/`) — `send(message)`.
   `DiscordNotificationProvider` (`DISCORD_WEBHOOK_URL`) → `NullNotificationProvider`.

AccaBonus and cards consume the shared `TheOddsApiClient` directly (set on
`app.state.arbs_client` / `app.state.cards_client`) rather than the `OddsProvider`
interface, because they query bespoke market/bookmaker combinations.

## 7. In-house devig + OCR handling (coupons)

### 7.1 Devig (in-house)
Fair probabilities are computed IN-HOUSE from bettingiscool's raw prices using
the power (logarithmic) devig applied per market (`odds/utils.py::devigd_multi`,
already used by AccaBonus/cards — the coupon path reuses it). Vendor-supplied
devigged fields (`todds`) are used only as a cross-check in tests. This is the
single pricing path for direct markets; the derived model (§8) consumes the same
devigged probabilities. (This replaces the pre-v4 behaviour where the coupon
tracker used raw Pinnacle prices as fair — R10 note; that exception is retired.)

### 7.2 OCR handling (auto-confirm + anomaly flags)
Uploads happen from a phone — there is NO blocking review screen. Instead:
- OCR rows persist immediately and pricing runs (status: processing →
  confirmed on save).
- Sanity checks per row, flagged with a ⚠ badge in the legs table (reasons in
  `legs.flags`): odds outside a plausible range for the market; fuzzy name-match
  confidence below threshold; computed value beyond a plausibility cap (default
  ±15%) — large "edges" are usually misread digits.
- Every row is editable inline (tap-to-edit: match, pick, your odds); edits
  reprice immediately and set `legs.edited = true`.
- **Re-scan button**: each coupon can manually re-trigger OCR on the stored
  image. Re-scan replaces UNEDITED rows with the fresh extraction; rows the user
  has edited are preserved (never overwrite a human correction with a machine
  re-read). Sanity checks and ⚠ badges re-run on the result.
- Fixtures that fail name-matching persist as "unmatched" rows with fair odds
  blank (never block the coupon); resolving them is an inline edit.
- KNOWN TRADE-OFF (accepted): a plausible misread can pass the checks; the
  value column is the practical tripwire.

Team-name normalisation (`app/normalise.py`): OCR'd names won't match provider
fixture names exactly ("Man Utd" vs "Manchester United"). A fuzzy-match layer
(difflib ratio, ≥0.6 threshold) sits between extraction and odds lookup. The
matched fixture name is stored on the leg (`matched_fixture`) and shown when it
differs from the OCR'd name; matches scoring < 0.75 are also logged as warnings.

## 8. Derived markets — Win and Over X  ★ core logic
Pinnacle does NOT price "Result & Over 2.5" as a standard market (its soccer set
is 1X2 / Asian handicap / totals incl. team totals). "Win and Over" must be
DERIVED. Naive moneyline × over is WRONG — winning and total goals are
positively correlated; multiplication overstates fair odds.

### 8.1 Model (`pricing/grid.py`)
- Fit a score-grid model per fixture from bettingiscool's devigged prices:
  - Inputs (devigged, from §7): 1X2 probabilities; totals ladder (as many lines
    as offered, e.g. O/U 2.0, 2.5, 3.0); team totals for both teams; Asian
    handicap lines if available.
  - Parameters: expected goals per team (λ_home, λ_away) + a dependence
    parameter. Use an independent-Poisson grid with a Dixon–Coles low-score
    correction, or a bivariate Poisson — the dependence term is effectively
    pinned by the draw price given the marginals.
  - Fit by minimising squared error between model-implied and devigged market
    probabilities across all available constraints. Truncate the grid at 10
    goals per team.
- Read derived probabilities off the fitted grid, e.g.
  P(home win ∧ total ≥ 3) = Σ grid[h,a] for h>a, h+a≥3. fair odds = 1/p.

### 8.2 Which coupons use which path
See §3 mapping. `coupons.type_code`/leg pick text select the path;
`legs.fair_odds_source` (direct | derived_model) records it.

### 8.3 Uncertainty & thresholds
- Direct markets: standard value threshold (per-coupon config, default 4–6%;
  value tiers hi ≥ +6% / mid ≥ +4% / lo > 0, `comparison.py`).
- Derived markets: model error is real (parametric form + interpolation; ~±1–3%
  on fair probability for mainstream fixtures). Therefore:
  - PROVISIONAL threshold 8% until measured (§8.4).
  - Display an uncertainty band next to derived values (`legs.uncertainty_band`)
    so a "+9%" derived flag reads e.g. "+9% (±3%)", with a small "derived"
    marker.

### 8.4 Calibration backtest (required — `tools/backtest_calibration.py`)
- Using historical fixtures with settlements (bettingiscool carries results):
  for a season+ of fixtures in the coupons' leagues, fit the grid from
  historical devigged prices, compute derived P(win ∧ over X), and score
  calibration against actual outcomes (reliability curve + Brier; bucket by
  probability).
- Output: measured calibration error band. Set the production derived-market
  threshold and the displayed uncertainty band FROM THIS MEASUREMENT.
- Also cross-check: on fixtures where the grid is fitted, reproduce the markets
  it was NOT fitted on (hold-one-out on totals lines) to sanity-check
  interpolation error.
- The script is committed and re-runnable when leagues/season change.

## 9. Refresh loop (coupons)
- Every 10 minutes APScheduler refreshes fair odds for all legs in confirmed
  coupons: fetch raw prices → devig → recompute direct + derived grids → value.
  If a Discord webhook is set, notify on positive-value legs. Alerts are
  deduplicated: a leg notifies only when its value tier (hi ≥ +6% / mid ≥ +4% /
  lo > 0) is new or changed since the last alert (`legs.notified_tier`; cleared
  when value drops non-positive, so a recovering leg re-alerts).
  `POST /coupons/{id}/refresh` triggers the same refresh on demand for one
  coupon. The staleness check (§5) runs on the same tick.

## 10. Auto-deletion (8 days)
- Daily APScheduler job + a run on startup (self-heals after downtime): delete
  `coupons` older than 8 days, cascading to `legs`, AND delete the image files
  from the volume. Never one without the other. In-app only; no host cron.
  (Window is `Settings.coupon_cleanup_days`, default 8.)

## 11. Authentication (≤4 users, shared data)
- Syndicate model: all users share the same coupons, uploads, and feeds. Logins
  exist to gate access and to attribute uploads (`coupons.uploaded_by`), not to
  partition data. All users are equal — no roles.
- Minimal in-app auth: a `users` table (bcrypt hashes), a signed session cookie
  (Starlette `SessionMiddleware`), and a login page. Users are seeded from
  `AUTH_USERS` in `.env` on startup. No registration / reset flows — change a
  password by editing `.env` and recreating the user.
- Caddy basic-auth is removed from `puntmore.com` (the separate
  `lab.puntmore.com` host keeps its own auth).
- **Machine access**: `GET /api/state` (`routers/state.py`) returns the whole
  app state (coupons+legs, AccaBonus feed, cards feed) as one JSON snapshot,
  guarded by the static `STATE_API_KEY` (`X-API-Key` header) instead of a
  session. Blank key disables it (503). Read-only by design; reads through the
  odds cache so polling costs no credits.

## 12. Frontend layout (v3 PWA — see `design/HANDOFF.md`)
Mobile-first PWA, warm paper theme, dense. Single column capped at 480 px,
safe-area insets, bottom tab bar (Coupon / AccaBonus / C/C). System sans for UI,
mono for all numbers. Value tiers colour the EV% badges (hi ≥ +6 % accent,
mid ink, lo faint).
- **Login** (`/login`): wordmark lockup, "Email or username" + password, LOG IN.
- **Coupon tracker** (`/`): header coupon pill (`COUPON:` name + chevron) +
  status chip; legs as value-ranked cards (match / pick + odds / badge). Rows
  with anomalies show a ⚠ badge; derived-market legs show the uncertainty band
  and a small "derived" marker. Tapping a leg edits it inline (reprices).
  Tapping the pill opens the coupon-picker bottom sheet (radio rows + an
  `↑ UPLOAD COUPONS` button). A "Re-scan" button re-runs OCR on the active
  coupon's image (preserving edited rows).
- **Upload** (`/upload`): coupon type + match-day date picker + image drop-zone,
  PROCESS IMAGE → rows auto-save and appear priced (no confirm gate). Below:
  PREVIOUS UPLOADS list.
- **Value tools** (`/accabonus`, `/cards`): title + min-edge control + freshness
  line. AccaBonus = feed of match cards (best side as the pick, league tag,
  ARB chip). C/C = match rows expanding to an inline bets accordion (single-open).

## 13. Build phases (see PLAN.md)
The suite (auth, AccaBonus, Cards, PWA) is built and deployed. The current work
is the **v4 coupon realignment** — moving the coupon tracker onto bettingiscool
with in-house devig, the derived score-grid model, and auto-confirm OCR.

### 13.1 Provider verification calls (gate for the realignment)
With a bettingiscool Starter key (€49), on a real upcoming fixture in each
league appearing on the physical coupons:
- Fetch fixtures for the league; confirm the match resolves to an event id.
- Fetch odds; confirm presence of: 1X2, ≥2 totals lines, TEAM TOTALS for both
  teams (the decisive check).
- Confirm raw prices are exposed (needed for in-house devig §7); note vendor
  devig fields if present (cross-check only).
- Record refresh latency and token/request cost for a full set of ~20 fixtures
  at 10-min cadence; confirm it fits the plan's quota.
- PASS = all of the above for every league on the coupons. Document results in
  `docs/provider-verification.md`. If it FAILS, stop and reassess providers
  before building further.

## 14. Verification expectations
Each phase ships with proof: pytest exercising API endpoints; provider
verification doc (§13.1); devig cross-check tests vs vendor `todds`; the derived
model reproduces held-out Pinnacle lines within tolerance; the calibration
backtest report committed; cleanup shown deleting both rows and image files; OCR
round-tripped on a real coupon image (auto-save + anomaly flags + re-scan
preserving edits); the auth flow (401 unauthenticated, 200 after login).
Frontend phases verify with a clean `npm run build` and a responsive manual
check.
