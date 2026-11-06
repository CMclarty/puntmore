# Finder — the module spec

Decided with Callum on 6 Nov 2026 (his decisions marked **(Callum)**, the author's defaults **(call)**); built the same day. `docs/decisions.md` wins over this file, and it changes in the same pull request as the code.

## What it is and why

A board beside Paddy and 2-Up: Callum's own tips, on `me.puntmore.com` and nowhere else. It turns what arrives from outside (tipsters by email, web pages, WhatsApp and whatever else carries a tip) into a feed of bets the Bet Placer can act on. The tips Callum follows have no fair line of ours to price against: each one needs judging, and that judgement is HubAI's on the Hub, with Callum asked when HubAI is unsure. **(Callum)**

The three boards (Paddy, 2-Up, Finder) are the sources of bet value and feed one consumer, the **Bet Placer** (`docs/placer-spec.md`), which sizes across all of them, runs the phones and keeps the records. Finder rows reach only Callum's Bet Placer; a third party's runs on 2-Up only. **(Callum)**

## Decisions

- **HubAI judges every tip.** The judgement comes from the tip itself: a tip offers a price and a fair price, and HubAI reads both; where the call is clear it advises a stake, where it is not it asks Callum (Telegram) and waits. **(Callum)**
- **Tips cover football and horse racing.** A football tip is a fixture, a market and a selection; a racing tip is a race (course and off time), a horse and win or each-way. **(Callum)**
- **HubAI advises, the Bet Placer decides.** The stake on a Finder row is HubAI's advice; the Bet Placer sizes every bet itself across all sources (its sizing page is the placer spec's). **(Callum)**
- **Rows go through Render, not Hub-to-Hub.** HubAI posts the judged row up to Render; the Bet Placer reads it from Render with the other two boards, one place and one row shape; the board on `me` shows the same rows. Cost: a round trip on a bet with no in-play urgency. **(call, agreed by Callum)**
- **A row leaves the feed** when the Bet Placer has placed it (added to its book, removed here), when the price it was found at is gone, or at kick-off. **(Callum)**
- **OddsPapi stays the odds source; the Scraper service reads every page any tool needs and fills what OddsPapi lacks.** **(Callum)**
- **Finder's interface is HTTP only**: the key-authenticated routes below.

## Goals, non-goals, anti-goals

