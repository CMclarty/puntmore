# frontend/ — rules for the React app

`docs/decisions.md` is read first and wins over this file; the repo's
`CLAUDE.md` applies everywhere and wins on any other conflict.

## Two sites, one app
The main domain shows every public tool, `me` shows Callum's personal tools
(`src/site.js`, mirroring `render/api/app/site.py`: `ME_TOOLS` is the list).
`me` exists only when the API's `ME_HOST` names it: the app reads it at boot
(`GET /api/auth/site`, `setMeHost`) before it renders; blank, there is one site
and it shows every tool. Each site shows only its own tools; a link to the
other site's tool goes there, same path. On the main domain an account without the coupon tracker lands on
its first granted tool. A tool's home-screen identity (title, icon, manifest)
is in `src/identity.js`, mirrored by `render/api/app/identity.py`.

## UI rules — these are TOOLS, build only what is asked
Callum has had to strip unrequested additions out repeatedly (Oct 2026). A
screen is for finding a bet and placing it in seconds.
- **Add NOTHING to a page that was not asked for.** No clocks or "updated at"
  times, no page titles or subtitles, no explanatory notes or captions, no
  section headings ("Other legs, best fit first"), no helper text, tooltips,
  badges, legends, summary stats or extra colours. If you think one would
  help, ASK — never ship it and mention it afterwards.
- **No new pages, panels or calculators unless asked.** A pasted spec or
  worked example is the maths to implement, not a request for a page that
  shows it (the acca calculator was built that way and removed).
- **Labels only where a control or column would be unreadable without one**
  — a column header, a button's own text. Never a sentence describing what
  the screen does.
- **Monochrome by default.** Colour only for meaning that was asked for.
- **Don't recommend; list.** Rank the options and let the user pick — no
  "best acca", "suggested" or "pick of the day" blocks unless asked.
- **Every live-price screen refreshes itself** (`hooks/useAutoRefresh.js`):
  every 2 min while on screen, and at once when the app is reopened after
  30 s away — silently, in place. Paddy, the coupon tracker and the 2-Up
  tab use it; a new tool showing prices must too. A failed refresh must
  never leave old prices looking current. The feeds read the server's
  caches, so refreshing costs no vendor quota.
- When asked to change one thing, change that thing. Don't tidy, restyle or
  "improve" the rest of the page alongside it.

## The 2-Up tab (`pages/TwoUpPage.jsx`, 6 Nov 2026)
The Feed alone: every value side on the server's scheduled board
(`/api/2up/feed?value_only=true`), best edge down, price and edge as % of
fair, with the board's **updated** time beside the refresh button and each
row's **priced** time. A line in the error style says when the board is stale
(an older board kept while OddsPapi is not answering), stopped by the kill
switch (`TWOUP_KILL_SWITCH`, global, `.env`) or not yet refreshed since boot.
An older API mid-deploy may send no `generated_at`, `priced_at` or `status`
keys: the page shows "—" and never throws. Nothing on it places a bet, keeps
an account or configures the feed: the dials are `.env` on Render. Helpers in
`twoup.js`, pinned by `twoup.test.js`.

## The acca maths (client-side, pinned by vitest)
- **Acca insurance** (`frontend/src/accaInsurance.js`, Oct 2026): the Acca Min
  tab value a 5-fold (every leg 1.20+) with the stake
  refunded as a bonus (`refund_value` 0.75, `refund_cap`) if exactly one leg
  loses. Client-side maths, pinned by `accaInsurance.test.js` to the offer's
  own worked cases — keep them passing. The builder takes ONE leg per match:
  the product rule assumes independent legs.
- **Acca views rank by Kelly growth** (Oct 2026): every offer's view scores a
  leg by the expected log-growth of an acca with it at that acca's OWN
  optimal stake (`accaInsurance.js::growthOf`) — bankroll-free, no risk dial;
  short legs that keep an acca paying back rise without an odds cap. The
  page is THREE TABS — Value (singles at fair or better), Refund (the
  insurance offer), Boost — each ONE list, best down (Callum, Oct 2026:
  "this is a tool — keep it clean"; open, tap one of three, bet in seconds).
  Filters (market, kick-off, max odds, min value, sort) are FOLDED
  under one line naming what is applied — never an always-open panel.
  Refund and Boost show a SCORE column: the leg's Kelly growth against a
  FIXED yardstick — 100 = a fair 1.25 leg (`refOdds`, the neutral partner it
  is scored beside), so a number means the same thing every day. Tiers (Callum):
  100+ GREEN = smash it, 80-99 ORANGE = filler, below 80 GREY = leave it. It was relative (best on the list = 100) until Oct 2026, which said
  nothing about whether a leg was any good.
  Above the list, ONE line: the acca of the top 5 by score (one per match)
  — its odds and absolute EV, so "is it worth betting" reads at a glance.
  Boost adds a stake: 0.10 Kelly on a £50k bankroll (`BOOST_KELLY`,
  `BOOST_BANKROLL` in CardsPage.jsx).
  Both have a floating "1 per match" button bottom-right (keeps each match's
  top-scoring leg — an acca takes one leg per game). NO clock / "updated"
  time or other decoration in the header: Callum has asked twice.
  Refund = the insurance offer; **Acca Boost** (`accaBoost.js`) = winnings +10/20/30%
  on 4/5/6 legs, win-or-lose, each leg scored in its best fold. Both are a
  plain list of qualifying legs, best down — NO recommended acca on the page
  (Callum: "this is a tool"); `bestAcca`/`bestBoostAcca` stay, tested. There is
  NO calculator page (removed Oct 2026, never asked for); the offer's terms
  are `DEFAULTS` in code. Tested by vitest.

## Commands
```
npm run dev          # dev server
npm run lint && npm test && npm run build
```
