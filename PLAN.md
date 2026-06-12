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
