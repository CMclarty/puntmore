# Puntmore — Coupon-tracker-only focus (v5) — July 2026

Goal: surface **only the coupon tracker** for now. AccaBonus and Cards/Corners
are hidden from the app and disabled in the backend. **All their code stays in
the repo** — this is a visibility change, not a removal, reversible by flipping
two flags. This narrows the surface while the coupon realignment (v4 below)
continues.

## Decisions locked in (do not re-litigate)
- **Coupon tracker is the only visible tool.** AccaBonus + Cards/Corners are
  hidden. Nothing about their compute/pricing logic is deleted.
- **Hidden = gated, not gone.** Config flags `ENABLE_ACCABONUS` /
  `ENABLE_CARDS` (`config.py`, default False) decide whether each tool's API
  router is mounted (`main.py`). The frontend mirrors this: the nav tabs
  (`BottomNav.jsx`, `hidden: true`) and page routes (`App.jsx`, redirect to `/`)
  are suppressed. Their pages/routers/tests remain in the tree.
- **CLAUDE.md's "keep the 3-tool suite" still holds** as the architecture — the
  tools are dormant, not dropped. Re-enabling is: set both flags true in `.env`,
  restore the tabs/routes in the frontend, rebuild.

## What changed (done)
- `config.py`: `enable_accabonus` / `enable_cards` bool flags (default False).
- `main.py`: AccaBonus + Cards routers only `include_router`'d when their flag
  is set. Coupons, upload, auth, `/state` unchanged.
- `frontend`: `BottomNav.jsx` tabs marked `hidden` + filtered; `App.jsx`
  `/accabonus`, `/tennis`, `/cards` redirect to the tracker; the two page
  imports dropped (files kept).
- `tests/conftest.py`: sets both flags before importing `app` so the kept
  AccaBonus/Cards tests still run. Full suite green; `npm run build` clean.

## Coupon odds = TheOddsApi provider + full-response cache (done)
The coupon tracker prices legs through `TheOddsApiProvider` (behind the
`OddsProvider` seam). It pulls one FULL `/v4/sports/{sport}/odds` response per
sport and reuses it for `coupon_odds_cache_ttl` across every leg and the 10-min
refresh loop — the loop reads through the cache and never adds vendor calls.

- **Config, backend-only (no UI):** `COUPON_ODDS_SOURCE` (default `theoddsapi`)
  and `COUPON_ODDS_CACHE_TTL` (default `86400` = 24 h, the free-tier-safe value —
  see ceiling below) in `config.py` / `.env`. The odds source is NOT surfaced in
  the frontend and must stay that way — it is an operator setting, not a user
  choice.
- **Own cache TTL:** `TheOddsApiClient.fetch(..., ttl=)` lets the coupon path use
  its own TTL, independent of `odds_cache_ttl` (the hidden AccaBonus/Cards 24-h
  cache). `TheOddsApiProvider(cache_ttl=...)` threads it through; `main.py` wires
  `settings.coupon_odds_cache_ttl`.
- Tests: `test_scheduler.py::test_provider_threads_cache_ttl_to_client`. 98/98
  green; `npm run build` clean.

### Free-tier quota ceiling (the real numbers)
The-odds-api bills `credits = markets × regions`. The coupon path pulls
`h2h,totals` (2 markets) × `eu` (1 region) = **2 credits per sport key per
pull**, all keys each cache cycle. With the loop polling continuously (an 8-day
coupon window ⇒ near-always a live coupon), one pull happens per cache window:

```
min safe TTL (s) = 2,592,000 × keys × 2 / 500 = 10,368 × keys
```

| Coupon sport scope | Credits/pull | Shortest cache ≤ 500/mo |
|---|---|---|
| 7 soccer leagues (default) | 14 | ~20 h (use ~24 h for margin) |
| 3 leagues | 6 | ~8.6 h |
| 1 league | 2 | ~2.9 h (the floor) |

