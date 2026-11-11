# Scraper jobs, the Paddy map and Paddy's goalkeeper saves: design

11 Nov 2026. Callum asked for this in chat and said "go ahead with the build", so this
document is the builders' brief, not something he has read line by line. Where a choice is
marked **(my call)** it is Claude's, not Callum's, and he can overturn it.

## What Callum asked for (his words)

- "On the Hub Control Center, we need to have … a list of jobs, and each job can be added to,
  altered, changed. Each job is a separate tile or separate entry with a target, a duration,
  logs, and then Hermes can manually change or add any of these."
- The first job: "Scrape the paddy power website for goalkeeper saves for all leagues (not all
  leagues but all the leagues in the project scope) every 15 minutes on Friday and Saturday
  only, forever."
- "One page is more than enough to rotate around 50 matches and get accurate prices. That's
  one page open."
- "The saves prices come from paddy and bookiebashing, oddspapi has nothing to deal with it."
- "You would click back to get to the page lists. You will need to build a working map of
  paddy power as part of the scraper."

## What exists today

- A Scraper **target** (`scraper/config.py::Target`, stored in `<data>/targets.toml`, changed
  only through `scraper/targets.py`): name, site, url, `every_min`, `window`, parser, post,
  a `matches` block (a list page whose match links each get a kept-open tab), and so on.
  The Console's Scraper tab shows them in a table with add / edit / enable / disable / remove;
  HubAI changes them with `python -m scraper targets …`.
- Prices leave the Scraper as `scraper_prices` drop files, which the Hub posts to Render
  (branch `scraper-control-paddy`, which this branch is stacked on). `PRICE_ROUTES` in
  `scraper/sinks.py` lists the Render routes allowed.
- Every page load goes through the guard (`scraper/guard.py`, limits in `scraper/sites.py`).
- Render's saves card (`render/api/app/cards/saves.py`) and the Bookie Bashing reader
  (`scraper/parsers/bb_saves.py`) model a MATCH-total saves line. Its docstring says Paddy's
  price comes from OddsPapi. That is wrong and never worked.

## What the real pages show (captures under `/opt/puntmore-scraper/data/samples/`)

Parsers and the map are written from these, never from a guess.

- **Football page** `https://www.paddypower.com/football`
  (`20261111-135929/pp-map-recon/`): `smspp/content-managed-page/v7` payloads. `00017-http.txt`
  has `layout.links` (each `linkIdentifier.competitionId` + `options.title`; the Premier
  League and Champions League are `EXTERNAL` links with a `url`, the rest are `NAV_NODE`
  with no url) and `attachments.competitions` (`{name, competitionId, eventTypeId, eventId}`).
  `00022-http.txt` has 120 competitions and 201 events. Competition ids seen:
  English Premier League 10932509, English Sky Bet Championship 7129730, English Sky Bet
  League 1 35, Spanish La Liga 117, Italian Serie A 81, German Bundesliga 59, French Ligue 1
  55, UEFA Champions League 228, UEFA Europa League 2005, UEFA Nations League A 12209069,
  B 12288805, C 12288803, D 12246908. **Not seen in any payload yet:** Portugal's Primeira
  Liga, the World Cup qualifiers (Europe), the Euro qualifiers.
  The saved document is the app shell: it has no links in it. Links exist only in the live
  DOM (`a[href]`), which is how `runner.match_links` already finds matches.
- **Competition list page** `…/football/english-premier-league`
  (`20261107-123420/pp-epl-most_list/00003-http.txt`): `smspp/competition-page/v3`,
  `attachments.events` (`eventId, name "Home v Away", competitionId, openDate`) and
  `attachments.competitions`.
- **Match page** `…/football/english-premier-league/arsenal-v-hull-36235029`
  (`20261107-123420/pp-epl-most_m_…arsenal-v-hull-36235029/00003-http.txt`, and the same for
  fulham-v-newcastle-36235204 and leeds-v-tottenham-36234722): `smspp/event-page/v5`. The
  FIRST payload after the page opens (no tab click) already carries the saves markets when
  Paddy offers them. Far from kickoff the page has no saves markets at all
  (`20261111-135929`, Man City v Fulham).
