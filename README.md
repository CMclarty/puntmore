# Puntmore

Betting tools: a web app on Render, and a home box on a residential connection that reads bookmaker pages and, for the 2-Up Engine, places bets. One repo, one directory per deployable. The rules for working here are in `CLAUDE.md`; current work is in `PLAN.md`; design detail is in `SPEC.md`.

## What runs where

| Directory | Runs on | Built and deployed by | Updated when |
|---|---|---|---|
| `api/` + `frontend/` | Render, one Docker web service (`puntmore.com`) | `render.yaml`: `api/Dockerfile` builds the frontend into the API image | **every push to `main`** |
| `scraper/` | home box, Docker container, real Chrome on Xvfb | `.github/workflows/scraper-image.yml` publishes to GHCR; the box's `update.sh` cron pulls `:latest` | push to `main` touching `scraper/` |
| `home/` | home box, systemd user services | `home/deploy/systemd/`; the box runs this checkout | `git pull` on the box |
| `clients/python/` | any machine client | from the repo | on demand |
| `local/` | one-off scripts on the home box | by hand | — |

Only `main` deploys Render, so nothing is pushed to `main` directly: work on a branch, open a pull request, let the path-scoped workflows run.

## The two boxes

**Render** prices and decides. It holds the odds API clients, the 2-Up decision layer, the per-client ledgers, and the control page that tells the scraper what to read. It never loads a bookmaker page.

**The home box** touches bookmakers. `scraper/` keeps Paddy Power and Bookie Bashing pages open in one real Chrome and posts prices to the API. `home/` is the 2-Up side: a persistent outbound link to Render, the client engine that places bets on sub-client phones, escalation to the local AI, and the Bet Finder's scrape sources. The contract between the two boxes is `home/PROTOCOL.md` and `home/schemas/`.

## Running the tests

```
cd api && .venv/bin/python -m pytest tests/ -q       # Render side
cd frontend && npm run lint && npm test && npm run build
python -m pytest scraper/tests -q                   # scraper (needs playwright + chromium)
cd home && ~/.venvs/puntmore/bin/pytest -q          # home box, including the protocol contract tests
```

Each directory has its own README with setup and day-to-day commands.
