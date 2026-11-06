# Coupon pricing (from the original web-app spec)

The coupon tracker's domain, OCR handling and pricing maths, kept from the web app's original
build specification because the code cites these sections by number (`§3`, `§7`, `§8`). The rest
of that specification described a stack that no longer exists (`docs/retired.md`).
`docs/decisions.md` wins over this file.

## 3. Domain concepts (coupon tracker)

- **Coupon type**: the app's top-level entity. There are exactly **seven**
  (`app/coupon_types.py`), each a standing market with **its own filtered list
  of fixtures** — NOT an accumulator with legs inside it. The tracker's
  dropdown selects one; each shows only its own fixtures.
- **Selection**: one fixture on a coupon type — match name, pick text, your
  odds, fair odds, value. Ranked best-value first, except on screens (below).
  Stored in the `legs` table for historical reasons; the name is a leftover
  from the accumulator model and does NOT mean a leg of a parlay.
- **Upload**: a photo of one coupon type, which is how selections and *your*
  prices get into that type's list. An upload is a delivery mechanism, not an
  entity — the type is what the UI is organised around, and one type's list
  spans every upload made against it. Status: **processing** (OCR running) →
  **confirmed** (priced). No manual-review gate — see §7.
- **Screen**: a coupon type with a `max_fair_odds` filter. Only GG Bonus is one
  (≤ 1.80, §8.5). Screens rank **shortest price first** — likeliest, which is
  the point — where every other type ranks by value. Unpriced rows survive the
  filter either way: an unpriced row is something to look at, not to hide.
- **Ladder coupon**: GG Bonus and GG No Draw print **no per-selection odds**.
  Getting N selections correct pays a fixed return read off a printed ladder.
  Observed on the Betfred midweek issue (20–23 Jul 2026): 3→9/2, 4→9/1, 5→16/1,
  6→28/1, 7→50/1, 8→100/1, 9→175/1, 10→300/1, 11→500/1, 12→800/1, 13→1500/1
  (14 and 15 existed but were cut off by the photo edge). Consequences the code
  depends on:
  - `legs.odds` is NULL on these rows and per-row EV is meaningless. Do not
    reintroduce a per-row price or a NOT NULL constraint.
  - **The ladder is DATA, not config.** It is printed on each issue and can
    change, so it is read off the photo and stored on the coupon
    (`coupons.payout_ladder`). `CouponType.default_ladder` is only a fallback
    for an unreadable scan, and using it logs a warning.
  - Value belongs to the COMBINATION: a rung's fixed return against the product
    of that many fair odds (`coupon_types.combination_value`). The best N-fold
    is always the N SHORTEST prices, since that minimises the product.
  - **Every rung is scored, never just the 3-fold** (`best_combination`).
    Which rung wins depends on the spread of prices and is not predictable from
    either direction — on one real board the 8-fold beat the 3-fold; on another
    the 3-fold won.
  - Break-even on an even N-way split is `payout^(1/N)`. Recorded as arithmetic
    only: the screen is a **shortlist of candidates**, not a set meant to be
    combined with itself. A selection near the threshold can be a filler
    alongside shorter ones, so do NOT treat 1.80 as mispecified or propose
    tuning it. Betting parameters are Callum's call.
- **Coupon periods**: the five priced types run a fixed weekly schedule
  (Mon–Wed, Wed–Fri, Sat, Sun). The two GG types are issued **Midweek** and
  **Weekend** on dates that MOVE — the observed midweek issue ran Mon–Thu,
  which is not any fixed window. Their real range is taken from the kickoffs
  OCR reads off the coupon (`coupon_periods.range_from_kickoffs`), never
  derived from the calendar.

### What OCR is and is not asked for (§7)
OCR returns a whole coupon — fixtures, kickoffs, the printed payout ladder and
date range — NOT a market per row. The market is known from WHICH coupon was
photographed, so it is filled in server-side from the coupon type. Asking the
model to infer it from the page header was how a GG coupon came back with zero
rows: the prompt demanded a `market` and `your_odds` the page never printed.

Coupons print a weekday and time ("MON 17:00"), never a date; kickoffs resolve
forward from the upload date.

### Coupon → market mapping (§8.2)

The seven coupon types the syndicate plays (confirmed July 2026). `type_code`
is a label only — the pricing path is chosen from each leg's **pick text**.