- **Saves markets are per goalkeeper, "N or more":** `marketType`
  `GOALKEEPER_TO_MAKE_<N>_OR_MORE_SAVES`, `marketName` "Goalkeeper To Make 9 Or More Saves",
  `marketStatus` OPEN, runners named "Arsenal Goalkeeper" / "Hull Goalkeeper" with
  `runnerStatus` ACTIVE and the price at `winRunnerOdds.trueOdds.decimalOdds.decimalOdds`.
  There are also first-half, 5-minute and 10-minute saves groups and specials ("Both GKs to
  make 10 or more saves between them"). No plain match-total saves market was seen.

Fixtures are trimmed copies of those payload bodies. Never commit a request URL from
`index.jsonl` (they carry Paddy's `_ak` app key).

## Part J: jobs

**A job is a Scraper target.** One list, one file, one change path; no second concept. The
Console and the docs call them jobs; the code keeps `Target` and `targets.toml` **(my call)**.

### J1. Days and an end date

Two new `Target` fields, validated in `scraper/targets.py`, settable from the CLI and Console:

- `days`: weekdays the job runs, e.g. `["fri", "sat"]` (lower-case three-letter names, local
  time). Empty or absent means every day.
- `until`: `"YYYY-MM-DD"`, the last local day the job runs. Absent means forever.

A job is **active** when it is enabled, today is one of its days, the time is inside its
`window`, and today is not after `until`. Outside that, it is treated exactly as "outside
window" is today (`runner._outside_window`): its pages close and nothing loads. A window that
wraps midnight belongs to the day it started on (Sat 20:00–02:00 runs until 02:00 Sunday)
**(my call)**. A job past its `until` reports state `finished`; it is not removed.

"Every 15 minutes on Friday and Saturday only, forever" is `every_min = 15`,
`days = ["fri","sat"]`, no `until`.

### J2. A log per job

`scraper/joblog.py`: one file per job at `<data>/logs/<name>.log`, one JSON object per line
`{"ts": <epoch>, "msg": "..."}`. Bounded: when a file passes 512 KB it is rewritten to its
newest 1000 lines **(my call)**. Every line the runner prints about a target today also goes
to that target's log (and still to stdout for the journal). Removing a job removes its log.

Readers: `python -m scraper targets log NAME [-n 50] [--json]`, and the Console.

### J3. The Console

The Scraper tab's Targets table becomes a list of **job tiles**, one per job, each showing:
name; the target (site and URL); the schedule in words ("every 15 min · Fri, Sat ·
07:00–01:00 · forever" / "until 30 Nov 2026"); state and detail from `status.json`; last load,
last read, rows sent; the newest log lines, with the rest on demand; and the same actions as
today (edit, enable, disable, remove). The add and edit forms gain days and an end date.
Nothing else is added to the page.

Routes: the existing `/api/actions/scraper/targets/*` accept `days` and `until`; new
`GET /api/scraper/log/{target}?n=…` returns the newest log lines. The Console still changes
targets only by calling the Scraper's own `targets` code path, as today.

### J4. HubAI

HubAI ("Hermes") uses the same CLI. The scraper-manager instructions in `hub/` gain the new
flags and `targets log`.

## Part M: the Paddy map, the rotation and saves

### M1. The Paddy map

`scraper/ppmap.py`, stored at `<data>/maps/paddypower.json`, written atomically. It holds
only what Paddy's own pages have sent:

- `competitions`: by Paddy `competitionId`: name, the list-page URL once the Scraper has
  actually been on that page (never built from the name), when last seen.
- `events`: by `eventId`: name, home, away, `competitionId`, kickoff, the match-page URL once
  seen as a link or visited, the tab names and saves market types last seen there, whether
  it had saves markets and when that was last checked.

It is fed by every Paddy payload the Scraper captures (football page, competition page,
event page) and by the links in the live DOM (`a[href]` with their text), on whatever job
loaded them. Events more than a day past kickoff are dropped.

The project's leagues are a constant in `ppmap.py` keyed by Paddy `competitionId`, using the
ids listed above. The three not yet seen are listed by name with no id; the map's report
says "not found on Paddy yet" for them, and they are filled in from a real payload when one
shows them, not guessed.

View: `python -m scraper map [--json]` (competitions in scope, their URLs, matches known,
which have saves). No Console page: none was asked for.

### M2. `pp_saves`: the reader

`scraper/parsers/pp_saves.py`, registered in `PARSERS`. From an `event-page/v5` payload it
returns one row per goalkeeper per line:

`{event_id, home, away, kickoff, team: "home" | "away", line, over}`

- Only `GOALKEEPER_TO_MAKE_<N>_OR_MORE_SAVES` (the full match). First-half, 5- and 10-minute
  groups and specials are left out.
- Only `marketStatus` OPEN and `runnerStatus` ACTIVE.
- `line` is `N - 0.5` (5 or more = over 4.5); `over` is the decimal price. Paddy offers no
  "under", so there is none.
- "Arsenal Goalkeeper" is matched to home or away by the event's own "Home v Away" name. A
  runner that matches neither side is dropped and logged, never guessed.

### M3. The rotation: one page

A new block on a target, `rotate`, beside `matches` (a target has one or the other):
`competitions` (Paddy ids; empty = the project's leagues) and `horizon_hours` (default 48:
only matches kicking off within this are visited) **(my call)**.

One page, kept open. A pass runs every `every_min`:

1. Open the target's URL, the football page, if the page is not already there.
2. For each competition in scope: go to its list page by clicking its link on the page; wait
   for its `competition-page` payload; the map learns the URL and the matches.
3. For each match in the horizon, nearest kickoff first: click its link; wait for that
   event's `event-page` payload; read it with `pp_saves`; write the rows as one
   `scraper_prices` file; go **back** to the list.
4. Back to the football page; next competition.

Rules:

- The runner is tick-driven, so the rotation is a small state machine that makes at most one
  move per tick. A pass that is still running when the next is due finishes first; two never
  overlap.
- **Every move is a load to the guard**: it asks `guard.can_load` and the spacer first and
  records the load after, by the rule already in `_open_market_tabs` (the URL or the document
  changed). When the budget is spent the rotation waits where it is; it does not skip the
  guard and it does not retry a block (a block is the existing 24 h cool-off).
- Plain and direct: clicks through the existing `browser.click`, waits on the page's own
  network through `browser.wait_for_feed`.
- A match whose page had no saves markets is marked so in the map and looked at again at
  most once an hour until they appear **(my call)**, so moves go to matches with prices.
- A link that cannot be found is logged in the job's log with what was on the page, and the
  rotation moves on.
- Each pass ends with one log line: matches visited, matches with saves, rows written, how
  long it took.
- Outside the job's days, window or end date the page closes (Part J).

**The limit is Callum's and is not changed here.** `sites.py` says Paddy 15 loads an hour,
120 a day, at least 90 s apart, and the register quotes it. This job as asked is about 50
matches every 15 minutes: roughly 400 moves an hour. At today's numbers the rotation reads
about 7 matches an hour, nearest kickoff first. The numbers change when Callum gives them, in
a dated register edit with the one-line change to `sites.py`.

### M4. The Hub

`PRICE_ROUTES` gains `/api/paddy/saves-prices`; the `scraper_prices` schema, `hub/PROTOCOL.md`
and the schema referee tests gain the row shape above. The Hub's posting code is unchanged.

### M5. Render

- `POST /api/paddy/saves-prices` on the ingest key, beside `most-prices`: stores Paddy's
  per-goalkeeper saves prices, kept 36 h, replaced per match on each post.
- The saves fair (`POST /api/paddy/saves-fair`) gains an optional `team` ("home" | "away").
  A fair with a team is a per-goalkeeper fair.
- The saves card joins Paddy's price to the fair on match, team and line. A match-total fair
  (no team) joins to nothing, because Paddy sells no match-total market.
- The OddsPapi lookup for saves (`totals_saves`) and the docstring that claims it are removed.
- Render still loads no page and places nothing.

Until a per-goalkeeper fair arrives, the saves card stays empty and Paddy's prices are stored
and visible through the ingest's read route.

## Not in this build

- A per-goalkeeper fair from Bookie Bashing. It needs the real logged-in page to write the
  reader from, and `bb-saves` is still an open item of Callum's in the register.
- Any change to Paddy's limits (above).
- A Console page for the map.

## Register

`docs/decisions.md` is not edited by builders. The lines this build needs (jobs with days and
an end date; Paddy's saves are per goalkeeper; the new route; Paddy's limits) are written by
Claude, dated, in the same pull request, once Callum has said so.

## For the builders

- Tests: Hub `cd hub && ~/.venvs/puntmore/bin/python -m pytest -q`; Scraper, from the repo
  root, `~/.venvs/puntmore/bin/python -m pytest scraper/tests -q` (10 known failures from the
  missing headless browser: 8 in `test_e2e_fakesite.py`, 2 errors in `test_bb_saves.py`);
  Render, from `render/api`, `.venv/bin/python -m pytest tests/ -q`.
- Never edit `/opt/puntmore-scraper/**` or the live `targets.toml`. Never load a Paddy or
  Bookie Bashing page from a test or a script.
- Docs change with the code: `scraper/AGENTS.md`, `hub/AGENTS.md`, `hub/PROTOCOL.md`,
  `render/api/AGENTS.md`, `docs/dataflow.md`, `docs/architecture.md`, `docs/api.md`, README.
