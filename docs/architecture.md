# Puntmore architecture

Two physical boxes. **Render** finds, prices and decides bets, holds the ledger and serves the app. The **Hub**, on a home connection and owned by Hermes, does only what needs a home IP, the phones or the GPU: bookmaker pages, placement, and the judgement calls when something goes wrong.

This is the one source for the architecture. The spec it implements is `docs/2up-engine-spec.md`; decisions are settled in `PLAN.md` → Decisions; rules are in `CLAUDE.md` and each directory's `AGENTS.md`. Update this file in the same pull request as the change it describes.

*As of 5 Nov 2026. State legend: **live** built and running · **scaffold** built, tested with fakes · **placeholder** a contract and a stub · **to build** specified, not started · **parked** code kept, switched off.*

## What runs where

```mermaid
flowchart LR
  subgraph RENDER["RENDER — finds, prices, decides; holds the ledger; always on; one deploy, three subdomains"]
    direction TB
    APP["Puntmore app<br/>FastAPI + React, one origin; SQLite ledger on disk<br/><i>live</i>"]
    subgraph TWOUP["2up.puntmore.com — the 2-Up product, serves every client"]
      T365["365 tool<br/>acca boost + 2-Up engine (goalscorers parked)<br/><i>to build</i>"]
      BFO["Bet Finder orchestrator<br/>turns the Hub's tips into candidate bets<br/><i>to build</i>"]
      HEP["Hub endpoint<br/>Render end of the WebSocket: bet lists down, reports up<br/><i>to build</i>"]
    end
    subgraph SHALGO["shalgo.puntmore.com — Paddy Power tools, fed by the Worker"]
      COUP["Coupon tracker<br/>photographed coupons priced off Pinnacle<br/><i>live</i>"]
      PADDY["Paddy<br/>corners, cards, goals, saves vs Paddy Power; its own setup<br/><i>live</i>"]
    end
    subgraph ME["me.puntmore.com — Cal's personal tools, owner only"]
      PERS["Personal tools<br/>dogs, Virtuals, P&amp;L, Kelly, shop<br/><i>live</i>"]
      PRIV["Private engine<br/>multi-bookmaker staking; consumes 2-Up as one source<br/><i>to build</i>"]
    end
  end

  subgraph HUB["HUB — only what needs a home IP, the phones or the GPU"]
    direction TB
    subgraph HERMES["Hermes — owner of the Hub: chief of staff on Telegram + four specialists · live"]
      MODEL["Local model<br/>llama-server, Qwen3.5-122B, two 131k slots; Hermes' brain, never prices a bet<br/><i>live</i>"]
    end
    WORKER["Worker · scraper/<br/>one real Chrome: Paddy Power and Bookie Bashing pages kept open, Bet Finder sources loaded-parsed-closed; budgets in code<br/><i>live · tips drop to the Hub</i>"]
    PLACE["Placement bot<br/>bet in → placed / price_changed / failed; no retry<br/><i>placeholder</i>"]
    LINK["Render link + inbox · hub/<br/>outbound WebSocket, durable outbox, idempotent; ingests the Worker's drops<br/><i>scaffold</i>"]
  end

  ODDS["OddsPapi<br/>the only odds source"] --> T365
  ODDS --> PADDY
  WORKER -- "prices, HTTP, live" --> PADDY
  WORKER -- "tips, events (files)" --> LINK
  LINK <-. "WebSocket v1: bet lists → · reports, tips ←" .-> HEP
  HEP -. bet lists .-> LINK
  LINK --> PLACE
  PLACE --> PHONES["Phones<br/>one bookmaker account each"]
  HERMES --> TG["Telegram<br/>Callum ↔ Hermes"]
  WORKER --> SITES["Bookmaker sites<br/>Paddy, Bookie Bashing, Bet365"]
```

## Components

| Component | What it is | Where | State |
|---|---|---|---|
| Puntmore app | One FastAPI + React deploy serving the three hostnames; SQLite ledger on disk | Render | live |
| 365 tool | Bet365 offers as one tool: acca boost, 2-Up engine; goalscorers parked | Render · 2up | to build (acca returns on OddsPapi; 2-Up spec only) |
| Bet Finder orchestrator | Turns the Hub's tips into candidate bets | Render · 2up | to build |
| Hub endpoint | The Render end of the WebSocket | Render · 2up | to build (contract in `hub/PROTOCOL.md`) |
| Coupon tracker | Photographed coupons priced off Pinnacle via the score grid | Render · shalgo | live |
| Paddy | Corners, cards, goals and saves against Paddy Power; its own setup, fed by the Worker | Render · shalgo | live |
| Personal tools | Dogs, Virtuals, P&L, Kelly, shop | Render · me | live, owner only |
| Private engine | Multi-bookmaker staking that consumes 2-Up as one source | Render · me | to build, to spec separately |
| Hermes | Owner of the Hub: chief of staff and four specialists | Hub | live (delegation plugin pending) |
| Local model | llama-server, Qwen3.5-122B, two slots; Hermes' brain, never in the pricing path | Hub, inside Hermes | live |
| Worker | `scraper/`: one real Chrome; Paddy Power and Bookie Bashing pages kept open, Bet Finder sources as `generic` targets | Hub | live (parsers partly written; sources drop tips to the Hub) |
| Placement bot | Bet in, placed / price_changed / failed out, on the phones | Hub | placeholder (dry-run adapter only; contract in `hub/puntmore_hub/placement/README.md`) |
| Render link + inbox | `hub/`: outbound WebSocket, durable outbox, client engine, the Worker's drops ingested; updates itself from `origin/main` every 10 min (`puntmore-update.timer`) | Hub | scaffold, 65 tests; waits on the Hub endpoint |
| Wire contract | `hub/PROTOCOL.md`, JSON Schema, examples, mock Render | both | v1, contract tests |

## The Render ↔ Hub contract

| Direction | Messages |
|---|---|
| Render → Hub | `bet_list`, `scrape_job`, `kill_switch`, `ping`, `ack` |
| Hub → Render | `hello`, `report` (placed, price_changed, failed, settled, balance), `tip`, `scrape_result`, `escalation`, `escalation_outcome`, `pong`, `ack` |
| Rules | outbound from the Hub only · bearer key · every message idempotent on `msg_id`, every bet on `bet_id` · a failed bet is never resent |

## Fixed by the spec

1. **Dry-run by default.** Two weeks of clean reports before real money.
2. **Kill switch is a file**, `hub/data/KILL`; no code change to flip.
3. **No retry, no re-route.** Failures go to the local AI.
4. **Real Chrome, no stealth.** Budgets in code, 24 h cool-off on a challenge.
5. **Bet Finder tips are owner-only.** Never offered to other clients.
6. **OddsPapi is the only odds source.** Render never loads a bookmaker page and never places a bet.
7. **Three subdomains, one deploy**, by design; not expected to scale beyond this.

## Open decisions and known weaknesses

1. **Placement is a stub.** One phone in dry-run is the next real milestone; everything else on the Hub is scaffolding around it. The local model is building the adapter.
2. **Folded (5 Nov):** the Bet Finder's sources are Worker targets; `hub/` no longer scrapes.
3. **Fixed (5 Nov):** `hub/` updates itself. `puntmore-update.timer` fast-forwards the box's checkout to `origin/main` every 10 minutes, reinstalls and restarts, never while a bet is in flight (`hub/README.md`, "Updating the Hub"). `scraper/` updates itself from an image.
4. **The placement service should run as its own Linux user** so no agent can read account credentials, whatever a scraped page says.
5. **Goalscorers** returns only via OddsPapi, after the open work (PLAN.md, Parked).
