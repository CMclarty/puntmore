# scraper/ — rules for the Scraper service

`docs/decisions.md` is read first and wins; the repo's `CLAUDE.md` applies everywhere and wins on any other conflict. Setup and
day-to-day commands are in `README.md`. The Scraper service is the ONLY thing on the Hub that loads a page:
every page any tool needs, filling what OddsPapi lacks. All its rows go to Scraper control (the
Hub's inbox, as drop files: `hub/PROTOCOL.md`) and from there to a board or to HubAI; Paddy's
prices are `scraper_prices` files that the Hub posts to Render.

## Boundaries (agreed; keep them)
- It detects challenges and logouts and **stops** (a Bookie Bashing logout after one sign-in,
  below). It never answers a check and never fakes a fingerprint. It types one login only:
  Bookie Bashing's, Callum's own, from the Scraper's `.env` (`BB_USERNAME`, `BB_PASSWORD`;
  Callum, 11 Nov 2026), once per logout and at most 3 sign-ins in 6 hours, then it waits for a
  person, with plain fill and click (`login.py`). A failed sign-in waits for a person or `resume
  bookiebashing`; a person is needed only then or for a challenge. The password is never written
  anywhere, and the capture never keeps the sign-in's own response. No proxies or IP rotation,
  no clicking through menus to look human, and no other ways to avoid detection. If Paddy blocks
  the Hub, the answer is to slow down or get the data another way, not to work around the block.
- One agreed exception (Callum, 26 Oct 2026): Chrome is started as a person's Chrome, without
  Playwright's `--enable-automation` test flag and with AutomationControlled off. Nothing else
  about the browser is changed or overridden. Its window has its own name on the desktop
  (`--class=puntmore-scraper`; Callum, 11 Nov 2026), so GNOME keeps it on its own workspace:
  a label for the window manager, which a page cannot read (`README.md`, "Its own workspace").
- Per-site rules live in code (`sites.py`). Config can only make it slower.
- Targets are direct page URLs in `targets.toml`.

## Module map
- `runner.py` - the loop: wires the modules below, owns the tabs, loads
  through the guard, applies a targets.toml edit (in place for a timing-only
  change).
- `schedule.py` - when each target is due: `every_min`, `stale_min`, jitter
  drawn once per load, the per-site load gaps, whether a job is active at
  all (`inactive`: `until`, `days`, `window`; a window past midnight belongs
  to the day it started, `job_day`), `priority`, and the
  kick-off sweep (`near_min` within `near_hours` of a kick-off the rows
  report; nearest kick-off first when a budget is short).
- `sinks.py` - where rows go, by `post`: `/api/...` targets:
  `scraper_prices` drop files the Hub posts to that route; `hub:<chan>`:
  tips (each row with its `tip_id`); events. All are drop files for the
  Hub; the Scraper sends Render nothing.
- `status.py` - the heartbeat, `data/status.json`, the per-site budget summary.
- `joblog.py` - one log per job, `data/logs/<name>.log` (JSON lines; past
  512 KB cut to its newest lines in 256 KB, 1000 at most); the runner
  writes it through `Runner._say`, `targets log` reads it. A write never
  raises. A site's block is a line in each of its jobs' logs
  (`_on_blocked`, never a url).
- `ids.py` - the one tip id, minted here when rows reach the Hub sink (a copy
  of `hub/puntmore_hub/ids.py`; a test in each suite pins the same vector).
- `parsers/` - one module per page kind; rows only from what the page shows.
- `targets.py` - the only writer of targets.toml (CLI, Console, HubAI).
- `config.py` - settings and target validation (`_target`, the clamps).
- `sites.py` - per-site rules in code: budgets, quiet hours, what a block means.
- `guard.py` - every load asks it first: budget, cool-off, quiet hours (SQLite).
- `browser.py` - the one Chrome: launch, block/logout detection, clicks.
- `login.py` - Bookie Bashing's login form, the login from `.env`, one sign-in
  per logout on the page already open (at most 3 in 6 hours, then it waits
  for a person), and `data/login.json` (the sign-ins' times, written before
  each is sent; the last attempt failed: nothing is tried again until logged
  in or `resume`). A form Chrome would send itself (the password in the url)
  is aborted inside Chrome; `safe_url` keeps such a url out of what is written.
