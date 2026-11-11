# Puntmore — project rules

Read `docs/decisions.md` first: it is the decisions register, and when any other file (this one
included) disagrees with it, it is right and the other file is stale. Then `README.md` for the
structure and the reading order, and `PLAN.md` for the open work. This file is guardrails for the
whole repo; each directory's `AGENTS.md` holds the rules for its code.

## What runs where

`README.md` has the full table; keep the two in step.

| Directory | Runs on | Deployed by | Updated when |
|---|---|---|---|
| `render/api/` + `render/frontend/` | Render, ONE Docker web service | `render.yaml`; `render/api/Dockerfile` builds the frontend into the API image, `app/asgi.py` serves both | **every push to `main`** |
| `scraper/` | Hub: the Scraper service, user service `puntmore-scraper` on the desktop session (Docker is the fallback for a box with no screen) | `scraper/deploy/native/`; fallback `scraper-image.yml` | `puntmore-update.timer`, every 10 min |
| `hub/` | Hub: the daemon, the Console, the Finder CLI, escalations to HubAI, the placement engine | `hub/deploy/systemd/` on a checkout | `puntmore-update.timer` pulls `main` every 10 min and restarts the services |

Nothing goes to `main` directly: branch, pull request, path-scoped workflows.

## The tools

Every tool is a route in one React app, not a separate build; the backend serves them all under
namespaced routers. Two hosts: the main domain serves every public tool, `me` serves Callum's
personal tools (`render/api/app/site.py`, mirrored in `render/frontend/src/site.js`); a retired
host is redirected to the main domain, same path (`docs/retired.md`). `me` exists only once
`ME_HOST` is set (the register); while it is blank, every tool below said to be "on `me`" is on
the main domain.

- **Coupon tracker** (`/`, `coupons` grant): photographed coupons priced off the score grid.
- **Paddy** (`/paddy`, API `/api/paddy/*`, `cards` grant, `ENABLE_CARDS`, package `app/cards/`):
  corners, cards, goals and saves against Paddy Power; a board, feed `GET /api/paddy/feed`.
  `/cards` is its legacy alias, page redirect and API mounted twice. The grant, flag and package
  keep the old name on purpose: renaming a grant revokes accounts' access.
- **Hills** (`/hills`, `hills` grant): William Hill boosted bet builders; priced from odds-api.net
  (`ODDSAPINET_API_KEY`), OddsPapi without it.
- **365** (`/365/<tab>`, `accabonus` grant): bet365's offers, one tool. Tabs: `2up`, **2-Up**
  (`app/twoup/`, `ENABLE_TWOUP`), a board whose one-way feed is `GET /api/2up/feed`, the page
  showing the same rows; `acca`, the acca boost, removed and returning on OddsPapi; `scorers`,
  goalscorers (`goalscorers` grant), parked behind `ENABLE_GOALSCORERS=false`. `/accabonus`,
  `/tennis` and `/goalscorers` redirect into it; don't add new code under `/tennis`.
- **Finder** (`/finder` on `me`, `finder` grant, `app/finder/`): Callum's tips, judged on the Hub
  by HubAI and posted to Render; a board, feed `GET /api/finder/feed` (`docs/finder-spec.md`).
- **Virtuals** (`/virtuals` on `me`): the virtual-racing boost priced inside the virtuals P&L
  ledger: Price → Boosted → Runners wheels, the boost's call (each way / win only / skip,
  `boost.js::verdictFor`) above the result keys, and the switch follows the call until tapped.
  A boosted bet stores the model's EV in pounds (`bets.ev`). `/boost` and `/pnl/virtuals`
  redirect there; an account granted `boost` without `pnl` gets the calculator (`BoostPage`).
- **P&L** (Dogs), **Kelly**, **the shop**: on `me`.
- **AW 26** (`/aw26` on `me`, `aw26` grant, `app/aw26/`): Callum's slips from the Paddy
  terminal, a page over the stored rows with two screens: a Summary (the all-time profit, the
  day against the targets, the selections in aggregate with win or lose) and a Slips sub
  screen. A photo uploaded on the page waits in a queue on Render (`aw_photos`) for the Hub
  to collect; HubAI reads it on the Hub and records the slip. Slips are recorded and marked
  on the same `aw26` routes by the page and by HubAI's key. Render does the sums
  (`aw26/offers.py`: EV from odds and fairs, returns from leg results) when a request asks;
  it reads no photo and runs nothing in the background, except the 04:00 sweep of the upload
  folder that removes files no row points at (`app/orphan_files.py`, the register).
  HubAI values a slip off Paddy's board as it stood when the slip was placed, which the
  Paddy sweep keeps for 14 days (`app/cards/history.py`, `GET /api/paddy/board-at`).
  Slips placed before 9 Nov 2026 stay stored and count nowhere.

The **Bet Placer** (`docs/placer-spec.md`) reads the three boards' feeds and places on the Hub;
nothing on Render stakes or places.

## Where the rules live

