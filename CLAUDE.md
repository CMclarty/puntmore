# Puntmore — project rules

Read `docs/decisions.md` first: it is the decisions register, and when any
other file (this one included) disagrees with it, it is right and the other
file is stale. Then `PLAN.md` for current work; `SPEC.md` is the web app's
original design and is history. This file is guardrails for the whole repo.

## What this is
A betting-tools suite: one FastAPI backend and one React frontend in ONE
Docker web service on Render, serving three subdomains (`2up`, `shalgo`, `me`;
`docs/decisions.md`), plus the Hub (`hub/`, `scraper/`) on Callum's box.

> History (superseded 5 Nov 2026): in July 2026 only the coupon tracker was
> surfaced, with AccaBonus and Cards/Corners hidden behind `ENABLE_ACCABONUS` /
> `ENABLE_CARDS`. The acca boost package and `ENABLE_ACCABONUS` are gone; Paddy
> is mounted on `ENABLE_CARDS`. Still true: the coupon tracker prices via
> **OddsPapi** (the only odds source), de-vigs in-house, derives Win-and-Over
> off the fitted score grid, and OCR **auto-confirms** (rows save and price on
> upload, with inline edit and re-scan).

What runs where (the full table is in `README.md`; keep the two in step):

| Directory | Runs on | Deployed by | Updated when |
|---|---|---|---|
| `render/api/` + `render/frontend/` | Render, ONE Docker web service | `render.yaml`; `render/api/Dockerfile` builds the frontend into the API image, `app/asgi.py` serves both | **every push to `main`** |
| `scraper/` | Hub, the Scraper: user service `puntmore-scraper` on the desktop session (Docker is the fallback for a box with no screen) | `hub/deploy/`; fallback `scraper-image.yml` → GHCR + `update.sh` cron | `puntmore-update.timer`, every 10 min |
| `hub/` | Hub, systemd user services | `hub/deploy/systemd/` on a checkout | `puntmore-update.timer` pulls `main` every 10 min and restarts the services (`hub/deploy/update.sh`) |
| `hub/puntmore_hub/placement/` | Hub → sub-client phones | part of `hub/` | **placeholder**: the bet-placing bot (bets in, results out); contract in its README |

Nothing goes to `main` directly: branch, pull request, path-scoped workflows.

