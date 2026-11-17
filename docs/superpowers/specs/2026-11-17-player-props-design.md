# Player props: Paddy Power against Pinnacle (design, 17 Nov 2026)

## Why

Callum added player props to the OddsPapi subscription for Paddy Power and Pinnacle and wants a
private value list over them: "match it to Paddy and create a new personal value tracker just
for me", "just do a bet tool similar to the main paddy", "don't worry about results", "have it
on render but only for me".

## Callum's decisions (17 Nov 2026)

- Both books come from OddsPapi. The Scraper is not involved.
- Fair is Pinnacle with its margin removed; a Paddy price above fair is value.
- **Identical lines only.** A Paddy line Pinnacle does not price at the same number is not
  estimated from a neighbouring line; it is left out.
- Whatever sports both books carry. Today that is football only: the plan refuses every other
  sport (403 Restricted sportId).
- No results, settling or bet log. A list to bet from, like Paddy's.
- On Render, for Callum alone.
- Build it now although Pinnacle's props are not on the feed yet ("assume they will and carry
  on").

## What the feed carries (read live 17 Nov 2026 with `GET /api/paddy/oddspapi-probe`)

- A market id in the catalogue is one market at one line. Player-prop entries have
  `playerProp: true`, `sportId: 10`, a `marketType`, a `period`, a `handicap` and named
  `outcomes`.
- Two shapes for the same nine statistics (goals, assists, cards, foulscommitted, offsides,
  saves, shots, shotsongoal, tackles):
  - `players-<stat>`, period `result`, handicap 0, outcomes named `0`, `1`, `1+`, `2+` ...
    (cards also `Yes`). Also `players-anytimegoalscorer` (`Yes`), `players-firstgoalscorer`
    and `players-lastgoalscorer` (period `fulltime`).
  - `playertotals-<stat>`, period `result`, handicap 0.5, 1.5 ..., outcomes `Over`, `Under`.
- A price sits at `bookmakerOdds.<book>.markets.<marketId>.outcomes.<outcomeId>.players.<playerId>`
  as `{price, active, ...}`. Player id `0` is "no player" (ordinary markets).
- Paddy Power quotes the `players-*` shape, one-sided: anytime scorer, first scorer and
  `players-goals` `2+` on most games; near kick-off also assists `1+`, cards `Yes`, fouls
  `1+..3+`, shots `1+..6+`, shots on goal `1+..4+`.
- Pinnacle (granted as `pinnacle+30`, 30 minutes delayed) carried no player-prop market on 171
  fixtures that day. Its shape is therefore unseen. The design reads either shape.
- Player names: `GET /players?sportId=10` (billable), rows holding `players: [{playerId,
  playerName: "Surname, First"}]`.

## The model: one statement, whatever the shape

Every quote from either book is turned into one of two statements about a player in a fixture:

- `at_least(stat, n)`: the player records `n` or more.
- `fewer(stat, n)`: the player records fewer than `n`.

| Catalogue | Outcome | Statement |
|---|---|---|
| `players-<stat>` | `N+` | at_least(stat, N) |
| `players-<stat>` | `0` | fewer(stat, 1) |
| `players-cards` | `Yes` | at_least(cards, 1) |
| `players-cards` | `No` | fewer(cards, 1) |
| `players-anytimegoalscorer` | `Yes` | at_least(goals, 1) |
| `players-anytimegoalscorer` | `No` | fewer(goals, 1) |
| `playertotals-<stat>` at line L (x.5 only) | `Over` | at_least(stat, L + 0.5) |
| `playertotals-<stat>` at line L (x.5 only) | `Under` | fewer(stat, L + 0.5) |

Anything else is not read: exact counts (`1`, `2`), first and last goalscorer (no opposite side
exists to remove a margin with), whole-number lines, other periods, other sports.