**30-min refresh is NOT reachable on the free tier at any usable scope.** The
default is therefore `86400` (24 h) — free-tier-safe at ~420 credits/month. On a
PAID tier, drop `COUPON_ODDS_CACHE_TTL` to `1800` for a 30-min refresh (a
one-line `.env` change, no code change).

## Compatibility for the eventual bettingiscool switch (keep this true)
bettingiscool is still the planned coupon odds provider (v4 §V1/§V2). The seam is
already shaped for a drop-in switch — do not regress it:
- Selection is a single branch in `main.py` keyed on `COUPON_ODDS_SOURCE`. Adding
  bettingiscool = implement `providers/bettingiscool.py` (satisfying
  `OddsProvider`) + an `elif settings.coupon_odds_source == "bettingiscool":`
  branch. No caller changes: the scheduler/upload paths depend only on the seam.
- Keep the switch **backend/config-only** — never add a provider picker to the UI.
- The v4 realignment (RawPrices seam, in-house devig, derived Win-and-Over) is the
  larger dependent work; TheOddsApi remaining the live provider until the €49 key
  lands does not change those plans.

---

# Puntmore Coupon Realignment (v4) — Plan

Goal: bring the **coupon tracker** into line with the reworked design (real
Pinnacle team totals, in-house devig, a proper derived Win-and-Over model, and
phone-friendly OCR) WITHOUT disturbing the rest of the deployed suite. Source of
truth: `SPEC.md` §5–§10, §13. The v3 plan below is complete and kept for history.

## Decisions locked in (from the June/July 2026 realignment review — do not re-litigate)
- **Keep the 3-tool suite.** AccaBonus, Cards, in-app auth, the v3 PWA, and the
  compose/nginx/Caddy deployment all stay. The seed spec's single-tool /
  Railway / Cloudflare-Access / sets-versions framing is STALE — ignore it.
- **Coupon odds move to bettingiscool** (Pinnacle-native, exposes team totals +
  raw prices) behind the existing `OddsProvider` seam. AccaBonus + Cards keep
  TheOddsApi. Two odds sources on purpose (SPEC §5). Paid Starter key (€49).
- **Keep the coupons/legs model** (the R1 migration stands). Do NOT reintroduce
  sets/versions/matches or the Mon–Wed/Sat/Sun set navigation.
- **Coupons devig IN-HOUSE** (power method, reuse `odds/utils.py::devigd_multi`).
  This reverses the R10 raw-Pinnacle exception. Vendor `todds` = cross-check only.
- **Win-and-Over is DERIVED** via a calibrated score-grid model (SPEC §8). The
  old naive h2h×totals `is_approx` path is retired. NEVER multiply moneyline×over.
- **OCR is auto-confirm** (no blocking review): rows auto-save + price, anomaly
  ⚠ flags, inline edit, re-scan preserving human edits (SPEC §7.2). Status
  becomes processing→confirmed.
- **Cleanup window → 8 days** for coupons (`Settings.coupon_cleanup_days`).

## Migration notes (target shape)
- `legs` gains: `fair_odds_source` (direct|derived_model), `uncertainty_band`
  (nullable), `flags` (nullable JSON — anomaly reasons), `edited` (bool).
- `coupons.status`: map `pending|live` → `processing|confirmed` (guarded DDL in
  `security.ensure_schema`; no Alembic).
- New modules: `providers/bettingiscool.py`, `app/pricing/` (devig pipeline +
  `grid.py` derived model), `api/tools/backtest_calibration.py`,
  `docs/provider-verification.md`.
- The `OddsProvider` seam changes from `get_fair_odds -> OddsResult(fair_odds)`
  to `get_market_prices -> RawPrices` (1X2 + totals ladder + team totals). The
  refresh loop and upload path move from "provider returns a price" to "provider
  returns raw prices → core devigs/derives → price". `MockOddsProvider` updates
  to match.

