# scraper/ — the Scraper service

The Scraper service: the one real Chrome on the Hub, on its own virtual screen. It reads every
page any tool needs and fills what OddsPapi lacks: Paddy Power pages kept
**open** (it listens to what they receive), Finder's sources loaded and
closed, Bookie Bashing's bet builder for its jobs (`bb-saves` the first) and its
Early Payout Tracker for 2-Up (`bb-2up`). After
the first load the site's own scripts keep each page fresh, so most targets
cost no further requests. Runs as the user service `puntmore-scraper` on its
own virtual screen, the user service `puntmore-screen` (below), updated by
`puntmore-update.timer` with the rest of the Hub. Docker is the fallback for a
box with no screen.

**What it reads is `data/targets.toml` on the Hub**, changed only through
`scraper/targets.py` (the Console, the CLI, HubAI); an edit applies without a
restart. Its state goes to `data/status.json`, which the Hub reads. Its rows go
to Scraper control as drop files (`hub/PROTOCOL.md`), Paddy's prices too: a
price target's rows are `scraper_prices` files that the Hub's inbox posts to
Render. The Scraper sends Render nothing, and nothing on Render configures it.

The parsers (`parsers/`, one module each) are written only from what the real
pages were seen to do. `bb_saves` DRIVES Bookie Bashing's Bet Builder (select
the game, type each line, read "Total Odds" — see its docstring; needs the
box's Chrome logged in, which the Scraper does itself with the login in
`.env`, below). Like every job that reads games, it reads only games
kicking off within 48 hours (`games.within_h`, below; Callum, 11 and 12 Nov
2026); with none that near it rests: its page is closed and nothing loads
until 48 hours before the next game it saw, or a day (`games.look_again_s`),
whichever is sooner. `status` shows it `paused` with the reason; `wake
bb-saves` ends the rest by hand.
`bb_builder` only brings the same Bet Builder up and keeps it up ("Update
Data" until "Standard View", again every `bb.update_every_s`, the logged-out
check: `bb_saves`' own code, `bring_up`) and returns no rows; it never applies
the 48-hour window and never rests. A job with it exists so the builder can be
looked at with `look` (below). It is the start of the general preset-driven
bet-builder job (`docs/superpowers/specs/2026-11-12-wop-check-design.md`).
`pp_most` reads Paddy's most-corners / most-cards three-ways (written from
the 29 Oct 2026 capture; see its docstring).
`tc_reload` reads Team Casino's reload-offers dashboard
(`https://www.teamcasino.com/reload-offers-dashboard`, signed in; written from
the page `look` saved on 13 Nov 2026, 121 offers). One row per offer: its id
(`data-offer-id`, the id Team Casino's own `?offerid=` links use), casino
(the page's "Select casino site" name for the block's `data-casino-id`; the
block's `data-casino-name` is wrong on every offer), section ("Low Risk"),
title, EV, EV per hour, bankroll, minutes, expiry (the text as shown, and
`data-offer-expires`: 00:00 UTC of the last day it is listed for), the game
plan as steps in the page's own words, the note boxes, every link in the
plan and GO TO OFFER, the games named in bold with an RTP in brackets, a
promo code where the plan says "promo code", and the flags the page shows
(hidden, pending, avoid, favourite). It reads the page's HTML and clicks
nothing: every control on that page changes Callum's account. Hidden,
pending and avoided offers are rows with their flag. A page still loading,
or with no offers, gives no rows and says why; when the page's "Offers
available" count differs from the rows read the job's log says so. Where
its rows go is not decided yet, so a `teamcasino` job runs it with no
`post`: the rows are read and counted (the job's status detail: "121 rows
read, sent nowhere (no post)"), kept in `data/latest/<job>.json` like every
job's last parse, and handed to nobody. The job is a `fetch` job: a page of
daily offers needs no page kept open, and kept open it cost about 29 loads a
day (a fresh page every `recycle.hours` and at every restart), Team Casino's
whole 30. As a fetch job, `every_min = 100` inside `window = "08:00-23:00"`
is 8-10 loads a day, with `wait_for = "#offers-main-wrap table[data-offer-id]"`
so it reads the offers, not the "Searching for most profitable offers.."
notice (`targets.example.toml`, `tc-reload-offers`).
`bb_2up` reads Bookie Bashing's Early Payout Tracker
(`https://www.bookiebashing.net/trackers/early-payout-tracker/#/`, signed in)
for 2-Up. Written from what the `bb-2up` job held on 18 Nov 2026: two saved
pages (194 rows each) and the payloads the page received. The page fetches a
ratings payload when it loads and about once a minute, and draws the table
from it; the table's "Last update" and "last refresh" text is written when
the table is drawn and never ticks. So the rows come from the newest ratings
payload in the capture (known by its shape, never by its url), and the table
is the check that the page drew it. One row per selection: the book
("bet365"), the event and its two teams, the selection and whether it is the
home or away team, the book odds and the lay as the payload has them (2.625,
where the table shows 2.63), the EV rating (100 is break-even) and N.Odds to
two places, the kick-off in UTC, the seconds since the record last changed
(now less its `changed`), and when it was read. Only games kicking off within
`games.within_h` hours (48 by default, the "Games" setting every job that
reads games follows) are rows. When the tracker is read cleanly and no game
on it is that near (midweek, usually), the job rests as `bb-saves` does:
its page is closed and nothing loads until 48 hours before the earliest game
the tracker lists, or a day (`games.look_again_s`), whichever is sooner; a
tracker with no selections rests 30 minutes, as an empty answer may pass. `status` shows it `paused` with the
reason; `wake bb-2up` ends the rest by hand. It clicks nothing: the page refreshes itself,
and its filters are Callum's saved settings on Bookie Bashing. Those other
than the three below (Competition, EV min, KO window, Odds above and below)
narrow the table and so the board: that is deliberate, the job reads the
tracker as Callum has it set, and it costs bets, never a wrong price.

Each parse that reads rows (once a minute) is one `scraper_prices` file for
`/api/2up/bb`: every selection on the tracker inside the window, which the
Hub posts within about 10 s, so Render can replace what it held. It posts
nothing, and the job's log says why, when no ratings payload has arrived yet,
when the newest is more than 3 minutes old (the page has stopped
refreshing), when the settings are not the ones the rows are read for
(Metric lay, Book bet365 alone, the fixed-trigger 2-up model, as the payload
shows them; a record with no lay rating to confirm the model by counts as
changed), when the page fetched fewer selections than the tracker has
(the payload's limit), when the table, its "N/M shown" line and the payload
disagree, when there is no tracker table (logged out, still loading) or its
header has changed, or when more than a tenth of the records cannot be read.
A record it cannot read is skipped and counted, never guessed.

A page that has stopped refreshing receives nothing, so its capture goes
quiet and the runner reloads it once it has been quiet for the job's
`stale_min` and loaded that long ago. 10 minutes is the floor
(`config._target`). With no `every_min` the page is otherwise reloaded only
by `recycle.hours` (a fresh page about every 30 minutes, about 2 loads an
hour and 48 a day of Bookie Bashing's 8 an hour and 120 a day); a page that
stays stuck is reloaded at most every 10 minutes, 6 an hour, and the guard
refuses any load past the budget. A rest pauses the job's no-rows alert
(`alerts.no_rows_min`, 60 minutes), so a quiet midweek raises none; it still
fires for a page that stays up and posts nothing for an hour (a stuck page,
changed settings, a short table). `min_rows` is left unset: a resting job
reads no rows by design. The job (`targets.example.toml`, `bb-2up`):

```sh
python -m scraper targets add --name bb-2up --site bookiebashing \
    --url 'https://www.bookiebashing.net/trackers/early-payout-tracker/#/' \
    --parser bb_2up --post /api/2up/bb --stale-min 10
python -m scraper targets set bb-2up --post /api/2up/bb --every-min none --stale-min 10   # the capture-only job, posting
```

## What it will and won't do
- Opens pages on `paddypower.com`, `bookiebashing.net`, `teamcasino.com` and
  Finder's `generic` sources (never one of the other three), within budgets
  (`behaviour.py`; these are the defaults): Paddy 15 loads/h and 120/day,
  Bookie Bashing 8/h and 120/day, Team Casino 6/h and 30/day, generic 60/h
  and 400/day.
  `targets.toml` cannot raise them; only Callum changes them, from the
  Console's Behaviour pane or `python -m scraper behaviour set` ("Changing
  how it behaves", below); HubAI does not. A request (`ask`, below) is
  outside Paddy's: its loads are counted apart (Callum, 26 Nov 2026).
- No Paddy traffic between 01:00 and 07:00 (Europe/London; `quiet.hours`,
  by default), except a request's (`ask`, below). Its pages are closed for
  those hours.
- Paddy 403/429 or Cloudflare check → every Paddy page closes and a 24 h
  cool-off starts. It also needs `resume`, and you get an alert.
- Bookie Bashing logout → it signs in once, on the page already open, with the
  login in `.env` (`login.py`), then reloads the tool through the guard. At
  most 3 sign-ins in 6 hours, then it waits for a person (a site that keeps
  ending the session, e.g. when you log in on another device). A failed
  sign-in (refused, no form, still logged out after it, a form the site's
  script did not send, or the 3-in-6-hours limit), a logout with no login in
  `.env`, or a check → one alert and a back-off, with the tab left open so
  you can sort it on the Scraper's screen (over VNC, "Its own screen" below;
  in Docker, the container's VNC). After a failed
  sign-in it does not try again until it sees the site logged in or you run
  `resume bookiebashing`, restarts included. Each sign-in is written to
  `data/login.json` before the form is sent; if that file cannot be written
  it does not sign in at all and the alert says "cannot write login.json"
  (once per start of the service).
- Team Casino logout (its members page keeps its url and shows the login
  form) → the same: it signs in once on the open page with `TC_USERNAME` and
  `TC_PASSWORD`, the page reloads itself, and the members url is loaded again
  through the guard; both count as loads. Each site's sign-ins are counted on
  their own (3 in 6 hours each), and one site's failed sign-in never stops
  another's. Its refusal text has never been seen, so none is read: the form
  still showing on the next read is a failed sign-in ("still logged out after
  signing in"), as is the site asking for a two-factor code (never typed).
  After that, exactly as Bookie Bashing: one alert saying why, a back-off
  with the tab left open, and no other sign-in until it sees the site logged
  in or you run `resume teamcasino` (which gives it one more try at once).
  A logout with no login in `.env`, or a block, is the same back-off. Its
  page reads as logged out while its login form, or the reset form that
  "Forgot Password?" shows in its place, is on screen.
- A block is also caught on a page that is already open: 3+ refused
  requests in 10 min, a Cloudflare page, or a logout. Every block alert
  carries a screenshot (also in `data/blocks/`). A Paddy cool-off alerts once
  when its hours are served and it waits on `resume`, and once when it runs
  again.
- It says when it is not working (`standing.py`; Callum, 12 Nov 2026): one
  alert, and one when it clears, for a loop that has made no progress for 3
  minutes ("stalled: ...", from the watchdog's own thread; "running again"
  if it ticks before the watchdog restarts it at 10 minutes, and "restarted
  by the watchdog" at the next start if it does, from `data/stalled.json`),
  a job whose parser has failed the same way for 15 minutes ("<job>: failing
  for 15 min: <first line>", then "reading again" when a parse works or the
  job rests), and a running job with a page open that its parser reads (a
  matches job: a match tab) that has handed over no rows for 60 minutes
  ("<job>: no rows for 60 min (<its status detail>)", then "delivering
  again"; not sent while that job's failing alert stands; a fetch job has
  `min_rows` instead). The no-rows clock counts only time the job is running
  with such a page open: a rest, quiet hours, a block or a restart pause it,
  a delivery starts it again. The thresholds are the "Alerts" settings
  (`alerts.stuck_s`, `alerts.failing_min`, `alerts.no_rows_min`; 0 turns
  one off). They go where a block's alert goes (`data/alerts.log`, the
  Console, the webhook) and a job's is a line in its log; a job's alert
  state is kept in `data/alerted.json`, so the service's own restart neither
  says one again nor loses a clock. The standing one is the job's `alert` in
  `status.json`.
- Paced like one person with a few tabs: page loads are spaced (Paddy 90 s+,
  Bookie Bashing 60 s+, and 20-45 s between any two), so nothing opens in a
  burst at startup or after quiet hours; timed reloads are jittered once per
  load. Market tabs are clicked through the mouse, then read down.
- It never answers a challenge and never fakes a fingerprint. It signs in to
  the sites its jobs need (Bookie Bashing, Team Casino), each with Callum's
  own login from `.env`, and never types a two-factor code; a person is
  needed there only after a failed sign-in or for a challenge. On the Hub Chrome keeps its
  own defaults: en-GB from the box's locale, London time, the box's own GPU
  and fonts, on its own virtual 1920x1080 screen. In Docker, with
  no screen, Chrome gets a 1600x1000 window on Xvfb's 1920x1080 screen and
  the en-GB language list by flag. Either way it is started as a person's
  Chrome, not a test run (Playwright's `--enable-automation` is not passed);
  `selfcheck` shows exactly what a site sees, the WebGL renderer included.
- **It runs on the Hub's residential connection**, which is less likely to be
  challenged than a datacentre IP. If Paddy challenges it anyway, the
  scraper stops and tells you. It does not work
  around the challenge.

## Finder's sources
Any other site, as a `generic` target (see `targets.example.toml`): loaded,
parsed once and closed, under its own budget in `behaviour.py` (60 loads/h,
400/day across all generic targets, 20 s+ apart, never Paddy or Bookie
Bashing). The parser returns tips; `post = "hub:tips"` writes them to
`data/hub/tips/` for the Hub's inbox, which stores them for HubAI to judge for
Finder. Too few rows (`min_rows`) or a block raises an alert here and an event
in `data/hub/events/` that the Hub turns into an escalation for HubAI.

## Changing targets
A target is a job: a page the Scraper reads, on a schedule (`every_min` or
`stale_min`), on chosen `days`, inside a `window`, until an end date
(`until`). `targets.toml` is changed only through `scraper/targets.py`: this CLI, the
Console and HubAI's scraper-manager all call it, so there is one config
store. It works on `SCRAPER_TARGETS`, else `SCRAPER_DATA_DIR/targets.toml`
(`--file` for another), and needs no browser.

```sh
python -m scraper targets list                 # every target, joined to status.json
python -m scraper targets show pp-epl-most --json
python -m scraper targets add --name racing-tips --site generic \
    --url https://tips.example.org/racing --parser racing_tips --post hub:tips \
    --every-min 360 --min-rows 2 --trust-weight 0.5 --disabled
python -m scraper targets set pp-epl-most --every-min 15 --priority 2 --window 07:00-23:30
python -m scraper targets set racing-tips --window none     # "none" clears a field
python -m scraper targets set racing-tips --days fri,sat --until 2026-11-30
python -m scraper targets set racing-tips --days none --until none   # every day, forever
python -m scraper targets set pp-epl-most --matches '{"max_tabs": 2, "market_tabs": ["Corners"]}'
python -m scraper targets set pp-epl-most --matches '{"link_re": "-v-", "max_tabs": 3,
    "market_tabs": ["Corners", "Cards"],
    "listings": ["https://www.paddypower.com/football/spanish-la-liga",
                 "https://www.paddypower.com/football/italian-serie-a"]}'
python -m scraper targets set pp-epl-most --within-h 72   # its own window; none = games.within_h
python -m scraper targets enable racing-tips
python -m scraper targets disable racing-tips
python -m scraper targets remove racing-tips
python -m scraper targets log racing-tips -n 50   # the job's own log, newest last
```

- `--matches` replaces the whole `[target.matches]` table: a field it leaves
  out goes back to its default, so give every field the job uses (as the
  `listings` example above does).
- `--listings url,url` changes only a matches job's further league pages and keeps the rest of
  its matches table (`none` clears them). The Console's edit form has the same field, "more leagues".
- `--within-h 72` gives a job its own window: only games kicking off within 72 hours are read
  (0 to 720; 0 is no limit; `none` goes back to the main setting, `games.within_h`). `show`
  prints it and `list` says "games within 72 h" (or "games: no limit") on the job's line. The
  Console's edit form has it as "only games within (hours)".
- Every change is validated as a whole by `config._target` (sites, hosts,
  clamps) before anything is written. A value the clamps would change is
  refused, not quietly raised: `--every-min 2` is an error, not 5. A refusal
  exits 2 with the reason (`{"error": "..."}` with `--json`).
- `--json` prints machine output: `list` the `targets.summary()` list,
  `show`/`add`/`set`/`enable`/`disable` that target's entry, `remove`
  `{"removed": name, "targets": [names left]}`. Each entry is every field of
  the target plus `status`: the running Scraper's last report on it from
  `status.json` (`state`, `detail`, `last_load`, `last_parse`,
  `rows_posted`, `alert`, `reported_at`), or `null` when it has none (disabled, or
  not reported yet), and `schedule`: the job's schedule in words
  (`targets.schedule_words`, "every 15 min · Fri, Sat · 07:00–23:30 · until
  30 Nov 2026"), which `list` prints too.
- Saves are atomic (temp file + rename in the same directory) under an
  `flock` on `targets.toml.lock`, so the Console, the CLI and HubAI cannot
  interleave and the running Scraper never reads half a file. The file is
  rewritten whole, so comments are lost: the first save keeps the
  hand-written file as `targets.toml.bak`.
- The running Scraper picks a change up within 60 s (its re-read of the
  file). A change to only `every_min`, `stale_min`, `near_min`, `near_hours`,
  `priority`, `window`, `days`, `until`, `min_rows`, `trust_weight` or `within_h` is applied in place: the open tabs stay and
  are rescheduled (a timed reload is redrawn from the last load), so it
  costs no load. A change to `url`, `site`, `mode`, `parser`, `post`,
  `wait_for` or `matches` closes the target's tabs and reopens them on the
  next tick. Disabling or removing a target closes its tabs.
- `priority` (higher goes first when a site's hourly budget is short),
  `window` (`"HH:MM-HH:MM"` local time; outside it the target's tabs close),
  `days` (`--days fri,sat`: only on those days; none = every day) and `until`
  (`--until 2026-11-30`: the last day it runs) are acted on by the runner
  (`schedule.py`). Off its days a job is `paused`, "not a run day"; after
  `until` it is `finished`, "ended <until>"; either way its tabs close.
- Each job keeps its own log, `SCRAPER_DATA_DIR/logs/<name>.log`
  (`joblog.py`): every line the runner prints about that job (window and
  days, kick-off sweep, load and parser failures, a `min_rows` breach and
  its recovery, market tabs, retired match tabs, dumps, each step of a look, signing in) and its
  deliveries ("3 row(s) handed to the Hub (hub:tips), 2 left out"): a
  delivery is logged when what was handed or left out changes, else at most
  every 10 min. A block of its site is a line in every job of that site,
  never with the page's url: "blocked: <reason> - backing off" (Bookie
  Bashing) once per block, as its alert, or "blocked: <reason> - every page
  closed, cooling off 24 h, then needs resume"; and "no longer blocked" when
  a back-off ends (unless "signed in" already says so). One
  `{"ts": <epoch>, "msg": "..."}` per line, a message cut at 2000
  characters. Other site-wide lines (paused, a cool-off served or over,
  `config: ...`) stay in the journal only. Past 512 KB a
  log is rewritten to its newest lines that fit in 256 KB, 1000 at most.
  `targets log <name> [-n 50]` prints it (`--json`: `{"name": name, "lines": [{"ts", "msg"}, ...]}`);
  `targets remove` removes it.

## Changing how it behaves
How the Scraper behaves - each site's limits, quiet hours, the cool-off and
back-off, every gap and jitter between loads, how it arrives on a page, clicks
and reads down it, the Bookie Bashing bet builder's pacing, and how far ahead
a game is read (`games.within_h`, 48 h, and `games.look_again_s`, a day; a
job may carry its own `within_h`, below) - is one catalogue, `scraper/behaviour.py`: every setting with its default, unit,
bounds and a line saying what it is. Read that file first when something has
gone wrong. What a block means, the allowed hosts and never answering a
challenge are not settings, and nothing about what the browser shows a site
is. These settings are Callum's (the register, "Scraping conduct"): he
changes them from the Console's Scraper tab ("Behaviour", which calls the
same library) or with the commands below; HubAI does not change them.

```sh
python -m scraper behaviour                        # every setting: group, key, value, default; * = changed
python -m scraper behaviour set paddypower.per_hour 10
python -m scraper behaviour set load.any_gap_s 30-60      # a range: low-high
python -m scraper behaviour set generic.quiet_hours true
python -m scraper behaviour set quiet.hours 1-7           # local hours, start-end
python -m scraper behaviour reset paddypower.per_hour     # back to its default
python -m scraper behaviour reset --all
python -m scraper behaviour --json                 # {"file", "changed", "problems", "settings": [...]}
```

- A change is written to `SCRAPER_DATA_DIR/behaviour.toml`: flat
  `"key" = value` lines, only the settings that differ from their default,
  each under a comment with its default and meaning. It is written only
  through `behaviour.py` (this CLI): checked against the setting's kind and
  bounds, under an `flock` on `behaviour.toml.lock`, saved atomically. A value
  outside the bounds, or an unknown key, is refused with a message naming the
  setting (exit 2; `{"error": "..."}` with `--json`), and nothing is written.
- The running service takes a change up within 60 s, without a restart: it
  checks the file once a minute (with `targets.toml`) and loads it when it
  has moved; a deleted file is every setting back to its default. The guard
  takes the new limits at once; each setting that moved is one line,
  `key: old -> new`, in the journal (`behaviour: ...`) and in
  `data/logs/behaviour.log`. A value already drawn from a range keeps until
  its next draw (a page's next timed reload or fresh page, the next round of
  market-tab clicks, a site's back-off, the next load's spacing, the
  service's own restart time). A file that does not load keeps the values in
  use, and an unknown key or a refused value keeps that setting's value; each
  is said once per edit, and nothing stops. `status.json` carries
  `"behaviour": {"changed": <settings not at their default>, "problems": [...]}`.
  Every other command reads the file when it runs.
- `--json` on `set` and `reset <key>` prints that setting's entry (`key`,
  `group`, `kind`, `value`, `default`, `lo`, `hi`, `unit`, `says`,
  `changed`); `reset --all` prints `{"reset": [the keys that were changed]}`.

## Running on the Hub (the default)
Since 5 Nov 2026 the Scraper runs on the Hub itself, not in a container, so the
sites see a person's Chrome: real GPU, real fonts. Measured that day, Chrome
157 in the container reported no WebGL at all (Xvfb, no GPU) and the
container's fonts; the same binary on the Hub reports `ANGLE (AMD, AMD Radeon
8060S Graphics (radeonsi strix_halo ACO), OpenGL ES 3.2)`. Since 11 Nov 2026
its screen is its own virtual one, not Callum's desktop (below).

What it needs: `sway` and `wayvnc` (`sudo apt install sway wayvnc`),
`/usr/bin/google-chrome`, the venv `~/.venvs/puntmore` with
`requirements.txt` installed, the Hub's user lingering (`loginctl
enable-linger`, so the user services run with nobody logged in) and in the
`render` and `video` groups (the GPU), and `/opt/puntmore-scraper/.env` and
`/opt/puntmore-scraper/data/targets.toml` as below.

```sh
docker stop puntmore-scraper                       # if the Docker Scraper runs: never both
scraper/deploy/native/install.sh                   # from the box's checkout, ~/Projects/puntmore
crontab -l | grep -v puntmore-scraper/update.sh | crontab -   # the container's updater
```

`install.sh` is idempotent: it checks `sway` and `wayvnc` are there, gives
your user `/opt/puntmore-scraper` (sudo, once; the container's files belong to
its uid), copies `deploy/native/puntmore-screen.service` and
`deploy/native/puntmore-scraper.service` into `~/.config/systemd/user/` with
the checkout it was run from in their paths, enables both (`default.target`;
an older install's `graphical-session.target` link is dropped first), and
restarts the screen, then the Scraper. It refuses while the container
`puntmore-scraper` runs, and so does the unit (`deploy/native/preflight.sh`,
which also clears the profile lock the container's Chrome left behind);
`deploy/run.sh` refuses the other way round.

The unit runs `~/.venvs/puntmore/bin/python -m scraper run` with the data in
`/opt/puntmore-scraper/data` (heartbeat, `status.json`, budget, profile,
targets, the Hub's drops), so the Hub reads it where it always has. It
restarts on any exit (the Scraper exits on its own every 1.5-2 h for a fresh
driver), gives Chrome 90 s to close cleanly, and is capped at 4 GB.

**Updates:** a merge to `main` that touches `scraper/` reaches the box through
`puntmore-update.timer`, which pulls the checkout and restarts
`puntmore-scraper` (and installs `requirements.txt` when it changed). A change
to either unit file needs `install.sh` run again; a change to `screen.conf`
needs `systemctl --user restart puntmore-screen` (which restarts the Scraper
too).

**Its own screen** (Callum, 11 Nov 2026). The Scraper's Chrome runs on a
virtual 1920x1080 screen, not on Callum's desktop, so nothing it does (start,
open a tab, take a screenshot) can take his screen, and it runs whether or not
he is logged in. Two user units:

- `puntmore-screen.service`: a headless `sway` (`WLR_BACKENDS=headless`) on
  the box's real GPU with its own Xwayland, configured by
  `deploy/native/screen.conf`: one output, no borders, and `wayvnc` on
  `127.0.0.1:5910` (this machine only: it has no password). It writes its
  Xwayland `DISPLAY` to `$XDG_RUNTIME_DIR/puntmore-screen.env` and counts as
  started only once it has. It never sees the desktop's display variables.
- `puntmore-scraper.service`: requires the screen and starts after it, reads
  `DISPLAY` from that file, and is restarted with it. `browser.py` starts
  Chrome on X11 as before; nothing about the browser changes, and a page
  sees the screen it is really on (measured: screen 1920x1080, dpr 1, the
  Radeon's WebGL, `navigator.webdriver` false).

To look at it, or to sign in by hand (Bookie Bashing or Team Casino after a
failed sign-in, a check), open the screen in a VNC viewer on the Hub:

```sh
remmina -c vnc://127.0.0.1:5910
```

and use the Chrome window there with the mouse and keyboard; close the viewer
when done (the Scraper carries on either way). Once per box, undo the
own-workspace setting this replaces (it named the window for GNOME's Auto Move
Windows extension, which made Chrome drag the desktop to workspace 2):

```sh
dconf reset /org/gnome/shell/extensions/auto-move-windows/application-list
```

The Docker fallback is unchanged (below).

```sh
journalctl --user -u puntmore-scraper -f
journalctl --user -u puntmore-screen -f
systemctl --user status|restart|stop puntmore-scraper
# from ~/Projects/puntmore; the commands find the service through its data dir:
export SCRAPER_DATA_DIR=/opt/puntmore-scraper/data
~/.venvs/puntmore/bin/python -m scraper status
~/.venvs/puntmore/bin/python -m scraper selfcheck   # what a site sees, WebGL renderer included
~/.venvs/puntmore/bin/python -m scraper dump bb-saves
~/.venvs/puntmore/bin/python -m scraper look bb-look '[{"click": "Show/Hide Presets"}]'   # steps, then saved (below)
~/.venvs/puntmore/bin/python -m scraper wake bb-saves   # end a job's rest (rest.py)
~/.venvs/puntmore/bin/python -m scraper ask '{"kind": "read", "urls": ["https://www.paddypower.com/football/..."], "keep": true}'   # read pages now (below)
~/.venvs/puntmore/bin/python -m scraper ask '{"kind": "price", "job": "bb-look", "items": [{"key": "1", "competition": "English Premier League", "home": "Arsenal", "away": "Chelsea", "preset": "Home 2,6"}]}'   # a preset's fair (below)
~/.venvs/puntmore/bin/python -m scraper resume paddypower
```

### Reading Paddy on request: `ask`
`python -m scraper ask '<request as JSON>' [--wait SECONDS] [--json]` has the
running service read Paddy Power match pages now (`scraper/ask.py`; Callum,
26 Nov 2026, `docs/decisions.md`). The command checks the request, writes it
to `data/requests/ask-<id>.json`, waits for the answer in
`data/requests/done/ask-<id>.json` (`--wait`, an hour by default) and prints
it; it exits 0 when the request is `done`, 2 when it is refused, 1 otherwise
(stopped, interrupted, not answered in time - the service carries on - or no
service running, when nothing is written). A request:

```json
{"kind": "read", "job": "pp-epl-most", "from": "2026-11-28T17:00", "to": "2026-11-30T23:59",
 "parser": "pp_most", "keep": false}
```

(an `id`, letters, digits, `.`, `_` and `-`, is made when not given)
names the games by a Paddy listing job and a kick-off range (a time with no
zone is London's): the match links on that job's listings whose kick-off is in
the range, nearest first. A listing the sweep holds open, loaded within the
job's `relist_min`, is read where it is; any other (the job resting, in quiet
hours, outside its window, or the tab stale) the request loads itself, one a
tick like its pages, reads and closes; each is in the result's `listings`
(`open`, `loaded`, `failed` or `skipped`). Or `"urls": [...]` in place of job,
from and to: at most 60, each on `paddypower.com`. `parser` is null or one of
the parsers that click nothing (`ask.READ_PARSERS`: `pp_most`; a parser that
drives a page, `bb_saves` say, is refused); `keep: true` saves each page to
`data/dumps/ask-<id>-<n>/` (`page.html`, `page.url`, a full-page `page.png`,
and `payloads.json`: what the tab's capture held, each body with its kind and
its url's path). A page whose parser failed is saved anyway; a page that ends
on another host (a redirect) is failed, not read and not saved. A page's url is
written without its query (it can carry the site's key): in the result, the
saved `page.url` and the log. On the page nothing is clicked but the cookie
banner.

The service answers one page a tick, so the sweep keeps running beside it,
with Paddy's gap between loads (90 s and more; thirty games take about three
quarters of an hour). A request's loads are not counted in Paddy's budget and
are made in quiet hours too; `status` shows them on a line of their own
(`paddypower  requested: 4 this hour, 12 today`). It stops at once on a
cool-off (`stopped`, the pages left `skipped`), and a challenge on a page it
loads is a block like any other: every Paddy page closed, the cool-off,
`resume`. One request runs at a time; another is refused ("another request is
running"). The result is written `running` with each item as it is done
(`url`, `home`, `away` and `kickoff` off the listing when it shows the game,
`state` read, failed or skipped, `reason`, the parser's `rows`, `saved`); if
the service restarts mid-request it is rewritten `interrupted`, keeping what
was done, and is never run again. A request file is removed only once its
result is written (a result that cannot be written is alerted and tried again
every tick); anything in `data/requests/` that is not a readable request (a
directory, a file over 1 MB, text that will not parse) is refused, or moved to
`data/requests/rejected/` when it cannot be removed. Results, entries moved
aside and the pages requests saved (`ask-<id>-<n>` only: never another dump)
are removed after 14 days, and a result's leftover temp file after an hour
(checked once a day). A page a request loads that lands on another host is
failed before anything is done on it (no consent click, no scrolling). The
gap holds across a restart: the service spaces its first Paddy load from the
last requested one. Each request's lines are in `python -m scraper targets log ask`.

### Pricing presets on request: `ask` with `price`
The same command, `"kind": "price"`, has the service read Bookie Bashing's fair
(the builder's Total Odds) for named presets on named games, on the bet builder
page a Bookie Bashing builder job keeps open (a job whose parser is
`bb_builder`: `bb-look`, which must be enabled and its page open):

```json
{"kind": "price", "job": "bb-look", "save": "no",
 "items": [{"key": "ars-che-h26", "competition": "English Premier League",
            "home": "Arsenal", "away": "Chelsea", "preset": "Home 2,6"}]}
```

At most 60 items, each `key` once. It is read only: nothing is saved to the
tracker and nothing that changes the account is pressed (`save` is `no`, the
default; `dry` and `press` are refused, "save not built"). Nothing is loaded:
when the job's page is not open (`bb-look has no open page`), Bookie Bashing is
cooling off, backing off or signing in, or the builder is not up (`builder not
ready: ...`), the request stops and says so. On the page
(`scraper/parsers/bb_presets.py`, written from the 12 Nov 2026 `bb-look`
dumps): the game is chosen in the builder's first block (home and away matched
against the Game(s) list under the competition named: the options whose name,
less its `(n/m)` count, is the name given, any case, else those starting with
it; the same teams listed twice is no game; a game already chosen is not chosen
again), the presets panel opened with "Show/Hide Presets" when it is shut, the
preset chosen in "Use Preset" (its name matched with whitespace collapsed, case
as given), and Total Odds read once it reads the same twice `bb.settle_s` apart
and is no longer the value before the preset (up to 6 s), with every block
checked to be on the game. Each tick works through items for at most
`bb.call_budget_s` (at least one), so a long request spans ticks; the result
shows the items done so far, each the request's item as given with `state`
(`priced`, `no_game`, `no_preset`, `unsettled`), `fair` (null unless priced),
`saved` (`no`) and `reason`. Items not reached when a request stops are left
out of `items`; the request's `reason` says why. One request at a time, of
either kind.

### Looking at a page: `look`
Parsers are written only from real saved pages, and some states of a page need
a click first (a panel opened, a preset loaded). `dump <job>` saves a job's
open page as it is; `look <job> '<steps as JSON>'` first takes a few plain
steps on it, then saves it the same way (html, url, a full-page screenshot, in
`data/dumps/<job>-<time>/`; `scraper/look.py`). It asks the running service,
which answers on its next tick (~15 s). The steps, in order, each a one-key
object, at most 12:

- `{"click": "<text>"}`: the first visible element whose own text is exactly
  that (buttons, then links, then any element; case as given), clicked with
  `browser.click` (which does not press when the page moved under the pointer).
- `{"select": {"field": "<label>", "option": "<text>"}}`: the `<select>` in
  the builder field whose button is `<label>`, set to the option whose text is
  `<text>`; `"prefix": true` takes the first option starting with it, `"nth": 1`
  the field in the second block. `{"select": {"has": "<an option's text>",
  "option": "<text>"}}` picks the select that holds that option instead (the
  stat chain's selects have no button).
- `{"type": {"field": "<label>", "text": "<text>"}}`: the input in that field,
  cleared and typed.
- `{"wait_ms": n}`: at most 5000.
- `{"dump": "<note>"}`: save the page now too, so one look can keep several
  states (`1-<note>.*`, ..., then `<n>-end.*`).

Every step is a line in the job's log (`look: clicked "Show/Hide Presets"`).
A step that cannot be done stops the look and says which and why
(`look: step 3 of 6 (...) failed: no visible element whose text is "..." -
stopped`); the page is still saved (`<n>-stopped.*`). Nothing in a look loads
a page: if a step changes the page's url, the look stops, says so, and the
change is counted against the site's budget as a load. Each Playwright call
in it has at most 8 s, so a look never holds the loop for long. The command
checks the steps' shape before it asks (a refusal exits 2 with the reason;
`--json` for machine output). **Not for Paddy Power jobs**, at the command
line or in the service: the Scraper clicks nothing there but a cookie banner
and market tabs.

A worked example: the bet builder's presets panel, which shows once a game is
chosen. The builder is kept up by a `bb_builder` job (a second Bookie Bashing
page, its loads counted in Bookie Bashing's budget; the Scraper may run
Bookie Bashing jobs, `docs/decisions.md`, Scraping conduct):

```sh
python -m scraper targets add --name bb-look --site bookiebashing \
    --url https://www.bookiebashing.net/tools/betbuilder/ --parser bb_builder
python -m scraper look bb-look '[
  {"select": {"field": "Competition", "option": "English Premier League", "prefix": true}},
  {"wait_ms": 1000},
  {"select": {"field": "Game(s)", "option": "Arsenal v", "prefix": true}},
  {"wait_ms": 1000},
  {"click": "Show/Hide Presets"},
  {"wait_ms": 1500},
  {"dump": "presets open"}
]'
python -m scraper targets log bb-look -n 12      # one line a step, then "look: saved data/dumps/bb-look-..."
```

## Running in Docker (a box with no screen)
The fallback: one real Chrome on Xvfb inside a container, for a box with no
desktop. The image sets `SCRAPER_IN_CONTAINER=1`, which is what gives Chrome
its fixed window and language flags (`browser.py`). Expect no WebGL there.
The container will not start while `data/heartbeat` is under 90 s old
(`deploy/entrypoint.sh`): another Scraper, the native one, runs on that data
dir. So a cron `update.sh` left behind on the Hub cannot put a second Chrome
on the profile.

## Setting up a box with no screen (once)
Any Linux host with Docker, amd64, about 3 GB RAM free. (On the Hub itself the
Scraper service runs natively on its own screen, above; this is the fallback only.)

```sh
sudo mkdir -p /opt/puntmore-scraper/data && cd /opt/puntmore-scraper
# from this folder of the repo:
#   deploy/run.sh, deploy/update.sh  -> /opt/puntmore-scraper/
#   .env.example                     -> /opt/puntmore-scraper/.env           (fill in)
#   targets.example.toml             -> /opt/puntmore-scraper/data/targets.toml (then edit)
chmod 600 .env
# The image is private on GHCR: log in once with a PAT that has read:packages
echo "$GHCR_PAT" | docker login ghcr.io -u cmclarty --password-stdin
./run.sh
( crontab -l 2>/dev/null; echo '*/15 * * * * /opt/puntmore-scraper/update.sh >> /opt/puntmore-scraper/update.log 2>&1' ) | crontab -
```

**Updates:** push to `main` with changes under `scraper/`. CI runs the
tests; `puntmore-update.timer` fast-forwards the Hub and restarts the service
within 10 minutes (in the Docker fallback, CI publishes the image and the
box's 15-minute cron restarts onto it). Profile, budget and cool-off state live in `data/`, so they survive
the restart.

### Logging in to Bookie Bashing after a failed sign-in, or clearing a check (VNC, Docker only)
VNC listens on the box's `127.0.0.1:5900` only. From your machine:

```sh
ssh -L 5900:localhost:5900 you@box        # then open a VNC viewer on localhost:5900
```

You'll see the live Chrome window. Log in on the Bookie Bashing tab. The
next read finds it logged in, and you get a "clear again" alert.

### Day to day (Docker)
```sh
docker logs -f puntmore-scraper
docker exec puntmore-scraper python -m scraper status          # budget, cool-offs, each target, heartbeat
docker exec puntmore-scraper python -m scraper dump bb-saves    # save the open page(s) to data/dumps/
docker exec puntmore-scraper python -m scraper look bb-look '[{"click": "Show/Hide Presets"}]'   # steps, then saved
docker exec puntmore-scraper python -m scraper wake bb-saves    # end a job's rest (rest.py)
docker exec puntmore-scraper python -m scraper resume paddypower
docker exec puntmore-scraper python -m scraper check            # load targets.toml and list the targets
docker exec puntmore-scraper python -m scraper selfcheck        # what a site sees of the browser (no network)
```

## Credentials (none in the repo)

In `/opt/puntmore-scraper/.env`: `SCRAPER_ALERT_WEBHOOK` (Discord), `SCRAPER_HEALTHCHECK_URL`
(optional), and `VNC_PASSWORD` on a box with no screen. It holds no Render key: the Hub posts
its prices (`PUNTMORE_RENDER_API_KEY` in the Hub's `.env`). The Scraper no longer reads
`SAVES_INGEST_KEY` or `PUNTMORE_URL`, so both can be deleted from `/opt/puntmore-scraper/.env`.
Each site the Scraper signs in to has its own login, Callum's own (leave either blank and a
person logs in instead): Bookie Bashing `BB_USERNAME` and `BB_PASSWORD`, Team Casino
`TC_USERNAME` (the email) and `TC_PASSWORD`. The Scraper types a site's login when that site's
page is found logged out, once per logout and at most 3 sign-ins in 6 hours for each site, then
it waits for a person; the password is never logged or kept, and Chrome is not given any of the
variables. It never types a two-factor code: an account with two-factor on is a failed sign-in.
A failed sign-in is noted in `data/login.json` (with each site's recent sign-in times) and
nothing is tried again on that site until it is seen logged in (log in on the Hub's screen) or
`python -m scraper resume <site>` is run, which forgets it and the sign-in times. The
session itself lives in the Chrome profile in the data directory; never share
`data/profile/`: it holds the login cookies.

## Each tab's state
Each match tab's status says what its market-tab clicks did (`loaded`,
`clicked, nothing new`, `not found`, ...). A tab that is not found saves the
page once per run to `data/samples/<run>/<target>-market-tab-not-found/`.

Samples: each tab keeps its first distinct payloads on disk
(`SCRAPER_CAPTURE_TAB_MB`, default 8, inside the run's `SCRAPER_CAPTURE_MB`),
first-party hosts only, repeats of the same URL+size skipped, plus the page's
HTML once. The sign-in's own response (it may repeat what was typed) is never
kept. `index.jsonl` marks each market-tab click and labels what followed it.

Health: on the Hub the Console's Scraper card goes down when the
heartbeat is 10 minutes stale (`PUNTMORE_SCRAPER_STALE_MINUTES`); in Docker the
healthcheck fails at 5.
Set `SCRAPER_HEALTHCHECK_URL` (e.g. healthchecks.io) for an alert when the
whole box goes quiet.

## Targets
One file: `data/targets.toml` (see `targets.example.toml`), or the file
`SCRAPER_TARGETS` names. It lives on the Hub and is changed only through
`scraper/targets.py` (Changing targets, above). The running
service checks it once a minute and applies an edit without a restart: a
removed target's tabs close, a new one opens on the next tick, and a changed
one is rescheduled in place or reopened (Changing targets, above). A
file that will not load (a half-written save, a bad target) is reported in the
log and the running targets are kept. The service writes its state to
`data/status.json` once a minute; the Hub reads it and `status` prints it.

Targets are of these kinds:
- **matches**: a league's listing page, e.g. a Paddy competition, whose match
  links are opened up to `max_tabs` at a time. Each match tab clicks the
  market's tabs once, then stays open. A tab that goes quiet is reloaded once,
  then retired. `listings` (in `[target.matches]`, at most 8, each on a host
  allowed for the site, none repeating `url` or each other) adds further
  competitions' listing pages to the same job (Callum, 12 Nov 2026): each is
  kept open in its own tab (`<name>:list`, then `<name>:list:1`, ...), loaded
  and relisted every `relist_min` on its own clock, one load per tick. Their
  match links are interleaved (the first of each listing, then the second of
  each, ...), so every competition gets a tab before any gets two;
  `max_tabs` is for the whole job. A listing tab is parsed like any other of
  the job's tabs.
  Only games kicking off within the job's window are opened (`within_h`, or
  `games.within_h`, 48 h; Callum, 12 Nov 2026): the kick-off of each match
  link is read off the listing the job already has open (`parsers/
  pp_listing.py`: Paddy's "Saturday 21" day headers and each fixture's
  "12:30", London time; no request), and a link is opened only when its
  kick-off is known and inside the window. An open match tab whose game has
  kicked off, or has left the window, is retired. A link with no readable
  kick-off (under a header the reader has not seen in a real page, such as
  whatever Paddy writes for today or in-play) is not opened; when the
  listings show links but none with a kick-off, the job says once in its log,
  and shows as its detail, "kick-off times could not be read from the listing
  - nothing opened" (a Paddy layout change shows), and does not rest. When
  every listing is open and no link is inside the window the job rests, as a
  parser's rest does: its tabs close, `data/resting.json` keeps it, `status`
  shows it `paused` ("no games within 48 hours - next is Man City v Fulham,
  Sat 21 Nov 12:30 - resting until Thu 19 Nov 12:30") until `within_h` hours
  before the next game the listings showed, or `games.look_again_s`, whichever
  is sooner; `wake <job>` ends it, and so does a change to the job's own
  `within_h`. A `within_h` of 0 reads no kick-off and opens every link, as
  before.
- **page**: one URL kept open, e.g. the Bookie Bashing bet builder. It is
  reloaded every `every_min`, or only when it goes quiet for `stale_min`.
  Its parser may rest it (`rest.py`): the page closes and nothing loads
  until the time the parser gave, as outside a `window`; `bb_saves` does so
  when no game kicks off within `games.within_h` hours (the catalogue's value:
  a parser is handed its page, not its job, so a `within_h` on `bb-saves`
  is not read). With a parser and no `post` its rows go nowhere: its status
  detail says how many the last parse read, and a line a parser hands back
  about its page (`parsers/rows.py`) is shown there too and written to the
  job's log when it changes.
- **fetch**: a `generic` Finder source (above), or any job whose page needs
  no keeping open (the Team Casino dashboard): loaded, parsed once (after
  `wait_for` shows, up to 20 s) and closed, every `every_min`
  (`fetch.default_min` without one). No recycle touches it: nothing is
  open. A load that fails tries again after `fetch.retry_s`. A load that finds the site logged out signs in there as
  a kept-open page does (once per logout, counted and capped, `login.py`)
  and gets one load at once: the visit, the sign-in's own navigation and the
  visit again that reads, 3 loads; if that load fails it waits
  `fetch.retry_s` like any other. A restart is not a load: each job's last
  good read (a parse that read rows) is kept in `data/fetched.json`, and
  after a restart its next run is that read plus its interval, but never
  sooner than `fetch.retry_s` after its last load of any kind (the guard's
  record), so a restart after a failed load, an empty read or a sign-in
  tries again on `fetch.retry_s`. Anything else that stops a fetch job runs
  it at once when it may run again: its window opening, a rest ending (or
  `wake`), the job added, enabled or changed (any field but the timings);
  a timing change redraws its next run, after a restart too.

Timings are clamped to the floors in `config._target`, and the site budgets
(`behaviour.py`) stay the ceiling whatever the file says.

Six fields say when and who first (`schedule.py`):
- **`near_min`** and **`near_hours`** (default 3): the kick-off sweep. A
  parser's rows may carry a `kickoff` (ISO 8601; `pp_most` and `bb_saves`
  do); the nearest one still ahead is the target's next kick-off (`status`
  shows it). While it is within `near_hours`, the target reloads, or a fetch
  target runs, every `near_min` instead of `every_min`, and among targets of
  equal priority the nearest kick-off gets a short budget's next load. After
  the kick-off the normal interval is back. The spec's "every 2-4 h, ~30 min
  in the last 3 h before kick-off, nearest first" is `every_min = 180,
  near_min = 30`. The site's budget is still the ceiling; a sweep that would
  exceed it simply waits.
- **`window`** (`"HH:MM-HH:MM"`, local time, may wrap midnight as
  `"22:00-02:00"`; the start minute is inside, the end minute is not): the
  hours a target may load at all. Outside it nothing is loaded and its open
  tabs are closed, within a tick of the window's end; `status` shows it
  `paused`, "outside window". When the window starts it opens again through
  the normal spacing and guard; a fetch target runs at the start. No window
  means always. A site's quiet hours and cool-offs still apply inside it.
- **`days`** (a list of `mon` .. `sun`, no repeats; none = every day) and
  **`until`** (`"YYYY-MM-DD"`, the last day it runs; none = forever): the
  days a job runs. Off its days it is `paused`, "not a run day"; after
  `until` it is `finished`, "ended <until>", whatever its site is doing.
  Either way nothing loads and its tabs close, as outside the window. A
  window past midnight belongs to the day it started: a Saturday
  `"20:00-02:00"` job runs until 02:00 on Sunday, and with `until` set to
  that Saturday it finishes at 02:00 (`schedule.job_day`).
- **`priority`** (whole number, default 0): who gets a site's loads when they
  are short. Loads are spaced, so one goes out at a time, to the first target
  in the tick's order that wants one. While a site has at least as many
  loads left (this hour, or today if fewer) as targets wanting one, that
  order is file order. When more of its targets want a load than it has
  left, the highest priority goes first, ties to whichever has been waiting
  longer (overdue the longest). A lower-priority target still gets the loads
  a higher one does not want: priority orders the queue, it does not reserve
  budget.

## Tests
```sh
pip install -r scraper/requirements.txt -r scraper/requirements-dev.txt
python -m playwright install chromium   # or SCRAPER_CHROME_PATH=/usr/bin/google-chrome
python -m pytest scraper/tests -q      # from the repo root; includes a real browser against a fake localhost site
```
