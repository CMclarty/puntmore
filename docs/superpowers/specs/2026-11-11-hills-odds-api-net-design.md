# Hills on odds-api.net — design

11 Nov 2026. Callum: "now we have a provider for william hill odds lets build that out ...
investigate and build". He skipped the design questions, so anything he did not decide is
marked **(my call)**. What only he can decide is listed at the end.

## Intent

odds-api.net becomes Puntmore's **second odds provider**, beside OddsPapi (Callum, 11 Nov
2026: "update the planning documents with the 2nd providor"). It carries William Hill's full
stats markets and Pinnacle on the games Hills cares about, so Hills (William Hill's corners,
cards and goals board, BB Boost's target, legs and bet log) moves to it. OddsPapi is a running
subscription (Callum: "Oddspapi is a subscription"); Paddy, 2-Up and the coupon tracker stay
on it. A third vendor, UK Odds API (ukoddsapi.com), was tested the same day and is not used:
no saves, Paddy's corners on few games, no sharp book, and its data looks collected from
Oddschecker.

**Goals**
- `/hills/feed`, `/hills/diagnostics`, `/hills/fixtures`, `/hills/target`, `/hills/legs` and
  `/hills/bets` behave as they do now, priced from odds-api.net.
- Pinnacle stays the fair side. Fairs are de-vigged in-house, as the register requires.
- BB Boost bets settle themselves from odds-api.net's results.
- The board's age is honest: it never claims fresher prices than it has.
- Hills spends credits only while someone has the page open, as it does now.

**Non-goals:** moving Paddy, 2-Up or the coupon tracker; streams or websockets; odds history;
a board-at history for Hills (Paddy's alone is kept); any change to the pricing model.

**Anti-goals:** using the vendor's de-vigged or `fair_odds` fields to price anything; using
a fair book other than Pinnacle when Pinnacle is missing; polling on a timer when nobody has
the page open; storing raw vendor responses (the instance has hit its memory cap before).

## What odds-api.net is (checked live, 11 Nov, 22 credits)

- Base `https://api.odds-api.net/v1`, header `X-API-Key`. Growth plan: 250,000 credits a
  month (the period runs 11 Nov to 11 Dec), 120 requests a minute. Every call costs one
  credit, failures included. `/usage` reports what has been spent.
- `GET /events?sport=soccer&start_from&start_to&limit=1000` lists about six days ahead (234
  events on 11 Nov, the last on 17 Nov). Each event carries a `league` *name* (no league id),
  `start_time` (unix), teams and the `bookmakers` that price it. Started and finished events
  drop off the list. There were no EPL events in the next 14 days (international break).
- `GET /events/{id}/odds/snapshot?bookmakers=williamhill,pinnacle&price_fields=odds&types=total,team total,handicap&periods=full time`
  returns both books in one call (~160 KB, ~1.4 s; ~1.2 MB without the filters). Each item
  has `bookmaker`, `bet_type`, `metric`, `period`, `line` (a string: `"over 9.5"`, `"+2.5"`),
  `side` (`home`/`away` on team totals and handicaps), `odds`, `is_available`. `market_key` is
  not consistent (`total/corners/over 9.5` beside `total/over 8.5` for the same market), so it
  is never read. Betfair's `metric` comes upper-case (`STANDARD`).
- England v Croatia (12 Nov): William Hill has corners, cards and goals totals and team
  totals at every half line; Pinnacle has a 5-rung corners ladder, a 5-rung cards ladder,
  goals totals (with quarter lines), one team-total line per team, and corners and cards
  handicaps. Saves come **only** as per-keeper player props on William Hill; there is no
  match saves total. Betfair and Matchbook carry goals and match odds only.
- **Freshness.** Each snapshot gives `bookmaker_as_of_ts_ms` per book and a
  `target_refresh_interval_seconds` for the event's kickoff band: 60 s inside six hours of
  kick-off, 2,559–3,599 s further out. Observed: William Hill 1.5–1.7 minutes old inside six
  hours; 64, 71, 74 and 108 minutes old on games 6–31 hours out. Pinnacle was always under five
  minutes old. `stale_bookmakers` names a book older than twice the target.
- `GET /events/{id}/results` gives `status: pending` until the game is settled, then
  `score_records`: `side` × `metric` (`corners`, `cards`, `standard` = goals, fouls, offsides,
  shots on target) × `period`, each with `status: final`. A J League Cup game that ended
  about 11:55 UTC was available by 12:01. There are no saves and no red/yellow breakdown.
- Paddy Power and bet365 are on the vendor (93 and 82 of 234 events) but on none of the 43
  Nations League games.

## The seam

A new source class, `OddsApiNetHillsSource` (`render/api/app/cards/oddsapinet.py`), with its
HTTP client in `render/api/app/cards/oddsapinet_client.py`. It sits at
`app.state.hills_client`, where `OddsPapiPaddySource(book=WILLIAM_HILL)` sits today.

**What it honours exactly** (what the shared cards router, `cards/feed.py` and the Hills
router read from `hills_client`):

| Member | Read by | Contract |
|---|---|---|
| `get_events()` | `build_feed`, `/hills/fixtures`, `/hills/legs` | events in the counts model's shape: `{id, home_team, away_team, commence_time, sport_key, fair_source, bookmakers: [{key, markets: [{key, outcomes: [{name, point, price}]}]}]}`, raw prices |
| `book`, `label` | `build_feed`, `diagnose` | `"williamhill"`, `"William Hill"` |
| `last_sweep` | `diagnose`, `/hills/oddspapi-check` | the same keys the OddsPapi sweep writes (below) |
| `priced_at` | `build_feed` | the oldest price behind the board |
| `fixtures` | `/hills/fixtures`, `PUT /hills/target` | `[{id, home, away, start, tournament_id, round}]` |
| `boards` | `/hills/legs` (truthiness only) | the per-event boards it holds |

**What it does not honour, on purpose (my call).** BB Boost's legs and settling read
OddsPapi's raw board (`boards[fid]["bookmakerOdds"]`, `market_index`, `priced_book`) and
`_client.get("/settlements")`. Faking OddsPapi's shapes from another vendor would be a lie in
the data. Instead the source gets three methods, and `hills/legs.py` and `hills/settle.py`
call them when the source has them, keeping the OddsPapi path unchanged for as long as it
lives:

- `hills_legs(fixture_id, home, away) -> list[dict]`: the same leg dicts as today. A leg's
  `market_id` is `"<key>:<line>"` (`"alternate_totals_corners:9.5"`) and its `outcome_id` is
  `"Over"` or `"Under"`, so a logged bet carries everything needed to settle it from a
  score.
- `owns_fixture(fixture_id) -> bool`: true for `oan:` ids.
- `leg_results(fixture_id, legs) -> {(market_id, outcome_id): "won" | "lost"}`.

Two additive changes outside Hills: `build_feed` takes a match's `priced_at` from
`client.priced_at_for(event_id)` when the source has that method, and `diagnose` decides
whether a fair side came with the sweep from `fair_source` being set, not from it starting
with `oddspapi`. Paddy's output is unchanged by both.

**Ids (my call).** Event ids are namespaced `oan:<vendor id>` everywhere Hills stores or
serves them (feed `event_id`, `HillsTarget.fixture_id`, `HillsBet.fixture_id`). An OddsPapi id
and an odds-api.net id can then never be confused, and settling can tell whose bet it is.

## Which games

- **Competitions (my call):** the same set Hills sweeps on OddsPapi, by odds-api.net's names,
  each mapped to the Sofascore id the rest of the code keys on (`target.py`'s competition ids,
  `TOURNAMENT_SPORT_KEYS` for the dispersion priors): EPL 17, English FA Cup 19, EFL Cup 21,
  UEFA Champions League 7, English Championship and EFL Championship 18 (the vendor lists
  both names), League 1 24, La Liga 8, Serie A 23, Bundesliga 35, Ligue 1 34, Portuguese
  Primeira Liga 238, UEFA Europa League 679, UEFA Nations League 23755, World Cup Qualifiers
  Europe 11. A constant, `HILLS_LEAGUES`, not a setting. Euro qualifiers have no name on the
  vendor and are dropped.
- **Window:** 168 hours, as now (`oddsapinet_window_hours`); the vendor lists about six days.
- **Fixtures** (what the target can be): every listed event in those competitions, whether
  or not William Hill prices it yet. A game listed twice under the two Championship names is
  kept once (same teams, same kick-off).
- **Snapshots** are taken only for events whose listing names `williamhill`.
- **Finals.** The vendor sends no round name, so `target.is_final` gets `None` and the
  existing rule applies: a cup counts as a final only when it is that cup's one game in the
  window.

## Market mapping

Only `period == "full time"`, `is_available == true`, `odds > 1.0`. `line` is read as
`"<over|under> <number>"` for totals and `"<signed number>"` for handicaps. Odd/even lines and
quarter lines (x.25, x.75) are dropped.

| odds-api.net (`bet_type`, `metric`, `side`) | Pipeline key | Books |
|---|---|---|
| `total`, `corners` | `alternate_totals_corners` | WH, Pinnacle |
| `total`, `cards` | `alternate_totals_cards` | WH, Pinnacle |
| `total`, `standard` | `totals_goals` | WH, Pinnacle |
| `team total`, `corners`, `home` / `away` | `team_corners_home` / `_away` | WH, Pinnacle |
| `team total`, `cards`, `home` / `away` | `team_cards_home` / `_away` | WH, Pinnacle |
| `handicap`, `corners` / `cards` | `corners_handicap` / `cards_handicap` | Pinnacle only |
| `player prop`, `player saves` | none: there is no match total | dropped |

Totals become `{name: "Over" | "Under", point, price}`. A handicap becomes
`{name: "Home" | "Away", point: the home side's handicap, price}`: the home item's line as
given, the away item's line negated, which is the convention `pricing/counts/split.py` reads.
Checked: the recorded England v Croatia snapshot, mapped this way and run through
`price_events` at min_edge −0.5, fits three ladders, gives 80 William Hill rows and reports
no split disagreements, with the handicap read on both team splits.

**Saves (Callum, 11 Nov 2026: "Remove saves on hills"):** Hills has no saves rows and no saves
legs. odds-api.net has no William Hill match saves total, and adding two keepers' props into
one would be a guess.

**The 11.5 corners line (Callum, 11 Nov 2026: "add the 11.5 corner lines"):** Hills shows
William Hill's 11.5 corners line. `paddy_excluded_lines` (`corners:11.5`) is Paddy's shop
rule and applies to the Paddy Power book only (`laddered.py::_price_paddy_lines`, keyed on
the `book` the pipeline already carries); Hills prices every half line William Hill quotes.

## Fairs

Pinnacle from the same snapshot, raw. `value.py` and `laddered.py` de-vig with the power
method, unchanged. `fair_source` on each event is `"odds-api.net"`; `last_sweep.fair_source`
is `"odds-api.net:pinnacle"`.

**When Pinnacle is absent** (Armenia v Cyprus, several Nations League games): the event
carries William Hill only and gets no rows, exactly as an OddsPapi event without a Pinnacle
book does today. It counts in `carried_paddy_prices` but not in `carried_pinnacle_prices`,
so `/hills/diagnostics` says why. BB Boost's legs still list William Hill's lines; legs need
no fair. No fallback fair book: Betfair carries no corners or cards, and the vendor's own fair
fields are cross-check only under the register.

## Settling

`/hills/bets` still settles what is due before answering: open bets with legs, kick-off two
hours to two days ago, each fixture at most every 30 minutes. For an `oan:` fixture it calls
`GET /events/{id}/results` (one credit). Nothing is settled unless `status == "available"`,
`event_status == "finished"`, and the records the leg needs are `final`. A match leg uses
home + away; a team leg uses its side; over wins when the count is above the line. Every leg
is a half line, so there are no pushes. A leg with no record (saves) stays open for a hand
result. The bet's result rule is unchanged. `settled_by` is `"odds-api.net"`, and the page
shows "auto" for it as it does for `"oddspapi"`.

**Cards (my call):** settled from the vendor's card count. William Hill's own rules on a red
card may count differently. The leg is still marked auto and anyone can override it by hand.

**Bets logged on OddsPapi ids** are not settled by the new source; they stay open for a hand
result. None should be due: the last Premier League round (7–9 Nov) is past its two-day
window before this ships.

## Budget, caching and freshness

- **Sweep only when the page asks.** No scheduler job. A request reads the board as it
  stands; the work happens on requests.
- **Listing:** one `/events` call, cached 600 s; follow `next_cursor`, at most 3 pages.
- **Per-event snapshot cache (my call):** an event's board is reused for the vendor's own
  `target_refresh_interval_seconds`, clamped to 120–3,600 s. Asking sooner than the vendor
  refreshes buys nothing. Boards for events no longer listed are dropped on every pass, so the
  cache is bounded by the window. Only parsed lines are kept, never the response.
- **Refresh in the background, serve what is held (my call).** A cold board is 40–60
  snapshots at ~1.4 s each, too slow for a page request. `get_events()` returns the boards it
  has at once and, when any are due, starts **one** background pass (never two at a time).
  The pass refreshes the due events nearest kick-off first, at most 60 a pass. The first
  request after a boot waits up to 15 s for that pass. Events not fetched yet are counted in
  `last_sweep.pending`, and `/hills/diagnostics` says "still fetching" rather than "nothing
  there". The target's own board is fetched in the request when `/hills/legs` needs it and it
  is missing or due (one credit).
- **Failures:** an event whose snapshot fails is not asked again for 600 s. A 429 or a spent
  budget ends the pass; the error is named in `last_sweep.errors`. Nothing retries.
- **Spacing and cap:** requests at least 0.5 s apart (the plan's 120 a minute). A daily cap,
  `oddsapinet_daily_credit_budget = 8000` (Callum, 11 Nov 2026: "there is 250k credits per
  month"), counted on every request sent; 8,000 × 31 = 248,000, inside the 250,000 in any
  month.
- **Expected spend:** about 1 credit per far-out game per hour plus 30 per game per hour
  inside six hours, while the page is open, plus 6 an hour for the listing. A Saturday with
  40 far-out games, 15 near ones and four hours on the page is about 2,000. A light weekday
  is about 300. Roughly 25,000 a month.
- **Staleness:** a match's `priced_at` is the older of William Hill's and Pinnacle's
  `bookmaker_as_of_ts_ms` (served per match through `priced_at_for`), and the source's
  `priced_at` is the oldest on the board. The page already shows the board's oldest. When a
  far-out game is on the board, the band will often read an hour or more. That is the truth:
  William Hill's far-out prices are that old. A match whose William Hill side the vendor
  marks stale is still priced; it is counted in `last_sweep.stale_williamhill`. Prices the
  vendor withholds as very stale are not requested (`include_stale` stays off).

`last_sweep` keys: `source` (`"odds-api.net"`), `book`, `fair_source`, `window_hours`,
`fixtures_in_window`, `called`, `carried_paddy_prices`, `carried_pinnacle_prices`, `pending`,
`stale_williamhill`, `failed`, `errors`, `requests_today`, `request_budget`, `next_kickoff`.

## Config and switching

- New env var **`ODDSAPINET_API_KEY`** (`settings.oddsapinet_api_key`), in `.env.example`
  and in `render.yaml` as `sync: false`. Callum sets it in Render's dashboard.
- `main.py` chooses Hills' source through `hills/vendor.py::build_hills_source`. With
  `ODDSAPINET_API_KEY` set, Hills uses odds-api.net. Otherwise, with an OddsPapi client,
  Hills stays on OddsPapi as today. With neither, there is no source.
- **Fallback (my call):** no switch setting. Until Callum sets the key, Hills runs on
  OddsPapi. Once it is set, Hills runs on odds-api.net. If odds-api.net misbehaves, removing
  the env var puts Hills back on OddsPapi.
- A target picked on OddsPapi before the switch keeps its OddsPapi id, so its legs come back
  empty. Re-pick it after the deploy.
- Each vendor settles only its own bets, and only while Hills is on it: on odds-api.net, bets
  logged with OddsPapi ids wait for a hand result (and the other way round). On odds-api.net
  only a result whose `status_detail` is "FT" settles: William Hill's lines are 90 minutes
  and the vendor does not say whether "full time" counts extra time, so "AET", "Pen" or no
  detail leaves the legs for a hand result (controller ruling, 11 Nov 2026).
- `/api/ready` gains `providers.hills_detail: {provider, degraded, requests_today}`. `degraded`
  is true when there is no source, or when the last sweep failed and carried nothing.
  `/hills/oddspapi-check` on an odds-api.net source answers that Hills is not on OddsPapi.

## What odds-api.net could do later (not in this work)

- **Paddy:** Paddy Power is on 93 of the 234 listed events but on none of the Nations League
  games. Check `/coverage?bookmaker=paddypower` against Paddy's competitions before
  considering it. Its per-event billing makes Paddy's 2-minute sweep cost per game, not per
  competition.
- **2-Up:** bet365 is on 82 of 234 events (not the Nations League). Check bet365's plan
  entitlement and early-payout coverage; the 5-minute job would bill one credit per game per
  pass.
- **Coupons:** Pinnacle 1X2, totals, team totals and handicaps are all there, but a 154-leg
  sheet refreshed every 10 minutes would be roughly 22,000 credits a day billed per event.
  It would need longer cache lives or the event-odds stream (3 concurrent stream units on
  this plan).
- `results` also gives goals, corners, cards, shots on target, fouls and offsides per side,
  which could settle AW 26 legs or check Paddy's history.

## The register

The register says **"OddsPapi is the only odds source"** (5 Nov 2026, Callum). This work
contradicts it, so it needs Callum's dated edit in the same pull request. Proposed wording,
replacing that bullet:

> - **OddsPapi prices Paddy, 2-Up and the coupon tracker; odds-api.net prices Hills.**
>   Callum, 11 Nov 2026: "now we have a provider for william hill odds lets build that out".
>   Callum, 11 Nov 2026: "update the planning documents with the 2nd providor";
>   "Oddspapi is a subscription".
>   TheOddsApi stays removed. Goalscorers is parked; if it returns it uses OddsPapi.
>   (5 Nov 2026, Callum; Hills 11 Nov 2026, Callum)

`docs/architecture.md`'s "Fixed 6" and diagram, and `render/api/AGENTS.md`'s "Odds sources",
follow the register.

## Docs to change

- `docs/decisions.md`: the bullet above, once Callum approves it.
- `render/api/AGENTS.md`: "Odds sources" names odds-api.net for Hills. A new section, "Hills
  (`cards/oddsapinet.py`) — odds-api.net", holds the facts above: billing, rate, freshness,
  mapping, results, no saves, `oan:` ids.
- `docs/architecture.md`: an odds-api.net node feeding Hills, and Fixed 6 rewritten.
- `docs/dataflow.md`: under "Where to look when something is lost", "a match missing from
  Hills: `/api/hills/diagnostics` (`sweep.pending`, `stale_williamhill`, errors)".
- `CLAUDE.md`: the Hills line says it prices from odds-api.net.
- `.env.example`, `render.yaml`: `ODDSAPINET_API_KEY`.
- `render/frontend/src/pages/HillsBuilder.jsx`: its comment says the bets settle from
  odds-api.net.

## Callum's jobs

1. Read the register line above; it records his 11 Nov words.
2. Set `ODDSAPINET_API_KEY` in Render's dashboard (Environment), then redeploy. The key is in
   the vendor-keys file he gave the controller; it never goes in the repo.
3. After the deploy, re-pick the Hills target if one was set.

## Decided by Callum, 11 Nov 2026

- odds-api.net is the second odds provider; OddsPapi stays (a subscription) for Paddy, 2-Up
  and the coupon tracker.
- "Remove saves on hills."
- "add the 11.5 corner lines": Paddy's excluded-lines rule applies to Paddy Power only.