| Code | Coupon | Market | Path |
|---|---|---|---|
| `TTS1` | Team to Score 1 | picked team's total **over 0.5** | DIRECT |
| `TTS2` | Team to Score 2 | picked team's total **over 1.5** | DIRECT |
| `TTS3` | Team to Score 3 | picked team's total **over 2.5** | DIRECT |
| `WO15` | Win and Over 1.5 | picked team wins ∧ match total > 1.5 | DERIVED (§8) |
| `WO25` | Win and Over 2.5 | picked team wins ∧ match total > 2.5 | DERIVED (§8) |
| `GGB` | GG Bonus | both teams score — **a fixture screen, not a priced leg** (§8.5) | DERIVED (§8) |
| `GGND` | GG No Draw | both teams score ∧ result is not a draw | DERIVED (§8) |
| `FREDB` | Fred Bundles | picked team **wins** (straight moneyline) | DIRECT |

**"Team to Score N" means N or more**, so the line is N−0.5 — `TTS2` is team
total over **1.5**, not 2.5. Getting this off by one misprices every TTS leg,
so it is stated explicitly rather than left to inference.

**Every one of these is team-specific**, including the GG pair via the fixture
they attach to. Resolving WHICH team the pick names is therefore a shared
prerequisite for all seven, not a per-market detail — see §8.6.

