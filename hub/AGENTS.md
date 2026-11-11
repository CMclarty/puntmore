# Working in hub/

`hub/` is the Hub's program apart from the Scraper service (`scraper/`): Scraper control's
intake (the daemon's inbox of the Scraper's drop files, which posts the Scraper's Paddy prices
on to Render, and its tipster-mailbox poll), HubAI's side (escalations, the Finder CLI and the
reading of AW 26's slip photos), the placement engine the Bet Placer starts from, and the
Console. It runs on Callum's box, never on Render, and is not part of the Render deploy
(`.github/workflows/hub-tests.yml` only tests it).
`docs/decisions.md` is read first and wins; the repo's `CLAUDE.md` applies here too. Read this
directory's `README.md` before changing anything, and `PROTOCOL.md` before touching the drop
files.

## Rules

- **`hub/` never loads a page.** Every page load is the Scraper service's; Finder's sources are
  Scraper targets (`site = "generic"`) and reach `hub/` as drop files under `<scraper data>/hub/`.
  `hub/` has no browser and no budget. Bookie Bashing is not a Finder source; the Scraper's one
  BB target (`bb-saves`) is the register's single named exception.
- **Email tipsters are polled here** (`mail.py`: one IMAP mailbox, `PUNTMORE_IMAP_*`; senders and
  parsers in `data/tipsters.toml`), never by Render and never by the Scraper. A parser never
  guesses: one that cannot read an email returns nothing and the tipster is escalated.
- **Tips go to Finder over HTTP only**: HubAI posts them with `puntmore finder post` on the
  ingest key; tips on Finder are Callum's only, never offered to a third party.
- **The drop files are `PROTOCOL.md` + `schemas/protocol.schema.json`**: three types,
  `scraper_tips`, `scraper_event` and `scraper_prices`. A change touches the Scraper's sink and
  the Hub's inbox in ONE pull request; `tests/test_protocol.py` and
  `scraper/tests/test_sinks_schema.py` are the referees.
- **Paddy's prices go to Render through the inbox only** (`inbox.post_prices`,
  `PUNTMORE_RENDER_API_KEY`); no retry loop: a file waits for the next pass, and is dropped once
  15 min old.
- **The daemon talks to Render for AW 26** (`aw26_loop.py`; the register's AW 26 entry). Its
  third loop, beside the inbox and the mailbox poll, starts only while `PUNTMORE_RENDER_HTTP`
  and `PUNTMORE_RENDER_API_KEY` are set (the key granted `aw26` and `cards` as well as
  `ingest`). Every `PUNTMORE_AW26_EVERY_S` (60 s) it takes the oldest waiting slip photo on
  Render's queue (`POST /api/aw26/photos/{id}/take`), downloads it to `data/aw26/` and starts
  HubAI on it (`PUNTMORE_HERMES_AW26_PROFILE`, default `betting-client-manager`, skill
  `puntmore-aw26`), one photo at a time; with no hermes binary it takes nothing. A run
  has `PUNTMORE_AW26_RUN_BUDGET_S` (600 s); 60 s past that its whole process group is killed.
  A photo HubAI neither recorded nor failed is failed "Qwen did not finish", and one the Hub
  could not download "the Hub could not fetch the photo". The photo is deleted after the run;
  HubAI's output stays in `data/aw26/<id>.txt` and the last pass is under `aw26` in
  `daemon-status.json`.
- **HubAI reads a slip by its runbook**, `deploy/hermes-skills/puntmore-aw26/SKILL.md`, which
  `puntmore update` installs in the reader's profile and in the chief of staff's skills
  (`<hermes home>/skills`; `update.py`, `skill_targets`). It works through `puntmore aw26`:
  `photos`, `take <id>`, `fail <id> --reason "..."`, `upload <file> --source page|telegram`,
  `board --at "<printed or ISO>" [--match "<Home v Away>"]` (Paddy's board as it stood then)
  and `record <file or -> [--dry-run]`. The chief of staff puts a slip photo sent on Telegram
  on the queue with `puntmore aw26 upload <file> --source telegram` and does not read it. The
  rules are the register's: Paddy Power accumulators only; the total odds, max return and
  bonus Paddy prints are never sent, Render does the sums; BONUS at the top of a slip is a free
  bet (offer `free`); a printed time (month first, London) becomes UTC on the Hub
  (`aw26.parse_printed`) and is never sent as printed.
- **Slip photos are private.** Never commit one; nothing in the repo carries a real receipt
  number, barcode or QR code, and an example uses the invented receipt
  `B0000000000000-000000-0`.
- **The placement engine is the Bet Placer's** (`docs/placer-spec.md`). Its rules stand while it
  lives here: dry-run is on by default (`PUNTMORE_DRY_RUN`) and only Callum switches it off,
  after two weeks of clean dry-run; the kill switch is the file `data/KILL` (`puntmore kill on`;
  anyone may turn it on, only Callum turns it off); no auto-retry and no re-routing of a failed
  bet, which is recorded and escalated to HubAI; every bet is idempotent on `bet_id`.
- Secrets go in `.env` (git-ignored). Never commit tokens, account details or `sub_clients.yaml`.

## Where to change what

| Want to... | Change |
|---|---|
| Add or pause a Scraper job (days, end date; a Finder source is `site = "generic"`) | the Console's Scraper tab, or `python -m scraper targets …` (`scraper/README.md`, "Changing targets"); never `targets.toml` by hand |
| Fix a broken parser | `scraper/parsers/<name>.py` + a fixture under `scraper/tests/fixtures/` |
| Add an email tipster | an entry in `data/tipsters.toml` (see `tipsters.example.toml`) + a parser in `puntmore_hub/tipsters/<name>.py` registered in `PARSERS`, written from a real email; `puntmore mail --dry-run` shows what it reads |
| Change how HubAI reads a slip photo | the runbook `deploy/hermes-skills/puntmore-aw26/SKILL.md` (on the box after the next `puntmore update`); what is sent to Render, `puntmore_hub/aw26.py` |
| Change where the drop files are read from | `.env` (`PUNTMORE_SCRAPER_DATA`; the old `PUNTMORE_WORKER_DATA` is still read) |
| Change the drop files | `PROTOCOL.md`, the schema, an example, `scraper/sinks.py`, `puntmore_hub/inbox.py`, in one pull request |
| Add a bookmaker account | `sub_clients.yaml` (see `sub_clients.example.yaml`); the Bet Placer's phone interface will replace it |
| Implement real placement | the Bet Placer's phone interface through DeviceFarmer (`docs/placer-spec.md`); today's stub is `puntmore_hub/placement/adapters/phone.py` |
| See what the Hub is doing | the Console at http://127.0.0.1:8090 (`puntmore web`, unit `puntmore-web`; code in `puntmore_hub/web/`) |
| Get a merged change onto the box | nothing: it arrives within 10 minutes via `puntmore-update.timer`; `hub/deploy/update.sh` by hand (README, "Updating the Hub") |

## Checks

```
cd hub && ~/.venvs/puntmore/bin/pytest -q   # must pass before any commit
puntmore health                             # exit 1 = Scraper down, a source blocked/unhealthy, or an open escalation
puntmore inbox                              # one pass over the drop files, then posts waiting price drops (the daemon: every 30 s, prices every 10 s)
puntmore mail --dry-run                     # what the tipster parsers would read; stores nothing
puntmore aw26 photos                        # AW 26's photo queue on Render: each photo's state, tries and reason
```

Directory ownership when several agents work at once: `render/` (Render) and `hub/` + `scraper/`
(the Hub) are edited by different branches; the Hub shares nothing with Render but the HTTP
routes in `docs/api.md`.
