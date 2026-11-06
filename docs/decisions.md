# Decisions register

The standing decisions for Puntmore, each with its date and who made it. This
is the first thing an agent reads, before `PLAN.md`, any `AGENTS.md`, README or
spec: **when any other file in this repo disagrees with a line here, this file
is right and the other file is stale.** A decision changes only by Callum
saying so, in a dated edit to this file in the same pull request as the code.

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
- **One product host and `me`.** Puntmore is the product: the main domain
  serves every public tool (the coupon tracker, Paddy, Hills, 365 with 2-Up
  inside it), the login and the API. `me` serves Callum's personal tools:
  Finder, dogs, Virtuals, P&L, Kelly, the shop. Shalgo is retired as a site
  and a brand: no separate name, icon, login or tool list;
  `shalgo.puntmore.com` redirects to the same path on the main domain so old
  links and home-screen icons keep working (its `/sw.js` is a worker that
  unregisters itself, so an app installed from there follows). The `2up`
  host is dropped. (6 Nov 2026, Callum)
  - **`me` is switched on by `ME_HOST`.** Blank (the default) there is no
    `me`: its tools are served, and their accounts sign in, on the main
    domain. The deployment step: add the `me.puntmore.com` DNS record and
    the Render custom domain, check the host resolves to the service, then
    set `ME_HOST=me.puntmore.com`. (6 Nov 2026, the branch's final review)
  - **`me` has its own login.** The session cookie is per host, so Callum
    signs in once per host; a known cost of `me`. (6 Nov 2026, the branch's
    final review)
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
  - **HubAI's Finder role is one Hermes skill**: read the new tips
    (`puntmore finder new`), judge each from its price and fair, ask Callum
    when unsure, post the verdict (`puntmore finder post`), and take a tip
    Callum types to it. A tip Callum requests by hand comes in on Telegram to
    HubAI. That skill is the next HubAI work. (6 Nov 2026, Callum)
- **The vocabulary.** A **bet** is a selection and a price. A **value bet** is
  a bet an internal tool found (Paddy or 2-Up pricing a market against a fair
  line). A **tip** is a bet from an external source (a tipster's email, a
  page, WhatsApp, or Callum asking for one by hand), which may carry its own
  fair price, the one HubAI judges it on. A **price** is a raw row the Scraper
  service reads off a Paddy page, which the Paddy board turns into value bets.
  The Scraper service hands up two kinds of row, prices and tips, and Scraper
  control routes them. Finder is the tips board: the tips HubAI has judged,
  its feed the ones it offered; a row there is "a tip on Finder". The Bet
  Placer places bets from all three feeds whatever their kind. (6 Nov 2026,
  Callum)
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
  orchestrator, candidate (tip or bet), Finder row, link daemon, the `2up`
  host, shalgo.

## The two boxes

- **Hub** is Callum's Strix Halo box on a residential connection; **Render** is
  the cloud service. (5 Nov 2026, Callum)
- **HubAI runs the Hub.** It is the Hermes layer: profiles chief of staff
  (default), betting-supervisor, scraper-manager, betting-client-manager and
  software-supervisor, all on one model, Qwen3.5-122B under llama-server
  (alias `qwen`); no second, cheaper model. (4–5 Nov 2026, Callum)
- **Render is one Docker web service**; every push to `main` deploys it. It
  serves the main domain and `me` from that one deploy, and is **not expected to scale beyond this**; do not design
  for scale. (5 Nov 2026, Callum; hosts 6 Nov 2026)
- **One repository for both boxes** (`CMclarty/puntmore`); the directory says
  which box. Nothing reaches `main` except through a pull request; agents work
  on branches in their own worktrees. (5 Nov 2026, Callum)
- **The repo is the single source.** The specs, the architecture and this
  register live here and nowhere else. (5 Nov 2026, Callum)

## Where the logic lives

- **Pricing and the boards live on Render; sizing, placing and the only book
  live in the Bet Placer; every page load lives in the Scraper service**
  (`scraper/`). Render never loads a bookmaker page and never places a bet.
  (5 Nov 2026, Callum; sizing moved to the Bet Placer 6 Nov 2026)
- **Finder is judged on the Hub by HubAI** (`docs/finder-spec.md`). A tip
  (email, page, WhatsApp) has no fair line of ours: HubAI judges it from the
  tip's own price and fair price, advises a stake or asks Callum, and posts the
  verdict to Render as a tip on Finder; the Bet Placer sizes it. The old line
  "the model never prices a bet" is struck. Tips on Finder are Callum's only,
  through the `finder`
  grant and the `me` host. (6 Nov 2026, Callum)
- **OddsPapi prices what it covers; the Scraper service reads every page any
  tool needs and fills what OddsPapi lacks**, so the sites see a residential
  IP and a real browser. (6 Nov 2026, Callum)
- **Three boards, one Bet Placer.** Paddy, 2-Up and Finder are the sources of
  bet value; the Bet Placer reads their three feeds, sizes across all of them,
  runs the phones and keeps the records. Its Console page gets a sizing page.
  (6 Nov 2026, Callum)
- **OddsPapi is the only odds source.** TheOddsApi is removed entirely, not
  gated. Goalscorers is parked; if it returns it uses OddsPapi. (5 Nov 2026,
  Callum)
- **365 is one tool**: AccaBonus + 2-Up (+ goalscorers when unparked). The
  `accabonus` grant keeps its name because renaming a grant revokes accounts'
  access. (5 Nov 2026, Callum)
- **Render holds no Scraper configuration and receives no Scraper status.**
  The Scraper's configuration is `targets.toml` on the Hub, changed only
  through `scraper/targets.py` (the Console's Scraper tab, the `targets` CLI,
  HubAI's scraper-manager), never by hand while the Scraper service runs.
  (5 Nov 2026, Callum)
- **Finder's sources are Scraper targets** (`site = "generic"`) and the
  tipster mailbox; `hub/` has no browser and no budget of its own. (5 Nov 2026,
  Callum)

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
- **Bookie Bashing is not a Finder source and the 2-Up rescue model is tuned
  against BB's 2-up tracker by hand.** BB's terms ban automated access.
  **OPEN, Callum to decide:** the Scraper's one existing BB target (`bb-saves`,
  Paddy's saves fair odds, a kept-open page reloaded every 10 minutes after a
  person logs in) predates this rule and contradicts it; keep it as the single
  named exception, or disable it. (raised 5 Nov 2026)
- **Never guess a tip.** Parsers are written from real pages only; a parser
  that cannot read the page returns nothing and `min_rows` raises the alarm.

## 2-Up and the Bet Placer

- **2-Up is a one-way feed of current value bets and nothing else.** It prices
  bet365's early-payout match-result markets against Pinnacle plus the rescue
  and serves the rows that beat fair, over HTTP, to two readers: the Bet Placer
  and the 2-Up tab as the manual backup. No account and no ledger on Render:
  nothing is reported back and Render keeps no log of bets. The global
  `TWOUP_KILL_SWITCH` empties the feed. (6 Nov 2026, Callum;
  `docs/2up-engine-spec.md`)
- **The 2-Up edge is proven. Do not spend effort questioning it.** The rescue
  table is tuned by hand against BB's tracker as part of testing. (5 Nov 2026,
  Callum)
