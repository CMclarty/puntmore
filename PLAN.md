# Puntmore — Plan

What is next, in order. The decisions are in `docs/decisions.md` (read it first; it wins over
this file). Work the open items in order; each ends with its Definition of Done.

## Open work

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
- DoD: pytest incl. hold-one-out; derived legs price and display their band;
  `npm run build` clean.

### 3. Calibration backtest  *(was Phase V4 — depends on 1)*
- [ ] `render/api/tools/backtest_calibration.py`: over a season+ of historical fixtures
      with settlements, fit grids, compute derived P(win ∧ over X), score
      against outcomes (reliability curve + Brier, bucketed).
- [ ] Replace the provisional 8% threshold and the displayed uncertainty band
      with the MEASURED error band.
- DoD: backtest report committed; thresholds wired to config; script re-runnable.

### 4. Cleanup window, staleness, deploy  *(was Phase V6)*
- [x] Coupon cleanup 30→8 days (`Settings.coupon_cleanup_days`, default 8,
      `COUPON_CLEANUP_DAYS`; 6 Nov 2026); `tests/test_cleanup.py` proves rows
      AND image files are deleted, a shared sheet photo with its last sibling.
- [x] Staleness banner (6 Nov 2026): prices older than 2× the server's
      10-minute refresh show one line, "Prices from HH:MM · not refreshed
      since", above the legs (`staleness.js`, pinned by vitest); nothing
      while they are fresh.
- [ ] Full verification: pytest green, `npm run build` clean, responsive
      390/768px, a Render deploy (merge to `main`) gated on `/api/ready` +
      live smoke test.

### 5. PWA polish & offline  *(was Phase R6 — the only unfinished v3 phase)*
- [ ] Live status `● LIVE · HH:MM`; stale/offline indicator when the service
      worker serves cache.
- [ ] Animations (accordion, sheet) ~150–200 ms ease; chevron rotation.
- [ ] Lighthouse PWA pass.

### 8. Bring back the P&L battle skin
Removed in August 2026 (Callum's call) to keep attention on finishing the
coupon tracker — a second copy of the P&L page meant every entry-flow change
had to be made twice, and it was made twice for the toolbar fix and again for
the Enter removal in the same week. Deliberately deferred, not abandoned: this
is a rebuild-when-the-tracker-is-done item, not a scheduled one.

What was removed: `render/frontend/src/pages/PnlBattlePage.jsx` and its CSS module
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

## Done

- **2.** OCR auto-confirm, inline edit and re-scan (V5). A real phone photo through `ClaudeOCRProvider` is still to be checked by hand.
- **2a.** The seven coupon types price on OddsPapi's grid (picked team resolved, TTS, GG No Draw, the GG Bonus screen).
- **2b.** Coupon processing rebuilt around whole coupons, printed ladders and variable periods.
- **2c.** Fred Bundles (Aug 2026). A real Bundles photo is still to be checked by hand.
- **2d.** Prices one row out: the photo is straightened and every price read twice (Sept 2026). A real sheet is still to be checked live.
- **2e.** Under-21, women's and reserve sides are never priced as the seniors (Sept 2026).
- **2f.** U19 fixtures: competition and day checks before a fixture is priced (Sept 2026).
- **11.** One odds source (OddsPapi), one 365 tool with 2-Up as a one-way feed inside it, no `/scraper` page (5–6 Nov 2026). Its grep for the old vendors still matches a legacy alias in `app/main.py` (`COUPON_ODDS_SOURCE=bettingiscool` routes to OddsPapi), test names and `tools/probe_oddspapi.py`; none is a live dependency.
- **12.** Finder (6 Nov 2026, `docs/finder-spec.md`).

The Bet Placer's open work (the phone interface, two weeks of dry-run) is in `docs/placer-spec.md`.

## Parked (after the open work is done)

### Goalscorers on OddsPapi
Parked 5 Nov 2026. The first/anytime-goalscorer finder (`render/api/app/goalscorers/`,
page `/365/scorers`, `goalscorers` grant) read a vendor that is now removed;
OddsPapi is the only odds source. Code and tests stay (the tests use a stub
client). To revive, and only via OddsPapi: confirm its catalogue carries a
goalscorer market (`/api/cards/oddspapi-check`), write an OddsPapi
watchlist/event source with the duck-typed `fetch_event` shape the service
expects, flip `ENABLE_GOALSCORERS`, and it is the 365 tool's scorers tab again.