## Phase V1 — Provider verification GATE (bettingiscool) ⏳ BLOCKED on €49 key
Nothing downstream starts until this passes (SPEC §13.1). External dependency:
buy a bettingiscool Starter key, put it in `.env` as `BETTINGISCOOL_API_KEY`.
- [ ] For a real upcoming fixture in EACH league on the physical coupons: the
      match resolves to a bettingiscool event id.
- [ ] Odds payload contains 1X2, ≥2 totals lines, and TEAM TOTALS for both
      teams (the decisive check — no team totals ⇒ stop and reassess provider).
- [ ] Raw prices exposed (for in-house devig); note whether `todds` present.
- [ ] Record refresh latency + token/request cost for ~20 fixtures at 10-min
      cadence; confirm it fits the plan quota.
- DoD: `docs/provider-verification.md` committed with results for every league.
  PASS on all leagues, or the phase fails and providers are reassessed.

## Phase V2 — bettingiscool provider + in-house devig (direct markets)
- [ ] `RawPrices` dataclass + `OddsProvider.get_market_prices` in `providers/base.py`.
- [ ] `providers/bettingiscool.py` (X-API-Key client → RawPrices: 1X2, totals
      ladder, team totals). `config.py` + `.env.example`: `BETTINGISCOOL_API_KEY`.
- [ ] `app/pricing/` devig pipeline: raw prices → `devigd_multi` → fair probs →
      direct-market fair odds (Team-to-Score 2+/3+ → team total O1.5/O2.5).
- [ ] Cross-check tests: in-house devigged probs vs vendor `todds` within tol.
- [ ] Wire refresh loop (`scheduler.py`) + upload pricing to the new pipeline;
      update `MockOddsProvider`; `main.py` lifespan selects BettingIsCool.
- [ ] `legs.fair_odds_source` column (guarded ALTER); direct legs = "direct".
      Win-and-Over legs left unpriced+flagged until V3 (no naive fallback).
- DoD: pytest green; a real fixture prices its direct markets end-to-end;
      `npm run build` clean.

## Phase V3 — Derived Win-and-Over model (SPEC §8)
- [ ] `pricing/grid.py`: fit λ_home/λ_away + dependence (independent Poisson +
      Dixon–Coles, or bivariate Poisson) to devigged 1X2/totals/team-totals by
      least squares; grid truncated at 10 goals/team.
- [ ] Derive P(win ∧ over X) off the grid → fair odds = 1/p; wire Win-and-Over
      legs to it; `fair_odds_source = "derived_model"`; set `uncertainty_band`.
- [ ] Hold-one-out test: refit dropping a totals line, reproduce it within tol.
- [ ] Provisional derived threshold 8% (until V4); UI shows derived marker +
      band ("+9% (±3%)"). Frontend TrackerPage badge update.
- DoD: pytest incl. hold-one-out; derived legs price + display band; build clean.

## Phase V4 — Calibration backtest (SPEC §8.4)
- [ ] `api/tools/backtest_calibration.py`: over a season+ of historical fixtures
      w/ settlements, fit grids, compute derived P(win∧over X), score against
      outcomes (reliability curve + Brier, bucketed).
- [ ] Set the production derived threshold + displayed uncertainty band FROM the
      measured error band (replace the provisional 8%).
- DoD: backtest report committed; thresholds wired to config; script re-runnable.

## Phase V5 — OCR auto-confirm + anomaly flags + inline edit + re-scan (SPEC §7.2)
- [ ] Remove the blocking review gate: upload → rows persist + price;
      status processing→confirmed. Schema: `flags`, `edited`, status remap
      (guarded DDL).
- [ ] Sanity checks → `legs.flags`: odds-range per market, fuzzy-match
      confidence, value plausibility cap (±15%). ⚠ badge rendering.
- [ ] Inline edit (match/pick/odds) reprices + sets `edited=true`. Unmatched
      rows persist with blank fair odds.
