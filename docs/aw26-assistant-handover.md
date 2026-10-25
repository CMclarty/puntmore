# AW26 → the project's assistant tool — handover (25 Oct 2026)

For the agent doing the AW26 build. Everything below was verified against the
repo on 25 Oct 2026 (file:line where it matters); nothing here is a guess.
Repo: `/home/callum/bettingtools`, branch `main`. **Every push to `main` is a
production deploy** (Render, `render.yaml`, `/api/ready` gates promotion) — see
`.claude/skills/deploy/SKILL.md`. Run `api/.venv/bin/python -m pytest -q`
(752 pass at `dd2186d`) and `cd frontend && npm run lint && npm run test &&
npm run build` before any push. UI changes get looked at in a browser first
(`.claude/skills/run/SKILL.md`). CLAUDE.md UI rule: build only what is asked;
no unrequested pages, labels or decoration.

## Callum's vision (his words, 25 Oct)

> Take periodic snapshots of the bets available — the bet-builder/coupon
> tracking, Bookie Bashing, and the Paddy tool — a snapshot figure of what's on
> offer. I upload photos of slips / bits of slips from the bookie terminal; it
> records them, marries each bet up to the line it came from, matches the EV,
> regularly checks results, and gives me a daily update: what bets have I done,
> how much staked, accumulated EV, total profit. Same for free bets: they're
> picked up after (from offers), then put on other value bets, tracked the same.

## What exists today (verified)

**Server AW26** (`api/app/aw26/`, `api/app/routers/aw26.py`, `aw26_ingest.py`):
- `AwSlip` (`models.py:290`): `placed_at`, `user`, `image_path`, `receipt`
  (unique), `offer` refund|boost|other, `stake`, `potential_returns`, `status`
  open|won|lost|refunded|void, `returns`, `settled_at`, `ev` (£, fixed at bet
  time), `ev_note`, `edited`. `AwLeg` (`:334`): `event/market/selection` as
  printed, `odds`, `kickoff`, `kind` corners|cards|goals|saves|other, `line`,
  `side`, `fair_odds`, `fixture_id`, `result` pending|won|lost|void.
  `AwFair` (`:360`): BB fairs, kept 14 days (`service.py:18`).
- Flow: photo → OCR (`providers/slip_ocr.py`) or desktop ingest
  (`POST /api/aw26/slips/ingest`, `SAVES_INGEST_KEY`) → `price_slip` once every
  leg has a fair (`service.py:113`) → `settle_slip` (`:137`) from leg results
  (`POST /api/aw26/results`, FlashScore stats, `aw26/results.py`) →
  `summary()` (`:163`) → daily Discord post 08:47 London (`scheduler.py:349`,
  `service.run_daily`). Targets: £1,000 EV/day, £100k profit; refund/boost
  accas default £50 (`service.py:21-26`).
- Offer maths (`aw26/offers.py`): refund EV adds stake×0.75×P(exactly one leg
  loses), no cap; boost +10/20/30% for 4/5/6-fold; a refund settles as
  `returns = 0.75×stake`, status "refunded"; a void leg does not disqualify.
- UI `frontend/src/pages/Aw26Page.jsx`: headline (EV today/target, profit,
  EV to date, volume, open, unpriced, "Settled today"), upload, slip list with
  inline editor. API calls `frontend/src/api.js:275-296`.

**Desktop / local pieces**:
- `/slip`, `/market`, `/bb-lookup` are slash commands at
  `/home/callum/.claude/commands/{slip,market,bb-lookup}.md`. `/slip` writes one
  row per leg to `/home/callum/bet-tracker/bets.csv` — **header only, never
  used** (verified: 1 line). It does NOT post to the server.
- `local/flashscore_results.py` (`check|once|run`, 8 leagues, goals/corners/
  saves; **cards sent as null, TODO** at `:16-18,84`). **Not scheduled**: no
  crontab, no systemd user timers (verified).
- `local/bookiebashing_tracker.py` and `bookiebashing_saves.py`: `parse_page`
  **raises NotImplementedError** (`:68`, `:85`) — the BB fairs feed into
  `/api/aw26/fairs` has never worked. BB login lives only on Callum's PC.

**Paddy value rows** are computed every 2-minute sweep (`scheduler.sweep_cards`)
and **never stored**. Row shape: `cards/value.py::CardValueRow` — `event_id,
home_team, away_team, commence_time, sport_key, market_key, line, side,
paddy_odds, pinnacle_odds, fair_odds, edge, basis` (basis = ladder | hybrid |
matched | saves). Coupon `Leg.fair_odds` is overwritten each refresh and legs
are deleted after kickoff. Nothing anywhere keeps a history of value on offer.

**Three ledgers** exist: server AW26, `bets.csv` (empty), the PnL `Bet` table
(`models.py:184-237`). The `settle-bet-slips` claude.ai skill settles by
two-source web search and caps a free-bet refund at £50 — the server does
neither (0.75×stake, no cap, single source).

## Build order (do these in sequence; each is independently shippable)

