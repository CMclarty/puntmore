# scraper/ — rules for the Scraper

`docs/decisions.md` is read first and wins; the repo's `CLAUDE.md` applies everywhere and wins on any other conflict. Setup and
day-to-day commands are in `README.md`; the boundaries that still apply are in
`HANDOVER.md`. The Scraper is the ONLY thing on the Hub that loads a page.

## Module map
- `runner.py` - the loop: wires the modules below, owns the tabs, loads
  through the guard, applies a targets.toml edit (in place for a timing-only
  change).
- `schedule.py` - when each target is due: `every_min`, `stale_min`, jitter
  drawn once per load, the per-site load gaps, `window`, `priority`.
- `sinks.py` - where rows go, by `post`: `/api/...` to Render with the
  ingest key, `hub:<chan>` drop files for the Hub (each row with its
  `tip_id`); events for the Hub.
- `status.py` - the heartbeat, `data/status.json`, the per-site budget summary.
- `ids.py` - the one tip id, minted here when rows reach the Hub sink (a copy
  of `hub/puntmore_hub/ids.py`; a test in each suite pins the same vector).
- `parsers/` - one module per page kind; rows only from what the page shows.
- `targets.py` - the only writer of targets.toml (CLI, console, Hermes).
- `config.py` - settings and target validation (`_target`, the clamps).
- `sites.py` - per-site rules in code: budgets, quiet hours, what a block means.
- `guard.py` - every load asks it first: budget, cool-off, quiet hours (SQLite).
- `browser.py` - the one Chrome: launch, block/logout detection, clicks.
- `capture.py` / `alerts.py` - what a page receives (and its disk samples) /
  the alert webhook and log.

## Rules
- **Bet Finder sources are Scraper targets** (5 Nov 2026, Callum: one Chrome,
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
  console, the scraper-manager); never by hand while the Scraper runs.
  Validation stays in `config._target`; `targets.py` must not grow its own
  copy of a clamp, host or site rule.
- The rule below is moved from `CLAUDE.md` on 5 Nov 2026 (its first lines
  updated the same day for the move to the Hub's desktop).

- **The Scraper (`scraper/`, Oct 2026) is a SEPARATE service** — since
  5 Nov 2026 ONE real Chrome on the Hub's desktop session, the systemd user
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
  or the file `SCRAPER_TARGETS` names), edited by Hermes — nothing on Render
  configures the Scraper (decision 5 Nov 2026; Puntmore's `/scraper` page and
  its config and status routes are gone). A target is a page, or a
  competition listing whose match links are opened (in-page tabs, parser,
  ingest path). The Scraper checks the file once a minute and applies an edit
  without a restart; a file that will not load is reported and the running
  targets are kept. It writes its state to `data/status.json` once a minute,
  which the Hub reads; the only thing it sends Render is the prices it posts.
  Timings are clamped to the scraper's own floors (`config._target`).
  HOW each site is treated is code
  (`sites.py`: budgets, Paddy quiet hours 01-07, a Paddy block = every Paddy
  page closed + 24 h cool-off needing `resume`) — no config edit can loosen it.
  It detects challenges and logouts (also on an already-open page: 3+
  refusals in 10 min, Cloudflare, logout) and never answers them — no
  fingerprint spoofing, no solvers, no proxies, no user-agent or navigator
  overrides; do not add any. Chrome is configured coherently, as a person's
  Chrome: the one departure from Playwright's defaults is that its
  `--enable-automation` test flag is not passed and AutomationControlled is
  off (Callum's decision, 26 Oct 2026 — `navigator.webdriver` false, no
  infobar); `python -m scraper selfcheck` prints what a site sees. Page loads are spaced per site and
  across sites (`sites.py load_gap_s`); jitter is drawn once per load. Parsers
  (`scraper/parsers/`, one module each) are written only from what the real
  pages were seen to do: `bb_saves` drives the Bookie Bashing bet builder
  (above; **OPEN, Callum to decide** in `docs/decisions.md` whether this one
  BB target stays as the named exception to "BB is never automated" or is
  disabled); `pp_most` reads Paddy's most-corners / most-cards three-ways
  (written from the 29 Oct 2026 capture).
