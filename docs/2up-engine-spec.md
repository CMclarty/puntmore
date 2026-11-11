# 2-Up — the module spec

Written by Callum on 3 Nov 2026 and narrowed to a feed on 6 Nov 2026. `docs/decisions.md` wins
over this file; it changes in the same pull request as the code. Items marked *call* are the
author's defaults, not Callum's words.

## What it is

The 2-Up module sits inside the 365 tool on Render (`render/api/app/twoup/`). It prices every
side of every match-result market in bet365's early-payout competitions against Pinnacle's fair
line plus the 2-up rescue, and serves the rows whose price beats fair. That feed is the product,
and it does nothing else: sizing, placing and the books are the Bet Placer's
(`docs/placer-spec.md`).

- **Two readers of the one feed.** The Bet Placer reads it over the API (Callum's, and a third
  party's, which runs on 2-Up only). The 2-Up tab on the 365 page shows the same rows so a person
  can place by hand when the Bet Placer is not running; it gets no row, column or filter the API
  does not serve.
- **No account, no ledger.** Render keeps no record of what anyone placed or how it settled, and
  nothing comes back to this module. The feed is one-way.
- **365 is one permission.** A Bet Placer's machine key (`app/apikeys.py`) carries the
  `accabonus` grant, the same grant as the page; there is no feed-only grant.

## The feed

- **A row is one side of one fixture**: `event_id`, `event`, `home`, `away`, **`kickoff`** (UTC),
  `competition`, `selection`, `side` (`home` | `away`), `market` (`Match Result`), **`price`**
  (bet365's decimal odds for that selection at `priced_at`), Pinnacle-fair `p_win` and
  `fair_price`, `rescue`, `edge` (`p_win + rescue − 1/price`), `ev`, and `priced_at` (when the
  prices behind the row were fetched). *Call:* a row's identity is `event_id` + `side`, stable
  across refreshes, so a reader can tell a re-price from a new row without any state on Render.
- **A row is in the feed while** the fixture is in a configured competition and inside the
  horizon (`twoup_horizon_hours`, 24 h), both books price all three outcomes, the price is
  inside the band (`twoup_min_price` .. `twoup_max_price`) and the edge is at or above the floor
  (`twoup_min_edge`). It leaves at `twoup_min_lead_minutes` before kick-off, or as soon as a
  refresh no longer meets one of those. *Call:* the full priced board stays available with a
  per-row `value` flag (`value_only=false`) so the page can show why a fixture is not value; the
  Bet Placer reads `value_only=true`.
- **Pre-match only.** No in-play rows and no in-play prices.
- **Refresh.** The board is rebuilt on a schedule (`twoup_refresh`, every `twoup_feed_ttl`, 5 min
  at the defaults) from the shared `OddsPapiClient` cache, never on a page request. The response
  carries `generated_at`, every row carries `priced_at`, and `status` says what happened:
  - `stale: true` when the last pass failed and the last good board is being served, with its
    old `priced_at` and `served_at`; a transient fetch error never empties the feed;
  - `degraded: true` when the board is partly priced, or when the rescue table is unreadable,
    with `cannot_price` naming each missing book, held chunk or quota, and the unreadable table;
  - `not_yet_refreshed: true` before the first pass after a deploy;
  - `killed: true` when the global `TWOUP_KILL_SWITCH` is on, which empties the feed.
- **Transport is HTTP pull.** `GET /api/2up/feed` is the whole API. *Call:* no push, no per-reader
  view: every reader gets the same rows.
- **Diagnostics**: `/api/2up/diagnostics` and the `twoup_detail` block of `/api/ready` report the
  last refresh, the account's request count and what could not be priced.

## The rescue model

bet365 does not price the 2-up concession into its odds: the match-result price is the same with
or without the offer, and bet365 carries it as a marketing cost managed by restricting the
qualifying leagues. So the value is a pure overlay on the standard line: edge = P(win) + P(two
up but fails to win) − 1/price. No published model conditions the rescue rate on match odds (the
unconditioned base rate is about 1.8% of matches, 5–7% of two-goal leads). The rescue table
(`render/api/app/twoup/rescue_table.json`) is bucketed by implied win probability.

**The edge is proven; do not gate work on a literature review** (5 Nov 2026, Callum). The table
is tuned by hand during testing against Bookie Bashing's 2-up tracker, with
`tools/twoup_backtest.py` over an exported CSV; with no ledger on Render, that CSV is the only
results source.

## The Bookie Bashing constraint

BB's terms (s.7) ban any automated access without written permission, and its bet-tracker API
returns 401 without a token. BB is never scraped for this module: the tracker comparison is
manual and user-triggered. Either Barbitrage Ltd gives written permission, or BB interaction
stays manual. (The Scraper's one existing BB target, `bb-saves`, is the register's single
named exception.)