TTS uses the `home_totals` / `away_totals` markets (OddsPapi's team totals), so it is
direct rather than derived. GG has no vendor market at all (the API serves
`moneyline | totals | home_totals | away_totals | spread`), so both GG types
come off the grid.

### Fred Bundles — a sheet of bets, not a list of fixtures

Every other type is ONE market over a fixture list, and the tracker's job is to
pick the good rows out of it. Bundles inverts that: Betfred has already built
the accumulators, so the sheet carries several complete bets, each a small group
of straight-win selections with ONE printed price for the whole group.

Three consequences, all of which the code has to respect:

- **Value belongs to the GROUP.** The sheet prices the bundle, never the
  selection, so a bundle leg carries no `odds` of its own and a per-row edge is
  meaningless. The figure is the printed acca price against the product of its
  legs' fair odds — `combination_value`, the same arithmetic as a ladder rung,
  applied to a group the COUPON chose rather than to the N shortest prices on
  the sheet. There is nothing to select, so every bundle is listed whether or
  not it holds value.
- **A bundle with any unpriced leg has NO value**, not a value computed off the
  legs that priced. An acca is only as knowable as its worst leg; quietly
  valuing a four-fold as a three-fold overstates it every time.
- **The sheet is not a rectangle**, so the grid rules (`coupon_grid`) must not
  run on it — see §7.3. The same fixture legitimately appears in more than one
  bundle.

Selections are straight wins, priced off de-vigged 1X2 alone: no team totals,
no score grid.

### 7.3 A bundle sheet is not a rectangle

The grid solver enforces one line per match, because on a grid a repeated
fixture is the read losing its place rather than the coupon printing a match
twice. On a bundle sheet that rule is wrong: a fixture appearing in two bundles
is the coupon doing its job, and deduplicating would silently shorten every
bundle after the first. There are also no market blocks to key by heading.

So `upload.audit_bundles` replaces `solve_grid` for these coupons, and measures
what a bundle sheet actually has: how many bundles were read, how many
selections they hold, how many selections arrived with no bundle heading (the
read losing a group), and how many bundles came back with no readable price —
the one number a bundle cannot be valued without.

## 7. In-house devig + OCR handling (coupons)

### 7.1 Devig (in-house)
Fair probabilities are computed IN-HOUSE from OddsPapi's raw prices using
the power (logarithmic) devig applied per market (`odds/utils.py::devigd_multi`,
shared with Paddy). Vendor-supplied de-vigged fields are cross-check only. This is the
single pricing path for direct markets; the derived model (§8) consumes the same
devigged probabilities.

### 7.2 OCR handling (auto-confirm)
Uploads happen from a phone — there is NO blocking review screen. Instead:
- OCR rows persist immediately and pricing runs (status: processing →
  confirmed on save).
- REMOVED July 2026 (Callum's call — rows are checked by hand anyway): the
  per-row sanity checks, the `legs.flags` column and the ⚠ badge. There is no
  automated anomaly flagging; the value column is what a bad read shows up in.
- Every row is editable inline (tap-to-edit: match, pick, your odds); edits
  reprice immediately and set `legs.edited = true`.
- **Re-scan button**: each coupon can manually re-trigger OCR on the stored
  image. Re-scan replaces UNEDITED rows with the fresh extraction; rows the user
  has edited are preserved (never overwrite a human correction with a machine
  re-read).
- Fixtures that fail name-matching persist as "unmatched" rows with fair odds
  blank (never block the coupon); resolving them is an inline edit.
- **The photo is straightened first** (`images.measure_skew`, Sept 2026): a
  tilt of under a degree drifts the far columns of a 78-row grid by most of a
  row, so the print at the right of row 55 lines up with the name on row 54.
  Rotation is measured off the text lines and removed before the read; the
  stored image is the level one.
- **Every price on a priced grid is read twice** (`providers/verified_ocr.py`,
  Sept 2026). After the whole-page read, the page is read again as five
  overlapping horizontal strips, and each price slot is kept only where two
  separate reads report the same value. A price seen once, or reads that
  disagree, is left unpriced and counted as `disputed` in the coupon's
  `scan_audit` (which fails `clean` and says so in its summary line). A strip
  read that fails fails the upload rather than saving the single read. Ladder
  coupons and bundle sheets are read once — nothing on them is a grid cell.
- KNOWN TRADE-OFF (accepted): a misread that BOTH reads make identically is
  not detected; the value column is the practical tripwire and rows are
  reviewed by hand. What the double read catches is the class that matters —
  a whole column slipped a row — because a slip has to recur across separate
  reads of separate pieces of the page to survive the vote.

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
- Fit a score-grid model per fixture from the devigged prices:
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
- Using historical fixtures with settlements (the results source is still to
  choose: `PLAN.md`, item 3):
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

### 8.5 GG derivations (both-teams-to-score)
Both GG types are read off the same
fitted grid as Win-and-Over. Let `g[h][a]` be the fitted probability of the
scoreline h–a.

- **P(BTTS)** = Σ `g[h][a]` for h ≥ 1, a ≥ 1.
- **P(GG No Draw)** = Σ `g[h][a]` for h ≥ 1, a ≥ 1, **h ≠ a**.
  Fair odds = 1/p. This is BTTS minus the score draws (1-1, 2-2, …) — NEVER
  BTTS × (1 − P(draw)). Both-teams-scoring and the result are correlated
  through the same grid, exactly as with Win-and-Over, so the product is wrong
  for the same reason.
- Both go UNPRICED when the grid cannot be fitted or its RMSE exceeds the
  refusal threshold. An unpriced leg beats a confidently wrong one.

**GG Bonus is not a priced leg — it is a fixture screen.** Rather than valuing
a selection the syndicate already made, it surfaces candidates:

- Compute P(BTTS) per fixture, fair odds = 1/p.
- **Include the fixture when fair odds ≤ 1.80** (i.e. P(BTTS) ≥ 0.5556).
- **Sort ascending by odds — shortest first**, since lower odds mean more
  likely both teams score, which is what the screen is looking for.

Note these are OUR derived fair odds, not a book price: there is no vendor BTTS
market to compare against, so the 1.80 cut is a threshold on the model's own
estimate. It therefore carries the §8.3 derived-market uncertainty and will
move if the §8.4 backtest recalibrates the grid. Treat 1.80 as a selection
filter, not a value claim — nothing here asserts an edge over a bookmaker.

### 8.6 Resolving the picked team (prerequisite for all seven)
**Done Aug 2026** (`app/picks.py`: the side comes from the pick's team
fragment; unresolved ⇒ unpriced). What it fixed: every coupon type above is
team-specific, but the pick matcher then read only `win` / `over N` /
`under N` and resolved no team at all. Consequences, fixed together:

- Moneyline takes outcome index 1 (home) unconditionally, and
  `p_win_and_over(..., home=True)` is hardcoded — so an **away**-team
  Win-and-Over leg is priced as though it were the home side. `pricing/grid.py`
  supports the away case and is tested for it; the provider never passes it.
- TTS cannot choose between `home_totals` and `away_totals` without it.

Resolve the picked team by matching the pick text against the fixture's
`runner_home` / `runner_away` (the same normalise + fuzzy path used for fixture
matching), and treat an unresolved team as UNPRICED with a leg flag rather than
defaulting to home.
