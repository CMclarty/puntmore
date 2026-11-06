# Decisions register

The standing decisions for Puntmore, each with its date and who made it. This
is the first thing an agent reads, before `PLAN.md`, any `AGENTS.md`, README,
handover or spec: **when any other file in this repo disagrees with a line
here, this file is right and the other file is stale.** A decision changes only
by Callum saying so, in a dated edit to this file in the same pull request as
the code. Older product decisions (coupon tracker, pricing, design) stay in
`PLAN.md` → Decisions and are not repeated here.

## Names and structure (6 Nov 2026, Callum)

Settled in conversation on 6 Nov 2026. Every document and comment uses these
names and no other; older names appear only in `docs/retired.md`.

- **Puntmore** is the project. **Render** is the cloud service, one deploy.
  **Hub** is Callum's box and the one program that runs on it; nothing on the
  box sits outside it. **Callum**, never "Cal", in writing.
- **Render serves boards.** A **board** is a tool page with a one-way HTTP
  **feed** of value rows: **Paddy** (corners, cards, goals, saves against
  Paddy Power), **2-Up** (bet365's early-payout value, inside the **365
  tool**), **Finder** (Callum's tips, judged on the Hub). Plus the coupon
  tracker, Hills and Callum's personal tools, which are not boards. No
  ledger, no account, no Hub endpoint on Render. `2up`, `twoup`, `cards`,
  `accabonus` and `finder` are identifiers only, never prose.
- **Two product hosts.** `shalgo` serves every public tool: the coupon
  tracker, Paddy, Hills and 365 with 2-Up inside it. `me` serves Callum's
  personal tools: Finder, dogs, Virtuals, P&L, Kelly, the shop. The main
  domain is the front door, the login and the API. The `2up` host is dropped.
- **The Hub has five sub-components.** **Scraper service**: the one real
  Chrome on the desktop, reads pages, hands rows up. **Scraper control**: the
  layer above it: rules, routing and switches for every source's rows (pages
  and the tipster mailbox), driven from the Console's control page; rows go
  from the Scraper service to Scraper control and from there to a board or to
  HubAI, never from the service straight to a board (the Paddy price posts
  go direct today and move behind Scraper control as the next Scraper work).
  **HubAI**: the Hub's AI, the whole Hermes layer (the chief of staff on
  Telegram and its specialist profiles, one Qwen model under llama-server);
  it judges tips for Finder and may drive Scraper control. **Bet Placer**:
  reads the three feeds, sizes, places on the phones through DeviceFarmer
  (bet365 through the phone's browser), keeps the only book, posts
  `placed` / `gone` to Finder; a black box for now, with a **phone
  interface** and a **staking logic** inside it. **Console**: the page that
  shows every sub-component and carries the control page.
- **The Console is the one control surface**, reachable from anywhere
  through a private tunnel; `me` links to it. Render holds no Hub config and
  no Hub commands (the 5 Nov rule stands).
- **A phone holds one or more bookmaker accounts**, in the bookmaker's app
  and in the phone's browser.
- **Third-party clients run the same Bet Placer on 2-Up only.** Paddy and
  Finder are Callum's. The Bet Placer's key on the Hub carries `accabonus`,
  `cards`, `finder` and `ingest`; a third party's carries `accabonus`.
- Dropped names: Worker, betting agent, client engine, placement bot,
  placement service, bet-placing service, private engine, local AI, local
  model (except for llama-server itself), sub-client, Bet Finder
  orchestrator, candidate tip, link daemon, the `2up` host.

## The two boxes

- **Hub** is Callum's Strix Halo box on a residential connection; **Render** is
  the cloud service. Those are the names. (5 Nov 2026, Callum)
- **Hermes owns the Hub.** The local model (llama-server, Qwen3.5-122B, alias
  `qwen`) sits under Hermes, not beside it. Hermes profiles: chief of staff
  (default), betting-supervisor, scraper-manager, betting-client-manager,
  software-supervisor. One local model for all of them; no second, cheaper
  model. (4–5 Nov 2026, Callum)
- **Render is one Docker web service**, every push to `main` deploys it, and it
  serves three subdomains from that one deploy: `2up` (the 2-Up product and
  the Hub endpoint), `shalgo` (the Paddy Power tools, a setup of their own, and
  the coupon tracker), `me` (Callum's own tools). It is **not expected to scale
  beyond this**; do not design for scale. (5 Nov 2026, Callum)
- **One repository for both boxes** (`CMclarty/puntmore`); the directory says
  which box. Nothing reaches `main` except through a pull request; agents work
  on branches in their own worktrees. (5 Nov 2026, Callum)
- **The repo is the single source.** The 2-Up spec, the architecture and this
  register live here and nowhere else; the earlier Claude Doc and artifacts are
  retired. (5 Nov 2026, Callum)

## Where the logic lives

- **Pricing and the value boards live on Render; staking, decision and
  placement live on the Hub; every page load lives in the Scraper**
  (`scraper/`). Render never loads a bookmaker page and never places a bet.
  (5 Nov 2026, Callum; "decision" moved to the Hub 6 Nov 2026, "The 2-Up
  engine and placement")
- **The private Bet Finder (Finder, `docs/finder-spec.md`) is judged on the
  Hub by Hermes.** Tips (email, pages, WhatsApp) have no fair line; Hermes
  matches and assesses each one, infers a stake or asks Callum, and posts the
  bet to Render through the Finder tool; the Bet Placer sizes it. The line
  "the local model never prices a bet" is **struck**. Finder rows are
  Callum's only. (6 Nov 2026, Callum)
- **OddsPapi prices what it covers; the Scraper fills the markets it does
  not**, and the Scraper is the Hub-side route for every page any tool
  needs, so the sites see a residential IP and a real browser. (6 Nov 2026,
  Callum)
- **Three boards, one placer.** Paddy, 2-Up and Finder are the sources of
  bet value; the Bet Placer (a separate tool, to spec) sizes across all of
  them, runs the phones and keeps the records, on the Hub and on third-party
  clients; its console gets a sizing page. (6 Nov 2026, Callum)
- **OddsPapi is the only odds source.** TheOddsApi is removed entirely, not
  gated. Goalscorers is parked; if it returns it uses OddsPapi. (5 Nov 2026,
  Callum)
- **365 is one tool**: AccaBonus + the 2-Up engine (+ goalscorers when
  unparked). The `accabonus` grant keeps its name because renaming a grant
  revokes accounts' access. (5 Nov 2026, Callum)
- **The `/scraper` control page is gone.** The Scraper's configuration is
  `targets.toml` on the Hub, changed only through `scraper/targets.py` (the console's Scraper tab, the `targets` CLI, the scraper-manager), never by hand while the Scraper runs; Render
  holds no scraper config and receives no scraper status. (5 Nov 2026, Callum)
- **The Bet Finder's sources are Scraper targets** (`site = "generic"`); `hub/`
  has no browser and no budget of its own. Bet Finder output is **owner-only**
  (`source_scope: owner`), never offered to another client. (5 Nov 2026,
  Callum; spec)

## Scraping conduct

- **Real, live Chrome on the Hub's own desktop**: the real binary, the real
  GPU and display, the real fonts, one persistent profile. The point is to
  minimise the risk of countermeasures, so a container on a virtual display
  does not satisfy it (measured 5 Nov 2026: no WebGL at all inside the
  container). The Docker image is a fallback for a box with no screen only.
  (26 Oct 2026, reaffirmed 5 Nov 2026, Callum)
- **No stealth or fingerprint tooling, no solvers, no proxies, no fake input,
  no user-agent or navigator overrides.** The one deliberate departure from
  Playwright's defaults is that Chrome's `--enable-automation` flag is not
  passed and AutomationControlled is off. (26 Oct 2026, Callum)
- **Budgets are code** (`scraper/sites.py`): Paddy Power 15 loads/h with quiet
  hours 01–07; Bookie Bashing 8/h; generic sources 60/h and 400/day. A block
  means a 24 h cool-off and a human `resume`; nothing retries. (Oct 2026)
- **Bookie Bashing is not a Bet Finder source and the 2-Up rescue model is
  tuned against BB's 2-up tracker by hand.** BB's terms ban automated access.
  **OPEN, Callum to decide:** the Scraper's one existing BB target (`bb-saves`,
  the Paddy tool's saves fair odds, a kept-open page reloaded every 10 minutes
  after a person logs in) predates this rule and contradicts it; keep it as the
  single named exception, or disable it. (spec; raised 5 Nov 2026)
- **Never guess a tip.** Parsers are written from real pages only; a parser
  that cannot read the page returns nothing and `min_rows` raises the alarm.

## The 2-Up engine and placement

- **The 2-Up module is a one-way feed of current value bets and nothing
  else.** It prices bet365's early-payout match-result markets against
  Pinnacle plus the rescue and serves the rows that beat fair, over HTTP, to
  two readers: the betting agent (a separate tool, its own spec) and the
  2-Up tab as the manual backup. **Staking lives in the client**: Kelly, the
  account rules, bet types, cover bets, dry-run and the per-account kill
  switch. **No account, no ledger on Render**: nothing is reported back and
  Render keeps no log of bets. **Bet Finder is not a Render instance**: tip
  finding lives on the clients. **A Hub endpoint is per tool**: the 2-Up
  module has none, so Render's end of the WebSocket (`/api/2up/ws`) goes with
  this work and the betting agent brings its own. **365 is one permission**:
  a betting agent's key carries `accabonus`, no feed-only grant. The lines
  below that speak of bet lists, reports, `bet_id` or the Bet Finder on
  Render describe the client's side or are history. (6 Nov 2026, Callum;
  `docs/2up-engine-spec.md`, "Decisions, 6 Nov 2026")
- **The 2-Up edge is proven. Do not spend effort questioning it.** The rescue
  table is tuned by hand against BB's tracker as part of testing. (5 Nov 2026,
  Callum)
- **Dry-run is the default** on every client; Callum switches it off after two
  weeks of clean dry-run reports, nobody else. **The kill switch is a file**
  (`hub/data/KILL`) and a per-client flag on Render; anyone may turn it on,
  only Callum turns it off. (spec)
- **No auto-retry and no re-routing of a failed bet.** `failed` is reported and
  escalated, full stop. A `price_changed` report re-runs the decision at the
  new price (that is the spec, not a retry). Every bet is idempotent on
  `bet_id`, every message on `msg_id`. (spec)
- **The wire contract is `hub/PROTOCOL.md` v1 + `hub/schemas/`**; a change
  touches both sides in one pull request.
- **The placement bot is a placeholder** (dry-run adapter); the local model is
  building the phone adapter. (5 Nov 2026, Callum)
- **OPEN, Callum to decide:** how a Bet Finder tip is staked. As built, tips
  are priced against Pinnacle like 2-Up rows and so almost never stake. The
  proposed rule is "follow the tipster": qualify when bet365's price is at or
  above the advised price, stake advised units × unit stake × trust weight,
  count the 2-Up rescue. (raised 5 Nov 2026)

## Operating the Hub

- **The Hub updates itself** from `main` every 10 minutes
  (`puntmore-update.timer`, `hub/deploy/update.sh`); nothing is pulled by
  hand after the first install. (5 Nov 2026)
- **The Hub Console** (`puntmore web`, `http://127.0.0.1:8090`) is how a person
  sees what every Hub component is doing; its actions need a token. (5 Nov
  2026, Callum)
- **The Scraper runs as the user service `puntmore-scraper` on the desktop
  session**, data in `/opt/puntmore-scraper/data`, updated by the same timer.
  (5 Nov 2026, Callum; landing in the `native-worker` branch)
- **Secrets live in `.env` files only**; a gitleaks pre-commit hook guards
  commits. Databases and build metadata are never committed.

## Agents

- **Claude Code agents** work in isolated worktrees on branches, open pull
  requests, and are reviewed before merge; they read this file first and treat
  any description of an earlier build as history, not instruction. Hermes
  agents on the Hub follow the runbook skills under `hub/deploy/hermes-skills/`.
  (5 Nov 2026)