### 1. Value snapshots — the foundation (M)
Everything else (marrying, attribution, "value on offer") depends on this.
- `models.py`: `ValueSnapshot` (`value_snapshots`: `id, at (UTC, index),
  source paddy|coupon|bb|market, n_lines, n_value (edge ≥ 0), best_edge,
  sum_edge_positive`) and `ValueLine` (`value_lines`: `id, snapshot_id FK,
  event_id, home, away, kickoff, market (corners|cards|goals|saves|team_corners
  |team_cards), line, side, pick, offered_odds, fair_odds, edge, basis`,
  index on `(snapshot_id)`, `(event_id, market, line, side)`). Tables are
  created by `Base.metadata.create_all` at startup (`main.py:60`) — no
  migration tooling; adding tables is enough, adding columns to existing
  tables is NOT (SQLite) — avoid.
- `aw26/snapshots.py`: `record(db, rows, source, now)`, `latest(db, source)`,
  `recent(db, hours)`, `prune(db, keep_days=30)`. A module-level throttle so
  the sweep writes at most every `AW26_SNAPSHOT_MINUTES` (settings, default 15).
- Hook into `scheduler.sweep_cards` right after `rows = price_events(...)`
  (`scheduler.py:~196`), inside its own try/except like the shadow_store block —
  **never let it break the sweep**, and **zero extra OddsPapi calls** (reuse
  `rows`). Use `SessionLocal()` as the other jobs do.
- `GET /api/aw26/snapshots?hours=24` → `{snapshots: [...], latest: {at,
  source, lines: [...]}}`. Pydantic models inline in the router (house style).
- Coupon legs with value and `/market` rows (source `market`) can post into
  the same table later; design the columns so they fit.
- Tests: record/latest/prune with the app's TestClient DB (see how
  `tests/test_aw26*.py` get a session); throttle; the sweep still returns rows
  if the DB write raises.

### 2. One ledger (S) — needs Callum's decisions first (see bottom)
- Change `/slip` (`~/.claude/commands/slip.md`) to POST to
  `/api/aw26/slips/ingest` with per-leg `fair_odds` from BB and the photo;
  retire the `stream=slip` CSV rows. `/market` rows → `POST /api/aw26/snapshots`
  (new, source `market`).
- Align `offers.py` and the `settle-bet-slips` skill on the refund rules.
- Fix the stale docs: `CLAUDE.md:63-66`, `PLAN.md:388-394, 425-429`,
  `docs/aw26-handover.md` (it plans Windows Task Scheduler; this PC is Linux).

### 3. Marry each leg to its line (M)
- `aw_legs` can't take new columns on SQLite without a migration — add a
  small `aw_leg_lines` table instead: `leg_id FK, line_id FK → value_lines,
  snapshot_at, offered_odds_at_snapshot, match_score, basis`.
- At ingest/price time: nearest snapshot **before** `placed_at`; exact match on
  fixture (names via `normalise.team_similarity`, as `aw26/legs.py:61` does),
  `kind`, `line`, `side`. Record offered-vs-taken price. Use the line's
  `fair_odds` when BB has none and say which source priced it in `ev_note`.

### 4. EV attribution (S)
- Per-leg edge; per-slip `ev_by_source`; extend `summary()` with the day's
  "value on offer" (from the latest snapshot) vs EV taken vs staked.

### 5. Free bets (M)
- `aw_free_bets`: `earned_from_slip_id, amount, earned_at, expires_at,
  used_by_slip_id, status`. A "refunded" settlement **creates a free-bet row**
  rather than cash returns. New offer `free`: stake not returned —
  EV = S·(P_win·odds − 1), P&L = winnings only. Running free-bet balance in
  `summary()` and the daily post.

### 6. Settlement cadence (S–M)
- systemd **user** timer (Linux) running `flashscore_results.py once` every
  ~2h 13:00–00:00 London; leagues taken from open legs' events; cards from the
  incident icons (the TODO). Delete `/results-check` and the unused
  `fixture_id` plan, or keep them deliberately.

### 7. Daily update (S)
- `daily_message()` gains "value on offer" (latest snapshot) and the free-bet
  balance. If a literal screenshot is wanted, render a PNG server-side;
  otherwise link `/aw26`. Confirm `DISCORD_ROUTES` has an `aw26=` route on
  Render (unverified).

## Constraints
- OddsPapi: 3,000 requests/day cap (`config.py:86-90`); snapshots must reuse
  sweep rows, never call the vendor.
- Bookie Bashing needs the login on Callum's PC; cloud sessions can't reach it.
  `parse_page` must be written from a real page dump.
- FlashScore is unauthenticated scraping with no second source.
- SQLite: one writer; new tables fine, new columns on existing tables need a
  migration path the repo doesn't have.

## Decisions Callum still owns (ask before building 2 and 5)
1. Refund rules: 0.75 × stake? a £50 cap? does a void leg still qualify?
2. Free-bet EV: keep 0.75 × stake at settlement, or realised conversion?
3. Results source: FlashScore alone, or require a second source as the
   `settle-bet-slips` skill does?
4. Any UI for snapshots/attribution (the UI rule says none until asked).
