# Puntmore

Betting tools in two boxes. **Render** is the cloud service: one deploy that prices markets and
serves the tools. The **Hub** is Callum's box on a residential connection: it reads every
bookmaker and tipster page, judges tips, and is where bets are placed from. One repository for
both, one directory per deployable.

## Read in this order

1. `docs/decisions.md`: the decisions register. It wins over every other file.
2. `docs/architecture.md`: the components, where each runs, what crosses between the boxes.
3. `docs/dataflow.md`: how one row travels from a page to a placed bet.
4. The three specs: `docs/2up-engine-spec.md` (2-Up), `docs/finder-spec.md` (Finder),
   `docs/placer-spec.md` (the Bet Placer).
5. `CLAUDE.md` (rules for the whole repo), then the `AGENTS.md` of the directory you work in.
6. `PLAN.md`: the open work.

Nothing about the system lives outside the repo.

## Render

One Docker web service; every push to `main` deploys it. Render never loads a bookmaker page and
never places a bet. **OddsPapi** prices Paddy, 2-Up and the coupon tracker; **odds-api.net**,
the second odds provider, prices Hills (11 Nov 2026).

It serves **boards**: tool pages, each with a one-way HTTP **feed** of value rows.

| Board | What it is | Feed |
|---|---|---|
| **Paddy** | corners, cards, goals and saves against Paddy Power | `GET /api/paddy/feed` |
| **2-Up** | bet365's early-payout value, inside the **365 tool** | `GET /api/2up/feed` |
| **Finder** | the tips board: the tips HubAI has judged; the feed is the ones it offered | `GET /api/finder/feed` |

Beside the boards: the coupon tracker, Hills, and Callum's personal tools (dogs, Virtuals, P&L,
Kelly, the shop, AW 26). No ledger, no account and no Hub endpoint on Render: the Hub calls
tools' own routes with a key, as HubAI's key collects AW 26's photos from the tool's queue
(`/api/aw26/photos`).

Two hosts from the one deploy: the **main domain** (`puntmore.com`) serves every public tool
(the coupon tracker, Paddy, Hills, 365 with 2-Up), the login and the API; **`me`**
(`me.puntmore.com`) serves Callum's personal tools, Finder among them, with its own login.
`me` is switched on by `ME_HOST`: blank (the default) there is no `me`, and its tools are served
on the main domain. To switch it on, add the `me.puntmore.com` DNS record and the Render custom
domain, check the host resolves to the service, then set `ME_HOST=me.puntmore.com` on Render.

## The Hub

One program on Callum's box, in five parts:

- **Scraper service** (`scraper/`): the one real Chrome on the desktop; reads every page any
  tool needs and fills what OddsPapi lacks. It hands up two kinds of row: **prices** (raw rows
  for the Paddy board, which turns them into value bets: Paddy Power's lines off a Paddy page,
  and the fair prices for Paddy's saves read from Bookie Bashing) and **tips** (bets from an
  external source).
- **Scraper control**: rules, routing and switches for every source's rows (pages and the
  tipster mailbox), driven from the Console; rows go from the Scraper service to Scraper control
  and on to a board or to HubAI. Built today: the targets file and its library
  (`scraper/targets.py`), the Hub's inbox of the Scraper's drop files and the mailbox poll
  (`hub/`). Paddy's prices go through it too: the inbox posts the Scraper's price drops to
  Render.
- **HubAI**: the Hub's AI, the whole Hermes layer (a chief of staff on Telegram and its
  specialist profiles, one Qwen model under llama-server). It takes every escalation and judges
  tips for Finder, including a tip Callum asks for on Telegram; that Finder skill is the next
  HubAI work.
- **Bet Placer**: reads the three feeds, sizes, places on the phones through DeviceFarmer,
  keeps the only book, and posts `placed` / `gone` to Finder. A black box for now
  (`docs/placer-spec.md`); the placement engine in `hub/puntmore_hub/placement/` is its start.
- **Console** (`puntmore web`): the page that shows every part and carries the control page;
  reachable through a private tunnel.

## What runs where

| Directory | Runs on | Deployed by | Updated when |
|---|---|---|---|
| `render/api/` + `render/frontend/` | Render, one Docker web service | `render.yaml`; `render/api/Dockerfile` builds the frontend into the API image | every push to `main` |
| `scraper/` | Hub: the Scraper service, user service `puntmore-scraper` on the desktop session (Docker is the fallback for a box with no screen) | `scraper/deploy/native/`; fallback `.github/workflows/scraper-image.yml` | `puntmore-update.timer`, every 10 min |
| `hub/` | Hub: the daemon, the Console, HubAI's CLI, the placement engine; systemd user services | `hub/deploy/systemd/` on the box's checkout | `puntmore-update.timer`, every 10 min |
| `clients/python/` | any machine client of the API | from the repo | on demand |
| `local/` | one-off scripts on the Hub | by hand | — |

Only `main` deploys, so nothing is pushed to `main` directly: work on a branch, open a pull
request, let the path-scoped workflows run.

## Tests

```
cd render/api && .venv/bin/python -m pytest tests/ -q                 # Render's API
cd render/frontend && npm run lint && npm test && npm run build       # Render's frontend
cd hub && ~/.venvs/puntmore/bin/python -m pytest -q                   # the Hub
PYTHONPATH=. ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q  # the Scraper (needs Chrome)
```

Each directory's README has its setup and day-to-day commands.
