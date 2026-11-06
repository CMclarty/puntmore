# Retired

What was removed, and where to find it if it is ever wanted again. One paragraph each; the
register's dated entries (`docs/decisions.md`) are the record of why.

## AW 26 — retired 29 Oct 2026

The acca project's slip ledger (`/aw26`): photos of Paddy terminal slips read
by OCR, priced from Bookie Bashing's Group Tracker fairs, settled from
FlashScore stats, with a morning Discord post. Callum retired it and asked for
the code to be kept in case it comes back.

- **Code:** the `archive/aw26` branch (main as it stood, `05ae31a`), including
  `render/api/app/aw26/`, its routers and tests, `render/frontend/src/pages/Aw26Page.jsx`,
  the slip OCR (`providers/slip_ocr.py`, `read_slip`), the desktop scripts
  `local/bookiebashing_tracker.py` and `local/flashscore_results.py` (and its
  systemd units), the Python client's `pm.aw26`, and
  `docs/aw26-handover.md` / `docs/aw26-assistant-handover.md`.
- **Data:** left in the database untouched — tables `aw_slips`, `aw_legs`,
  `aw_fairs`, `aw_leg_lines`, `value_snapshots`, `value_lines`. Nothing reads
  or writes them now; the value snapshots (which were AW 26's) stopped too.
- **Kept:** the Paddy health lines that rode on AW 26's 08:47 job now run as
  their own job (`paddy_health`), on the same `aw26` Discord route.
- **To bring it back:** restore those paths from `archive/aw26`
  (`git checkout archive/aw26 -- <paths>`), re-add the `aw26` tool to
  `security.py`, the routers to `main.py`, the models, schemas, scheduler job
  and nav entry, and the frontend route. The tables are still there.

## The Hub's link to Render — removed 6 Nov 2026

A WebSocket from the Hub to Render (`/api/2up/ws`) carried bet lists down and reports, tips,
scrape results and escalation outcomes up, with an outbox, acks and replays (`hub/PROTOCOL.md`
v1). Render's end, the Bet Finder orchestrator on Render (`app/betfinder/`, its `tips` table) and
the 2-Up engine's staking, ledger and per-client kill switch went first; the Hub's end (the
`link/` client, the mock Render, `link-check`, the outbox, the wire message types and examples)
went in the names-and-strip work. What remains is the Scraper's drop files (`hub/PROTOCOL.md`).
The code is in git history (the commit "hub: the wire is gone"); the staking code is at `f1f3172^`.

## The shalgo site — retired 6 Nov 2026

`shalgo.puntmore.com` was a second product site for Paddy and Hills, with its own name, icon,
manifest and login. The main domain now serves every public tool; a request to the shalgo host
is redirected to the same path on the main domain (`render/api/app/site.py`, `ShalgoRedirect`),
so old links and home-screen icons keep working. The `2up` host was planned and never built.

## Old names — dropped 6 Nov 2026

The register's "Names and structure" lists the names no longer used: Worker (the Scraper
service), betting agent, client engine, placement bot, placement service, bet-placing service and
private engine (the Bet Placer), local AI and local model (HubAI), sub-client (a phone and its
accounts), Bet Finder orchestrator, candidate tip (a tip), link daemon (the Hub daemon), the
`2up` host, shalgo. Old commits, branches (`native-worker`, `home-box`) and environment variables
read as fallbacks (`PUNTMORE_WORKER_DATA`, `PUNTMORE_ESCALATE_TO_LOCAL_AI`) still carry them.

## Handover and verification documents — deleted 6 Nov 2026

- `docs/api-handover.md`: the 26 Oct build brief for the API layer; its result is `docs/api.md`.
- `docs/scraper-handover.md`: the 26 Oct brief for Render's `/scraper` control page and
  server-fed Scraper config, removed 5 Nov 2026.
- `scraper/HANDOVER.md`: the 18 Oct handover of Scraper v0.1. Its boundaries are in
  `scraper/AGENTS.md`, its credentials in `scraper/README.md`.
- `docs/provider-verification.md` and `render/api/tools/probe_corners.py`: the TheOddsApi and
  bettingiscool probes, for vendors removed 5 Nov 2026. The lesson that regions are about the
  bookmaker is kept in `render/api/AGENTS.md`.

All are in git history before this work's commits.

## The original web-app spec and the plan's history — cut 6 Nov 2026

`SPEC.md` described the pre-November stack (nginx and docker-compose, bettingiscool and
TheOddsApi, AccaBonus, `lab.puntmore.com`); only its coupon sections (§3, §7, §8) are kept.
`PLAN.md` lost its History (the v2–v5 phases), its "Current status" and items 6 (bettingiscool),
7, 9 (AW 26, above) and 10 (the 2-Up Hub scaffold, whose open work moved to
`docs/placer-spec.md`); the R8 eligibility facts and the R10 bias lesson are in
`render/api/AGENTS.md`. The 2-Up spec's staking, tenancy, interfaces, cover bets and Bet Finder
sections moved to `docs/placer-spec.md` or were dropped.