- [ ] Re-scan endpoint + button: re-run OCR, replace UNEDITED rows only,
      re-run checks. Frontend UploadPage + TrackerPage.
- DoD: OCR round-trip on a real coupon image (auto-save, flags, re-scan keeps
      edits); pytest green; build clean.

## Phase V6 — Cleanup window, staleness, verification & deploy
- [ ] Coupon cleanup 30→8 days (`Settings.coupon_cleanup_days`, default 8);
      test shows rows AND image files deleted.
- [ ] Staleness banner: prices older than 2× refresh interval → last-updated
      banner (SPEC §5).
- [ ] Docs sync check; full verification: pytest green, `npm run build` clean,
      responsive 390/768px, `docker compose up --build -d` + live smoke test.

---

# Puntmore PWA Redesign (v3) — Plan

Source of truth: `design/HANDOFF.md` + `design/Puntmore-Flow.html` (6 screens + 2
sub-flows). Read the handoff before each phase. The v2 plan below this section is
complete and kept for history.

## Decisions locked in (do not re-litigate)
- **Paper theme replaces dark.** Tokens: `--paper #FCFBF8`, `--zebra #F4F2EC`,
  `--ink #1A1A18`, `--muted #6B6862`, `--faint #9C988F`, `--line #D8D5CD`,
  `--line-strong #BBB7AD`, `--accent #2F7D55`, `--accent-soft #E1EFE7`.
  System sans for UI; mono (`ui-monospace…`) for ALL numbers (odds/values/status/
  dates). Do NOT use Caveat — that's flow-diagram annotation only.
- **Mobile-first PWA.** Single mobile column, safe-area insets, cap width on tablet
  (center the column). Bottom tab bar (Coupon/Tennis/C/C). Touch targets ≥44px.
  Don't reproduce the mock's phone bezel/notch — build screen contents only.
- **Keep image-OCR upload** (NOT CSV/XLSX). Screen 06 is restyled over the existing
  image-upload → OCR → correction-review → confirm pipeline. The drop-zone copy can
  stay generic; the `<input>` accepts images, not `.csv/.xlsx`.
- **Rework the backend coupon model** to the design's shape: a Coupon is a named
  accumulator with N legs (replaces the sets/versions/templates machinery).
- **Density principle still applies** (see memory) — just light now.

## Open product questions (resolve in Phase 0, don't block planning)
- **Coupon name origin:** design shows "Saturday Acca". Propose: optional name field
  on upload, default `"{type_code} · {match_day}"`. Confirm.
- **Match day:** dropdown of days vs date picker. Propose a date → derive day label.
- **Login identifier:** handoff says "email address"; current auth is username.
  Propose: keep username under the hood, label field "Email or username". Confirm.
- **Value tiers:** hi ≥ +6% (green), mid (ink), lo < +4% (faint) are illustrative —
  wire to real cutoffs. Confirm thresholds or keep as constants for now.
- **Dropping sets/versions:** the rework removes the Mon–Wed/Sat/Sun set nav and the
  3-versions-per-coupon cap. Confirm that machinery goes.

## Data-model rework (target shape)
- `coupons` (accumulators): id, name, type_code (e.g. "TTS2"), match_day,
  status (pending|live), image_path (OCR source), created_at.
- `legs`: id, coupon_id (FK, cascade), match_name, pick (selection text,
  e.g. "PSG HT/FT"), odds (your_odds), fair_odds, value, external_event_id, rank.
- Value tier derived from `value`. Map current `Match`→`Leg`, current
  `Version`→`Coupon`, current coupon-template→`type_code`, current `Set`→`match_day`.
- Migration: dev DB is reseeded; provide a fresh `seed.py`. SQLite — recreate tables
  (drop old sets/versions/matches/coupons-as-templates), no Alembic.

---

## Phase R0 — Foundations & decisions ✅
All product questions resolved (all defaults). Paper tokens replaced dark CSS vars.
`manifest.webmanifest` + `icon.svg` added. Mobile app shell (max-width 480px,
safe-area insets). Build clean.

