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
| 2 | A parser reads rows | **rows**: event, market, selection, optional advised price / stake units / tip time / expiry / origin; a tipster's email is parsed the same way on the Hub by `mail.py` (`puntmore_hub/tipsters/`, one parser per tipster from `tipsters.toml`) | Scraper, `latest/<target>.json`; a tipster email: Hub, in memory (the message is recorded in `mail_seen`) | **`tip_id`** minted here: sha1 of source, normalised event, market, selection, day (`scraper/ids.py`, pinned equal to `hub/puntmore_hub/ids.py`, which `mail.py` uses for a mail tip) | — |
| 3 | Rows leave the Scraper | one **drop file** per run under `<data>/hub/tips/` (tip sources), or one POST to Render for Paddy prices (`/api/paddy/*` with the ingest key); problems are **event** files under `<data>/hub/events/`; each file is one protocol envelope (`scraper_tips`, `scraper_event`). A tipster email's rows have no file: they go straight to stage 4 inside the Hub (`inbox.ingest_rows`, the one storage path), and a message that parses to nothing or whose parser fails becomes the escalation directly | Scraper writes, Hub reads and moves to `done/` | `tip_id` on every row; `msg_id` per file | event kinds: `unhealthy_source`, `blocked_source` |
| 4 | The Hub takes them in | a **tip** row in the Hub store, deduplicated on `tip_id` (since 6 Nov 2026 no `tip` message is queued: tips go to Hermes, stages 3'–6'); an event becomes an **escalation** | Hub, `hub.sqlite` (`tips`, `escalations`) | `tip_id` | escalation: open, resolved |
| 5 | The wire | the **envelope** `{type, client_id, msg_id, ts, body}` over the Hub's outbound WebSocket; every non-control message is acked, replays are acked and ignored, unacked messages are re-sent after a reconnect under the same `msg_id`. **Render has no end of this wire since 6 Nov 2026** (`/api/2up/ws` is removed); the betting agent defines its own Hub endpoint | contract: `hub/PROTOCOL.md` + `hub/schemas/protocol.schema.json`, tested on the Hub's side and the Scraper's | `msg_id` | — |
| 6 | (removed 6 Nov 2026) | Render stores no raw tip: there is no `tips` table, no Bet Finder orchestrator and no Hub socket on Render; Finder (3'–6') keeps only Hermes's verdicts | — | — | — |
| 3' | Finder: the Hub stores the tip | the stage-4 **tip** row; until Hermes posts it, it is "new" (`puntmore finder new`: tips with no `finder_posted` row) | Hub, `hub.sqlite` (`tips`, `finder_posted`) | `tip_id` | — |
| 4' | Finder: Hermes judges it | a **Finder row**: the tip's event, market, selection, the price and fair price it quotes, origin, advised stake, one-line reason and a verdict, posted by `puntmore finder post` to `POST /api/finder/rows` on the ingest key; on a 200 the tip is marked in `finder_posted` with the status it carried | Hermes on the Hub (`hub/puntmore_hub/finder.py`); Callum asked on Telegram when Hermes is unsure | `tip_id` | `asked`, `offered`, `rejected` |
| 5' | Finder: Render keeps it | one row in `finder_rows`, upserted on `tip_id`; a second live row for the same bet is stored `rejected` (`duplicate of <tip_id>`); Render sets `expired` at the earlier of kick-off and the tip's expiry, every minute; kept 30 days | Render, `finder_rows` (`app/finder/rows.py`) | `tip_id` | `asked → offered \| rejected \| expired`; `offered → placed \| gone \| expired`; `placed`, `gone`, `rejected`, `expired` final |
| 6' | Finder: the placer reads it | `GET /api/finder/feed` (the `offered` rows, `finder` grant; also `GET /api/value/finder`, private), and the placer posts `placed` or `gone` back (`POST /api/finder/rows/{tip_id}/status`, ingest key; `puntmore finder status`) — the one thing that flows back, no stake, price or record | the Bet Placer (its own spec); the me page `/finder` shows the board | `tip_id` | `placed`, `gone` |
| 7 | The 2-Up feed prices fixtures | **candidates**: one per fixture side, each with `priced_at` (when its prices were fetched); the scheduler job `twoup_refresh` swaps the board in whole every `twoup_feed_ttl`, and `GET /api/2up/feed` serves it, never fetching on a request. On a total fetch failure the last good board stays, with its old `priced_at` and `status.stale: true` | Render, in memory (`twoup/model.py:Candidate`, `twoup/feed.py:TwoUpFeed.served`) | `event_id` (OddsPapi) and `side`; no `tip_id` | `status`: `stale`, `not_yet_refreshed`, `killed` |
| 8–11 | The client's | Staking, the bets, placing and settling are the client's (the betting agent, and the Hub's placement bot); Render sees none of it and nothing comes back to it | the client | — | — |
| 12 | People watch | the Hub Console reads the Scraper's files, the Hub store and Render's `/api/ready`; the 365 page's 2-Up tab reads the same feed as the API, showing when it was updated and when it was priced | nothing new is written | — | the words above, never new ones |

## The four rules

1. **Everything that moves is a message in the one envelope.** The wire has
   it, and so do the Scraper's drop files and event files: types
   `scraper_tips` and `scraper_event` in `hub/schemas/protocol.schema.json`,
   with the same fields (`type`, `client_id`, `msg_id`, `ts`, `body`), so a
   file on disk is only a transport. The one schema file is checked by the
   Scraper's tests (what the sinks write) and the Hub's (what the inbox reads and
   what the link sends); Render's vendored copy went with its Hub endpoint on
   6 Nov 2026.
2. **One id, minted at first sight, never re-minted.** `tip_id` is made by the
   Scraper when the row is parsed (by the Hub's `mail.py` for a tipster email,
   with the same function) and travels unchanged to the Hub, to Hermes and on
   to Finder on Render (`finder_rows.tip_id`, the key every post and status
   report is idempotent on). A feed row's id is `event_id` + `side`, stable across
   refreshes. `msg_id` belongs to one message only. Nobody down the chain
   recomputes any of them.
3. **One lifecycle per object, defined once.** The status words above are the
   whole vocabulary: a feed's in `twoup/feed.py:TwoUpFeed.status`, a target's in
   `scraper/status.py`, a Finder row's in `app/finder/rows.py` (from
   `docs/finder-spec.md`). The console,
   the CLI, the docs and the tests use those words. No component invents a
   state, and a new state is a change to this page.
4. **Config flows down, data flows up, never on the same path.** Decisions in
   the register, targets through `scraper/targets.py`, Render settings in its
   config: all of that moves down and is read, never sent. Rows, tips and
   candidates move up. The removed `/scraper`
   page was config riding the data path.

## Where to look when something is lost

- A tip that never reached the client: `tip_id` in the Scraper's drop file
  (for a tipster email, `puntmore mail --dry-run` shows the rows and ids a
  parser reads; `data/mail-status.json` the last poll), then in the Hub's
  `tips` table, then `puntmore finder new` (not yet posted by Hermes) or the
  `finder_posted` row (posted, with the status), then `GET /api/finder/board`
  on Render (its status, and `reason` for a rejected or duplicate row).
- A fixture missing from the feed: `GET /api/2up/diagnostics` (what the last
  pass saw and could not price, the dials), then `status` on the feed itself
  (`stale`, `not_yet_refreshed`, `killed`) and each row's `priced_at`.
