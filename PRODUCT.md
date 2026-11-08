# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Callum**, the owner and the main user. An expert punter who built the tools and runs the box they depend on. Reads a board on his phone near kickoff, picks a line, switches to the bookmaker's app, places, comes back. Seconds matter; nothing on a board needs explaining to him.
- **A few known people, by invitation** (confirmed 8 Nov 2026). Logins are handed out, never signed up for. Today: the coupon syndicate (four equal members sharing one set of coupons, no roles) and friends with named grants to particular tools. They know what the tools are; they are operators, not prospects.
- **Third-party Bet Placers**: machines, not people. A client's own box runs the Bet Placer against the 2-Up feed with an API key that carries only that grant.

Not for strangers. puntmore.com is not sold and has no public sign-up (confirmed 8 Nov 2026).

## Product Purpose

Puntmore finds value bets and puts them in front of the person (or the Bet Placer) who will place them, before the price goes. A **bet** is a selection and a price; a **value bet** is one priced above an in-house fair price; a **tip** is a bet from an outside source that HubAI has judged. The boards show the rows; the feeds carry the same rows to the Bet Placer. The coupon tracker prices the syndicate's paper coupons from a photo. The personal tools (dogs, Virtuals, P&L, Kelly, the shop) are Callum's own.

Success on a board is a bet placed at a price above fair, with the fair, the price, the edge and the kickoff legible at a glance on a phone.

## Positioning

- **Fair prices are computed in-house** by de-vigging raw bookmaker prices (the power method), across every tool. Vendor "fair" fields are cross-checks only. Incomplete markets go unpriced rather than mis-priced.
- **Two sources of truth, one feed**: OddsPapi for the markets it covers, and a real Chrome on a residential connection (the Scraper on the Hub) for the pages it doesn't. The cloud never touches a bookmaker page.
- **The boards are one-way feeds.** No ledger, no account, no Hub endpoint on Render. Sizing, placing and the only record of bets live on the Hub, in the Bet Placer.
- **Tips are judged, not relayed.** Finder shows tips after HubAI has weighed each against its own price and fair.

## Operating Context

- **Two boxes.** Render (cloud, one deploy per push to `main`) serves the boards, the tracker, the personal tools, the login and the API. The Hub (Callum's box) reads pages, judges tips and places bets; its Console is a separate, private surface.
- **Two hosts from one deploy.** `puntmore.com` is the product (every public tool). `me` (`me.puntmore.com`) is Callum's personal tools with its own login, switched on by `ME_HOST`; until its DNS exists (as of 8 Nov 2026) everything is served on the main domain.
- **Bookmakers in play**: Paddy Power (corners, cards, goals, saves), bet365 (2-Up early payout, goalscorers), William Hill (bet builders). Bets are placed in the bookmakers' own apps and sites, never through Puntmore.
- **Match-day rhythm.** Prices are refreshed on tiers that follow kickoff; boards are interesting in the hours before a game and empty between rounds. Kickoff times are GMT, stored naive.
- **Installed as a PWA** on phones, one icon per tool (`render/frontend/public/icons`), the manifest rewritten per tool as the shell is served.

## Capabilities and Constraints

- **Tools and routes**: coupon tracker (`/`, `/t/<code>`, `/upload`; grant `coupons`), Paddy board (`/paddy`; `cards`), Hills (`/hills`; `hills`), 365 (`/365`, with 2-Up at `/365/2up`; `accabonus`, and goalscorers at `/365/scorers`; `goalscorers`), Finder (`/finder`; `finder`), P&L dogs (`/pnl/dogs`; `pnl`), Virtuals (`/virtuals`), Kelly (`/kelly`), shop (`/shop`). A user sees only the tools their grant names; the bottom tab bar is built from that list.
- **Accounts**: in-app sessions (bcrypt, signed cookie), users seeded from settings, named grants only. No registration, no password reset, no roles. Machine callers use `pm_` API keys with their own grants.
- **Feeds** are plain HTTP GETs on the API (`/api/paddy/feed`, `/api/2up/feed`, `/api/finder/feed`), each gated by the board's grant.
- **Data limits that shape the UI**: a board row needs every price it depends on to be fresh (a stale leg hides the row); the Paddy board only lists a fixture when Paddy prices those markets on it; OddsPapi bills per request, so refresh cadence is a budget, not a dial the user turns.
- **Stack**: React 19 with React Router 7, Vite, CSS modules; FastAPI on Render. Mobile-first, single column, bottom tab bar, safe-area insets, capped width on tablet, touch targets at least 44px. Inputs are 14px with `maximum-scale=1` so iOS does not zoom and strand the layout.
- **Terminology that is fixed**: bet, value bet, tip, price, board, feed, Hub, Render, HubAI, Scraper service, Scraper control, Bet Placer, Console. Dropped names (Worker, betting agent, Bet Finder, shalgo and the rest listed in `docs/decisions.md`) never appear in copy. "Callum", never "Cal", in writing.
- **Undecided**: whether the boards share one identity or each tool keeps its own icon-led identity on the home screen; when `me` goes live on its own host.

## Brand Commitments

- **Names**: Puntmore (the product, `puntmore.com`); Me (the personal site). Per-tool names: Paddy, 2-Up, 365, Hills, Finder, the coupon tracker.
- **Assets**: per-tool SVG and PNG icons in `render/frontend/public/icons`; the app icon `render/frontend/public/icon.svg`.
- **Voice**: plain, short, operator-to-operator. Boards state facts (price, fair, edge, kickoff, priced-at); nothing sells.
- **The incumbent look is not pinned.** The "paper" theme (`--paper #FCFBF8`, ink `#1A1A18`, green accent `#2F7D55`, mono for numbers) from the coupon-tracker era is what ships today. Callum confirmed on 8 Nov 2026 that it is open to replacement; a later redesign may choose a new world. Until one does, refinements keep it.

## Evidence on Hand

- Live, real data on every board (OddsPapi prices, the Scraper's rows, HubAI's judgements). Real coupons and photos in the tracker.
- Team-name spellings for every covered competition in `render/frontend/src/teams.js`.
- No testimonials, customers, case studies, press, pricing or sign-up numbers exist, and none are to be invented.

## Product Principles

1. **Legible in three seconds on a phone.** A row's price, fair, edge and kickoff are read at a glance; anything that slows that down loses.
2. **Fresh or absent.** A price the reader can't trust is not shown; "priced at" is always visible.
3. **The board informs, the bookmaker's app acts.** Puntmore never pretends to place a bet; it hands the reader off cleanly and takes them back.
4. **Operators, not prospects.** No onboarding theatre, no marketing copy, no explaining what a corner is. Invited users already know.
5. **Honest numbers.** In-house fair prices, stated uncertainty where it is measured, nothing dressed up.

## Accessibility & Inclusion

Phone-first use outdoors and in pubs: touch targets at least 44px, high legibility of numbers, no reliance on colour alone for edge or staleness. No formal standard has been set.
