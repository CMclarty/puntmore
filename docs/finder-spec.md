# Finder — the private Bet Finder (design, 6 Nov 2026)

Built: 6 Nov 2026, plan `docs/superpowers/plans/2026-11-06-finder.md`.

Written 6 Nov 2026 from a conversation with Callum; his decisions are marked **(Callum)**, the author's defaults **(call)**. `docs/decisions.md` wins over this file. It is the design a builder plans from; it changes in the same pull request as the code.

## What it is and why

A third board beside Paddy and 2-Up: Callum's own bet finder, owner-only, on `me.puntmore.com`. It turns what arrives from outside (tipsters by email, web pages, WhatsApp and whatever else carries a tip) into a feed of bets the Bet Placer can act on. It exists because the tips Callum follows have no fair line to price against: each one needs judging, and that judgement is Hermes's on the Hub, with Callum asked when Hermes is unsure. **(Callum)**

The three boards (Paddy, 2-Up, Finder) are the sources of bet value. They feed one consumer, the **Bet Placer** (a separate tool, its own spec): it works out stakes across all sources, runs the phones and keeps the records, on Callum's Hub and on third-party clients. Finder rows reach only Callum's placer, never a third party. **(Callum)**

## Decisions

- **Hermes assesses every tip.** The judgement comes from the tip itself: a tip offers a price and a fair price, and Hermes reads both; where the call is clear it infers a stake, where it is not it asks Callum (Telegram) and waits. **(Callum)** The register line "the local model never prices a bet" is struck, dated 6 Nov 2026.
- **Tips cover football and horse racing.** A football tip is a fixture, a market and a selection; a racing tip is a race (course and off time), a horse and win or each-way. **(Callum)**
- **Hermes advises, the Bet Placer decides.** The stake on a Finder row is Hermes's advice; the placer sizes every bet itself across all sources (its console gets a sizing page; that is the placer's spec). **(Callum)**
- **Rows go through this tool, not Hub-to-Hub.** Hermes posts the assessed bet up to Render; the placer reads it from Render with the other two boards, one place and one row shape; the me board shows the same rows. Cost: a round trip on a bet with no in-play urgency. **(call, agreed by Callum)**
- **A row leaves the feed** when the placer has placed it (added to the placer's log, removed here), when the price it was found at is gone, or at kick-off. **(Callum)**
- **OddsPapi stays the odds source; the Scraper fills the markets OddsPapi does not cover** and is the Hub-side route for every page any tool needs, so the sites see a residential IP and a real browser. **(Callum)**
- **The Hub endpoint is per tool** (register, 6 Nov 2026): Finder's is a pair of key-authenticated HTTP routes, below; no WebSocket.

## Goals, non-goals, anti-goals

- Goals: every tip Callum receives is seen, judged once, and either offered to the placer, asked about, or rejected with a reason he can read; the placer reads Finder in the same shape as 2-Up and Paddy; football and racing both fit the one row.
- Non-goals: staking, placing, records (the placer's); pricing a tip against OddsPapi or any line but the one the tip gives; any client but Callum.
- Anti-goals: a guessed bet. A tip Hermes cannot read, or that gives no fair price, is never offered; it is shown as asked or rejected. A row offered twice for one tip. A tip reaching a third-party client.

## Constraints

- Owner-only: served on the me subdomain and behind its own grant (`finder`, **call**); the board and the feed are invisible to every other account and key.
- Render never loads a page and never places a bet. Email, pages and chat are read on the Hub.
- Secrets in `.env` only; the Hub posts with the existing ingest key (`SAVES_INGEST_KEY`, the `ingest` grant), as the Scraper does for Paddy. **(call)**
- Nothing in this tool retries or re-routes a bet. A failed placement is the placer's to escalate.
- Idempotent on `tip_id`: minted once at first sight on the Hub (`scraper/ids.py` = `hub/puntmore_hub/ids.py`: sha1 of source, normalised event, market, selection, day) and never re-minted; a re-posted row with the same `tip_id` updates, never duplicates.

## The flow

1. **Intake, on the Hub.** A tipster email is polled and parsed by the Hub (`hub/puntmore_hub/tipsters/`, one parser per tipster, exists). A web source is a Scraper `generic` target dropping rows into the Hub inbox (exists). WhatsApp and anything else is a channel Hermes reads itself (how is Hermes's business, not this spec's). Every intake path yields the same row: `source_id, event, market, selection, advised_price?, advised_stake_units?, tip_time?, expiry?, tip_id`.
2. **Assessment, by Hermes.** Hermes reads the tip's event, market, selection, the price it quotes and the fair price it quotes, and decides. **(call, the rule to tighten in use):** **offer** when the tip gives both prices and the quoted price beats the fair one, with the tip's advised stake where it gives one and a one-line reason; **ask** Callum when the tip gives no fair price, cannot be read with confidence, or names an event Hermes cannot place on a day and time; **reject** when the quoted price does not beat the fair one, with the reason. No price is looked up: the Hub has no OddsPapi client and Render spends no quota on a tip; the placer sees the live price when it places and reports `gone` if the tip's price is no longer there. A Telegram answer turns an asked row into offered or rejected.
3. **Post to Render.** Hermes posts the row and its status (below). Render keeps it, serves it, and expires it at kick-off on its own clock.
4. **The placer reads** `GET /api/finder/feed` (status `offered` only) and, when it has placed or found the price gone, posts that status back so the row leaves. That status post is the one thing that flows back; it carries no stake, price or record (those are the placer's log).
5. **Callum watches** the board on me: offered, asked (with what Hermes wants to know), rejected (with why), and recently placed or gone. **(call)**

## The row

One shape for the placer across all three boards: the cross-tool `ValueSelection` (`render/api/app/schemas.py`, served at `/api/value/{source}`), with `source = "finder"`, `bookmaker` as the tip names it, `odds` the price the tip quotes, `fair` the fair price the tip quotes, `edge = odds / fair - 1` as the shape defines it, `sport` = `football` | `horse`, `event` the fixture ("Home v Away") or the race ("Course HH:MM"), `kickoff` the kick-off or the off time (UTC), `market` = the football market as the tip names it, or `win` | `each_way` for a horse, `selection` the team, outcome or horse. Finder adds, in the same row: `tip_id`, `origin` (the tipster or page), `advised_stake` (the tip's or Hermes's advice, pounds, null when neither gives one), `reason` (one line), `status`, `expires_at`, `asked_at`, `answered_at`, `seen_at`. A row whose `fair` is missing is `asked`, never `offered`.

Status words, defined once here and used by the board, the feed, the placer and the tests: `asked` → `offered` | `rejected` | `expired`; `offered` → `placed` | `gone` | `expired`. `placed`, `gone`, `rejected` and `expired` are final. Nothing else; a new word is a change to this file. **(call)** Render sets `expired` at the earlier of kick-off and the tip's own `expiry`, on `asked` rows too, and refuses (409) a re-post that would move a final row; a Telegram answer that arrives after kick-off therefore changes nothing. **(call)** One live bet per fixture, market and selection: a second row for the same `event_id`, `market`, `selection` while one is `asked` or `offered` is stored `rejected` with reason `duplicate of <tip_id>`, whatever its own `tip_id` or source; `tip_id` alone (a hash of the tip's words and day) is not enough to stop a reworded or re-sent tip being placed twice.

## Interfaces (Render)

- `POST /api/finder/rows` (ingest key): a list of rows as above; upsert on `tip_id`; from Hermes.
- `POST /api/finder/rows/{tip_id}/status` (ingest key): `{status: placed|gone, at}`; from the placer. `expired` is set by Render at kick-off; `offered|rejected` only by Hermes's own re-post.
- `GET /api/finder/feed` (`finder` grant; the placer's key carries it): rows with status `offered`, soonest kick-off first, `generated_at`, every row with `seen_at`.
- `GET /api/finder/board` (`finder` grant): every row of the last 3 days, all statuses, for the page.
- `GET /api/value/finder` (`finder` grant only, **call**): the `offered` rows in the cross-tool shape. Finder is **not** a source of the combined `GET /api/value` and the `value` grant does not reach it: that feed is read by machine keys that may belong to others, and it filters on `edge`, which Finder rows do not have. `*` includes `finder` as it includes every tool, so the standing rule is: a key or account issued to anyone but Callum never carries `*`, only named grants (`render/api/AGENTS.md`, API clients). **(call)**
- Page `/finder` on me; nav entry "Finder"; the `finder` grant on Callum's account only.

## What this replaces

The Render-side Bet Finder orchestrator (`render/api/app/betfinder/`, the `tips` table, the `tip` message on the removed WebSocket) is being deleted by the 2-Up work of the same day (`docs/superpowers/plans/2026-11-06-twoup-value-feed.md`); Finder is built after that lands and adds, never migrates: nothing in the old tables is carried over. On the Hub, `mail.py` and the inbox keep parsing; the rows they produce go to Hermes instead of the outbox, and the Hub's `tip` outbox route is retired in the same change. The register's "tip finding lives on the clients" holds: the finding and the judging are on the Hub; Render only keeps, serves and expires what Hermes decided.

## Left to the builder

- The me subdomain's gating in the frontend (`site.js` knows shalgo and puntmore; me is in the register but not yet in the code): add it the way shalgo is done, with Finder as its first tool. The personal tools the architecture already places on me (dogs, Virtuals, P&L, Kelly, shop) stay where they are until Callum moves them; this work does not change what any other account can reach. **(call)**
- Storage: one SQLite table `finder_rows` on the Render disk, retention 30 days, swept by the existing scheduler.
- Hermes's prompts, its Telegram asking, its WhatsApp reading: on the Hub, in Hermes's own configuration, against the contract above and the offer / ask / reject rule in step 2, which Callum tightens in use. The Hub's existing `mail.py` and inbox stay the intake; what changes is where their rows go (to Hermes, then to `POST /api/finder/rows`) instead of the removed Render `tip` message.
- Tests: the status machine; upsert on `tip_id`; expiry at kick-off; the feed shows `offered` only; the grant hides everything from a non-owner; the ingest key is required on both POSTs.
