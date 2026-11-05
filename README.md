# Puntmore

Betting tools: a web app on Render, and a Hub on a residential connection that reads bookmaker pages and, for the 2-Up Engine, places bets. One repo, one directory per deployable, and **one source for everything**: this file says what runs where, `CLAUDE.md` the rules, `PLAN.md` what is decided and what is next, `docs/architecture.md` the architecture, `docs/2up-engine-spec.md` the 2-Up spec, `SPEC.md` the web app's design. Nothing about the system lives outside the repo.

## What runs where

| Directory | Runs on | Built and deployed by | Updated when |
|---|---|---|---|
| `api/` + `frontend/` | Render, one Docker web service (`puntmore.com`) | `render.yaml`: `api/Dockerfile` builds the frontend into the API image | **every push to `main`** |
| `scraper/` | Hub, Docker container, real Chrome on Xvfb | `.github/workflows/scraper-image.yml` publishes to GHCR; the box's `update.sh` cron pulls `:latest` | push to `main` touching `scraper/` |
| `hub/` | Hub, systemd user services | `hub/deploy/systemd/`; the box runs this checkout | `git pull` on the box |
| `hub/puntmore_hub/placement/` | Hub, driving the sub-client phones | part of `hub/` | **placeholder**: the bet-placing bot. Bets in, results out. Contract in its README; only the dry-run adapter exists |
| `clients/python/` | any machine client | from the repo | on demand |
| `local/` | one-off scripts on the Hub | by hand | — |

Only `main` deploys Render, so nothing is pushed to `main` directly: work on a branch, open a pull request, let the path-scoped workflows run.

## The two boxes

**Render** finds, prices and decides bets and holds the ledger. It is one deploy serving three subdomains: `2up.puntmore.com` (the 2-Up product: the 365 tool, the Bet Finder orchestrator, the Hub endpoint), `shalgo.puntmore.com` (the Paddy Power tools, a setup of their own fed by the Hub's worker, and the coupon tracker) and `me.puntmore.com` (Cal's personal tools, owner only). It runs the app, the 365 tool (acca boost, goalscorers and the 2-Up engine, one tool), the Bet Finder orchestrator, and the odds API client. **OddsPapi is the only odds source** (the leftover TheOddsApi code goes in PLAN.md item 11). Render never loads a bookmaker page and never places a bet.

**The Hub** does only what needs a residential IP, the phones or the GPU. `scraper/` keeps Paddy Power and Bookie Bashing pages open in one real Chrome and posts prices to the API. `hub/` holds the link to Render, the client engine that places bets on sub-client phones, escalation, and the Bet Finder's scrape sources. Hermes owns the Hub: it runs the schedules and takes every escalation. The bet-placing bot itself, the part that takes a bet and returns `placed`, `price_changed` or `failed`, is a placeholder: `hub/puntmore_hub/placement/README.md` has its contract and the stub to replace. The contract between the two boxes is `hub/PROTOCOL.md` and `hub/schemas/`.

## Running the tests

```
cd api && .venv/bin/python -m pytest tests/ -q       # Render side
cd frontend && npm run lint && npm test && npm run build
python -m pytest scraper/tests -q                   # scraper (needs playwright + chromium)
cd home && ~/.venvs/puntmore/bin/pytest -q          # Hub, including the protocol contract tests
```

Each directory has its own README with setup and day-to-day commands.
