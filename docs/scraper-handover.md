# Scraper → a Puntmore-controlled worker — handover (26 Oct 2026)

For the agent doing the scraper build. Verified against the repo and the
running box on 26 Oct 2026; nothing here is a guess. Repo
`/home/callum/bettingtools`, branch `main`. **Every push to `main` is a
production deploy** (Render) — the agent does NOT push; it commits on its own
branch and reports. Run `api/.venv/bin/python -m pytest -q` (779 pass at
`7087e85`) and `cd frontend && npm run lint && npm run test && npm run build`
before reporting. UI changes get looked at in a browser first
(`.claude/skills/run/SKILL.md`). CLAUDE.md UI rule: build only what is asked;
no unrequested pages, labels or decoration.

## What Callum asked for (26 Oct, his words, lightly tidied)

> Modularise the scraper and formalise it with markets, target leagues,
> timings. The controls for that should be a page on Puntmore. Eventually the
> scraper will move to another box, so keep it platform agnostic. It should be
> an extension of the Puntmore git anyway.

So: the server is the control plane, the scraper is a worker that asks it what
to do and reports back. Config is edited on a Puntmore page; the scraper picks
it up without a restart.

## What exists today (verified)

**Scraper v0.1** — on branch `scraper-v0.1` (local branch exists; also
`origin/scraper-v0.1`), under `local/scraper/`. Never merged. The running
container (`ghcr.io/cmclarty/puntmore-scraper:latest` on this PC, data in
`/opt/puntmore-scraper/data/`) is byte-identical to that branch. Read
`local/scraper/HANDOVER.md` and `README.md` on the branch first.

- One real Chrome (Playwright, persistent profile, Xvfb + VNC in Docker),
  kept-open pages, passive capture of every JSON response / websocket frame
  the page receives (`capture.py`), per-site load budgets and quiet hours in
  SQLite (`guard.py`, `sites.py` — code, not config: config can only make it
  slower), Discord alerts on a block/logout (`alerts.py`).
- `config.py` reads `data/targets.toml`: `[[target]]` with `name`, `site`,
  `url`, `every_min`, `stale_min`, `parser`, `post`, and `[target.matches]`
  (`link_re`, `max_tabs`, `market_tabs`, …) for a competition listing whose
  match links are opened in tabs. **This is the half-formalised version of
  what Callum wants** — one URL per league, markets as tab names, timings per
  target, and a restart to change any of it.
- `parsers.py`: `bb_saves` and `pp_match` both raise `NotImplementedError`.
  **No parser has ever been written.** The box has captured real payloads:
  `/opt/puntmore-scraper/data/samples/20261018-172951/` (205 MB, readable):
  `pp-epl_list/` and one dir per EPL match page, each with `index.jsonl` +
  numbered payload files. Paddy's prices come from
  `https://smp.paddypower.com/www/sports/fixedodds/readonly/v1/getMarketPrices`
  (164 payloads on one match page) and `ips.paddypower.com/inplayservice/v1.0/livedata`.
  The `bb-saves` sample is a WooCommerce ping only — the BB target URL in
  `/opt/puntmore-scraper/data/targets.toml` is still the `REPLACE-with…`
  placeholder, so there is no Bookie Bashing data yet.
- CI `.github/workflows/scraper-image.yml` (on the branch): pytest, then
  publish the image to GHCR on push to `main` touching `local/scraper/**`.
  **Never run.** The server's `/opt/puntmore-scraper/update.sh` (cron, 15 min)
  pulls `:latest` and restarts onto it. `.env` on the box already has
  `PUNTMORE_URL`, `SAVES_INGEST_KEY`, `SCRAPER_ALERT_WEBHOOK`, `VNC_PASSWORD`.
- Tests on the branch: `local/scraper/tests/test_guard_config.py`,
  `test_e2e_fakesite.py` (real browser against a fake localhost site).

**Server side** (`api/app/`):
- Ingest endpoints the scraper posts to, key-guarded by `SAVES_INGEST_KEY`
  (`routers/saves_ingest.py`, `require_ingest_key`): `POST /api/paddy/saves-fair`
  (Bookie Bashing fair saves, `FairSavesIn`) and `POST /api/paddy/most-prices`
  (Paddy's "most corners/cards" three-way prices, `PaddyMostIn`: home, away,
  kickoff, market most_corners|most_cards, home/tie/away_price). Both write
  to bounded in-memory stores (`cards/saves.py`, `cards/most.py`).
- Paddy's saves LINES and every corners/cards line come from OddsPapi, not
  the scraper. **The scraper's Paddy job is only the "most" markets**; its
  Bookie Bashing job is the fair saves prices.
- Page gating: `frontend/src/App.jsx` `protectTool(<Page/>, 'tool')` against
  the user's grant (`"*"` users see everything). Nav "More" sheet lists tools.
- Daily Paddy health lines (`cards/health.py`, posted under the AW 26 morning
  message) already report `saves`/`most` store sizes — "Scraper: saves 0 ·
  most 0 ⚠ nothing posted" is what Callum sees until this works.
