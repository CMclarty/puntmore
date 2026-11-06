# How information flows

One page for the whole chain, read after `docs/decisions.md`. Each stage says
what record it produces, who owns it, where it lives, the id it carries and the
status words it may use. The four rules at the end are what keep the chain
standard; a change that breaks one of them is a design change, not a bug fix,
and needs a line in the register.

## The chain

| # | Stage | Record produced | Owner, where it lives | Id | Status words |
|---|---|---|---|---|---|
| 1 | A page is loaded | nothing durable: a page load, counted against the site's budget | Scraper, `guard.sqlite3` | — | per target: `running`, `paused`, `blocked`, `retired` (in `status.json`) |
| 2 | A parser reads rows | **rows**: event, market, selection, optional advised price / stake units / tip time / expiry / origin | Scraper, `latest/<target>.json` | **`tip_id`** minted here: sha1 of source, normalised event, market, selection, day (`scraper/ids.py`, pinned equal to `hub/puntmore_hub/ids.py`) | — |
| 3 | Rows leave the Scraper | one **drop file** per run under `<data>/hub/tips/` (Bet Finder sources), or one POST to Render for Paddy prices (`/api/paddy/*` with the ingest key); problems are **event** files under `<data>/hub/events/`; each file is one protocol envelope (`scraper_tips`, `scraper_event`) | Scraper writes, Hub reads and moves to `done/` | `tip_id` on every row; `msg_id` per file | event kinds: `unhealthy_source`, `blocked_source` |
| 4 | The Hub takes them in | a **tip** row in the Hub store, deduplicated on `tip_id`, and one `tip` message queued in the outbox; an event becomes an **escalation** | Hub, `hub.sqlite` (`tips`, `outbox`, `escalations`) | `tip_id`; `msg_id` per message | escalation: open, resolved |
| 5 | The wire | the **envelope** `{type, client_id, msg_id, ts, body}` over the Hub's outbound WebSocket to `/api/2up/ws`; every non-control message is acked, replays are acked and ignored, unacked messages are re-sent after a reconnect under the same `msg_id` | contract: `hub/PROTOCOL.md` + `hub/schemas/protocol.schema.json`, copied verbatim on Render and tested on both sides | `msg_id` | — |
| 6 | Render stores the tip | one row in the **`tips`** table; the row the endpoint writes is the row the Bet Finder works on | Render, `tips` | `tip_id` (unique; first copy kept) | `new` → `priced` or `quarantined` or `duplicate` or `expired` → `decided` / `not_placed` → `settled` |
| 7 | The 2-Up feed prices fixtures | **candidates**: one per fixture side, `source_scope: shared`; Bet Finder tips become candidates with `source_scope: owner` | Render, in memory per pass (`twoup/model.py:Candidate`) | `event_id` (OddsPapi) and, for a tip, its `tip_id` in `extra` | — |
| 8 | The decision stakes | a **bet** in the ledger per client and account | Render, `twoup_bets` | **`bet_id`** derived from client, account, event, selection and day (`decision.bet_id_for`); a re-price gets `-r1` | `queued` → `sent` → `placed` / `price_changed` / `failed` → `settled` |
| 9 | Bets go to the Hub | a **`bet_list`** message; a bet is never put in a second frame; `mark_sent` only on the Hub's ack | Render dispatcher → Hub inbox | `msg_id`, `bet_id` | — |
| 10 | The Hub places | a **bet** row on the Hub and a **report** per outcome (`placed`, `price_changed`, `failed`, `settled`, `balance`); dry-run reports say so; a failure is escalated, never retried | Hub, `hub.sqlite` (`bets`, `outbox`), the placement adapter | `bet_id` | received, placing, then the report's kind |
| 11 | Render settles | the ledger row moves to its terminal state; a settled Bet Finder bet credits its tip's source (strike rate, ROI) | Render, `twoup_bets`, `tips`, `betfinder_sources` | `bet_id` → `tip_id` | as in 6 and 8 |
| 12 | People watch | the Hub Console reads the Scraper's files, the Hub store and Render's `/api/ready`; the 365 page's 2-Up tab and Render's operator routes read the feed, the ledger and the tips | nothing new is written | — | the words above, never new ones |

## The four rules

1. **Everything that moves is a message in the one envelope.** The wire has
   it, and so do the Scraper's drop files and event files: types
   `scraper_tips` and `scraper_event` in `hub/schemas/protocol.schema.json`,
   with the same fields (`type`, `client_id`, `msg_id`, `ts`, `body`), so a
   file on disk is only a transport. The one schema file is checked by the
   Scraper's tests (what the sinks write), the Hub's (what the inbox reads and
   what the link sends) and Render's (its vendored copy is byte-identical).
2. **One id, minted at first sight, never re-minted.** `tip_id` is made by the
   Scraper when the row is parsed and travels unchanged to the Hub, over the
   wire, into Render's `tips` row, into the candidate, and back from the
   settled bet to the source credit. `bet_id` is made once by the decision and
   travels the other way. `msg_id` belongs to one message only. Nobody down
   the chain recomputes any of them.
3. **One lifecycle per object, defined once.** The status words above are the
   whole vocabulary: a tip's in `render/api/app/betfinder`, a bet's in
   `twoup/ledger.py:STATUSES`, a target's in `scraper/status.py`. The console,
   the CLI, the docs and the tests use those words. No component invents a
   state, and a new state is a change to this page.
4. **Config flows down, data flows up, never on the same path.** Decisions in
   the register, targets through `scraper/targets.py`, Render settings in its
   config: all of that moves down and is read, never sent. Rows, tips,
   candidates, bets, reports and settlements move up. The removed `/scraper`
   page was config riding the data path.

## Where to look when something is lost

- A tip that never reached Render: `tip_id` in the Scraper's drop file, then in
  the Hub's `tips` table, then in the outbox (`acked_at` empty means Render
  never acked), then in Render's `tips` (`GET /api/2up/hub/tips`).
- A bet that never placed: `bet_id` in `twoup_bets` (status `sent` with no
  report means the Hub has it), then the Hub's `bets` table and its outbox.
- A source whose tips never stake: `GET /api/2up/betfinder/quarantine` for the
  reason, `tips.status` for where it stopped.
