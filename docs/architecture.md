# Puntmore architecture

Two boxes. **Render** prices markets and serves the boards; it never loads a bookmaker page and
never places a bet. The **Hub**, Callum's box on a residential connection, runs one program in
five parts: it reads every page, judges tips, and is where bets are placed from. The names are
the register's (`docs/decisions.md`, "Names and structure"); the register wins over this file.
Update this file in the same pull request as the change it describes.

*As of 6 Nov 2026. State: **live** built and running · **built** built and tested, not yet in
daily use · **to build** specified, not started · **parked** code kept, switched off.*

## The picture

```mermaid
flowchart TB
  subgraph RENDER["RENDER — one Docker web service; two hosts"]
    direction TB
    subgraph MAIN["main domain — every public tool, the login, the API"]
      PADDY["Paddy board<br/>corners, cards, goals, saves vs Paddy Power<br/><i>live</i>"]
      T365["365 tool<br/>with the 2-Up board inside<br/><i>live</i>"]
      PUB["Coupon tracker · Hills<br/><i>live</i>"]
    end
    subgraph ME["me — Callum's personal tools; on the main domain until ME_HOST is set"]
      FINDER["Finder board<br/>Callum's tips, judged on the Hub<br/><i>live on the main domain until ME_HOST is set</i>"]
      PERS["Dogs · Virtuals · P&amp;L · Kelly · shop<br/><i>live on the main domain until ME_HOST is set</i>"]
    end
  end

  ODDS["OddsPapi<br/>the only odds source"] -- "prices (HTTPS)" --> PADDY
  ODDS -- "prices (HTTPS)" --> T365

  subgraph HUB["HUB — Callum's box, one program"]
    direction TB
    SVC["Scraper service<br/>one real Chrome on the desktop; every page any tool needs<br/><i>live</i>"]
    CTRL["Scraper control<br/>rules, routing, switches for every source's rows<br/><i>part built: targets library, inbox, mailbox poll</i>"]
    HUBAI["HubAI<br/>the Hermes layer: chief of staff + specialists, one Qwen model<br/><i>live; Finder skill to build</i>"]
    subgraph PLACER["Bet Placer — a black box for now"]
      STAKE["staking logic<br/><i>to build</i>"]
      PHONEIF["phone interface<br/><i>to build</i>"]
    end
    CONSOLE["Console<br/>every part, live; the control page<br/><i>built</i>"]
  end

  SITES["Sites<br/>Paddy Power, tip sources; one Bookie Bashing page (open item)"] -- "pages" --> SVC
  MAILBOX["Tipster mailbox"] -- "IMAP" --> CTRL
  SVC -- "tips: drop files (scraper_tips, scraper_event)" --> CTRL
  SVC -- "prices: POST /api/paddy/saves-fair, /most-prices (direct today)" --> PADDY
  CTRL -- "new tips (hub.sqlite); escalations" --> HUBAI
  HUBAI -- "POST /api/finder/rows" --> FINDER
  HUBAI <-->|"Telegram: questions, tips asked for by hand"| CALLUM["Callum"]
  PADDY -- "GET /api/paddy/feed" --> STAKE
  T365 -- "GET /api/2up/feed" --> STAKE
  FINDER -- "GET /api/finder/feed" --> STAKE
  STAKE --> PHONEIF
  PHONEIF -- "DeviceFarmer (local port)" --> PHONES["Phones<br/>bookmaker accounts in the app and the browser"]
  PLACER -- "POST /api/finder/rows/{tip_id}/status (placed | gone)" --> FINDER
  CONSOLE -. "reads" .- SVC
  CONSOLE -. "reads" .- CTRL
  CONSOLE -. "reads" .- HUBAI
  TUNNEL["private tunnel"] --- CONSOLE
  ME -. "links to" .- TUNNEL

  THIRD["Third-party client<br/>its own Bet Placer, 2-Up only"] -- "GET /api/2up/feed" --> T365
```

## Components