## Phase R1 — Backend: coupon model rework ✅
New `Coupon` (accumulator) + `Leg` model; dropped Set/Version/template-Coupon/Match.
New seed.py with sample accas. Routers: `GET /coupons`, `GET /coupons/{id}/legs`.
Upload: `POST /upload` (type_code + match_day + image) → OCR → confirm → live.
Scheduler + cleanup updated. R1-migration in `ensure_schema()`. 59/59 tests green.

## Phase R2 — Backend: tennis & C/C feeds ✅
Added `GET /tennis/feed` ({match, pick, odds, value, is_arb}) and `GET /cards/feed`
(grouped by match, sorted by best_value). Legacy endpoints kept. 59/59 tests green.

## Phase R3-R5 — Frontend: PWA shell + all screens ✅
Bottom tab bar (Coupon/Tennis/C/C), paper theme throughout. Login restyled (wordmark
lockup, "Email or username", LOG IN). Service worker registered. All four pages
rewritten: TrackerPage (coupon pill + picker sheet + leg cards), UploadPage (drop-zone
+ OCR review + previous uploads), ArbsPage (tennis feed cards), CardsPage (accordion).
Old components (NavTabs, SetNav, CouponList, VersionPanel, etc.) deleted. Build clean.

## Reassessment pass (post R5) ✅
- SPEC.md rewritten around the v3 model (coupons+legs, pending→live, PWA layout §11).
- Added `POST /coupons/{id}/refresh` (on-demand fair-odds refresh, reads through
  cache) + "↻ Refresh odds" button on the tracker — replaces the lost
  `/versions/{id}/refresh-odds` capability.
- Added test_feeds.py (8 tests: feed shapes, grouping, min_edge, arb flag, auth)
  and 3 refresh-endpoint tests. 70/70 green.
- Dropped dead `getArbs`/`getCards` exports from frontend api.js.

## Phase R7 — Alert dedupe & odds-match transparency ✅
(Done out of order, before R6 — correctness hardening prioritised per user.)
- [x] Repo put under git (initial commit `8f76fbc`); `.gitignore` excludes `.env`,
      DBs, venv, node_modules, dist. **Remote push still manual** — add a private
      remote and push.
- [x] Discord value-bet alerts deduped: notify only when a leg's value tier
      (hi/mid/lo, mirrors frontend cutoffs) is new or changed; tracked in new
      `legs.notified_tier` column (guarded ALTER in `ensure_schema`).
- [x] Fuzzy-match transparency: `OddsResult.matched_fixture` returned by
      `TheOddsApiProvider`, persisted on `legs.matched_fixture`, shown in the
      tracker when it differs from the OCR'd name. Borderline matches
      (0.6 ≤ score < 0.75) logged as warnings.
- [x] Compound picks ("win and over X.X") badged approximate: `Leg.is_approx`
      property + `LegOut.is_approx`; tracker renders `fair ≈X.XX` with tooltip.
- [x] 82/82 tests green (test_scheduler.py added: dedupe, tiers, fixture
      surfacing, borderline logging); `npm run build` clean.

## Phase R8 — AccaBonus pivot (tennis → all Acca Boost leagues) ✅ (June 2026)
Done — do NOT redo or re-research. The tennis tracker is now the AccaBonus
tracker, scanning bet365's Acca Boost-eligible competitions.

