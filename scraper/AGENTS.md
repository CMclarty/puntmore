# scraper/ — rules for the Paddy / Bookie Bashing worker

The repo's `CLAUDE.md` applies everywhere and wins on any conflict. Setup and
day-to-day commands are in `README.md`; the boundaries that still apply are in
`HANDOVER.md`. The rule below is moved verbatim from `CLAUDE.md` on
5 Nov 2026. The Hub's 2-Up code (`hub/`) borrows `browser.py` from
here; keep the realism rules in this directory.

- **The scraper (`scraper/`, Oct 2026) is a SEPARATE service** — its own
  Docker image (`.github/workflows/scraper-image.yml` → GHCR), on its own
  Linux server, updated by that server's `update.sh` cron. It is NOT part of
  the Render deploy and the no-docker-compose rule is untouched (plain
  `docker run`); nothing in it imports the API. One real Chrome on Xvfb keeps
  every target's page OPEN and listens to what the page receives, so after
  the first load most targets cost no requests. WHAT it reads is config, set
  on Puntmore's **`/scraper` page** (`scraper` grant, `routers/scraper.py`):
  leagues (competition listings), markets (in-page tabs, parser, ingest
  path; a Bookie Bashing market is one page with its own url) and per-site
  timings. The box GETs `/api/scraper/config` (key-guarded, the saves key)
  every 5 min and applies a change without a restart; it POSTs
  `/api/scraper/status` once a minute. The config is the SAME structure
  `targets.toml` holds (`config._target` reads both); the toml is only the
  offline fallback, after the cached `data/config.json`. Timings are clamped
  server-side to the scraper's own floors. HOW each site is treated is code
  (`sites.py`: budgets, Paddy quiet hours 01-07, a Paddy block = every Paddy
  page closed + 24 h cool-off needing `resume`) — no page edit can loosen it.
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