| Directory | File | What it covers |
|---|---|---|
| `render/api/` | `render/api/AGENTS.md` | odds sources, pricing and OCR hard rules, the provider seams, API clients |
| `render/frontend/` | `render/frontend/AGENTS.md` | UI rules (tools, build only what is asked), the acca maths |
| `scraper/` | `scraper/AGENTS.md` | the Scraper service: every page load on the Hub |
| `hub/` | `hub/AGENTS.md` | the Hub: daemon, Finder CLI, escalations, Console, the placement engine |

An agent working in a directory reads its `AGENTS.md`; Claude Code loads it through the
directory's `CLAUDE.md`. The architecture is `docs/architecture.md`, the flow of a row is
`docs/dataflow.md`, the specs are `docs/2up-engine-spec.md`, `docs/finder-spec.md` and
`docs/placer-spec.md`; all live here and nowhere else, and change in the same pull request as the
code.

## Hard rules (do not violate)

- NEVER commit secrets. All credentials (OddsPapi key, OCR key, Discord webhook, auth users and
  session key) live in `.env` only. `.env` is git-ignored; only `.env.example` is committed.
- Render (`render/api/`) never places a bet and never loads a bookmaker page; no
  bookmaker-account automation is added to it (`render/api/AGENTS.md`, API clients). Pricing and
  the boards live on Render; sizing and placing live in the Bet Placer; every page load lives in
  the Scraper service (`scraper/`).
- Every directory's own hard rules are in its `AGENTS.md`; they are not optional because they
  live in a different file.

## Workflow

- Work the numbered items in `PLAN.md` → **Open work**, in order. Complete and verify one before
  starting the next. Tests and verification are part of each item.
- The register is settled: do not re-litigate it. A decision changes only by Callum, in a dated
  edit to the register.
- Secrets: a gitleaks pre-commit hook blocks commits containing them. On a fresh clone, enable it
  with `git config core.hooksPath .githooks` (gitleaks must be on PATH). Rules are in
  `.gitleaks.toml`; the tool's defaults miss this repo's known leak shape, so don't replace them
  with a stock config.

## Stack and deployment

- Backend: FastAPI (Python), one app, routers namespaced per tool.
- Frontend: one React (Vite) app. `render/api/Dockerfile` builds it and copies the static bundle
  into the API image; `app/asgi.py` serves the SPA at `/` and the API under `/api`, ONE origin
  (required: the session cookie is `same_site=lax` and the frontend calls relative `/api/...`).
  CSS modules + vars in `index.css`.
- **Deployment is Render only.** ONE Docker web service (`render.yaml`), `autoDeploy: true`:
  **every push to `main` deploys** (`puntmore.com`). There is no local docker-compose or nginx
  stack; don't reintroduce one. To deploy, merge a pull request into `main`. Health gate:
  `/api/ready`.
- Storage: SQLite on the Render persistent disk at `/data` (`DATABASE_URL`); uploaded images
  under `/data/uploads`. Single instance: SQLite has one writer.
- Scheduler: APScheduler, in-process.

## Adding a new tool page

1. Add the compute logic under `render/api/app/<tool>/` (and, if it needs odds, a consumer of
   the shared `OddsPapiClient`).
2. Add a router under `render/api/app/routers/<tool>.py`; include it in `main.py`.
3. Add a Pydantic response schema in `schemas.py`.
4. Frontend: a page under `render/frontend/src/pages/`, a route in `App.jsx`, a tool in
   `components/BottomNav.jsx`, and its grant in `site.js`'s `ME_TOOLS` (and `site.py`'s) if it
   belongs on `me`. Reuse an existing page's CSS module where the layout matches.

## Fresh clone

Everything except `.env` is in the repo.

```
git clone git@github.com:CMclarty/puntmore.git && cd puntmore
uv venv render/api/.venv
uv pip install -r render/api/requirements.txt -r render/api/requirements-dev.txt \
   --python render/api/.venv/bin/python3
(cd render/frontend && npm install)
git config core.hooksPath .githooks     # gitleaks pre-commit (needs gitleaks on PATH)
```

`.env` is the ONLY thing that cannot be cloned: copy it across by hand; every variable is
documented in `.env.example`. Without it you still get the full test suite (hermetic: the OCR
and odds providers are mocked), the frontend build and deploys. What needs real keys is live
verification (the real OCR over a coupon photo, priced against the live board), which catches
the bugs unit tests cannot.

## Commands

```
# API tests (from render/api/):
.venv/bin/python -m pytest tests/ -q

# Run the API locally (from render/api/):
.venv/bin/uvicorn app.main:app --reload

# Frontend dev server, unit tests (vitest), lint and build (from render/frontend/):
npm run dev
npm test && npm run lint && npm run build

# Deploy: merge a pull request into main (Render autoDeploys).
gh pr create --base main

# Install a Python package into the venv (no pip in the venv; use uv):
uv pip install <package> --python render/api/.venv/bin/python3
```