- Goals: every tip Callum receives is seen, judged once, and either offered to the Bet Placer, asked about, or rejected with a reason he can read; the Bet Placer reads Finder in the same shape as 2-Up and Paddy; football and racing both fit the one row.
- Non-goals: staking, placing, records (the Bet Placer's); pricing a tip against OddsPapi or any line but the one the tip gives; anyone but Callum.
- Anti-goals: a guessed bet. A tip HubAI cannot read, or that gives no fair price, is never offered; it is shown as asked or rejected. A row offered twice for one tip. A tip reaching a third party.

## Constraints

- Callum's only: served on `me` and behind its own grant (`finder`, **call**); the board and the feed are invisible to every other account and key.
- Render never loads a page and never places a bet. Email, pages and chat are read on the Hub.
- Secrets in `.env` only; the Hub posts with the ingest key (`SAVES_INGEST_KEY`, the `ingest` grant), as the Scraper does for Paddy. **(call)**
- Nothing in this tool retries or re-routes a bet. A failed placement is the Bet Placer's to escalate.
- Idempotent on `tip_id`: minted once at first sight on the Hub (`scraper/ids.py` = `hub/puntmore_hub/ids.py`: sha1 of source, normalised event, market, selection, day) and never re-minted; a re-posted row with the same `tip_id` updates, never duplicates.

## The flow

1. **Intake, on the Hub (Scraper control).** A tipster email is polled and parsed by the Hub (`hub/puntmore_hub/mail.py`, one parser per tipster in `tipsters/`). A web source is a Scraper `generic` target whose rows arrive as a drop file and are stored by the Hub's inbox. WhatsApp and anything else is a channel HubAI reads itself. Every intake path yields the same tip: `source_id, event, market, selection, advised_price?, advised_stake_units?, tip_time?, expiry?, tip_id`.
2. **Judgement, by HubAI.** HubAI reads the tip's event, market, selection, the price it quotes and the fair price it quotes, and decides. **(call, the rule Callum tightens in use):** **offer** when the tip gives both prices and the quoted price beats the fair one, with the tip's advised stake where it gives one and a one-line reason; **ask** Callum when the tip gives no fair price, cannot be read with confidence, or names an event HubAI cannot place on a day and time; **reject** when the quoted price does not beat the fair one, with the reason. No price is looked up: the Hub has no OddsPapi client and Render spends no quota on a tip; the Bet Placer sees the live price when it places and reports `gone` if the tip's price is no longer there. A Telegram answer turns an asked row into offered or rejected.
3. **Post to Render.** HubAI posts the Finder row and its status (below) through the Finder CLI (`puntmore finder`, `hub/README.md`). Render keeps it, serves it, and expires it at kick-off on its own clock.
4. **The Bet Placer reads** `GET /api/finder/feed` (status `offered` only) and, when it has placed or found the price gone, posts that status back so the row leaves. That status post is the one thing that flows back; it carries no stake, price or record (those are the Bet Placer's book).
5. **Callum watches** the board on `me`: offered, asked (with what HubAI wants to know), rejected (with why), and recently placed or gone. **(call)**

## The row

One shape for the Bet Placer across all three boards: the cross-tool `ValueSelection` (`render/api/app/schemas.py`, served at `/api/value/{source}`), with `source = "finder"`, `bookmaker` as the tip names it, `odds` the price the tip quotes, `fair` the fair price the tip quotes, `edge = odds / fair - 1` as the shape defines it, `sport` = `football` | `horse`, `event` the fixture ("Home v Away") or the race ("Course HH:MM"), `kickoff` the kick-off or the off time (UTC), `market` = the football market as the tip names it, or `win` | `each_way` for a horse, `selection` the team, outcome or horse. Finder adds, in the same row: `tip_id`, `origin` (the tipster or page), `advised_stake` (the tip's or HubAI's advice, pounds, null when neither gives one), `reason` (one line), `status`, `expires_at`, `asked_at`, `answered_at`, `seen_at`. An `offered` row carries `kickoff`, `odds` and `fair`; a post saying `offered` without any of them is refused whole (422). An `asked` row may leave all three out, since HubAI asks exactly when it cannot place a tip on a day and time or the tip quotes no price, and so may the `rejected` answer to it. **(Callum, ruling FR1, 6 Nov 2026)**

Status words, defined once here and used by the board, the feed, the Bet Placer and the tests: `asked` → `offered` | `rejected` | `expired`; `offered` → `placed` | `gone` | `rejected` | `expired`. `placed`, `gone`, `rejected` and `expired` are final. Nothing else; a new word is a change to this file. **(call)** `offered` → `rejected` is HubAI withdrawing a row (a tipster's retraction), by re-posting it `rejected` with the reason; a row the Bet Placer already took is `placed` and final, so the withdrawal is refused and the Bet Placer's book is the truth. **(Callum, ruling FR4, 6 Nov 2026)** Render sets `expired` at the earlier of kick-off and the tip's own `expiry`, on `asked` rows too; an `asked` row with no kick-off expires at the tip's own `expiry`, else 48 hours after HubAI last posted it (`seen_at`) **(FR1)**. Render refuses (409) a re-post that would move a final row (in `POST /api/finder/rows` the row is listed under `refused` and left as it was); a Telegram answer that arrives after kick-off therefore changes nothing. **(call)** One bet per fixture, market, line and selection: a second row for the same bet while one is `asked`, `offered` or `placed` is stored `rejected` with reason `duplicate of <tip_id>`, whatever its own `tip_id` or source; `gone`, `rejected` and `expired` rows do not hold the bet. **(Callum, ruling FR2, 6 Nov 2026)** `tip_id` alone (a hash of the tip's words and day) is not enough to stop a reworded or re-sent tip being placed twice, so the fixture is matched on `event_id`, the canonical key HubAI fills: `football:<YYYY-MM-DD>:<home>-v-<away>` or `horse:<YYYY-MM-DD>:<course>:<HH:MM>`, lower-case ASCII, words joined by hyphens, no punctuation (the full rule is HubAI's contract in `hub/README.md`, "Finder"). The event's own words, case- and space-folded, are the fallback when a row has none. **(Callum, ruling FR3, 6 Nov 2026)**

## Interfaces (Render)

- `POST /api/finder/rows` (ingest key): a list of rows as above; upsert on `tip_id`; from HubAI. The answer lists every posted row with the status Render stored (`rows: [{tip_id, status}]`), and the Hub records that, not what HubAI sent: an `offered` stored as a duplicate is `rejected`, a withdrawal of a placed row is `placed`. **(FR5)** A tip HubAI cannot post at all (another sport, nothing readable) is skipped on the Hub (`puntmore finder skip TIP_ID --reason …`), so it leaves HubAI's list of new tips and never reaches Render. **(Callum, ruling FR5, 6 Nov 2026)**
- `POST /api/finder/rows/{tip_id}/status` (ingest key): `{status: placed|gone, at}`; from the Bet Placer. `expired` is set by Render at kick-off; `offered|rejected` only by HubAI's own re-post.
- `GET /api/finder/feed` (`finder` grant; the Bet Placer's key carries it): rows with status `offered`, soonest kick-off first, `generated_at`, every row with `seen_at`.
- `GET /api/finder/board` (`finder` grant): every row of the last 3 days, all statuses, for the page.
- `GET /api/value/finder` (`finder` grant only, **call**): the `offered` rows in the cross-tool shape. Finder is **not** a source of the combined `GET /api/value` and the `value` grant does not reach it: that feed is read by machine keys that may belong to others, and it filters on `edge`, which Finder rows do not have. `*` includes `finder` as it includes every tool, so the standing rule is: a key or account issued to anyone but Callum never carries `*`, only named grants (`render/api/AGENTS.md`, API clients). **(call)**
- Page `/finder` on `me`; nav entry "Finder"; the `finder` grant on Callum's account only.

## Storage and configuration

- One SQLite table `finder_rows` on the Render disk, retention 30 days, swept by the scheduler.
- HubAI's prompts, its Telegram asking and its WhatsApp reading live in HubAI's own configuration on the Hub, against the contract above and the offer / ask / reject rule in step 2.
- Tests cover the status machine, upsert on `tip_id`, expiry at kick-off, the feed showing `offered` only, the grant hiding everything from anyone but Callum, and the ingest key on both POSTs.
