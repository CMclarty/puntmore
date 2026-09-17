# Puntmore — Plan

Current work is in **Open work** below. **Decisions** are settled — don't
re-litigate them. **History** is a record of completed phases, kept because the
reasoning behind some of them is expensive to reconstruct.

> Consolidated July 2026 from four stacked plan generations (v2 rework, v3 PWA
> redesign, v4 coupon realignment, v5 coupon-only focus). Those generations used
> three different phase prefixes (`A–D`, `R0–R10`, `V1–V6`) and the file read
> newest-first, so the open work sat below the completed work and phase `V2` was
> easy to confuse with plan `v2`. There is now one list. Old phase IDs are kept
> in History so existing commit messages still resolve.

---

## Current status (July 2026)

Only the **coupon tracker** is surfaced. AccaBonus and Cards/Corners are gated
off (`ENABLE_ACCABONUS` / `ENABLE_CARDS`, default False) — hidden, not removed.

The coupon tracker prices via **bettingiscool** (item 6, landed) and de-vigs
in-house. Win-and-Over legs are **derived off the fitted score grid** (item 1,
landed) — no longer an independence approximation. OCR **auto-confirms** with
per-leg anomaly flags, inline edit and re-scan (item 2, landed).

Remaining: the derived uncertainty band is computed but not displayed, and it
stays provisional until the calibration backtest (item 3) measures it.

---

## Decisions (settled — do not re-litigate)

### Product
- **Coupon tracker is the only visible tool.** AccaBonus + Cards stay in the
  tree, gated behind config flags. Re-enabling = flip both flags, restore the
  nav tabs/routes, rebuild.
- **Keep the 3-tool suite as the architecture.** The tools are dormant, not
  dropped.
- **Keep the coupons/legs model.** Do NOT reintroduce sets/versions/matches or
  the Mon–Wed/Sat/Sun set navigation.
- **Syndicate model**: 4 members, shared data, all equal. No roles, no per-user
  partitioning. Auth = in-app sessions (bcrypt + signed cookie); no JWT, no
  registration, no password reset.
- **Image OCR upload, not CSV/XLSX.**
- **Coupon cleanup window is 8 days**, and deletes BOTH database rows AND the
  stored image files. Never one without the other.
  *(Supersedes the v2-era 30-day window.)*