| Component | What it is | Where | State |
|---|---|---|---|
| Render service | FastAPI + React, one origin, SQLite on the disk; the main domain and `me` (`app/site.py`; `me` only once `ME_HOST` is set) | Render | live |
| Paddy | Corners, cards, goals and saves against Paddy Power (`app/cards/`); OddsPapi plus the Scraper's posts | Render · main | live |
| 365 tool, 2-Up | bet365's offers; 2-Up (`app/twoup/`) is a served board refreshed by `twoup_refresh`, read at `GET /api/2up/feed` and on the 2-Up tab; acca boost returns on OddsPapi; goalscorers parked | Render · main | live |
| Coupon tracker, Hills | photographed coupons priced off the score grid; William Hill boosts | Render · main | live |
| Finder | `finder_rows`, its status machine, `/api/finder/*` (`app/finder/`, `docs/finder-spec.md`) | Render · me | live, Callum only; on the main domain until `ME_HOST` is set |
| Personal tools | dogs, Virtuals, P&L, Kelly, shop | Render · me | live on the main domain until `ME_HOST` is set |
| Scraper service | `scraper/`, user service `puntmore-scraper`: one real Chrome on the desktop session, budgets in code (Docker is the fallback for a box with no screen) | Hub | live |
| Scraper control | `targets.toml` changed only through `scraper/targets.py` (Console, CLI, HubAI); the Hub daemon's inbox of the drop files and its tipster-mailbox poll (`hub/`) | Hub | part built |
| HubAI | the Hermes layer: chief of staff on Telegram, specialist profiles, Qwen3.5-122B under llama-server; takes every escalation; judges tips for Finder (`puntmore finder`) and takes a tip Callum asks for on Telegram | Hub | live; the Finder skill to build |
| Bet Placer | reads the three feeds, sizes, places through DeviceFarmer, keeps the only book, posts `placed` / `gone` to Finder (`docs/placer-spec.md`); starts from the placement engine in `hub/puntmore_hub/placement/` (dry-run adapter only) | Hub; third parties, 2-Up only | to build |
| Console | `puntmore web`: a card per part, the kill switch, the Scraper tab; actions behind a token | Hub | built (localhost; the tunnel is to build) |

## What crosses between the boxes

Four HTTP flows and nothing else; no socket, no Hub endpoint on Render.

| Flow | Route | Who | Key |
|---|---|---|---|
| Paddy prices up | `POST /api/paddy/saves-fair`, `/api/paddy/most-prices` | Scraper service (to move behind Scraper control) | ingest key |
| Tips up to Finder | `POST /api/finder/rows` | HubAI, through `puntmore finder post` | ingest key |
| Feeds down | `GET /api/paddy/feed`, `/api/2up/feed`, `/api/finder/feed` | Bet Placer | its key: `accabonus`, `cards`, `finder`, `ingest` (a third party's: `accabonus`) |
| Placed / gone up | `POST /api/finder/rows/{tip_id}/status` | Bet Placer, through `puntmore finder status` | ingest key |

The Console also reads Render's `/api/ready`. Render holds no Hub configuration and sends no Hub
command.

## Fixed

1. **Dry-run by default** in every Bet Placer; two weeks of clean dry-run before real money.
2. **The kill switch is a file**, `hub/data/KILL`; Render's `TWOUP_KILL_SWITCH` empties the 2-Up feed.
3. **No retry, no re-route** of a failed bet; it goes to HubAI.
4. **Real Chrome, no stealth.** Budgets in code, 24 h cool-off on a block.
5. **Tips on Finder are Callum's only**, behind the `finder` grant and on `me` (the main domain until `ME_HOST` is set); never in the combined value feed, never offered to a third party.
6. **OddsPapi is the only odds source.** Render keeps no record of anyone's bets.
7. **One deploy**, not designed to scale.

## Open

1. **Scraper control routes Paddy's prices.** Today the Scraper service posts them to Render directly; they move behind Scraper control next.
2. **HubAI's Finder skill** (to build): one Hermes skill that reads the new tips (`puntmore finder new`), judges each from its price and fair, asks Callum when unsure, posts the verdict (`puntmore finder post`), and takes a tip Callum types to it on Telegram. The next HubAI work.
3. **The tunnel** that makes the Console reachable from anywhere, and the link to it from `me`.
4. **The Bet Placer box**: the phone interface through DeviceFarmer and the staking logic (`docs/placer-spec.md`). It should run as its own Linux user, so no agent can read account credentials whatever a scraped page says.
5. **Set `ME_HOST` once the DNS points at Render.** Until then `me` is off and its tools (Finder, dogs, Virtuals, P&L, Kelly, the shop) are served on the main domain. The step: add the `me.puntmore.com` DNS record and the Render custom domain, check the host resolves to the service, then set `ME_HOST=me.puntmore.com` on Render.
6. **Goalscorers** returns only via OddsPapi (`PLAN.md`, Parked).