- `capture.py` / `alerts.py` - what a page receives (and its disk samples;
  never the sign-in's own response) / the alert webhook and log.

## Rules
- **Finder's sources are Scraper targets** (5 Nov 2026, Callum: one Chrome,
  one budget, one alert path). `site = "generic"` may name any host except the
  two bookmakers; its budget is in `sites.py` (60/h, 400/day, no quiet hours,
  a block = 24 h cool-off + `resume generic`). A generic target is `fetch`
  mode: loaded, parsed once, closed. Its parser follows the same contract as
  every other parser and emits TIPS (event, market, selection, optional
  advised_price / advised_stake_units / tip_time / expiry / origin). With
  `post = "hub:tips"` the rows go to `data/hub/tips/` as one file per run;
  `min_rows` breaches and blocks go to `data/hub/events/`. The Hub's
  `puntmore inbox` reads both. Never let a generic target reach Paddy Power
  or Bookie Bashing; never add a second budget anywhere.
- targets.toml is edited only through `scraper/targets.py` (the CLI, the
  Console, HubAI's scraper-manager); never by hand while the Scraper runs.
  Validation stays in `config._target`; `targets.py` must not grow its own
  copy of a clamp, host or site rule.
- **The Scraper service is a SEPARATE service**: ONE real Chrome on the Hub's desktop session, the systemd user
  service `puntmore-scraper` (`deploy/native/`), updated by
  `puntmore-update.timer` with the rest of the Hub, so the sites see a
  person's Chrome: the box's GPU, fonts and screen (in the container Chrome
  had no WebGL at all). Docker (`.github/workflows/scraper-image.yml` → GHCR,
  plain `docker run`, the no-docker-compose rule untouched) is only the
  fallback for a box with no screen: Xvfb and VNC exist only there, and the
  window and language flags apply only there (`SCRAPER_IN_CONTAINER=1`) and
  headless. Never run both: they share one profile, one budget, one data dir.
  It is NOT part of the Render deploy; nothing in it imports the API. Its
  Chrome keeps every target's page OPEN and listens to what the page receives, so after
  the first load most targets cost no requests. WHAT it reads is config, and
  the config is ONE file: **`targets.toml` on the Hub** (`data/targets.toml`,
  or the file `SCRAPER_TARGETS` names), changed through `targets.py`; nothing
  on Render configures the Scraper. A target is a page, or a
  competition listing whose match links are opened (in-page tabs, parser,
  ingest path). The Scraper checks the file once a minute and applies an edit
  without a restart; a file that will not load is reported and the running
  targets are kept. It writes its state to `data/status.json` once a minute,
  which the Hub reads; it sends Render nothing; its prices go through the Hub.
  Timings are clamped to the scraper's own floors (`config._target`).
  HOW each site is treated is code
  (`sites.py`: budgets, Paddy quiet hours 01-07, a Paddy block = every Paddy
  page closed + 24 h cool-off needing `resume`) — no config edit can loosen it.
  It detects challenges and logouts (also on an already-open page: 3+
  refusals in 10 min, Cloudflare, logout) and never answers a challenge — no
  fingerprint spoofing, no solvers, no proxies, no user-agent or navigator
  overrides; do not add any. A Bookie Bashing logout is the one thing it
  answers, by signing in once with the login in `.env` (`login.py`). Chrome
  is configured coherently, as a person's
  Chrome: the one departure from Playwright's defaults is that its
  `--enable-automation` test flag is not passed and AutomationControlled is
  off (Callum's decision, 26 Oct 2026 — `navigator.webdriver` false, no
  infobar); `python -m scraper selfcheck` prints what a site sees. Page loads are spaced per site and
  across sites (`sites.py load_gap_s`); jitter is drawn once per load. Parsers
  (`scraper/parsers/`, one module each) are written only from what the real
  pages were seen to do: `bb_saves` drives the Bookie Bashing bet builder
  (above; this one BB target is the single named exception to "BB is never
  automated", `docs/decisions.md`); `pp_most` reads Paddy's most-corners / most-cards three-ways
  (written from the 29 Oct 2026 capture).