- **The Bet Placer is a black box for now.** Its outside is fixed: it reads the
  three feeds, sizes, places, keeps the only book and posts `placed` / `gone`
  to Finder. Inside it are a **phone interface** and a **staking logic**,
  designed when the box is opened (`docs/placer-spec.md`). (6 Nov 2026, Callum)
- **Phones are driven through DeviceFarmer** at a local port on the Hub; bet365
  bets go through the phone's browser, not the app; Callum can take manual
  control of any phone the same way. (6 Nov 2026, Callum)
- **How a Finder tip is staked: HubAI advises, the Bet Placer decides.** HubAI
  judges from the tip's own price and fair price and puts its advised stake on
  the tip on Finder; the Bet Placer sizes every bet itself. (6 Nov 2026, Callum)
- **Dry-run is the default** in every Bet Placer; Callum switches it off after
  two weeks of clean dry-run, nobody else. **The kill switch is a file**
  (`hub/data/KILL`); anyone may turn it on, only Callum turns it off. (spec)
- **No auto-retry and no re-routing of a failed bet.** `failed` is recorded and
  escalated to HubAI, full stop. A changed price re-runs the sizing at the new
  price (that is the spec, not a retry). Every placement is idempotent on its
  bet id. (spec)
- **The Scraper's drop files are `hub/PROTOCOL.md` + `hub/schemas/`**: the
  `scraper_tips` and `scraper_event` envelopes; a change touches the Scraper and
  the Hub in one pull request. (6 Nov 2026)