- [x] **Eligibility verified against bet365's published Acca Boost terms
      (June 2026, via aceodds.com/promotions/bet365-football-acca-bonus):**
      Tennis all ATP/WTA/Challenger (whole group), MMA any event (whole group);
      restricted: Basketball NBA/Euroleague/EuroBasket/FIBA/Olympics,
      Am. Football NFL only, Baseball MLB/NPB/KBO, Ice Hockey
      NHL/Worlds/Olympics. **Cricket and rugby are NOT eligible** (the
      pre-pivot filter wrongly included them). **Soccer IS eligible for the
      boost but is deliberately EXCLUDED from this tool — user decision
      (June 2026): football is covered by the coupon tracker and cards/corners
      tools. Don't re-add it.** Pre-season/exhibition/All-Star excluded per
      terms (`_EXCLUDED_RE`). Encoded in
      `accabonus/arbs.py::select_accabonus_keys` + tests. Re-verify terms only
      if bet365 changes the offer.
- [x] Multi-outcome de-vig: `odds/utils.py::devigd_multi` de-vigs across all
      priced outcomes (handles a draw generically when both books price one,
      e.g. MMA fight-outcome); `ArbRow`/`ArbOut` gained nullable `*_draw`
      fields; arb check generalised to best-price-per-outcome. The old 2-way
      `devigd` kept for cards.
- [x] Rename throughout: `app/accabonus/`, `routers/accabonus.py`,
      `/accabonus/{arbs,feed}`, `ACCABONUS_SPORT_KEYS`, `AccaBonusFeedItem`
      (+ `sport` league tag, rendered on feed cards), `AccaBonusPage.jsx`,
      route `/accabonus`, stacked-legs nav icon. **Legacy `/tennis/*` API paths
      and the `/tennis` route are intentional aliases/redirects** for stale
      cached PWA shells — keep until clients have rotated; don't add code there.
- [x] Quota reality (testing phase): without soccer the discovered scan is
      ≈ 10–30 keys ≈ 300–900 credits/month at the 24 h cache — can still
      **exceed the 500 free-tier cap** in a busy tennis month. While on the
      free tier set `ACCABONUS_SPORT_KEYS` to a few leagues if needed; the cap
      stops applying after the planned paid upgrade (user decision).
- [x] 96/96 tests green (`test_accabonus.py`: eligibility, 3-way, draw edge,
      legacy aliases); `npm run build` clean; deployed + live smoke-tested.

## Phase R9 — `/state` snapshot endpoint ✅ (June 2026)
- [x] `GET /api/state` (`routers/state.py`): whole-app JSON snapshot — coupons
      with legs (best value first), AccaBonus feed, cards feed, `generated_at`.
      For machine consumers (dashboards/bots).
- [x] Auth: static `STATE_API_KEY` via `X-API-Key` header (constant-time
      compare), NOT session-guarded; blank key = 503/disabled. A live key is
      already set in `.env` (generated June 2026). Reads through the odds cache
      — polling costs zero credits. Keep the endpoint read-only.
- [x] `test_state.py` covers 503/401/200 + leg sorting; verified live.

## Phase R10 — Power-method de-vig ✅ (June 2026)
- [x] Replaced the multiplicative de-vig with the **power method** in
      `odds/utils.py` (user decision): solve k so `Σ (1/price)^k = 1`,
      `fair = price^k` (bisection, tol 1e-13). Loads more vig onto longshots —
      corrects the favourite–longshot bias the multiplicative method ignores.
      Applies to AccaBonus + cards via the shared seam. NOTE: the coupon
      tracker does NOT de-vig — `providers/odds_api.py::_extract_fair_odds`
      uses raw Pinnacle prices as fair (pre-existing behaviour, left
      unchanged; coupon "value" is therefore slightly conservative).
      `margin` stays the raw overround. 97/97
      tests green (power-vs-multiplicative direction test added; the
      multiplicative-formula assertion in test_cards rewritten to
      method-agnostic invariants + same-k check).

## Phase R6 — Polish, offline, verification
- [ ] Live status `● LIVE · HH:MM`; stale/offline indicator when SW serves cache.
- [ ] Animations (accordion, sheet) ~150–200ms ease; chevron rotation.
- [ ] Full verification: pytest green, `npm run build` clean, responsive 390/768px,
      Lighthouse PWA. Deploy (`docker compose up --build -d`) + live smoke test.