A **selection** is (fixture id, player id, stat, n). Paddy's price for a selection is its
`at_least` quote. Pinnacle's fair for the same selection needs **both** its `at_least` and its
`fewer` quote for that exact (player, stat, n): the two are de-vigged with the power method
(`odds/utils.devigd`, the register's measured default) and the `at_least` side is the fair.
`value = paddy / fair - 1`. The join is on OddsPapi's own fixture id and player id: no name
matching anywhere.

When a book quotes the same statement twice (both shapes), the first active price above 1.0 is
kept and the clash is counted.

## Behaviour

- **Source.** `app/props/` reads the raw boards the Paddy sweep already holds
  (`OddsPapiPaddySource.boards`, both books merged per fixture, and `.fixtures` for names and
  kick-offs). It makes **no odds request of its own**, with one exception: a read before the
  source has ever swept (the first after boot) runs the Paddy sweep, as the Paddy feed does.
  A board whose last sweep failed is not shown. Its one request is `/players` for
  names, cached a day by the client.
- **Feed.** `GET /api/props/feed?min_edge=` (default 0.0, bounds -0.5..0.5): one item per
  fixture still to kick off, soonest first, each with its bets best value first:
  `{event_id, match, home, away, commence_time, priced_at, best_value, bets: [{player,
  player_id, stat, n, pick, odds, fair, value, pinnacle_margin}]}`. `pick` is how the page
  prints it: "Erling Haaland 2+ shots on target", "Erling Haaland to score", "Bukayo Saka to
  be carded", "Declan Rice 1+ assists". A player with no known name is left out and counted.
- **Diagnostics.** `GET /api/props/diagnostics`: per stat, how many Paddy selections, how many
  Pinnacle selections with both sides, how many matched, and why the rest did not
  (`no_fair_player`, `no_fair_line`, `fair_one_sided`, `fair_crossed` for a Pinnacle pair
  with no margin, `unnamed`), plus a one-line verdict.
  "Pinnacle is posting no player markets" must be readable there in one request: it is the
  state the tool launches in.
- **Refusals.** A selection that cannot be priced exactly is left out and counted, never
  approximated (the register's rule for every tool).
- **Page.** `/props` on `me` (the main domain while `me` is off), grant `props`, flag
  `ENABLE_PROPS` (default on). The Paddy board's look and code (`CardsPage.module.css`, the
  band, pills, `useAutoRefresh`): band name `PROPS` in Paddy's green, "Updated 3m", pills for
  markets (one per stat), kick-off window and max odds, sort on the Odds and Value headings.
  One list: every bet at or above 103% of fair (Callum agreed the 3% start), best first. Each
  row: the pick, the match (short team names) and kick-off, Paddy's odds, the value % with
  Paddy's tiers, the fair. No Refund, Boost or Free bet switch, no bet log.
- **Only Callum.** `props` is a new grant in `ALL_TOOLS` and `ME_TOOLS`. Callum's account is
  not in code: it comes from `AUTH_USERS` on Render and holds `*`, which carries every tool.
  No coded account and no API client is granted `props`; any other account holding `*`
  would see it.

## Not in this build

- Results, settling, a bet log, staking.
- Discord alerts and the cross-tool value feed (`/api/value`).
- A Bet Placer feed.
- Estimating a fair for a line Pinnacle does not quote.

## Calls made by the designer (not Callum's words)

- The page lists from 103% of fair; the API still serves from fair so the cut-off can move
  without a deploy of the API.
- One-sided Pinnacle quotes price nothing.
- Anytime goalscorer is read as goals 1+, as Paddy's own `players-goals` starting at `2+`
  implies.
- The tool is called "Props".

## Known weakness

Pinnacle arrives 30 minutes late. Player prices move most when line-ups are announced, about
an hour before kick-off, so a Paddy price can look like value against a Pinnacle price that
has already moved. The page shows the age of the prices; nothing else guards this yet.

## Build slices

1. **Backend.** `app/props/model.py` (catalogue to statements, boards to quotes, the join and
   value, pure), `app/props/service.py` (reads the source, names, feed and diagnostics),
   `routers/props.py`, schemas, `ENABLE_PROPS`, the `props` grant in `security.py` and
   `site.py`, `identity.py`, mounted in `main.py`. Tests for every table row above, both
   shapes on the fair side, the one-sided refusal, a suspended price, an x.0 line, the
   duplicate statement, the unnamed player, the empty Pinnacle side.
2. **Frontend.** `pages/PropsPage.jsx`, route, nav entry, `site.js`, `identity.js`, `api.js`,
   pure helpers with vitest tests.
3. **Documents.** The register (a dated entry with Callum's words above), `CLAUDE.md`,
   `README.md`, `docs/api.md`, `docs/dataflow.md`, `render/api/AGENTS.md`,
   `render/frontend/AGENTS.md`, `.env.example`.