- **Coupon kickoff times are GMT.** The time printed on a coupon is GMT, so it
  is stored naive and read back as UTC everywhere (countdown, kicked-off sweep).
  No local-time conversion — one would shift every kickoff by an hour through
  BST. (July 2026, Callum's call.)
- **Coupons are structure, not inference.** OCR transcribes the whole grid —
  every market block, keyed by its printed heading, each pair positional — and
  the app matches headings and assigns sides in code. The model is never asked
  which block to read or which team a price belongs to. Consequence: one photo
  of a sheet fills every market printed on it, sharing one stored image.
  (July 2026, after a coupon came back with home/away prices crossed.)
- **One issue per (type, period); the newest upload is the coupon.** A new
  upload replaces the previous one of the same type AND period — row and image
  both — but only once it has read rows, so a failed or empty scan never takes
  a working coupon with it. Different periods of one type still coexist: a GG
  midweek and a GG weekend are two different bets. (July 2026, Callum's call.)
- **An issue is addressable, and the newest one is the default.**
  `/t/<code>/<coupon_id>` is one issue; the ladder combination, the fixture list
  and the counts are all per issue. A type-wide ladder figure could only ever
  describe the newest upload, which made the live issue's value unreadable the
  moment the next one landed. Opening a type resolves to its most recent window
  (the coupon just photographed); `/t/<code>/all` spans every issue and is
  reached from the tab row.
- **OCR is auto-confirm** — rows auto-save and price immediately with per-row
  anomaly flags and inline editing. *(Supersedes the v2-era rule that OCR always
  passes through a blocking manual-correction step.)*

### Pricing
- **Fair odds are computed IN-HOUSE by de-vigging raw prices** with the power
  method (`odds/utils.py`). Vendor-supplied de-vigged fields are cross-check
  only, so providers stay swappable. Applies to every tool including coupons.
- **Power method, not multiplicative** (June 2026). Solve k where
  `Σ(1/price)^k = 1`, `fair = price^k`. Loads more vig onto longshots, matching
  the favourite–longshot bias. `margin` stays the raw overround.
- **Incomplete markets go unpriced.** De-vigging needs the full outcome set;
  where it isn't available the provider returns None. A vigged price mislabelled
  as fair is worse than no price.
- **Win-and-Over must be DERIVED** from a calibrated score grid. NEVER price it
  as moneyline × over — result and total goals are correlated, so the product
  overstates fair odds. (Currently still approximated; see open item 1.)
- **Derived-market thresholds come from MEASURED backtest calibration**, not
  assumption. Until measured: provisional 8% with the uncertainty band shown.

### Providers & quota
- **TheOddsApi is the live coupon odds provider.** `COUPON_ODDS_SOURCE`
  (default `theoddsapi`), backend-only — never surface a provider picker in the
  UI.
- **bettingiscool is deferred, not blocking.** It remains attractive (Pinnacle
  native, exposes team totals) but nothing waits on it. Adding it = implement
  `providers/bettingiscool.py` against the `OddsProvider` seam + one `elif` in
  the `main.py` lifespan. Keep that seam intact.
  *(Supersedes v4's "coupon odds move to bettingiscool", which gated all
  realignment work behind a €49 key purchase.)*
- **Free tier is 500 credits/month**; credits = markets × regions. The coupon
  path pulls `h2h,totals` × `eu` = 2 credits per sport key per cache cycle.
  `min safe TTL (s) = 10,368 × keys` → 7 leagues needs ~20 h, so the default is
  86400 (24 h, ~420 credits/month). **A 30-min refresh is unreachable on the
  free tier at any usable scope.** On a paid tier, drop
  `COUPON_ODDS_CACHE_TTL` to 1800 — an `.env` change, no code change.
- **The 500-credit cap is a testing-phase constraint.** A paid upgrade is
  planned; don't cut scope permanently to fit it.
- **Never call external APIs from core logic** — everything goes through the
  seams (`OCRProvider`, `OddsProvider`, `TheOddsApiClient`,
  `NotificationProvider`).

### Design
- **Paper theme.** `--paper #FCFBF8`, `--zebra #F4F2EC`, `--ink #1A1A18`,
  `--muted #6B6862`, `--faint #9C988F`, `--line #D8D5CD`,
  `--line-strong #BBB7AD`, `--accent #2F7D55`, `--accent-soft #E1EFE7`.
  System sans for UI, mono for ALL numbers. Caveat is flow-diagram annotation
  only — never in the app.
- **Mobile-first PWA.** Single column, safe-area insets, capped width on tablet,
  bottom tab bar, touch targets ≥44px.

---

## Open work

Ordered by dependency, then value. Each item ends with its Definition of Done.

### 1. Derived Win-and-Over score grid  *(was Phase V3)* — MOSTLY DONE
Retires the last naive-independence path in pricing.
- [x] `pricing/grid.py`: fit λ_home/λ_away + dependence (independent Poisson +
      Dixon–Coles, or bivariate Poisson) to de-vigged 1X2 + totals by least
      squares; grid truncated at 10 goals/team.
- [x] Derive P(win ∧ over X) off the grid → fair odds = 1/p. Wire Win-and-Over
      legs to it.
- [x] Hold-one-out test: refit dropping a totals line, reproduce it within tol.
- [x] Retire `Leg.is_approx` and its `≈` badge (July 2026 — the badge described
      pricing that no longer happens).
- [ ] **Still open:** surface the derived marker + band in the UI
      ("+9% (±3%)"), and set `fair_odds_source = "derived_model"` /
      `uncertainty_band` on the leg. Deliberately deferred into item 3 — the
      band's number comes from the backtest, and shipping a provisional ±band
      as if it were measured is the kind of false precision the 8% threshold
      rule exists to prevent.
- **Scope note:** TheOddsApi exposes no team totals, so the fit uses 1X2 + the
  single totals line — 3 constraints for 2 parameters. Identifiable, but the
  dependence parameter is less well pinned than team totals would allow. A
  fuller ladder via `alternate_totals` adds a third market (2 → 3 credits per
  sport per pull, pushing min safe TTL ~20 h → ~31 h), so it is **paid-tier
  only**.
- DoD: pytest incl. hold-one-out; derived legs price and display their band;
  `npm run build` clean.

### 2. OCR auto-confirm, anomaly flags, inline edit, re-scan  *(was Phase V5)* — DONE
- [x] Remove the blocking review gate: upload → rows persist + price; status
      `processing`→`confirmed`. Schema: `flags`, `edited`, status remap (guarded
      DDL in `ensure_schema`, no Alembic).
- [x] Sanity checks → `legs.flags` (`checks.py`): odds-range per market,
      fuzzy-match confidence, value plausibility cap (±15%). ⚠ badge rendering.
- [x] Inline edit (match/pick/odds) reprices and sets `edited=true`. Unmatched
      rows persist with blank fair odds.
- [x] Re-scan endpoint + button: re-run OCR, replace UNEDITED rows only, re-run
      checks. **Never overwrite a human correction with a machine re-read.**
- **Note:** re-scan skips *ranks* held by edited rows rather than appending
  fresh rows beside them — OCR rows are positional, so appending duplicates the
  leg instead of replacing it.
- **Still to verify manually:** the round-trip on a real coupon photo. Tests
  cover it with `MockOCRProvider`; nobody has run a real phone image through
  `ClaudeOCRProvider` since auto-confirm landed.
- DoD: pytest green (149); `npm run build` clean; V5 migration exercised
      against a pre-V5 database.

### 2b. Coupon processing rebuilt around what coupons actually are  — DONE
Driven by the first real coupon photo (Betfred GG Bonus, 20–23 Jul 2026), which
invalidated several assumptions at once.

- [x] OCR returns a whole coupon (fixtures + kickoffs + printed payout ladder +
      date range), not bare rows. It is no longer asked for a per-row market —
      that is known from which coupon was photographed.
- [x] Ladder coupons: the printed ladder is stored per coupon and every rung is
      scored to find the best combination. `flat_payout=5.5` was only the
      3-fold rung of a ladder running to 13+.
- [x] Variable periods (GG Midweek/Weekend) with dates taken from the coupon's
      own kickoffs. The observed issue ran Mon–Thu, which no fixed window
      covers.
- [x] `FIXTURE_LIMIT` 500 → 1000 (the API's max). At 500 the board truncated
      mid-week and match rate on a real coupon was 32/74; at 1000 it is 67/74.
- **Verified end to end on the real photo:** 74 fixtures read, ladder read,
  window Mon 20 – Thu 23 derived from fixtures and matching the printed text,
  67 priced against live bettingiscool.
- **Still open:** 6 fixtures unmatched (the coupon's Monday games had already
  played and were off the board) and 1 matched-but-unpriced.

### 2a. Price the seven coupon types  *(market definitions in SPEC §3, §8.5, §8.6)*
Ordered so the shared prerequisite lands first.

> **Aug 2026 — read this before debugging "nothing prices".** These markets all
> price on **bettingiscool** and NONE of them price on the TheOddsApi fallback,
> which the app drops to silently when `BETTINGISCOOL_API_KEY` is unset. A TTS
> coupon on the fallback is 100% blank by construction. Check
> `/api/ready` → `providers.odds_detail` FIRST: it names the provider and lists
> what it cannot price. A blank tracker is a config question before it is a
> pricing question.

- [x] **Resolve the picked team** (SPEC §8.6) — DONE for BOTH providers
      (Aug 2026). The side comes from the pick's team fragment scored with
      `team_similarity` (`app/picks.py`); unresolved ⇒ UNPRICED, never home.
      `TheOddsApiProvider` still hardcoded home until this pass, so which
      provider a deploy ran decided whether away selections were priced as
      their opponent.
- [ ] **TTS1/2/3** → DIRECT off `home_totals` / `away_totals` at line N−0.5.
      Note the off-by-one: "Team to Score 2" is over **1.5**.
- [ ] **GG No Draw** → derive off the grid: Σ g[h][a] for h≥1, a≥1, h≠a.
      Never BTTS × (1 − P(draw)) — same correlation trap as Win-and-Over.
- [ ] **GG Bonus** → a fixture SCREEN, not a priced leg: P(BTTS) per fixture,
      include when fair odds ≤ 1.80, sort ascending (shortest first). Needs a
      UI surface of its own; it does not fit the leg table.
- DoD: each type prices on a real fixture or refuses cleanly; away-side legs
      verified against home-side; pytest green; build clean.

### 2c. Fred Bundles — DONE (Aug 2026)
Betfred's pre-made win accas as a coupon type (`FREDB`). A third sheet shape:
several complete bets, each a group of straight-win selections under one
printed price.
- [x] `bundles` flag on `CouponType`; `Leg.bundle` / `Leg.bundle_odds` with
      guarded DDL in `ensure_schema`.
- [x] OCR reads the grouping (`OcrHint.bundles`, a bundle branch in the prompt);
      the bundle name and price are repeated on every row of the group, so one
      fumbled row does not cost the bundle its price.
- [x] `coupon_types.bundles_on` prices each acca off `combination_value`; any
      unpriced leg ⇒ no value at all.
- [x] `audit_bundles` replaces the grid solver — the sheet is not a rectangle
      and a fixture may appear in several bundles.
- [x] `GET /coupons/types/FREDB/bundles`, `best_bundle` on the type and each
      issue, and a bundle card view in the tracker.
- **Still to verify manually:** nobody has run a REAL Bundles photo through
  `ClaudeOCRProvider`. Tests cover the shape with a stub. Two things to check
  against a real sheet: that the bundle headings come back as printed, and
  whether the sheet's header needs a `title_conflicts` veto (none is configured
  — a guessed one would block real uploads).

### 2d. Prices one row out — straighten the photo, read every price twice — DONE (Sept 2026)
The tracker priced line 55 with line 54's prices. Every earlier OCR fix was
about interpretation (which block, which side) and this was not: it is the
association between the fixture column and the price columns slipping a row,
either in the model over 78 dense rows or in the photo itself (a tilt of 0.7°
drifts the far edge by most of a row at 2576 px). Nothing downstream could see
it — the rectangle was intact and every price was a real price.
- [x] `images.measure_skew` / `straighten`: rotation measured off the text
      lines (projection-profile variance, pure Pillow) and removed in
      `prepare_for_ocr`. Rotation only; perspective is not corrected.
- [x] `providers/verified_ocr.py::VerifiedOCRProvider`: wraps the real
      provider; a priced grid is read whole, then again in five overlapping
      strips (`images.strips`), and each price slot is kept only where two
      separate reads agree. Disputed cells are unpriced and counted in the
      scan audit; a failed strip read fails the upload. Not a dial.
- [x] `OcrHint.strip` / `.headings`, `ExtractedCoupon.disputed_cells` /
      `.verified` / `.reads`, `GridAudit.disputed`.
- [x] 29 new tests, including the bug itself: a page read slipped from row 10
      is outvoted by two strips and every row prices correctly. 601 pass.
- **Still to verify live** — nobody has run a real sheet through it here (no
  key, no photo in the sandbox): that the strip reads come back keyed by the
  supplied headings, that a real sheet reconciles with few or no disputed
  cells, and the wall-clock cost of six reads per upload.
- Known gap, left alone: `rescan_coupon` slots edited rows back by RANK. If a
  re-read returns a different row count (a strip-recovered row, say) the ranks
  past that point shift and an edit can land beside the wrong fixture. Fixing
  it properly needs the leg to remember the OCR name it was read under.

### 3. Calibration backtest  *(was Phase V4 — depends on 1)*
- [ ] `api/tools/backtest_calibration.py`: over a season+ of historical fixtures
      with settlements, fit grids, compute derived P(win ∧ over X), score
      against outcomes (reliability curve + Brier, bucketed).
- [ ] Replace the provisional 8% threshold and the displayed uncertainty band
      with the MEASURED error band.
- DoD: backtest report committed; thresholds wired to config; script re-runnable.

### 4. Cleanup window, staleness, deploy  *(was Phase V6)*
- [ ] Coupon cleanup 30→8 days (`Settings.coupon_cleanup_days`, default 8);
      test proves rows AND image files are deleted.
- [ ] Staleness banner: prices older than 2× the refresh interval show a
      last-updated banner.
- [ ] Full verification: pytest green, `npm run build` clean, responsive
      390/768px, `docker compose up --build -d` + live smoke test.

### 5. PWA polish & offline  *(was Phase R6 — the only unfinished v3 phase)*
- [ ] Live status `● LIVE · HH:MM`; stale/offline indicator when the service
      worker serves cache.
- [ ] Animations (accordion, sheet) ~150–200 ms ease; chevron rotation.
- [ ] Lighthouse PWA pass.

### 6. bettingiscool provider — DONE
Landed July 2026 and is now the live coupon odds source (`COUPON_ODDS_SOURCE`
defaults to `bettingiscool`); it feeds the score grid in item 1.
- [x] `providers/bettingiscool.py` + the `main.py` branch. No caller changes —
      the seam absorbed it as designed.
- **Known risk (accepted):** one-person vendor. The seam is the mitigation.

### 7. If Cards/Corners is ever re-enabled, it needs rebuilding
Not scheduled — the tool is gated off (`ENABLE_CARDS=False`) and nothing
depends on it. Logged so the breakage isn't rediscovered by surprise.

`cards/client.py` requests `alternate_totals` from TheOddsApi's **bulk**
endpoint. That endpoint serves featured markets only and rejects the request
with `INVALID_MARKET`, so the tool has never returned a row — and
`alternate_totals` is *goal* totals anyway, not cards or corners. Real corners
and cards markets exist (`alternate_totals_corners`, `alternate_totals_cards`)
but only on the **per-event** endpoint, at `events × markets × regions` credits
per sweep, and Paddy Power did not price them on any fixture sampled.

Full evidence, including the caveats on that sampling, is in
`docs/provider-verification.md` — read it before spending credits re-deriving
this.

### 8. Bring back the P&L battle skin
Removed in August 2026 (Callum's call) to keep attention on finishing the
coupon tracker — a second copy of the P&L page meant every entry-flow change
had to be made twice, and it was made twice for the toolbar fix and again for
the Enter removal in the same week. Deliberately deferred, not abandoned: this
is a rebuild-when-the-tracker-is-done item, not a scheduled one.

What was removed: `frontend/src/pages/PnlBattlePage.jsx` and its CSS module
(both in git history — restore from the commit that deletes them rather than
rewriting from scratch), the lazy import and three routes in `App.jsx`, the two
`PageIdentity` entries, and the `battle` chip on the plain page.

What deliberately STAYED, so bringing it back is a smaller job than removing it
was:
- `/pnl/battle`, `/pnl/dogs/battle` and `/pnl/virtuals/battle` still resolve —
  now as redirects to the plain ledger. The skin was installable to a phone's
  home screen, so an already-installed shortcut must not land on a 404.
- `public/icons/pnl-{dogs,virtuals}-battle.svg` and the matching
  `public/manifests/*.webmanifest` are untouched, for the same reason.

**When it comes back, do not re-fork the page.** The two copies drifted apart
every time the entry flow changed. The entry pad — stake box, result keys, odds
wheels, the four-figure guard, the saved flash — should be ONE component that
takes its styles from a CSS module passed in (or from tokens the skin
redefines), with the skin supplying only the chrome and the palette. The
skin's canvas chart is genuinely its own thing and can stay separate.

**Definition of done:** `/pnl/dogs/battle` renders the skin again; entering a
bet on either skin produces an identical payload (same test, run twice with
different wrappers); and there is exactly one implementation of the entry pad
in the tree.

---

## History

Chronological. Original phase IDs preserved.

### v2 — Rework (complete)
- **A — Documentation truth pass.** SPEC.md and CLAUDE.md rewritten around what
  actually exists (three tools, unified frontend, `TheOddsApiClient`). PS3838
  removed as a backend. `CACHE_TTL` moved into `Settings.odds_cache_ttl`.
- **B — Auth.** `users` table, bcrypt, `SessionMiddleware`, startup seeding from
  `AUTH_USERS`. Login/logout/me endpoints.
- **C — Mobile-friendly UI.**
- **D — Loose ends.**

### v3 — PWA redesign (complete except item 5 above)
- **R0 — Foundations.** Paper tokens replaced the dark theme; manifest + icon;
  mobile app shell.
- **R1 — Coupon model rework.** `Coupon` (accumulator) + `Leg`; dropped
  Set/Version/template-Coupon/Match. 59/59 green.
- **R2 — Tennis & C/C feeds.**
- **R3–R5 — Frontend.** Bottom tab bar, paper theme, all four pages rewritten,
  service worker. Old components deleted.
- **Reassessment (post-R5).** SPEC rewritten around coupons+legs; added
  `POST /coupons/{id}/refresh`; test_feeds.py. 70/70 green.
- **R7 — Alert dedupe & match transparency.** Repo under git. Discord alerts
  deduped via `legs.notified_tier` (notify only on new/changed tier) — without
  this, live coupons re-alert every 10 minutes. `OddsResult.matched_fixture`
  surfaced so a wrong fuzzy match is visible; borderline matches
  (0.6 ≤ score < 0.75) logged. 82/82 green. *(Done before R6 — correctness
  prioritised over polish.)*
- **R8 — AccaBonus pivot** (June 2026). Tennis tracker → all bet365 Acca
  Boost-eligible competitions. **Do not redo this research.** Eligibility
  verified against bet365's published terms: tennis all ATP/WTA/Challenger, MMA
  any event; restricted to NBA/Euroleague/EuroBasket/FIBA/Olympics,
  NFL only, MLB/NPB/KBO, NHL/Worlds/Olympics. **Cricket and rugby are NOT
  eligible** (the pre-pivot filter wrongly included them). **Soccer IS eligible
  but is deliberately excluded** — it's covered by the coupon tracker and
  cards/corners. Pre-season/exhibition/All-Star excluded. Encoded in
  `accabonus/arbs.py::select_accabonus_keys`. Re-verify only if bet365 changes
  the offer. Legacy `/tennis/*` paths kept as deliberate aliases for stale
  cached PWA shells. 96/96 green.
- **R9 — `/state` snapshot.** Whole-app JSON for machine consumers, guarded by a
  static `STATE_API_KEY` (constant-time compare, blank = 503). Reads through the
  odds cache, so polling costs zero credits. Keep it read-only.
- **R10 — Power-method de-vig.** Replaced multiplicative de-vig in
  `odds/utils.py`. Applied to AccaBonus + cards. 97/97 green.
  ⚠️ **This phase's note recorded that the coupon tracker did not de-vig and
  called the resulting value "slightly conservative". That was backwards** — no
  de-vig means fair odds are too low, so value was over*stated* and Discord
  fired on non-value legs. The error stood until July 2026 (see v5 below).
  Lesson: state the *direction* of a known bias, not just its existence.

### v4 — Coupon realignment
Superseded in part. V1/V2's bettingiscool dependency is now open item 6; V3–V6
are open items 1–4. The realignment's pricing decisions survive in **Decisions**
above.

### v5 — Coupon-only focus (July 2026)
- Config flags `enable_accabonus` / `enable_cards` (default False) gate the
  routers in `main.py`; `BottomNav.jsx` tabs hidden, `App.jsx` routes redirect.
  `tests/conftest.py` sets both flags so the kept tests still run.
- Coupon pricing moved to `TheOddsApiProvider` behind the `OddsProvider` seam,
  with its own `COUPON_ODDS_CACHE_TTL` independent of the AccaBonus/Cards cache.
  One full `/odds` response per sport is reused across every leg and the 10-min
  refresh loop. 98/98 green.
- **In-house de-vig for coupons** — closed the R10 gap above. 1X2 de-vigs across
  the full outcome set, totals across both sides; incomplete markets return
  None. Fair odds rose ~1.7% on a typical market, which is exactly the phantom
  value that had been inflating every leg. Win-and-Over still multiplies two
  de-vigged prices (conservative bias — misses value rather than inventing it)
  until open item 1 lands. 102/102 green.
- Secret scanning: gitleaks pre-commit hook (`.githooks/`, `.gitleaks.toml`).
  The default ruleset did **not** catch this repo's real leak — a 32-char
  lowercase hex API key, too few distinct symbols to clear the entropy
  threshold — so the custom rules key off the adjacent variable name instead.