---

# Puntmore — Rework Plan (v2) — COMPLETE (kept for history)

Decisions locked in (do not re-litigate):

- **Free Odds API tier for now** — 24 h cache stays; make TTL a config value so a paid
  upgrade later is a one-line `.env` change.
- **4 members, shared data (syndicate), all equal** — logins identify who did what;
  no roles, no per-user data partitioning.
- **Auth = in-app sessions, minimal**: `users` table + bcrypt + Starlette
  `SessionMiddleware` signed cookie. No JWT, no registration, no password reset.
- **Mobile-friendly UI** is in scope. No other new features.
- **SPEC.md gets rewritten around reality** (TheOddsApi, unified frontend, 3 tools).

History: phases 1–6 of the original coupon tracker build are complete (see git/SPEC
for context). The three frontends were merged into `frontend/`; odds fetching was
unified into `api/app/odds/client.py::TheOddsApiClient`. 83/83 tests green at start.

Rules for the implementing model:
- Complete and verify one phase before starting the next. Run
  `api/.venv/bin/python -m pytest api/tests/ -q` and `npm run build` (in `frontend/`)
  at the end of every phase; both must pass.
- Never call external APIs from core logic — everything goes through the seams
  (`OCRProvider`, `OddsProvider`, `TheOddsApiClient`, `NotificationProvider`).
- Do not shorten the odds cache TTL default (86400). Quota math: free tier is
  500 credits/month; ~2 credits per sport/odds call.
- OCR output always passes through the manual-correction step. Never auto-confirm.
- The 30-day cleanup deletes BOTH DB rows AND image files. Never one without the other.

---

## Phase A — Documentation truth pass ✅
Goal: every doc describes the software that actually exists.

- [x] Rewrite `SPEC.md`: three tools (coupon tracker / AccaBonus arbs / cards & corners),
      unified React frontend, shared FastAPI backend, `TheOddsApiClient` data layer,
      de-vig methodology, free-tier quota budget, Caddy → nginx → containers topology.
      Kept §3 (domain concepts), §7 (OCR correction), §9 (auto-deletion); deleted PS3838.
- [x] Rewrite `CLAUDE.md` structure section: `frontend/` (single app), `api/`, `nginx/`.
      Removed the three per-service dirs and "Adding a new service"; replaced with
      "Adding a new tool page". Provider guide now documents `app/odds/client.py` as the
      shared HTTP/cache layer; cache-TTL guardrail references `Settings.odds_cache_ttl`.
- [x] Moved `CACHE_TTL` into `Settings` as `odds_cache_ttl: int = 86400`; wired through
      `TheOddsApiClient(api_key, cache_ttl=...)` and `main.py`. Added commented
      `ODDS_CACHE_TTL=` to `.env.example` with the paid-tier note.
- [x] DoD: docs free of PS3838-as-backend, old per-service dirs, and false 1-hour-cache
      claims. (Code column `ps3838_event_id` still exists — rename deferred to Phase D.)
      83/83 tests green.

## Phase B — Auth (4 users, shared data) ✅
Goal: login page in front of everything; the app knows who acted.

Backend:
- [x] Added deps `bcrypt`, `itsdangerous`; added to `requirements.txt`.
- [x] `models.py`: `User` table (id, username unique, password_hash, created_at).
- [x] `config.py`: `secret_key` + `auth_users`; both in `.env.example`. Empty
      `secret_key` → random key generated at startup with a logged warning.
- [x] Startup seeding (`security.seed_users`, called in lifespan): parses
      `auth_users`, creates missing users (bcrypt), never modifies existing.
      Documented in CLAUDE.md/SPEC §10.
- [x] `routers/auth.py`: `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`.
- [x] `SessionMiddleware` added (`same_site="lax"`, `https_only=False`, 30-day).
- [x] Guard: single `security.require_user` dependency applied to all six data
      routers via `include_router(..., dependencies=[Depends(require_user)])`.
      Auth router open; `/auth/me` self-guards.
