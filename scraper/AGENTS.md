# scraper/ — rules for the Worker

The repo's `CLAUDE.md` applies everywhere and wins on any conflict. Setup and
day-to-day commands are in `README.md`; the boundaries that still apply are in
`HANDOVER.md`. The Worker is the ONLY thing on the Hub that loads a page.

- **Bet Finder sources are Worker targets** (5 Nov 2026, Callum: one Chrome,
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
- The rule below is moved verbatim from `CLAUDE.md` on 5 Nov 2026.

- **The scraper (`scraper/`, Oct 2026) is a SEPARATE service** — its own
  Docker image (`.github/workflows/scraper-image.yml` → GHCR), on its own
  Linux server, updated by that server's `update.sh` cron. It is NOT part of
  the Render deploy and the no-docker-compose rule is untouched (plain
  `docker run`); nothing in it imports the API. One real Chrome on Xvfb keeps
  every target's page OPEN and listens to what the page receives, so after
  the first load most targets cost no requests. WHAT it reads is config, and
  the config is ONE file: **`targets.toml` on the Hub** (`data/targets.toml`,
  or the file `SCRAPER_TARGETS` names), edited by Hermes — nothing on Render
  configures the Worker (decision 5 Nov 2026; Puntmore's `/scraper` page and
  its config and status routes are gone). A target is a page, or a
  competition listing whose match links are opened (in-page tabs, parser,
  ingest path). The Worker checks the file once a minute and applies an edit
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
  (above); `pp_most` is unwritten (Oct 2026 — no priced most market in the
  samples yet).
