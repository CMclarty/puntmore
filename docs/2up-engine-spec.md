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
rapid movement, which will show value. both are valid". The register records that, that the two
modes are Bookie Bashing's and Pinnacle's, and that bet365's price comes from the tracker. The
register removed the rule that Bookie Bashing is never automated on 12 Nov 2026; the tracker is
read like any other Scraper job.

Everything below marked *call* is the author's (Claude's and the coordinating agent's), a
default Callum has not decided; each is a setting or a line of code he can change.

- **Where the prices come from.** The Hub's Scraper reads the tracker's own data on its own
  screen, only games kicking off within 48 hours, and posts nothing from a hung or short page;
  the Hub posts the whole table to `POST /api/2up/bb` on the ingest key, and each post replaces
  the snapshot Render holds. Render never loads the tracker. bet365's price on every row is the
  tracker's (`book_odds`); the feed asks OddsPapi for Pinnacle only (OddsPapi refuses bet365 on
  the current plan).
- *call:* **The snapshot is held in memory**, never in a table: a deploy or restart loses it and
  the board says `not_yet_refreshed` until the next post. A post whose newest `seen_at` is older
  than the held one's is refused (409).
- *call:* **`TWOUP_BET365_SOURCE=oddspapi`** puts back the behaviour before 18 Nov 2026: bet365's
  1X2 from OddsPapi and the Pinnacle mode alone.
- **`bb` mode, trusted as given.** A row is flagged when Bookie Bashing's rating (its EV for the
  2-up bet, 100 = break-even) is at or above *call:* `twoup_bb_min_rating`, 101. Its fair is
  BB's `n_odds`; on a row only BB priced, `ev` = rating/100 − 1 and `edge` = ev/price, the same
  probability units as the model's edge.
- **`pinnacle` mode.** The rescue model above, unchanged, with bet365's price from the tracker row
  and Pinnacle's 1X2 de-vigged. *call:* The row is joined to an OddsPapi fixture by team names and
  kick-off with the matcher the Paddy board uses for the Scraper's saves prices (both names at
  0.80 similarity or better, home to home and away to away, within 12 hours). It is never
  guessed: a row that joins no fixture, more than one where no single one is exact, or a fixture
  whose names also match the other way round (a derby of two similar names) has no `pinnacle`
  verdict and is counted. A youth, reserve or women's side never joins a senior fixture.
- *call:* **Both are valid, so a row flagged by either mode is value**, inside the price band
  (`twoup_min_price`..`twoup_max_price`, which applies to both modes), the horizon and the
  minimum lead; `sources` says which. Where both priced a row the model fields are Pinnacle's and
  BB's sit in `bb_rating` and `bb_fair`. Rows the modes disagree on strongly (the Pinnacle mode
  flags a row Bookie Bashing rates below 100) are counted (`modes_disagree`) and left as value:
  whether that should change is Callum's call.
- *call:* **On this plan Pinnacle is the SLOWER side, by 30 minutes.** OddsPapi grants Pinnacle as
  `pinnacle+30`: its prices are 30 minutes behind, so the delay is subtracted when saying when a
  Pinnacle price was true (`fair_priced_at`). Callum's "pinnacle feeds will sometimes capture much
  more rapid movement" holds for Pinnacle's own market, not for the copy this plan receives: a
  `pinnacle`-only flag shortly after late team news can be a stale line that bet365 and Bookie
  Bashing have already moved past. A row whose Pinnacle price is older than
  `twoup_pinnacle_max_age_s`, 5700 s (the client's slowest cache tier of an hour, the 30-minute
  delay and the feed's 5 minutes), gets no `pinnacle` verdict and is counted; a Pinnacle refresh
  that has stalled past that limit is said in `cannot_price` ("pinnacle mode: ...") however
  recently a tracker post composed the board.
- *call:* **Freshness by the price's own time.** Each row carries `bet365_priced_at` (the row's
  `seen_at` less `updated_s`, the data's true age), `fair_priced_at` (Pinnacle's fetch less the
  delay, null without a Pinnacle side) and `priced_at`, the older of the two. A row whose bet365
  price is older than `twoup_bb_row_max_age_s`, 600 s, is dropped and counted; a row with no age
  (`updated_s` null) is dropped and counted. 600 s stays because the Scraper agent saw the
  tracker's data change about every one to two minutes, so 600 s is five to ten missed changes,
  a stalled tracker rather than a quiet one; a larger sample may move it. A snapshot older than
  `twoup_bb_max_age_s`, 300 s, by when it was received or by its newest row, serves nothing from
  either mode. A row read more than 120 s ahead of Render's clock is refused at the post.
- *call:* **A row whose `selection` is not the team on its `side`** (by the join's own similarity,
  and better than the other team) is dropped at the post and counted.
- *call:* **A snapshot with no usable bet365 row is not a quiet board.** An empty post, a post
  with no bet365 home or away row, or one whose every bet365 row has an unknown or too old price,
  is an "ALL" gap in `cannot_price`. Only usable rows that all kick off outside the horizon make
  an honest empty board. When no game is within 48 hours the Scraper's job rests and posts
  nothing, so the snapshot ages and the board says so: a rest and a broken reader look the same
  from Render.
- **What cannot apply.** The tracker posts no competition, so the competition list
  (`twoup_tournament_ids`) cannot filter a row only BB priced; the tracker lists only early-payout
  offers, so it is taken as the list. The list still decides which fixtures Pinnacle's half
  fetches, and so which rows the `pinnacle` mode can join.