- [x] Attribution: nullable `versions.uploaded_by`, set from session in upload.
      `security.ensure_schema()` adds the column to a pre-existing DB on startup.
- [x] Tests (`test_auth.py`, 13): hashing roundtrip, parse/seed idempotency +
      no-modify, login 401/200, `/auth/me`, logout, guard 401-without/200-with.
      `conftest.py` bypasses `require_user` so the existing 83 tests pass unchanged.

Frontend:
- [x] `LoginPage` at `/login` (own card module, Puntmore-branded).
- [x] App boot calls `/auth/me`; gates routes; `api.js` redirects to `/login` on
      any 401 (and `getMe` returns null on 401 without redirect, for boot).
- [x] NavTabs shows username + logout button (right-aligned).

Infra:
- [x] Removed `basic_auth` from the `puntmore.com` Caddy block (lab host untouched).
- [x] DoD: 96/96 tests green; frontend build green. Manual browser check pending
      (run `docker compose up --build`, set `AUTH_USERS` in `.env`).

## Phase C — Mobile-friendly UI ✅
Goal: fully usable at 390 px wide (no body-level horizontal scroll).

- [x] Tables scroll internally: `ValuePage .surface`, `VersionPanel .tableWrap`,
      and `UploadPage .tableWrap` all `overflow-x: auto`.
- [x] `TrackerPage` ≤ 768 px: `.body` stacks vertically; coupon list becomes a
      horizontal scrollable chip row (`CouponList` media query); VersionPanel full width.
- [x] `NavTabs` ≤ 480 px: brand + username hidden, tabs scroll, logout kept.
- [x] Value pages: header wraps; page padding drops to 12/8 px; ThresholdControl
      hint hidden < 480 px.
- [x] Touch targets ≥ 40 px on touch widths (coupon chips, logout, vTab bumped).
- [x] UploadPage review table wrapped (overflow-x).
- [x] DoD: `npm run build` clean. Manual responsive viewport check pending (needs
      running app).

### Density pass (mid-Phase, per user) ✅
User directive: "utility, not a marketing product — cut buffer spacing to a minimum."
Saved to memory ([[feedback-dense-utilitarian-ui]]). Tightened table cells
(8–9 px → 3 px), nav bar (44 → 36 px), panel headers, page paddings, and card
paddings across all surfaces. Build green.

## Phase D — Loose ends ✅
- [x] Phase 4b leftover: `cleanup_old_versions` now notifies (versions + image-file
      counts) and `start_scheduler` registers an `EVENT_JOB_ERROR` listener that
      notifies on any job failure. Startup cleanup passes the notifier too.
      2 new tests in test_phase6.
- [x] Replaced all `datetime.utcnow()` with a shared `time_utils.utcnow()` (naive UTC,
      computed the non-deprecated way). Warnings down from 76 → 2 (both third-party).
- [x] Deleted the dead `TENNIS_SPORT_KEYS` alias in `tennis/arbs.py` (no references).
- [x] Deleted the PS3838 provider (`providers/ps3838.py`) + its 3 rate-limit tests
      (per user decision). Renamed `ps3838_event_id` → `external_event_id` in
      `models.py` + `schemas.py`, with a guarded `ALTER TABLE ... RENAME COLUMN`
      migration in `security.ensure_schema`.

### Discovered / handled
- [x] `app/market_map.py` was orphaned by the PS3838 removal (no live consumer).
  Deleted the module and its 4 tests in test_phase3. PS3838 + market_map now fully
  gone from live code and tests; only the column-rename migration in security.py
  still names the old column (required to detect it).
- [ ] DoD: zero deprecation warnings from our own code in pytest output; tests green.

---

Out of scope (explicitly): paid Odds API tier, bet logging/P&L, roles/permissions,
password reset flows, Postgres, per-user data separation.
