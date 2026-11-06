# The Scraper's drop files

The Scraper service hands the Hub its rows and its problems as files on the Hub's own disk; nothing
here crosses a network. Each file is one JSON envelope, validated by `schemas/protocol.schema.json`.
A change to the format touches the Scraper (`scraper/sinks.py`) and the Hub (`puntmore_hub/inbox.py`)
in one pull request.

## The envelope

| Field | Meaning |
|---|---|
| `type` | `scraper_tips` or `scraper_event`; nothing else is valid |
| `client_id` | always `scraper` |
| `msg_id` | one per file (hex) |
| `ts` | when the file was written (ISO 8601) |
| `body` | by type, below |

## The two types

| Type | Where | Body |
|---|---|---|
| `scraper_tips` | `<scraper data>/hub/tips/<ms>-<source>.json`, one per run of a target posting to `hub:<channel>` (a Finder source) | `source, at, url, channel, trust_weight, rows[]`; every row has `event, market, selection` and its `tip_id` (`$defs/scraper_tip_row`) |
| `scraper_event` | `<scraper data>/hub/events/<ms>-<ref>.json` | `kind` (`unhealthy_source` or `blocked_source`), `ref`, `detail`, `at` |

`at` is unix seconds. Every file is written atomically (temp file + rename), so the Hub never reads
half a file, and the Hub moves each one to `hub/done/` once handled.

## Rules

- **Never a guessed row.** The Scraper's sink keeps out any row the Hub would reject (no event,
  market or selection, or a price outside 1.01–1000), so a file it writes always validates; the
  Hub checks again on ingest.
- **`tip_id` is minted once**, where the tip is first seen (`scraper/ids.py` = `hub/puntmore_hub/ids.py`),
  and never re-minted downstream (`docs/dataflow.md`, rule 2).
- **A file written before the envelope** (a bare body) is still read as it is, so an update never
  strands what is already on disk.

## Tests

The canonical examples are `schemas/examples/scraper_tips.json` and `scraper_event.json`.
`scraper/tests/test_sinks_schema.py` checks what the sinks write, `hub/tests/test_inbox.py` what the
inbox reads, and `hub/tests/test_protocol.py` that the schema holds these two types and no other.
