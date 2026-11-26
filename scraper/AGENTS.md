# scraper/ — rules for the Scraper service

`docs/decisions.md` is read first and wins; the repo's `CLAUDE.md` applies everywhere and wins on any other conflict. Setup and
day-to-day commands are in `README.md`. The Scraper service is the ONLY thing on the Hub that loads a page:
every page any tool needs, filling what OddsPapi lacks. All its rows go to Scraper control (the
Hub's inbox, as drop files: `hub/PROTOCOL.md`) and from there to a board or to HubAI; Paddy's
prices are `scraper_prices` files that the Hub posts to Render.

## Boundaries (agreed; keep them)
- It detects challenges and logouts and **stops** (a logout on a site that signs in, after one
  sign-in, below). It never answers a check and never fakes a fingerprint. It signs in to the
  sites its jobs need, each with Callum's own login from the Scraper's `.env`, one `Form` per
  site in `login.py` (Bookie Bashing `BB_USERNAME`, `BB_PASSWORD`; Team Casino `TC_USERNAME`,
  `TC_PASSWORD`; Callum, 13 Nov 2026), once per logout and at most 3 sign-ins in 6 hours per
  site, then it waits for a person, with plain fill and click. It never types a two-factor
  code. A failed sign-in waits for a person or `resume <site>`; a person is needed only then or
  for a challenge. The password is never written anywhere and never goes in a url, and the
  capture never keeps the sign-in's own response. No proxies or IP rotation,
  no clicking through menus to look human, and no other ways to avoid detection. If Paddy blocks
  the Hub, the answer is to slow down or get the data another way, not to work around the block.
- One agreed exception (Callum, 26 Oct 2026): Chrome is started as a person's Chrome, without
  Playwright's `--enable-automation` test flag and with AutomationControlled off. Nothing else
  about the browser is changed or overridden. It runs on its own virtual screen
  (`puntmore-screen`, a headless sway on the box's GPU; Callum, 11 Nov 2026), never on Callum's
  desktop: a page sees the screen it is really on, and a person reaches it over VNC on
  `127.0.0.1` only (`README.md`, "Its own screen").
- What a block means and the hosts a job may load are code (`sites.py`, `config.py`). Its
  limits, timings and clicks are the behaviour catalogue (`behaviour.py`): Callum changes them
  live (the Console's Behaviour pane or `python -m scraper behaviour`), every change is logged,
  and HubAI does not change them. `targets.toml` cannot loosen them.
- Targets are direct page URLs in `targets.toml`.
- `look` is how a page is learned (a few plain steps on a job's open page, then the page saved);
  it is not for Paddy Power pages.

## Module map
- `runner.py` - the loop: wires the modules below, owns the tabs, loads
  through the guard, applies a targets.toml edit (in place for a timing-only
  change) and a behaviour.toml edit (the guard follows; each change logged).
- `schedule.py` - when each target is due: `every_min`, `stale_min`, jitter
  drawn once per load, the per-site load gaps, whether a job is active at
  all (`inactive`: `until`, `days`, `window`; a window past midnight belongs
  to the day it started, `job_day`), `priority`, and the
  kick-off sweep (`near_min` within `near_hours` of a kick-off the rows
  report; nearest kick-off first when a budget is short), and `within_h`:
  the hours ahead a job reads games, its own `within_h` or `games.within_h`.
- `sinks.py` - where rows go, by `post`: `/api/...` targets:
  `scraper_prices` drop files the Hub posts to that route; `hub:<chan>`:
  tips (each row with its `tip_id`); events. All are drop files for the
  Hub; the Scraper sends Render nothing.
- `status.py` - the heartbeat, `data/status.json`, the per-site budget summary.
- `rest.py` - a job's rest: a parser raises `Rest(until, why)` when its page
  has nothing to read for a while, and a listing job rests when no match on
  its listings kicks off within its window (`runner._window_links`); the
  runner closes the job's tabs and loads nothing for it until then (clamped
  to a minute..a week), shows it `paused` with the reason, and keeps it in
  `data/resting.json` across restarts; a changed or removed target loses it
  (and a change to its `within_h`), `python -m scraper wake <target>` ends it.
- `fetched.py` - each fetch job's last good read (a parse that read rows),
  `data/fetched.json`, written atomically: what a restart draws the job's
  next run from (`Runner._fetch_due`), so a restart is not a load.
- `joblog.py` - one log per job, `data/logs/<name>.log` (JSON lines; past
  512 KB cut to its newest lines in 256 KB, 1000 at most); the runner
  writes it through `Runner._say`, `targets log` reads it. A write never
  raises. A site's block is a line in each of its jobs' logs
  (`_on_blocked`, never a url).
- `ids.py` - the one tip id, minted here when rows reach the Hub sink (a copy
  of `hub/puntmore_hub/ids.py`; a test in each suite pins the same vector).
- `parsers/` - one module per page kind; rows only from what the page shows.
  `bb_builder` keeps the Bookie Bashing bet builder up and reads nothing
  (`bb_saves.bring_up`, shared): a page to `look` at, the start of the
  preset-driven bet-builder job. `tc_reload` reads Team Casino's
  reload-offers dashboard, one row per offer, from the page's HTML; it
  clicks nothing (every control there changes Callum's account). `bb_2up`
  reads Bookie Bashing's Early Payout Tracker for `/api/2up/bb`: the rows
  from the newest ratings payload the page received (known by its shape),
  the table as the check that the page drew it; it posts nothing when the
  payload is over 3 minutes old, the settings changed, or the board is short,
  and on a healthy read with no game within `games.within_h` it rests as
  `bb_saves` does. `dom.py`
  is a small DOM over a page's HTML (the standard library's html.parser),
  so such a parser's tests need no browser. A parser may return
  `rows.Rows` (a list with `said`): a line about the page, which the runner
  shows in the job's status and logs when it changes (`_said_read`; a job
  with no `post` also shows how many rows it read).
- `targets.py` - the only writer of targets.toml (CLI, Console, HubAI).
- `config.py` - settings and target validation (`_target`, the clamps).
- `behaviour.py` - how the Scraper behaves, as one catalogue (`SETTINGS`): each
  site's limits and quiet hours, the cool-off, sign-ins and back-off, the
  spacing and jitter of loads, arriving on a page, clicks, reading down, the
  BB builder's pacing, and how far ahead games are read (`games.within_h`,
  `games.look_again_s`); each with default, unit, bounds and a line. Every
  module reads `behaviour.get(key)` when it acts and holds no number of its
  own for these. Overrides: `<data>/behaviour.toml`, only non-defaults,
  written only by `set_`/`reset` (lock, check, atomic save; `python -m
  scraper behaviour`, the Console), read at start by `load` and again by
  the runner within 60 s of a change, no restart (a bad file or key is
  reported once per edit and the values in use kept, never raised; each
  value that moved is a line in `data/logs/behaviour.log`).
- `sites.py` - per-site rules in code: what a block means, whether it needs a
  login, what marks a logged-out page where the url does not (Team Casino's
  login form, or the reset form shown in its place), Paddy's market-list url (its limits are in `behaviour.py`).
- `guard.py` - every load asks it first: budget, cool-off, quiet hours (SQLite);
  a request's load (`ask.py`) skips only the budget and quiet-hours checks;
  built from `behaviour.py`'s limits, which it keeps a copy of (`use`). Its
  record of loads (two days) also gives a fetch job's last load (`last_load`).
  A request's load (`ask.py`) is recorded in a table of its own,
  `requested_loads` (`requested`), never in `loads`, so never in `spent`,
  `can_load` or `last_load` (26 Nov 2026). `loads` keeps its three columns:
  the service of the version before inserts three values into it.
- `ask.py` - `python -m scraper ask '<json>'`: a request for the running
  service, `data/requests/ask-<id>.json`, answered by `Runner._ask` in
  `data/requests/done/ask-<id>.json` (`running` with the items so far, then
  `done`, `refused`, `stopped`, or `interrupted` when the service restarted
  mid-request; never run twice). One at a time (the runner's own request,
  not a file): another is refused. A request file goes only once a result
  for it is written; anything else there is refused or moved aside to
  `requests/rejected/`. Kind `read`: Paddy Power match pages, by a Paddy
  listing job and a kick-off range or by address (at most 60). A job's
  listing is read where the sweep holds it open and fresher than its
  `relist_min`, else loaded by the request and closed once read (the
  result's `listings`). Each page is loaded in a tab of its own, one load a
  tick; a page that ends off Paddy's hosts is failed unread; a parser from
  `READ_PARSERS` (parsers that click nothing) runs over it; it is saved
  with its payloads (`keep`); the tab is closed. Page urls are written
  without their query (`bare`). Its loads keep the pacer and the hosts rule
  and stop on a cool-off or back-off; they skip Paddy's budget and quiet
  hours and the 48-hour window (Callum, 26 Nov 2026). On the page it clicks
  only the cookie banner. Results and saved pages go after 14 days
  (`prune`). Localhost counts as Paddy's (`config.paddy_page`) only under
  `config.LOCAL_PADDY`, which the tests set.
- `look.py` - `python -m scraper look <job> '<steps>'`: a few plain steps
  (click a named control with `browser.click`, set a select, type in a
  field, wait, save) on a job's open page, answered by `Runner._look` from
  `data/requests/look-<job>`, then the page saved to `data/dumps/`; a step
  that moves the page stops it and is counted as a load; refused for Paddy
  Power jobs.
- `browser.py` - the one Chrome: launch, block/logout detection (per site: a
  site's own mark from `sites.py`, else a visible password field or a login
  path), clicks
  (their waits, steps and aim: `behaviour.py`). A click is aimed, then checked: if the element moved or something else is under the pointer at the press, it is aimed once more and otherwise not pressed (12 Nov 2026).
- `login.py` - one login `Form` per site that needs one (Bookie Bashing, Team
  Casino), each site's login from `.env`, one sign-in per logout on the page
  already open (at most `login.max_signins` in `login.window_h` hours for each
  site, 3 in 6 by default, then it waits for a person), and `data/login.json`
  (each site's sign-in times, written before each is sent; the last attempt
  failed: nothing is tried again on that site until logged in or `resume
  <site>`). A two-factor box shown is a failed sign-in; no code is typed. Any
  request naming a password field in its url (a form Chrome would send itself
  as a GET) is aborted inside Chrome, for every site; a POST, the password in
  its body, goes. `safe_url` keeps such a url out of what is written.
- `capture.py` / `alerts.py` - what a page receives (and its disk samples;
  never the sign-in's own response) / the alert webhook and log.
- `standing.py` - when the Scraper is not working it says so, once, and once
  when it clears: a stalled loop (`__main__.Watchdog`, on its own thread,
  never the browser; `data/stalled.json` for the next start), a job failing
  the same way, a running job with pages open handing over no rows
  (`JobAlerts`, fed by the runner; `data/alerted.json`, so a restart neither
  repeats nor loses one). Thresholds: the "Alerts" settings (`behaviour.py`).
  Nothing in it raises: a write or a send that fails is printed and dropped.

## Rules
- **Finder's sources are Scraper targets** (5 Nov 2026, Callum: one Chrome,
  one budget, one alert path). `site = "generic"` may name any host except
  Paddy Power's, Bookie Bashing's and Team Casino's; its budget is `behaviour.py`'s (60/h, 400/day, no quiet hours,
  by default; a block = 24 h cool-off + `resume generic`, in `sites.py`). A generic target is `fetch`
  mode: loaded, parsed once, closed. Its parser follows the same contract as
  every other parser and emits TIPS (event, market, selection, optional
  advised_price / advised_stake_units / tip_time / expiry / origin). With
  `post = "hub:tips"` the rows go to `data/hub/tips/` as one file per run;
  `min_rows` breaches and blocks go to `data/hub/events/`. The Hub's
  `puntmore inbox` reads both. Never let a generic target reach Paddy Power,
  Bookie Bashing or Team Casino (`config.OWN_SITE_HOSTS`); never add a second
  budget anywhere. The one exception (Callum, 26 Nov 2026): a request's loads
  (`ask.py`) are recorded apart and are outside Paddy's hourly and daily
  budget and its quiet hours; they still keep the gaps between loads and
  stop on a cool-off.
- targets.toml is edited only through `scraper/targets.py` (the CLI, the
  Console, HubAI's scraper-manager); never by hand while the Scraper runs.
  Validation stays in `config._target`; `targets.py` must not grow its own
  copy of a clamp, host or site rule.
- **The Scraper service is a SEPARATE service**: ONE real Chrome on the Hub, the systemd user
  service `puntmore-scraper` (`deploy/native/`), on its own virtual 1920x1080 screen, the user
  service `puntmore-screen` (a headless sway with its own Xwayland; VNC on `127.0.0.1:5910`
  only), not on Callum's desktop; updated by
  `puntmore-update.timer` with the rest of the Hub, so the sites see a
  person's Chrome: the box's GPU and fonts, on that screen (in the container Chrome
  had no WebGL at all). Docker (`.github/workflows/scraper-image.yml` → GHCR,
  plain `docker run`, the no-docker-compose rule untouched) is only the
  fallback for a box with no screen: Xvfb exists only there, and the
  window and language flags apply only there (`SCRAPER_IN_CONTAINER=1`) and
  headless. Never run both: they share one profile, one budget, one data dir.
  It is NOT part of the Render deploy; nothing in it imports the API. Its
  Chrome keeps every target's page OPEN and listens to what the page receives, so after
  the first load most targets cost no requests. WHAT it reads is config, and
  the config is ONE file: **`targets.toml` on the Hub** (`data/targets.toml`,
  or the file `SCRAPER_TARGETS` names), changed through `targets.py`; nothing
  on Render configures the Scraper. A target is a page, or a
  competition listing (and, by `listings`, further competitions' listings in
  the same job) whose match links are opened (in-page tabs, parser, ingest
  path). The Scraper checks the file once a minute and applies an edit
  without a restart; a file that will not load is reported and the running
  targets are kept. It writes its state to `data/status.json` once a minute,
  which the Hub reads; it sends Render nothing; its prices go through the Hub.
  Timings are clamped to the scraper's own floors (`config._target`).
  What a block means is code (`sites.py`: a Paddy block = every Paddy page
  closed + 24 h cool-off needing `resume`; Bookie Bashing and Team Casino
  back off, their tabs left open). Each site's budgets and quiet
  hours (Paddy 15/h, 01-07, by default) are `behaviour.py`'s settings, which
  only Callum changes (the Console's Behaviour pane or `python -m scraper
  behaviour`; every change logged); no `targets.toml` edit can loosen them.
  A request's loads (`ask.py`) skip Paddy's budget and quiet-hours checks
  and no other: the gaps between loads, the hosts rule and the cool-off
  hold for them. A request also reads games outside the 48-hour window,
  and may load its job's listing itself (Callum, 26 Nov 2026; the register).
  It detects challenges and logouts (also on an already-open page: 3+
  refusals in 10 min, Cloudflare, logout) and never answers a challenge — no
  fingerprint spoofing, no solvers, no proxies, no user-agent or navigator
  overrides; do not add any. A logout on a site its jobs need a login for
  (Bookie Bashing, Team Casino) is the one thing it answers, by signing in
  once with that site's login in `.env` (`login.py`; never a two-factor
  code). Chrome
  is configured coherently, as a person's
  Chrome: the one departure from Playwright's defaults is that its
  `--enable-automation` test flag is not passed and AutomationControlled is
  off (Callum's decision, 26 Oct 2026 — `navigator.webdriver` false, no
  infobar); `python -m scraper selfcheck` prints what a site sees. Page loads are spaced per site and
  across sites (`behaviour.py`: `<site>.load_gap_s`, `load.any_gap_s`); jitter is drawn once per load. Parsers
  (`scraper/parsers/`, one module each) are written only from what the real
  pages were seen to do: `bb_saves` drives the Bookie Bashing bet builder
  (above; a Bookie Bashing job like any Callum adds, `docs/decisions.md`); `pp_most` reads Paddy's most-corners /
  most-cards three-ways (written from the 29 Oct 2026 capture); `pp_listing`
  reads the kick-off times off a Paddy listing (written from the 12 Nov 2026
  dump; the coming week's headers, "Tomorrow" and a bare weekday, from the 19 Nov 2026 dump); `tc_reload` reads Team Casino's reload-offers dashboard (written
  from the signed-in page `look` saved on 13 Nov 2026); `bb_2up` reads Bookie
  Bashing's Early Payout Tracker for 2-Up (written from the `bb-2up` job's
  dumps and capture of 18 Nov 2026; kept open, posted to `/api/2up/bb`). Where `tc_reload`'s rows go is
  not decided (Callum's design is open), so a `teamcasino` job runs it with
  no `post`: rows read and counted, sent nowhere. That job is `mode =
  "fetch"` (load, read, close, `every_min` inside a `window`, `wait_for` the
  offers): kept open, the page cost ~29 of Team Casino's 30 loads a day.
  After a restart a fetch job's next run is its last good read
  (`fetched.py`) plus its interval, never sooner than `fetch.retry_s` after
  its last load (`Runner._fetch_due`); its window opening, `wake`, being
  added, enabled or changed run it at once; a fetch load that signs in gets
  one load at once, then `fetch.retry_s`. Every job that reads games reads only those kicking off within 48
  hours (`games.within_h`, or a job's own `within_h`; 0 is no limit): with
  none that near it rests (`rest.py`; Callum, 11 and 12 Nov 2026). A request
  (`ask.py`) is not a job: it may read games further out (Callum, 26 Nov 2026).
