# 2-Up — the module spec

Written by Callum on 3 Nov 2026, narrowed to a feed on 6 Nov 2026, and given two modes over
Bookie Bashing's Early Payout Tracker on 18 Nov 2026. `docs/decisions.md` wins over this file; it
changes in the same pull request as the code. Items marked *call* are the author's defaults, not
Callum's words.

## What it is

The 2-Up module sits inside the 365 tool on Render (`render/api/app/twoup/`). It takes bet365's
match-result prices in the early-payout competitions from Bookie Bashing's Early Payout Tracker,
judges each in two modes (below), and serves the rows that are value. That feed is the product,
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
  `fair_price`, `rescue`, `edge` (`p_win + rescue − 1/price`), `ev`, `priced_at` (when the
  prices behind the row were read), `sources` (the modes that flag it) and Bookie Bashing's
  `bb_rating` and `bb_fair`. *Call:* a row's identity is `event_id` + `side`, stable across
  refreshes, so a reader can tell a re-price from a new row without any state on Render; with
  bet365's price from the tracker, `event_id` is made from the tracker row
  (`bb:<home>|<away>|<yyyy-mm-dd>`), never from the OddsPapi fixture it is joined to, so a join
  that comes and goes never makes one selection two bets.
- **A row is in the feed while** it is inside the horizon (`twoup_horizon_hours`, 24 h), its
  price is inside the band (`twoup_min_price` .. `twoup_max_price`) and at least one mode flags
  it. It leaves at `twoup_min_lead_minutes` before kick-off, or as soon as a refresh no longer
  meets one of those. *Call:* the full priced board stays available with a per-row `value` flag
  (`value_only=false`) so the page can show why a fixture is not value; the Bet Placer reads
  `value_only=true`.
- **Pre-match only.** No in-play rows and no in-play prices.
- **Refresh.** Pinnacle's board is fetched on a schedule (`twoup_refresh`, every
  `twoup_feed_ttl`, 5 min at the defaults) from the shared `OddsPapiClient` cache, and the board
  is composed from it and the tracker snapshot by that job and again on each tracker post; never
  on a page request. The response carries `generated_at`, every row carries `priced_at`, and
  `status` says what happened:
  - `not_yet_refreshed: true` before the first pass after a deploy, and before the first tracker
    post (the snapshot is held in memory, so a deploy or restart loses it until the next post);
  - `degraded: true` when the board is partly priced, or when the rescue table is unreadable,
    with `cannot_price` naming each gap: a tracker snapshot older than `twoup_bb_max_age_s`
    (300 s) is an "ALL" entry and the feed then serves no rows; Pinnacle or OddsPapi failing
    while the tracker is fresh is a "pinnacle mode: ..." entry, never ALL, and the `bb` mode's
    rows stand;
  - `stale: true` when the last pass failed and the last good board is being served, with its
    old `priced_at` and `served_at`;
  - `killed: true` when the global `TWOUP_KILL_SWITCH` is on, which empties the feed.
- **Transport is HTTP pull.** `GET /api/2up/feed` is the whole API. *Call:* no push, no per-reader
  view: every reader gets the same rows.
- **Diagnostics**: `/api/2up/diagnostics` and the `twoup_detail` block of `/api/ready` report the
  last refresh, the account's request count and what could not be priced; the diagnostics also
  report the tracker snapshot held (its age, rows received, bet365 rows, matched, unmatched and
  ambiguous, dropped by reason) and the mode settings.

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

## Bookie Bashing's Early Payout Tracker, and the two modes

Callum, 18 Nov 2026: "the 2up feeds two modes, with one being a passive scrape of the bookie
bashing feed https://www.bookiebashing.net/trackers/early-payout-tracker/#/" and "the dynamic will
be that bookie bashing is a 'trusted' source, but pinnacle feeds will sometimes capture much more
rapid movement, which will show value. both are valid". The register removed the rule that Bookie
Bashing is never automated on 12 Nov 2026; the tracker is read like any other Scraper job.

- **Where the prices come from.** The Hub's Scraper reads the tracker on its own screen and the
  Hub posts the whole table to `POST /api/2up/bb` on the ingest key; each post replaces the
  snapshot Render holds. Render never loads the tracker. bet365's price on every row is the
  tracker's (`book_odds`); the feed asks OddsPapi for Pinnacle only (OddsPapi refuses bet365 on
  the current plan). `TWOUP_BET365_SOURCE=oddspapi` puts back the behaviour before 18 Nov 2026:
  bet365's 1X2 from OddsPapi and the Pinnacle mode alone.
- **`bb` mode, trusted as given.** A row is flagged when Bookie Bashing's rating (its EV for the
  2-up bet, 100 = break-even) is at or above `twoup_bb_min_rating`, 101 by default. Its fair is
  BB's `n_odds`; on a row only BB priced, `ev` = rating/100 − 1 and `edge` = ev/price, the same
  probability units as the model's edge.
- **`pinnacle` mode.** The rescue model above, unchanged, with bet365's price from the tracker row
  and Pinnacle's 1X2 de-vigged. The row is joined to an OddsPapi fixture by team names and
  kick-off with the matcher the Paddy board uses for the Scraper's saves prices (both names at
  0.80 similarity or better, within 12 hours); a row that joins none, or more than one where no
  single one is exact, is never guessed: it has no `pinnacle` verdict and is counted.
- **Both are valid.** A row flagged by either mode is value (inside the price band, the horizon
  and the minimum lead); `sources` says which. Where both priced a row the model fields are
  Pinnacle's and BB's sit in `bb_rating` and `bb_fair`.
- **Freshness.** A snapshot older than `twoup_bb_max_age_s` (300 s) serves nothing from either
  mode. A row Bookie Bashing last saw more than `twoup_bb_row_max_age_s` (600 s) ago is dropped and
  counted. A row's `priced_at` is when bet365's price was read (the row's `seen_at` less
  `updated_s`), or the earlier of that and Pinnacle's fetch where Pinnacle priced it.
- **What cannot apply.** The tracker posts no competition, so the competition list
  (`twoup_tournament_ids`) cannot filter a row only BB priced; the tracker lists only early-payout
  offers, so it is taken as the list. The list still decides which fixtures Pinnacle's half
  fetches, and so which rows the `pinnacle` mode can join.
- **The defaults are Callum's to change**: the rating threshold, the price band (which applies to
  both modes) and the two ages are settings on Render.
