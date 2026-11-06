# 2-Up Engine — Module Spec (for One Bet's Journey)

Written 3 Nov 2026 by Cal; moved into the repo 5 Nov 2026 as the single source. The Claude Doc it came from is retired. Where a section below disagrees with a **Decisions** section, the decisions win, newest first; edit this file in the same pull request as the code it changes.

The 2-Up module inside the 365 tool on Render: takes OddsPapi prices and serves a feed of the 2-up value bets that exist right now. Since 6 Nov 2026 it does nothing else: staking, placement and the books belong to the client; the betting agent that reads the feed is a separate tool with its own spec.

## Decisions, 6 Nov 2026

Callum's, in conversation on 6 Nov 2026; recorded in `docs/decisions.md` the same day. They narrow the module to one job and supersede the sections marked below.

### What the module is

- **A feed of current value bets, nothing more.** The 2-Up module prices every side of every match-result market in bet365's early-payout competitions against Pinnacle's fair line plus the 2-up rescue, and serves the rows whose price beats fair. That feed is the product.
- **Two readers of the one feed.** The betting agent (a separate tool, spec'd separately) reads it over the API and does the placing. The 2-Up tab on the 365 page shows the same rows so a person can place by hand when the agent is not running. The page is the manual backup: it gets no row, column or filter the API does not serve.
- **Staking lives in the client.** Kelly, the account-preservation rules, the betting window, exposure, stake rounding and ceilings, bet types (doubles, trebles, fourfolds), cover bets, dry-run and the per-account kill switch are the client's. None of it is computed on Render.
- **No account, no ledger.** Render keeps no record of what any client placed or how it settled. Nothing comes back from a client to this module: no `placed`, `price_changed`, `failed`, `settled` or `balance`. The feed is one-way. The tenancy model (client → sub-client, pooled bankrolls, per-client ledgers, payback terms) is out of this module.
- **Bet Finder is not a Render instance.** Finding tips from tipsters and scrapes is a client function. The Bet Finder orchestrator, the `owner` candidate scope, Render's `tips` table and the `tip` message on the wire go. What a client does with its own tips is settled in that client's spec, not here.
- **The rescue table stays hand-tuned** against Bookie Bashing's 2-up tracker with `tools/twoup_backtest.py` over an exported CSV. With no ledger on Render, that CSV is the only results source, as the 5 Nov decision already said.

### The feed (the builder's contract; items marked *call* are the author's defaults, not Callum's words)

- **A row is one side of one fixture**: `event_id`, `event`, `home`, `away`, **`kickoff`** (the match's kick-off time, UTC; Callum's requirement), `competition`, `selection`, `side` (`home` | `away`), `market` (`Match Result`), **`price`** (the decimal odds bet365 quotes for that selection at `priced_at`; Callum's requirement), Pinnacle-fair `p_win` and `fair_price`, `rescue`, `edge` (`p_win + rescue − 1/price`), `ev`, and `priced_at` (when the prices behind the row were fetched). *Call:* a row's identity is `event_id` + `side`, stable across refreshes, so a reader can tell a re-price from a new row without any state on Render.
- **A row is in the feed while** the fixture is in a configured competition and inside the horizon (`twoup_horizon_hours`, 24 h), both books price all three outcomes, the price is inside the band (`twoup_min_price` .. `twoup_max_price`) and the edge is at or above the floor (`twoup_min_edge`). It leaves at `twoup_min_lead_minutes` before kick-off, or as soon as a refresh no longer meets one of those. *Call:* the full priced board stays available with a per-row `value` flag (today's `value_only=false`) so the page can show why a fixture is not value; the agent reads `value_only=true`.
- **Pre-match only.** No in-play rows and no in-play prices; the module has no idea of the score.
- **Refresh.** The board is rebuilt on a schedule from the shared `OddsPapiClient` cache (`twoup_feed_ttl`, 5 min at the defaults; the client's own kick-off-tiered TTLs still apply underneath), never on a page request. The response carries `generated_at`, every row carries `priced_at`, and `status` says what could not be priced and why (missing book, held chunk, quota). *Call:* a failed refresh keeps serving the last good board with its old `priced_at`, and marks `status.degraded`; it never serves an empty feed because of a transient fetch error. (The Paddy tool's silent hourly tier and missing priced-at time, 6 Nov 2026, is the lesson.)
- **Transport is HTTP pull.** `GET /api/2up/feed` is the whole API. *Call:* no push, no per-client view of the feed: every reader gets the same rows.
- **365 is one permission.** A betting agent's machine key (`app/apikeys.py`) carries the `accabonus` grant, the same grant as the page; there is no feed-only grant, and whatever else sits under 365 (diagnostics, the acca boost when it returns) is open to that key. (6 Nov 2026, Callum)
- **The 2-Up module has no Hub endpoint.** A Hub endpoint belongs to the tool that needs one, one per tool; the betting agent brings its own. The Render end of the WebSocket (`/api/2up/ws`, `app/hub/`, the `hub` grant, the `hub_*` tables and the `/api/2up/hub/*` operator routes) is removed with this work. The Hub-side link client (`hub/puntmore_hub/link`, the Console's Render card, `puntmore link-check`) then has no Render end until the betting agent defines its own; that is the betting agent's spec, and `hub/` is not changed here beyond its docs. (6 Nov 2026, Callum)
- **Kill switch.** *Call:* the global `TWOUP_KILL_SWITCH` stays and empties the feed with `status.killed=true`; the per-client flag goes with the clients.
- **Diagnostics stay**: `/api/2up/diagnostics` and the `twoup_detail` block in `/api/ready` report the last refresh, the account's request count and what could not be priced.

### What this supersedes

| Section below | Status from 6 Nov 2026 |
|---|---|
| Purpose and boundaries, "Owns" | owns price ingestion, the fair line, the rescue model and the value feed only; bet selection, staking, exposure and daily stats are the client's |
| Deployment topology, Bet Finder orchestrator and per-client ledgers on Render | removed |
| What's shared vs what's Cal's; `source_scope` | removed: one feed, no owner scope |
| Multi-client access, per-client auth and onboarding | reduced to machine keys for readers of the feed; no client records |
| Tenancy model | out of this module |
| Decision model, Account-preservation rules | the client's |
| Interfaces: bet list out, reports back | removed; the row above is the only interface |
| Cover bets and failure handling | the client's |
| Bet Finder (sibling module) | not on Render; kept below as history |
| Operational safeguards: per-client kill, idempotent `bet_id`, dry-run | the client's; the global kill switch stays |
| Open questions: Kelly fraction, stake ceiling, cover budget, payback terms, daily stats | the client's or the betting agent's |

### What goes from the code, what stays

*Call, from reading the code on 6 Nov 2026; the plan settles the order.* Stays: `app/twoup/feed.py`, the pricing half of `model.py` (`fair_1x2`, `RescueTable`, `edge`, `price_fixture`), `rescue_table.json`, `tools/twoup_backtest.py`, `GET /api/2up/feed`, `/api/2up/diagnostics`, the `2up` source in `/api/value`, the Feed on the 2-Up tab. Goes: the staking half of `model.py`, `decision.py`, `ledger.py` and the `twoup_*` client, account, bet and balance tables, the clients and ledger routes, the scheduled decision pass and the bet-list dispatcher, the Ledger on the 2-Up tab, `app/betfinder/` and its routes and tables, and the Hub endpoint on Render (`app/hub/`, `routers/hub_ws.py`, its grant, tables and routes; the vendored protocol schema and its byte-identical test with it). Settings that only fed staking (`twoup_kelly_fraction` through `twoup_decision_minutes`) go with it.

## Decisions, 5 Nov 2026

Settled in build; the sections below are updated where they said otherwise.

- The two physical boxes are **Hub** (Cal's machine, residential IP) and **Render**. On the Hub only what needs a home IP, the phones or the GPU; everything else on Render.
- Bet finding, pricing and decision live on Render, beside the ledger. The local model never prices a bet.
- 365 and 2-Up are one tool, **365**: acca boost, goalscorers, 2-Up engine. Goalscorers is parked until rebuilt on OddsPapi; the acca boost returns on OddsPapi.
- **OddsPapi is the only odds source.** The other vendors are removed from the code.
- **Hermes owns the Hub**: a chief of staff on Telegram plus four specialists, on the local model. It runs the schedules and takes every escalation.
- The /scraper control page on Render is removed; the Scraper's config lives on the Hub.
- The Bet Finder's scrape sources fold into the Scraper (`scraper/`): one Chrome, one budget, one alert path.
- The 2-up edge is proven. The rescue model is tuned during testing against Bookie Bashing's 2-up tracker, by hand (BB bans automated access).
- Render stays one deploy serving three subdomains; it is not expected to scale beyond this.
- Code: github.com/CMclarty/puntmore (monorepo). Hub side in `hub/`; the wire contract in `hub/PROTOCOL.md` and `hub/schemas/`.

## Purpose and boundaries

One module, living on the Puntmore server: it prices 2-up value and decides what to bet, how much, and on which account. It never places bets.

- Owns: price ingestion from OddsPapi (the only odds source), fair line (Pinnacle de-vigged), 2-up rescue model, bet selection, staking, per-account exposure, daily stats.
- Does not own: placement mechanics (client engine), bankroll top-ups.
- Bet365 only at launch. Bet types: singles, doubles, trebles, occasional fourfolds.
- Keep the pricing output on a WebSocket so other Puntmore tools can consume it later.

## Deployment topology

Two physical systems. The boundary between them is the WebSocket/queue layer, so each side can be restarted independently.

**Render (cloud) — the bet engines; one deploy, three subdomains**

- 365 tool: the 2-Up engine (pricing + decision), one tool; the acca boost returns on OddsPapi and goalscorers is parked
- Bet Finder orchestrator (scheduler, state, matching, dedupe; parsing is the Scraper's, on the Hub)
- Per-client ledgers and reporting
- Odds API client

**Subdomains (fixed 5 Nov 2026, `docs/decisions.md`):**

- `2up.puntmore.com` — the 2-up tool and its client API. REST for auth, onboarding, ledger reads; WebSocket on the same host for bet lists out and reports back.
- `shalgo.puntmore.com` — the Paddy Power tools (a setup of their own, fed by the Scraper) and the coupon tracker.
- `me.puntmore.com` — Cal's personal tools: dogs, virtuals, and the private multi-bookmaker engine (PP bet builders, 2-ups, 365 goalscorer arbs (parked), tipsters, scrapes). Owner-only; not a client product.

The 2-up engine spec'd in this doc is `2up`. The private engine on `me` is a separate, broader system that consumes `2up` as one of several bet sources and has its own staking layer across all bookmakers — to be spec'd separately.

**Hub (Cal's box, residential IP) — owned by Hermes**

- Phones — the sub-clients; the client engine drives placement on them
- The Scraper (`scraper/`) — Playwright driving real headful Chrome on the Hub's desktop, persistent profile; user service `puntmore-scraper` (Docker the fallback for a box with no screen)
- Claude Desktop
- Hermes (owner of the Hub), on the local model — the higher-level reasoning layer: receives escalations (failed bets, unhealthy sources, kill-switch triggers), decides, and instructs the engines

**What's shared vs what's Cal's.** Puntmore — the 2-up engine — is the product; it serves every client. The Bet Finder (scraper and tipster bets) is Cal-only: its output is routed exclusively to Cal's client record and never offered to other clients. In code this is a `source_scope` on every candidate bet: `shared` (2-up) or `owner` (Bet Finder); the decision layer only considers `owner` bets for the owner client.

**Multi-client access.** Cal's Hub is one client of the Render server, not the only one. Other clients/users connect their own placement side the same way. So Render must be built as a service from day one:

- Per-client auth — API key or token per client; every WebSocket connection and report is scoped to its `client_id`. No client can see another's bets, balances or ledger.
- Client onboarding is config, not code: register a client, issue a key, attach sub-clients.
- Rate limits and the kill switch apply per client as well as globally.
- The local AI on Cal's box has admin scope over Render (all clients); ordinary clients do not.

**Traffic**

- Odds API → Render only. No bookmaker pricing calls from the Hub.
- Bookmaker page loads → Hub only. Render never touches PP/Bet365 sites.
- Render → Hub: bet lists, scrape jobs. Hub → Render: placement reports, balances, scraped tips, escalation outcomes.
- Hub must be reachable from Render through a persistent outbound connection (WebSocket client or tunnel from home), not inbound port exposure.

## Tenancy model

The engine is multi-tenant. Every record is keyed `client_id → sub_client_id`.

- **Client** — a separate customer and entity with its own pooled bankroll. Cal may sell the service to others, so clients are fully isolated from each other.
- **Sub-client** — a device, probably a phone, with one bookmaker account on it. This is the placement endpoint.

Kelly is computed on the client's pooled bankroll; the resulting stake is distributed across that client's sub-clients. The account-preservation rules apply per sub-client.

**Per-client reporting.** Each client gets its own ledger — stakes, returns, P&L, open exposure, per sub-client and in total — because there will be a payback/settlement arrangement between Cal and each client. Terms of that settlement are an open question.

## Decision model

Constrained optimisation. Objective: maximise expected log bankroll growth (Kelly) on each client's pooled bankroll. Hard constraints:

1. **Bankroll** — client-level stake bounded by pooled balance; each sub-client's share bounded by its own balance less open exposure.
2. **Availability** — only what's in today's feed; no turnover target.
3. **Account preservation** — the rule set below.

Per account, per day: rank candidates by edge, size with fractional Kelly, apply constraints 3 → 1 → 2, drop what fails.

**2-up rescue model.** Bet365 does not price the 2-up concession into its odds — the match-result price is identical with or without the promo; they carry it as a marketing cost managed by restricting qualifying leagues. So the value is a pure overlay on the standard line: edge = P(win) + P(two up but fails to win) − 1/price. No published model conditions the rescue rate on match odds (unconditioned base rate ≈ 1.8% of matches, \~5–7% of two-goal leads). The rescue table (`render/api/app/twoup/rescue_table.json`) is bucketed by implied win probability and tuned by hand against Bookie Bashing's 2-up tracker (`tools/twoup_backtest.py` over an exported CSV); there is no historical-data build.

**Decision 5 Nov 2026: the edge is proven; do not gate the build on a literature review. The rescue model is fine-tuned during testing against Bookie Bashing's 2-up tracker, by hand (BB bans automated access).**

## Account-preservation rules

| Rule | Value |
| --- | --- |
| Betting window | 07:30–22:00 local |
| Multi-leg timing | ≥1 leg within 24 h; all legs within 72 h |
| Stake rounding | Nearest of £1, £2, £5, £10, £20, £50, £100… |
| Bets per account per day | Hard max 20 |
| Stake ceiling | Dynamic from account bankroll and recent stakes; strong upper cap, no floor |
| Stake growth | Must not visibly step up after wins — cap rate of change vs. trailing stakes |

## Interfaces

Cal controls the client engine, so these schemas are authoritative.

**Odds API in** — Bet365 and Pinnacle match-result prices for early-payout qualifying leagues (OddsPapi, the only odds source; key in env).

**Bet list out** (to client engine): `bet_id, account_id, legs[], stake, expected_price, bet_type, not_before, not_after`

**Reports back** (from client engine):

- `placed` — bet\_id, actual\_price, timestamp
- `price_changed` — bet\_id, new\_price → re-run the decision at the new price
- `failed` — bet\_id, reason → drop and escalate
- `settled` — bet\_id, outcome, return
- `balance` — account\_id, balance, timestamp

Engine tracks per-account open exposure so pending bets are never double-counted.

## Cover bets and failure handling

**Cover bets** — occasional non-value bets (Lucky 15s, bet builders) that Cal specifies, to make the account look less systematic. A known cost with its own small budget; the optimiser accounts for the drag.

**Failures** — no auto-retry, no re-routing. Any `failed` report is dropped and flagged to the local AI on the Hub, which owns the decision on what happens next.

## Bet Finder (sibling module)

Scraper/ingest engine. Sources bets from outside the 2-up model and normalises them into the same candidate-bet shape the decision layer consumes. It never stakes and never places.

**Sources**

- Tipsters by email — IMAP poll of a dedicated mailbox; one parser per tipster.
- Tipsters by Substack — RSS/email delivery into the same mailbox; same parser path.
- Regular scrapes — the Scraper's `generic` targets (`site = "generic"` in `targets.toml` on the Hub), each on its own interval under the generic site budget.

**Pipeline**

1. Fetch raw item (email body, post, page).
2. Parse → structured tip: `source_id, event, market, selection, advised_price, advised_stake_units, tip_time, expiry`.
3. Resolve event against the odds API so the selection maps to a real `event_id` and current Bet365 price.
4. Dedupe across sources (same event/selection within a window).
5. Emit as a candidate bet with `origin = tipster|scrape`, so the decision layer can stake it by source track record rather than a 2-up edge.

**Existing scraper tooling (reuse, don't rebuild)**

> History (superseded 5 Nov 2026): the WOP Finder, Watchtower and `~/apitest/` builds below are the record of where the Scraper came from; what runs now is `scraper/` (`scraper/HANDOVER.md`, `scraper/README.md`).

Two prior builds cover the scrape side; the Bet Finder's "regular scrapes" should be those, adapted to emit candidate bets.

Decision, 5 Nov 2026: the Bet Finder's scrape sources (`hub/`, YAML + parser + fixture each) fold into the Scraper (`scraper/`), so one Chrome, one request budget and one alert path serve both. The Scraper's targets live on the Hub; the /scraper page on Render is removed.

- **WOP Finder** — Paddy Power scraper for tiered bet-builder packages (WOPs) and corners/cards O/U lines. Split deployment: Render-hosted orchestrator (scheduler, JSON state keyed on PP event IDs, FlashScore fixture join, Discord reporting) + home-machine Docker worker running Playwright in real headful Chrome on a residential connection, persistent profile, Watchtower auto-update from GitHub. Cadence: fixture generation Mon/Thu, daily market sweep. Handoff doc already written.
- **PP saves/cards scraper** (`HANDOVER.md` on the home server, `~/apitest/`) — compares Paddy Power goalkeeper saves, cards and corners against BookieBashing fair odds. Carries the hard rules below.

**Hard rules inherited from the PP scraper** — apply to every scrape source:

- Real headful Chrome, one persistent profile, no stealth/fingerprint-spoofing tooling, no fake mouse movement.
- Request budget per site, in code (`scraper/sites.py`): Paddy Power 15 loads/h with quiet hours 01–07, Bookie Bashing 8/h, generic sources 60/h and 400/day; random pauses, human-like page interaction. Sweeps every 2–4 h, \~30 min in the last 3 h before kick-off; nearest kick-offs first if the budget runs short.
- On any challenge/CAPTCHA/block: 24 h cool-off + alert.
- Daily 09:00 Discord heartbeat with 24 h summary; crash alert with traceback, startup message, hang watchdog, stale-data check, alert de-dup. Silence means broken.
- Paddy Power is blocked in Claude in Chrome; all PP automation runs on the home machine via Playwright.

**BookieBashing constraint** — BB's terms (s.7) ban any automated access without written permission; the bet-tracker API returns 401 without a token. Do not scrape BB unattended. Either obtain written permission from Barbitrage Ltd or keep BB interaction manual/user-triggered. BB is not a Bet Finder scrape source until that's resolved.

**Per-source config** — name, type, fetch schedule, parser, trust weight, active flag. Adding a tipster is a config entry plus a parser, not a code change elsewhere.

**Source tracking** — log every tip and its settled outcome so each source gets a rolling ROI/strike rate; this feeds the trust weight and lets dead sources be switched off.

**Failure handling** — unparseable item → quarantine queue and flag, never a guessed bet. Scraper break (layout change, block) → source marked unhealthy, flag up.

**Open questions**

- [ ] Initial tipster list and scrape targets
- [ ] Does a tipster bet pass through Kelly or use the tipster's advised stake units? (OPEN, Callum to decide: `docs/decisions.md`, "how a Bet Finder tip is staked")
- [ ] (resolved) Bet Finder bets are Cal-only; 2-up bets go to all clients

## Operational safeguards

- **Kill switch** — a file on the Hub (`hub/data/KILL`) and a per-client flag on Render (plus the global `TWOUP_KILL_SWITCH`), that halts the bet feed immediately with no code change.
- **Idempotency** — every bet carries a unique `bet_id`; the client engine echoes it on every report. A replayed or duplicated message never causes a second placement.
- **Dry-run mode** — the engine produces bet lists and full reports but sends nothing to any client. Run for at least two weeks against live prices to validate staking and the rescue model before real money moves.

## Open questions

- [ ] Kelly fraction
- [ ] Dynamic stake-ceiling formula (window, max step)
- [ ] Cover-bet budget as % of turnover
- [x] (closed 5 Nov 2026) Historical data source for the rescue model: none; the table is tuned by hand against BB's 2-up tracker
- [ ] (resolved) Hermes on the Hub; the escalation contract is in hub/PROTOCOL.md
- [ ] Daily stats report: contents and destination

* [ ] Client payback terms and reporting period
