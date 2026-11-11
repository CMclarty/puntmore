# How information flows

One page for the whole chain, read after `docs/decisions.md`. Each stage says what record it
produces, who owns it, where it lives, the id it carries and the status words it may use. The
four rules at the end keep the chain standard; a change that breaks one of them is a design
change, not a bug fix, and needs a line in the register.

## The chain

| # | Stage | Record produced | Owner, where it lives | Id | Status words |
|---|---|---|---|---|---|
| 1 | A page is loaded | nothing durable: a page load, counted against the site's budget | Scraper service, `guard.sqlite3` | — | per target: `running`, `paused`, `blocked`, `retired` (`status.json`) |
| 2 | A parser reads rows | **prices** (raw rows the Paddy board turns into value bets: Paddy Power's lines off a Paddy page, and the fair saves prices read from Bookie Bashing, `bb-saves`) or **tips** (an external source's bets): event, market, selection, optional advised price, stake units, tip time, expiry, origin. A tipster's email is parsed the same way on the Hub (`hub/puntmore_hub/mail.py`, one parser per tipster in `tipsters/`) | Scraper service, `latest/<target>.json`; an email: the Hub, in memory (the message recorded in `mail_seen`) | **`tip_id`**, minted here: sha1 of source, normalised event, market, selection, day (`scraper/ids.py` = `hub/puntmore_hub/ids.py`) | — |
| 3 | Rows leave the Scraper service | a **drop file** per run under `<data>/hub/tips/` (`scraper_tips`), problems as **event** files under `<data>/hub/events/` (`scraper_event`): `hub/PROTOCOL.md`. Paddy's prices are the exception for now: POSTed straight to Render (`/api/paddy/*`, ingest key) | Scraper service writes; the Hub reads and moves each file to `done/` | `tip_id` on every row; `msg_id` per file | event kinds: `unhealthy_source`, `blocked_source` |
| 4 | Scraper control takes them in | a **tip** row in the Hub store, deduplicated on `tip_id` (`inbox.ingest_rows`, the one storage path, also for an email's rows); an event becomes an **escalation** for HubAI | the Hub daemon (`hub/puntmore_hub/inbox.py`, `mail.py`), `hub.sqlite` (`tips`, `escalations`) | `tip_id` | escalation: open, resolved |
| 5 | HubAI judges the tip | a **tip on Finder**: the tip's event, market, selection, the price and fair price it quotes, origin, advised stake, a one-line reason and a verdict, posted by `puntmore finder post` to `POST /api/finder/rows`; the tip is marked in `finder_posted` with the status Render stored. `puntmore finder skip` marks a tip HubAI cannot post | HubAI (`hub/puntmore_hub/finder.py`); Callum asked on Telegram when HubAI is unsure | `tip_id` | `asked`, `offered`, `rejected` |
| 6 | The board keeps it | Finder: one row in `finder_rows`, upserted on `tip_id`; a second row for a bet already `asked`, `offered` or `placed` is stored `rejected` (`duplicate of <tip_id>`), matched on the canonical `event_id`; `expired` at the earlier of kick-off and the tip's expiry; kept 30 days. Paddy and 2-Up build their rows on Render from OddsPapi (2-Up: `twoup_refresh` swaps the board in whole every `twoup_feed_ttl`; on a failed pass the last good board stays with `status.stale: true`) | Render: `finder_rows` (`app/finder/rows.py`); `twoup/feed.py:TwoUpFeed.served`; `cards/feed.py` | Finder: `tip_id`; 2-Up: `event_id` + `side` | Finder: `asked → offered \| rejected \| expired`; `offered → placed \| gone \| rejected \| expired`; 2-Up `status`: `stale`, `not_yet_refreshed`, `killed`, `degraded` |
| 7 | The Bet Placer reads the feeds | `GET /api/paddy/feed`, `/api/2up/feed`, `/api/finder/feed`; sizing and placing are inside the Bet Placer, through DeviceFarmer to the phones; its book is its own | the Bet Placer (`docs/placer-spec.md`) | the feed's own id | its own |
| 8 | Placed or gone | for a tip on Finder, `POST /api/finder/rows/{tip_id}/status` (`puntmore finder status`): the one thing that flows back; no stake, price or record | the Bet Placer → Render | `tip_id` | `placed`, `gone` |
| 9 | People watch | the Console reads the Scraper service's files, the Hub store and Render's `/api/ready`; the boards' pages read the same feeds as the API | nothing new is written | — | the words above, never new ones |

## AW 26's photos (beside the chain)

A slip photo goes its own short way, not through the chain. The page uploads a photo
(`POST /api/aw26/photos`) → Render keeps it in `aw_photos`, `waiting` → HubAI's key takes it
(`reading`), fetches the file and reads it on the Hub → records the slip with `photo_id`
(`done`; a receipt already in: `duplicate`; no slip made of it: back to `waiting`, `failed`
after 3 tries, resent from the page). Id: the photo's `id`, then the slip's `receipt`. Status
words `waiting | reading | failed | duplicate | done`, defined in `render/api/app/aw26/photos.py`.
Each leg's fair comes from Paddy's board as it stood at the slip's printed time
(`GET /api/paddy/board-at`, kept by the Paddy sweep in `paddy_lines` for 14 days after
kick-off, `render/api/app/cards/history.py`); a leg not on it is neutral.
The Hub's side of it is not built yet: until it is, a photo stays `waiting`.

## The four rules

1. **Everything that moves between parts is one record shape per hop.** The Scraper service's
   drop files are envelopes of `hub/schemas/protocol.schema.json` (`type`, `client_id`,
   `msg_id`, `ts`, `body`; types `scraper_tips` and `scraper_event`), checked by the Scraper's
   tests (what the sinks write) and the Hub's (what the inbox reads); a file on disk is only a
   transport. Rows to and from Render are the shapes in `docs/api.md`.
2. **One id, minted at first sight, never re-minted.** `tip_id` is made by the Scraper service
   when the row is parsed (by the Hub's `mail.py` for an email, with the same function) and
   travels unchanged to the Hub store, to HubAI and on to the **tip on Finder** on Render
   (`finder_rows.tip_id`, the key every post and status report is idempotent on). A 2-Up row's
   id is `event_id` + `side`, stable across refreshes. `msg_id` belongs to one drop file only.
   Nobody down the chain recomputes any of them.
3. **One lifecycle per object, defined once.** The status words above are the whole vocabulary:
   a feed's in `twoup/feed.py:TwoUpFeed.status`, a target's in `scraper/status.py`, a tip on
   Finder's in `app/finder/rows.py` (from `docs/finder-spec.md`), an AW 26 photo's in
   `app/aw26/photos.py`. The Console, the CLI, the docs and
   the tests use those words; a new state is a change to this page.
4. **Config flows down, data flows up, never on the same path.** Decisions in the register,
   targets through `scraper/targets.py`, Render settings in its config: all of that moves down
   and is read, never sent. Rows, tips and feed rows move up.

## Where to look when something is lost

- **A tip that never reached Finder:** its `tip_id` in the Scraper service's drop file (for an
  email, `puntmore mail --dry-run` shows the rows and ids a parser reads; `data/mail-status.json`
  the last poll); then the Hub's `tips` table; then `puntmore finder new` (not yet posted by
  HubAI) or its `finder_posted` row (posted, with the status Render stored); then
  `GET /api/finder/board` on Render (its status, and `reason` for a rejected or duplicate row).
- **A photo that never became a slip:** `GET /api/aw26/photos` (its state, tries and reason).
- **A drop file that is not being read:** the Console's Daemon card (the last inbox pass, the
  drop files waiting) and `systemctl --user status puntmore-link`.
- **A fixture missing from the 2-Up feed:** `GET /api/2up/diagnostics` (what the last pass saw
  and could not price), then `status` on the feed (`stale`, `not_yet_refreshed`, `killed`,
  `degraded`) and each row's `priced_at`.
- **A match missing from Hills:** `GET /api/hills/diagnostics` (`sweep.pending`,
  `sweep.stale_williamhill`, `sweep.errors`), then each match's `priced_at` on `/api/hills/feed`.