## Operating the Hub

- **The Hub updates itself** from `main` every 10 minutes
  (`puntmore-update.timer`, `hub/deploy/update.sh`); nothing is pulled by
  hand after the first install. (5 Nov 2026)
- **The Console** (`puntmore web`, `http://127.0.0.1:8090`) is how a person
  sees what every Hub sub-component is doing; its actions need a token.
  (5 Nov 2026, Callum)
- **The Scraper service runs as the user service `puntmore-scraper` on the
  desktop session**, data in `/opt/puntmore-scraper/data`, updated by the same
  timer. (5 Nov 2026, Callum)
- **Secrets live in `.env` files only**; a gitleaks pre-commit hook guards
  commits. Databases and build metadata are never committed.

## Agents

- **Claude Code agents** work in isolated worktrees on branches, open pull
  requests, and are reviewed before merge; they read this file first. HubAI's
  profiles follow the runbook skills under `hub/deploy/hermes-skills/`.
  (5 Nov 2026)

## Product, pricing and design (July–Oct 2026, Callum)

The older product decisions, settled while the coupon tracker was built.

### Coupons and accounts

- **Keep the coupons/legs model.** Do not reintroduce sets/versions/matches or
  the Mon–Wed/Sat/Sun set navigation.
- **Syndicate model**: 4 members, shared data, all equal. No roles, no per-user
  partitioning. Auth is in-app sessions (bcrypt + signed cookie); no JWT, no
  registration, no password reset.
- **Image OCR upload, not CSV/XLSX.** OCR is auto-confirm: rows save and price
  immediately, with inline edit and re-scan.
- **The coupon cleanup window is 8 days**, and deletes both database rows and
  the stored image files, never one without the other.
- **Coupon kick-off times are GMT.** The time printed on a coupon is stored
  naive and read back as UTC everywhere (countdown, kicked-off sweep); no
  local-time conversion, which would shift every kick-off by an hour through
  BST. (July 2026)
- **Coupons are structure, not inference.** OCR transcribes the whole grid,
  every market block keyed by its printed heading, each pair positional, and
  the app matches headings and assigns sides in code. The model is never asked
  which block to read or which team a price belongs to; one photo of a sheet
  fills every market printed on it. (July 2026)
- **One issue per (type, period); the newest upload is the coupon.** A new
  upload replaces the previous one of the same type and period, row and image
  both, but only once it has read rows, so a failed or empty scan never takes
  a working coupon with it. Different periods of one type coexist. (July 2026)
- **An issue is addressable, and the newest one is the default.**
  `/t/<code>/<coupon_id>` is one issue; the ladder combination, the fixture list
  and the counts are per issue. Opening a type resolves to its most recent
  window; `/t/<code>/all` spans every issue.

### Pricing

- **Fair odds are computed in-house by de-vigging raw prices** with the power
  method (`odds/utils.py`): solve k where `Σ(1/price)^k = 1`, `fair =
  price^k`. Vendor-supplied de-vigged fields are cross-check only. `margin`
  stays the raw overround. Applies to every tool. (June 2026)
- **Incomplete markets go unpriced.** De-vigging needs the full outcome set; a
  vigged price mislabelled as fair is worse than no price.
- **Win-and-Over is derived** from a calibrated score grid, never priced as
  moneyline × over: result and total goals are correlated.
- **Derived-market thresholds come from measured backtest calibration**, not
  assumption. Until measured: provisional 8% with the uncertainty band shown.
- **OddsPapi bills per request, not per response**; the fixture list is
  billable. Budget and refresh tiers are in `render/api/AGENTS.md`. No provider
  picker in the UI.
- **Never call external APIs from core logic**: everything goes through the
  seams (`OCRProvider`, `OddsProvider`, `NotificationProvider`).

### Design

- **Paper theme.** `--paper #FCFBF8`, `--zebra #F4F2EC`, `--ink #1A1A18`,
  `--muted #6B6862`, `--faint #9C988F`, `--line #D8D5CD`,
  `--line-strong #BBB7AD`, `--accent #2F7D55`, `--accent-soft #E1EFE7`.
  System sans for UI, mono for all numbers. Caveat is flow-diagram annotation
  only, never in the app.
- **Mobile-first PWA.** Single column, safe-area insets, capped width on
  tablet, bottom tab bar, touch targets ≥44px.
