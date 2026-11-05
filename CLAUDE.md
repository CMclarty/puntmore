# Puntmore — project rules

Read `SPEC.md` for design detail and `PLAN.md` for current work. This file is
guardrails for the whole repo.

## What this is
A self-hosted betting-tools suite: three tools share one FastAPI backend and one
React frontend, behind a shared nginx layer.

> **Current status (July 2026):** only the **coupon tracker** is surfaced.
> AccaBonus and Cards/Corners are hidden — their routers are gated on
> `ENABLE_ACCABONUS` / `ENABLE_CARDS` (default False) and their nav tabs/routes
> are suppressed. Code, tests, and the seams are all kept; this is a visibility
> gate, not a removal. The disabled routers / coupon-only UI are intentional —
> do not "fix" them. The coupon tracker prices via **OddsPapi**
> (`COUPON_ODDS_SOURCE=oddspapi`, the only odds source), de-vigs in-house,
> and derives Win-and-Over off the fitted score grid. OCR **auto-confirms**: rows save and
> price on upload, with inline edit and re-scan.

What runs where (the full table is in `README.md`; keep the two in step):

| Directory | Runs on | Deployed by | Updated when |
|---|---|---|---|
| `render/api/` + `render/frontend/` | Render, ONE Docker web service | `render.yaml`; `render/api/Dockerfile` builds the frontend into the API image, `app/asgi.py` serves both | **every push to `main`** |
| `scraper/` | Hub, Docker | `scraper-image.yml` → GHCR; the box's `update.sh` cron | push to `main` touching `scraper/` |
| `hub/` | Hub, systemd user services | `hub/deploy/systemd/` on a checkout | `git pull` on the box |
| `hub/puntmore_hub/placement/` | Hub → sub-client phones | part of `hub/` | **placeholder**: the bet-placing bot (bets in, results out); contract in its README |

Nothing goes to `main` directly: branch, pull request, path-scoped workflows.

The three tools are routes in one React app (`/`, `/accabonus`, `/cards`), not
separate builds. The backend serves them all under namespaced routers.
(`/tennis` is the AccaBonus tool's legacy prefix — kept as redirect/API aliases
for stale PWA clients; don't add new code under it.)
**365** (Oct 2026) is the bet365 offers, one tool with a tab each at
`/365/<tab>`: `acca` (the acca boost, removed 5 Nov 2026 and returning on OddsPapi, `accabonus` grant) and `scorers`
(the goalscorer finder, `goalscorers` grant, PARKED 5 Nov 2026 behind `ENABLE_GOALSCORERS=false`); 2-Up joins when its bet365 feed
exists. `/accabonus`, `/tennis` and `/goalscorers` redirect into it; the
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
| `scraper/` | `scraper/AGENTS.md` | the Paddy / Bookie Bashing worker |
| `hub/` | `hub/AGENTS.md` | the 2-Up Hub: link, placement, escalation, Bet Finder sources |

`PLAN.md` → **Decisions** is settled and applies everywhere. The architecture is
`docs/architecture.md` and the 2-Up spec is `docs/2up-engine-spec.md`; both live
here and nowhere else, and change in the same pull request as the code.

## Hard rules (do not violate)
- NEVER commit secrets. All credentials (OddsPapi key, OCR key, Discord
  webhook, auth users + session key) live in `.env` only.
  `.env` is git-ignored; only `.env.example` is committed.
- The API (`render/api/`, on Render) never places a bet and no bookmaker-account
  automation is added to it (`render/api/AGENTS.md`, API clients). Placement and the
  engine that produces bet lists live on the Hub (`hub/`, decision 5 Nov 2026).
- Every directory's own hard rules are in its `AGENTS.md`; they are not
  optional because they live in a different file.

## Workflow
- Work the numbered items in `PLAN.md` → **Open work**, in order. Complete and
  verify one before starting the next.
- Tests/verification steps are part of each item, not an afterthought.
- `PLAN.md` → **Decisions** is settled — do not re-litigate.
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
  don't reintroduce one. To deploy: push to `main`. Health gate is `/api/ready`.
- Storage: SQLite on the Render persistent disk at `/data` (`DATABASE_URL`);
  uploaded images under `/data/uploads`. Single instance — SQLite has one writer.
- Scheduler: APScheduler (in-process).

## Adding a new tool page
1. Add the compute logic under `render/api/app/<tool>/` (and a client consumer of
   `TheOddsApiClient` if it needs odds).
2. Add a router under `render/api/app/routers/<tool>.py`; include it in `main.py`.
3. Add a Pydantic response schema in `schemas.py`.
4. Frontend: add a page under `render/frontend/src/pages/`, a route in `App.jsx`, and a
   tab in `components/NavTabs.jsx`. Reuse `DataTable.module.css` for tables.

## Fresh clone
Everything except `.env` is in the repo. Verified July 2026 by cloning from
GitHub and building from nothing: the test suite, the frontend build and the
Render Docker image all come up clean with NO credentials present.

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
suite (321 tests, hermetic: the OCR and odds providers are mocked), the frontend
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

# Deploy: push to main — Render autoDeploys (no local stack).
git push origin HEAD:main

# Re-run the derived-market calibration backtest (from render/api/):
.venv/bin/python tools/backtest_calibration.py

# Frontend unit tests (from render/frontend/) — vitest, currently the acca-insurance maths:
npm test

# Install a new Python package into the venv (no pip in venv — use uv):
uv pip install <package> --python render/api/.venv/bin/python3
```