Every tool is a route in one React app, not a separate build; the backend
serves them all under namespaced routers. (History: the original three tools
were `/`, `/accabonus`, `/cards`; `/tennis` is the AccaBonus tool's legacy
prefix, kept as redirect aliases for stale PWA clients; don't add new code
under it.)
**365** (Oct 2026) is the bet365 offers, one tool with a tab each at
`/365/<tab>`: `acca` (the acca boost, removed 5 Nov 2026 and returning on OddsPapi, `accabonus` grant) and `scorers`
(the goalscorer finder, `goalscorers` grant, PARKED 5 Nov 2026 behind `ENABLE_GOALSCORERS=false`); the 2-Up engine
(`app/twoup/`, Nov 2026, `ENABLE_TWOUP`) is the live part of it. `/accabonus`, `/tennis` and `/goalscorers` redirect into it; the
grants, flags and API paths keep their old names.
The cards & corners tool is now **Paddy** (Oct 2026: corners, cards, goals and
saves, all against Paddy Power): page `/paddy`, API `/api/paddy/*`. `/cards`
is its legacy alias in the same way — page redirect, API mounted twice. The
permission, `ENABLE_CARDS` and the `app/cards/` package keep the old name on
purpose: renaming the permission revokes existing accounts' access.
**Virtuals** (Oct 2026) is the virtual-racing boost priced INSIDE the virtuals
P&L ledger, one page at `/virtuals`: Price → Boosted → Runners wheels, the
boost's call (each way / win only / skip, `boost.js::verdictFor`) above the
result keys that file the bet, and the switch follows the call until tapped.
A boosted bet stores the model's EV in pounds (`bets.ev`), which is virtuals'
EV line — it has no Betfair SP. `/boost` and `/pnl/virtuals` redirect there;
an account granted `boost` without `pnl` gets the old calculator
(`BoostPage`) at the same address. The nav calls the dogs ledger "Dogs".
**AW 26** (the acca project's slip ledger) was retired on 29 Oct 2026 — see
`docs/retired.md`; its code is on the `archive/aw26` branch.

## Where the rules live
This file holds what applies to the whole repo. Each deployable has its own
`AGENTS.md` with the rules for that code, moved out of here on 5 Nov 2026 with
their reasons intact (an agent working in that directory reads it; Claude Code
loads it through the directory's `CLAUDE.md`):

| Directory | File | What it covers |
|---|---|---|
| `render/api/` | `render/api/AGENTS.md` | odds sources, pricing and OCR hard rules, the provider seams, API clients |
| `render/frontend/` | `render/frontend/AGENTS.md` | UI rules (tools, build only what is asked), the acca maths |
| `scraper/` | `scraper/AGENTS.md` | the Scraper: every page load on the Hub |
| `hub/` | `hub/AGENTS.md` | the 2-Up Hub: link, placement, escalation, the inbox of the Scraper's drops |

`docs/decisions.md` is read first and wins; then `PLAN.md` → **Decisions** (the
older product decisions) is settled and applies everywhere. The architecture is
`docs/architecture.md`, how information moves through it is `docs/dataflow.md`, and the 2-Up spec is `docs/2up-engine-spec.md`; all three live
here and nowhere else, and change in the same pull request as the code.

## Hard rules (do not violate)
- NEVER commit secrets. All credentials (OddsPapi key, OCR key, Discord
  webhook, auth users + session key) live in `.env` only.
  `.env` is git-ignored; only `.env.example` is committed.
- The API (`render/api/`, on Render) never places a bet and no bookmaker-account
  automation is added to it (`render/api/AGENTS.md`, API clients). The engine that
  produces bet lists lives on Render; placement lives on the Hub (`hub/`);
  every page load lives in the Scraper (`scraper/`). Decisions 5 Nov 2026.
- Every directory's own hard rules are in its `AGENTS.md`; they are not
  optional because they live in a different file.

## Workflow
- Work the numbered items in `PLAN.md` → **Open work**, in order. Complete and
  verify one before starting the next.
- Tests/verification steps are part of each item, not an afterthought.
- `docs/decisions.md` first, then `PLAN.md` → **Decisions**, are settled — do not
  re-litigate. A decision changes only by Callum, in a dated edit to the register.
- Secrets: a gitleaks pre-commit hook blocks commits containing them. On a fresh
  clone, enable it with `git config core.hooksPath .githooks` (gitleaks must be
  on PATH). Rules are in `.gitleaks.toml` — the tool's defaults miss this repo's
  known leak shape, so don't replace them with a stock config.

## Stack & deployment
- Backend: FastAPI (Python), one app, routers namespaced per tool.
- Frontend: one React (Vite) app. `render/api/Dockerfile` builds it and copies the
  static bundle into the API image; `app/asgi.py` serves the SPA at `/` and the
  API under `/api` — ONE origin (required: the session cookie is `same_site=lax`
  and the frontend calls relative `/api/...`). CSS modules + vars in `index.css`.
- **Deployment is Render only.** ONE Docker web service (`render.yaml`),
  `autoDeploy: true` → **every push to `main` deploys** (`puntmore.com`, served at
  216.24.57.x). There is NO local docker-compose / nginx stack — it was removed;
  don't reintroduce one. To deploy: merge a pull request into `main`. Health gate
  is `/api/ready`.
- Storage: SQLite on the Render persistent disk at `/data` (`DATABASE_URL`);
  uploaded images under `/data/uploads`. Single instance — SQLite has one writer.
- Scheduler: APScheduler (in-process).

## Adding a new tool page
1. Add the compute logic under `render/api/app/<tool>/` (and, if it needs odds,
   a consumer of the shared `OddsPapiClient`).
2. Add a router under `render/api/app/routers/<tool>.py`; include it in `main.py`.
3. Add a Pydantic response schema in `schemas.py`.
4. Frontend: add a page under `render/frontend/src/pages/`, a route in `App.jsx`, and a
   tool in `components/BottomNav.jsx` (and `site.js` if it belongs on shalgo).
   Reuse an existing page's CSS module where the layout matches.

## Fresh clone
Everything except `.env` is in the repo. (History: verified July 2026 by cloning from
GitHub and building from nothing: the test suite, the frontend build and the
Render Docker image all came up clean with NO credentials present.)

```
git clone git@github.com:CMclarty/puntmore.git && cd puntmore
uv venv render/api/.venv
uv pip install -r render/api/requirements.txt -r render/api/requirements-dev.txt \
   --python render/api/.venv/bin/python3
(cd render/frontend && npm install)
git config core.hooksPath .githooks     # gitleaks pre-commit (needs gitleaks on PATH)
```

`.env` is the ONLY thing that cannot be cloned — copy it across by hand; every
variable is documented in `.env.example`. Without it you still get the full test
suite (hermetic: the OCR and odds providers are mocked), the frontend
build, and deploys. What needs real keys is LIVE verification — running the real
OCR over a coupon photo and pricing it against the live board — which is what
catches the bugs unit tests cannot.

## Commands
```
# Run tests (from render/api/):
.venv/bin/python -m pytest tests/ -q

# Run API locally (from render/api/):
.venv/bin/uvicorn app.main:app --reload

# Run the frontend dev server (from render/frontend/):
npm run dev

# Deploy: merge a pull request into main — Render autoDeploys (no local stack).
gh pr create --base main

# The derived-market calibration backtest (tools/backtest_calibration.py) is
# PLANNED, not written: PLAN.md open item 3.

# Frontend unit tests (from render/frontend/) — vitest, currently the acca-insurance maths:
npm test

# Install a new Python package into the venv (no pip in venv — use uv):
uv pip install <package> --python render/api/.venv/bin/python3
```