- `local/bookiebashing_saves.py` is the older PC-side BB reader; its
  `parse_page` is also `NotImplementedError`. Retire it once the scraper's
  `bb_saves` exists.

## Build (in this order)

### 1. Bring the scraper into the repo
Merge `scraper-v0.1` and move `local/scraper/` → `scraper/` (top level: its own
Dockerfile, requirements, tests; nothing in it imports the API). Fix the
workflow paths and `working-directory`. Keep the `HANDOVER.md` boundaries
section — they still apply (detect challenges, never answer them; no
fingerprint faking, proxies or IP rotation; per-site ceilings in code).

### 2. Server: scraper config + status
Tables (`models.py`) and a router `routers/scraper.py`, registered in
`main.py` under `/api/scraper`:
- **Leagues**: `name`, `site` (paddypower), `url` (the competition listing),
  `enabled`. Seed EPL from the current `targets.toml`.
- **Markets**: `key` (`most`, `bb_saves`, …), `label`, `site`, `tabs` (the
  in-page tabs to click, e.g. `["Corners", "Cards"]`), `parser`, `post`
  (the ingest path), `enabled`. Seed `most` → `/api/paddy/most-prices` and
  `bb_saves` → `/api/paddy/saves-fair` (its `url` is a page target, editable —
  Callum fills in the real BB saves page on the Puntmore page).
- **Timings** per site: `every_min`, `stale_min`, `max_tabs`, `open_gap_min`,
  `relist_min`, `tab_max_hours`. Clamped server-side to the scraper's floors
  (`config._target`) so a page edit can never make it faster than the code
  allows; `sites.py` remains the ceiling regardless.
- **Status**: one row per target (`name`, `site`, `url`, `state`
  running|paused|blocked|retired, `detail`, `last_load`, `last_parse`,
  `rows_posted`, `updated_at`) plus per-site budget spent and cool-off.
- Routes: `GET /api/scraper/config` and `POST /api/scraper/status` guarded by
  `require_ingest_key` (same key the box already has); the admin reads/writes
  (`GET /api/scraper`, `PUT …/leagues/{id}`, `PUT …/markets/{key}`,
  `PUT …/timings/{site}`) session-guarded by a `scraper` tool grant.
- `GET /api/scraper/config` returns the targets list in the SAME shape the
  scraper's `config._target` reads (so the toml and the server are two
  sources of one structure), built from enabled leagues × enabled markets for
  that site, plus page targets (bb_saves) and the timings.

### 3. Scraper: config from the server, status back, hot reload
- `config.load()`: if `PUNTMORE_URL` + `SAVES_INGEST_KEY` are set, fetch
  `/api/scraper/config`, cache to `data/config.json`, fall back to the cache
  when the server is unreachable, and to `targets.toml` when neither exists
  (tests, offline). `SCRAPER_TARGETS` env still forces the toml.
- Runner re-fetches every 5 min (`CONFIG_EVERY_S`); a changed config closes
  tabs of removed targets and starts new ones — no restart. Site rules stay
  in `sites.py`.
- Status: POST once a minute (throttled) — what `python -m scraper status`
  prints, per target, plus budget and cool-off.
- Keep the Docker image the deployable unit (platform agnostic: any Linux
  box with Docker). `run.sh`/`update.sh` unchanged.

### 4. Parsers as modules
`scraper/parsers/__init__.py` holds `PARSERS`; one module per parser:
- `pp_most.py`: from `capture.recent` `getMarketPrices` payloads on a match
  tab, find the "most corners" / "most cards" three-ways and emit
  `PaddyMostIn` rows (home/away from the page, kickoff ISO, three prices).
  Write it from the samples; put a TRIMMED payload (just the markets needed,
  a few KB) under `scraper/tests/fixtures/` and test against it. If the
  samples hold no "most" market, say so in the report with the market names
  you did find — do not guess a shape.
- `bb_saves.py`: stays `NotImplementedError` until Callum sets the real BB
  URL and a dump exists; say so in the report.

### 5. Puntmore page `/scraper` (grant `scraper`)
One page, mobile-first like the rest: leagues (toggle, URL), markets
(toggle, tabs), timings per site (numbers), and the status table (per
target: state, last load/parse, rows posted; per site: budget, cool-off).
Edits save on change. Nothing else — no charts, no log viewer.

### 6. Tests and docs
Scraper tests under `scraper/tests` (config-from-server with a fake HTTP
server, hot reload adds/removes tabs, parser fixtures); API tests for the
router (key vs session guards, clamping, config shape matches
`config._target`). Update `scraper/README.md` (server install stays as is;
targets now come from the page) and the CLAUDE.md scraper entry.

## Constraints
- Do not touch the Paddy pricing code (`api/app/cards/`, `pricing/`) or
  `CardsPage.jsx`.
- Work in a worktree on branch `scraper-control`; commit as you go; **never
  push**. Report: what was built, test counts, what was NOT done and why,
  and anything Callum has to do on the box (none expected: the image path and
  `.env` are unchanged).
- Never read or copy `/opt/puntmore-scraper/data/profile/` (login cookies).
