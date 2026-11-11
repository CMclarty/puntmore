# AW 26 Plan B: the Hub reads slip photos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A photo uploaded on the AW 26 page (or sent to HubAI on Telegram) is collected by the Hub, read by HubAI, priced off Paddy's board as it stood when the slip was placed, and recorded on Render with no typing.

**Architecture:** Render already holds the photo queue and the record route (Plan A). The Hub gains a small Render client and `puntmore aw26 ...` commands (deterministic code: times, totals, 409s), a daemon loop that takes one waiting photo at a time and starts HubAI on it with the photo attached, and a runbook that tells HubAI how to read a Paddy slip. Results (Plan C) are not in this plan.

**Tech Stack:** Python 3 stdlib on the Hub (urllib, zoneinfo, subprocess), pytest; FastAPI/SQLAlchemy on Render for the carry-in fixes; Hermes (`hermes chat --image`).

**Spec:** `docs/superpowers/specs/2026-11-09-aw26-qwen-design.md` ("HubAI on the Hub", "What Qwen reads, and how", "Hub", "HubAI runbook"). The register (`docs/decisions.md`) wins where they differ: fairs come from `GET /api/paddy/board-at` at the slip's printed time, not the live feed (10 Nov 2026); a free bet is its own slip, offer `free` (10 Nov 2026).

## Global Constraints

- The register wins over the spec and this plan. Its edits go in this pull request, dated, in Callum's words (Task 6).
- Callum, 11 Nov 2026: AW 26 records **Paddy Power accumulators only**. Doubles, multiway bets and other bookmakers' slips are refused, not recorded.
- Callum, 11 Nov 2026: "paddy make mistakes on payout calcs. always calc your own." Printed total odds, max return and bonus are never sent or trusted; Render works returns from the legs (`aw26/offers.py`, unchanged).
- Callum, 11 Nov 2026: "free bets will always say BONUS at the top of the slip" → offer `free`. No BONUS → not free.
- Leg price: the odds as printed on the leg (decimal as shown; a fraction converted exactly, 2/7 → 1 + 2/7). My call.
- The terminal prints times as `MM/DD/YYYY, h:mm[:ss] AM/PM`, London time. The Hub converts these to ISO UTC; Render's `parse_when` would misread or drop them, so a printed time never reaches Render raw.
- Slip photos are never committed (Callum: "these photos can't be made public"). Training photos live in `~/aw26-training-slips/` only. Nothing in the repo carries a real receipt number, barcode or QR data.
- `hub/` code never loads a web page. Stdlib only on the Hub (as `finder.py`). Secrets from `.env` only; HubAI never sees the Render key, it runs `puntmore aw26 ...`.
- Hub tests: `cd hub && ~/.venvs/puntmore/bin/pytest -q`. Render tests: `cd render/api && .venv/bin/python -m pytest tests/ -q` (create the venv per CLAUDE.md "Fresh clone" if the worktree lacks one, or use the main checkout's `render/api/.venv` python).
- Docs change in the same PR as the code.

## Review Focus

1. A printed time that is ambiguous or unreadable (`10/12/2026, 3:00 PM` must be 12 Oct; `garbage`) → converted correctly, or the record is refused before it reaches Render. Never "now".
2. The same slip photographed twice → the second photo ends `duplicate` (Render's 409), and the Hub's `record` exits 0 for it.
3. HubAI crashes, hangs or ends without recording or failing → the Hub fails the photo "Qwen did not finish", so it never sits in `reading`.
4. Render or the key missing, or Render down → the daemon loop does nothing and logs; the inbox loop is unaffected.
5. A photo already `done` must never become `duplicate` (carry-in 1), and an upload whose answer fails to build must not delete committed files (carry-in 3).

---

### Task 1: Render carry-ins from Plan A

**Files:**
- Modify: `render/api/app/aw26/record.py` (`_duplicate`)
- Modify: `render/api/app/routers/aw26.py` (`upload_photos`, `_keep`)
- Modify: `render/api/tests/test_aw26_photos.py` (`test_a_record_racing_another_for_the_same_photo_is_refused`, new tests)

**Interfaces:** none new. `photos._move(db, photo_id, states, refusal, **values)` is the guarded UPDATE to reuse.

- [ ] **Step 1: Failing test** `test_a_done_photo_is_never_made_a_duplicate`: record slip A with photo P (P done), then record a slip with the same receipt and `photo_id=P` via the service (bypassing the router's early `done` check by calling `record._duplicate(db, P.id, receipt)` directly). Assert `Refused` with status 409 is raised, and `db.get(AwPhoto, P.id).state == "done"` and `slip_id` unchanged.
- [ ] **Step 2:** Run it; expect FAIL (state becomes `duplicate`).
- [ ] **Step 3:** `_duplicate` moves the photo with `photos._move(db, photo_id, ("waiting", "reading", "failed"), ..., state="duplicate", reason=str(refusal))`, commits only when a row moved; a `Refused` from `_move` is swallowed (the 409 for the receipt is still raised).
- [ ] **Step 4:** Fix `test_a_record_racing_another_for_the_same_photo_is_refused` so it reaches the guard: keep the photo object/reference across the two sessions as Plan A's review said (the second record must get past the early `photo.state == "done"` check and be refused by `photos.done`'s guarded UPDATE). Prove it reaches the guard: temporarily break the guard locally, see the test fail, restore.
- [ ] **Step 5: Failing test** `test_an_answer_that_fails_after_commit_keeps_the_photos`: monkeypatch `AwPhotoOut.model_validate` to raise after the commit; POST two photos; assert the rows exist and both files are still on disk.
- [ ] **Step 6:** In `upload_photos`, the `try/except → _undo` covers storing only; `db.commit()` is the last statement inside it; building the answer (`AwPhotoOut...model_dump`) happens after the `try`.
- [ ] **Step 7:** Run the full Render suite; all pass. Commit `AW 26: guard a duplicate, keep committed photos, a race test that reaches the guard`.

### Task 2: The Hub's AW 26 client and printed times

**Files:**
- Create: `hub/puntmore_hub/aw26.py`
- Modify: `hub/puntmore_hub/finder.py` (move `_open`/`_post` request building into a shared helper only if it stays small; otherwise aw26.py has its own, same shape)
- Test: `hub/tests/test_aw26.py`

**Interfaces — Produces (later tasks use these exact names):**
- `parse_printed(text: str) -> str` — `"10/17/2026, 12:06:07 PM"` (also without seconds, without the comma, or already ISO 8601) → ISO UTC `"2026-10-17T11:06:07Z"`; London time, DST-correct (`zoneinfo.ZoneInfo("Europe/London")`). Anything else raises `ValueError("unreadable time: ...")`. Month first, always.
- `class RenderError(RuntimeError)` with `.status: int` and `.detail: str`.
- `request(cfg, method: str, path: str, *, body: dict | None = None, files: list[tuple[str, bytes, str]] | None = None, form: dict | None = None, opener=None) -> tuple[int, bytes, str]` — status, body bytes, content type. Bearer `cfg.render_api_key` to `cfg.render_http + path`. Raises `RuntimeError` naming the variable when either is blank. Multipart when `files` is given (stdlib, hand-built boundary).
- `configured(cfg) -> bool` — both `render_http` and `render_api_key` non-blank.

- [ ] **Step 1: Failing tests** in `test_aw26.py`:
  - `parse_printed("10/17/2026, 12:06:07 PM") == "2026-10-17T11:06:07Z"` (BST)
  - `parse_printed("11/14/2026, 3:00 PM") == "2026-11-14T15:00:00Z"` (GMT)
  - `parse_printed("10/12/2026, 3:00 PM") == "2026-10-12T14:00:00Z"` (12 Oct, never 10 Dec)
  - `parse_printed("2026-10-17T11:06:07Z") == "2026-10-17T11:06:07Z"`
  - `parse_printed("12:30 PM")` and `parse_printed("17/10/2026 12:30")` raise `ValueError`
  - `request` with a fake opener: sends `Authorization: Bearer k`, JSON body, returns `(409, b'{"detail":"x"}', "application/json")` without raising on 409
  - `request` with blank `render_http` raises `RuntimeError` mentioning `PUNTMORE_RENDER_HTTP`
- [ ] **Step 2:** Run; FAIL.
- [ ] **Step 3:** Implement. 12-hour clock: `12:xx AM` is 00:xx, `12:xx PM` is 12:xx.
- [ ] **Step 4:** Run the hub suite; PASS. Commit `Hub: AW 26 client, and the terminal's printed times to UTC`.

### Task 3: `puntmore aw26 ...` commands

**Files:**
- Modify: `hub/puntmore_hub/aw26.py`
- Modify: `hub/puntmore_hub/cli.py` (`cmd_aw26`, parser)
- Test: `hub/tests/test_aw26.py`

**Interfaces — Consumes:** Task 2. Render routes (all `/api/aw26/...` behind `aw26`, Paddy's behind `cards`): `GET /photos`, `POST /photos/{id}/take`, `GET /photos/{id}/image`, `POST /photos/{id}/fail {reason}`, `POST /photos` (multipart `files`, form `source`), `POST /slips/record` (`AwSlipRecordIn`), `GET /api/paddy/board-at?at=<iso>`.

**Produces (functions, each returns a dict printed as JSON by the CLI):**
- `photos(cfg, opener=None) -> list[dict]`
- `take(cfg, photo_id: int, dest_dir: Path, opener=None) -> Path` — take, then download the image to `dest_dir/<id><ext>` (ext from content type: `.jpg`, `.png`, `.webp`, else `.img`).
- `fail(cfg, photo_id: int, reason: str, opener=None) -> dict`
- `upload(cfg, path: Path, source: str = "telegram", opener=None) -> dict`
- `board(cfg, at: str, match: str | None = None, opener=None) -> list[dict]` — `at` through `parse_printed`; `match` keeps only games whose `home`, `away` or `match` contains any word of length ≥ 4 from `match`, case-insensitive. Each game printed compact: `event_id, match, commence_time, bets[{market, line, side, pick, odds, fair}]`.
- `record(cfg, slip: dict, *, dry_run: bool = False, opener=None) -> dict`:
  - `placed_at` and each leg's `kickoff` through `parse_printed` (a blank kickoff stays blank); `ValueError` → refused before any request, exit 2, message names the field.
  - Drops any key Render does not take (`total_odds`, `max_return`, `bonus`, `returns`, `potential_returns`), so a printed total can never be sent.
  - `dry_run`: prints the normalised body and posts nothing.
  - Render 200 → `{"recorded": <slip>}`; 409 → `{"duplicate": <detail>}` and **exit 0** (carry-in 4: "already a slip"/"already in" is success); any other status → exit 1 with the detail.
- CLI: `puntmore aw26 photos | take <id> | fail <id> --reason R | upload <file> [--source page|telegram] | board --at T [--match M] | record <file|-> [--dry-run]`.

- [ ] **Step 1: Failing tests** with a fake opener recording requests:
  - `record` converts `placed_at "10/16/2026, 4:17:52 PM"` to `"2026-10-16T15:17:52Z"` in the posted body and strips `max_return`
  - `record` with `placed_at "yesterday"` raises before the opener is called
  - `record` on a 409 returns `{"duplicate": ...}`; the CLI exits 0
  - `record(..., dry_run=True)` calls no opener
  - `board(at="10/17/2026, 12:06:07 PM", match="Everton v Chelsea")` requests `/api/paddy/board-at?at=2026-10-17T11%3A06%3A07Z` and keeps only the Everton game from a two-game fixture
  - `take` writes the image bytes to `<dest>/<id>.jpg`
  - `upload` posts multipart with `source=telegram`
- [ ] **Step 2:** Run; FAIL. **Step 3:** Implement. **Step 4:** Hub suite PASS.
- [ ] **Step 5:** Commit `Hub: puntmore aw26 commands for HubAI`.

### Task 4: The daemon's photo loop

**Files:**
- Modify: `hub/puntmore_hub/config.py` (`hermes_aw26_profile` from `PUNTMORE_HERMES_AW26_PROFILE`, default `betting-client-manager`; `aw26_every_s` from `PUNTMORE_AW26_EVERY_S`, default 60; `aw26_run_budget_s` from `PUNTMORE_AW26_RUN_BUDGET_S`, default 900)
- Modify: `hub/.env.example`
- Create: `hub/puntmore_hub/aw26_loop.py`
- Modify: `hub/puntmore_hub/daemon.py` (start the loop on its own thread when `aw26.configured(cfg)`; docstring no longer says "Nothing here talks to Render")
- Test: `hub/tests/test_aw26_loop.py`, `hub/tests/test_daemon.py`

**Interfaces — Produces:**
- `read_once(cfg, *, opener=None, run=None) -> dict` — one pass: list photos; for the oldest `waiting` one only: `take` → `run(cmd, timeout)` → afterwards list photos again; if that photo is still `reading`, `fail(..., "Qwen did not finish")`. Returns `{"photo": id | None, "outcome": "recorded"|"duplicate"|"failed"|"not finished"|"idle"}`. The downloaded file is deleted after the run.
- `brief(photo_id: int, image: Path) -> str` — tells HubAI: follow the `puntmore-aw26` skill, the photo id, the attached image path, and that it must end with exactly one of `puntmore aw26 record` (with `photo_id`) or `puntmore aw26 fail <id> --reason`.
- `command(cfg, photo_id, image) -> list[str]` — `[hermes_bin, "-p", hermes_aw26_profile, "chat", "--oneshot", "-q", brief, "--image", str(image), "-s", "puntmore-aw26", "--run-budget", str(aw26_run_budget_s)]`. Before relying on these flags, check `hermes chat --help` on the box; adjust names if they differ and say so in the report.
- `run` default: `subprocess.run(cmd, timeout=aw26_run_budget_s + 60, stdout=<data_dir>/aw26/<id>.txt, stderr=STDOUT)`; a timeout kills it and counts as not finished.
- A missing hermes binary → the loop logs once per pass and takes nothing (a photo is never taken that nobody will read).
- The daemon writes the last pass to `daemon-status.json` under `"aw26"` (same shape as `"inbox"`).

- [ ] **Step 1: Failing tests:**
  - two waiting photos → only the oldest is taken; `run` called once with `--image` pointing at the downloaded file
  - after `run`, Render still shows the photo `reading` → a fail with reason "Qwen did not finish" is posted
  - after `run`, the photo is absent from `GET /photos` (done) → no fail posted, outcome `recorded`
  - `run` raising `subprocess.TimeoutExpired` → fail posted
  - hermes binary missing → no `take` request at all
  - `daemon.run` with Render not configured starts no aw26 thread (existing daemon test style)
- [ ] **Step 2:** FAIL. **Step 3:** Implement. One run at a time: the loop is one thread, sequential. **Step 4:** Hub suite PASS.
- [ ] **Step 5:** Commit `Hub: the daemon hands each waiting slip photo to HubAI`.

### Task 5: The runbook, and installing it

**Files:**
- Create: `hub/deploy/hermes-skills/puntmore-aw26/SKILL.md`
- Modify: `hub/puntmore_hub/update.py` (`skill_profiles` → skill to a list of skills directories; `sync_hermes_skills`)
- Test: `hub/tests/test_update.py`

**Runbook content (SKILL.md, plain and short; the model is small):**
1. **Reading a slip photo** (the daemon's brief points here):
   - Refuse, with `puntmore aw26 fail <id> --reason ...`: not a Paddy Power slip ("not a Paddy slip"); not an ACCUMULATOR — any DOUBLE, MULTIWAY, TREBLE or singles slip ("not an acca"); no complete receipt number ("no receipt number"); no leg readable ("unreadable").
   - Receipt: the full `B…-…-…` number under the shop name, every character. Part hidden → fail.
   - Placed time: the time beside the receipt, copied exactly as printed (`10/16/2026 4:17:52 PM`); the `puntmore` command converts it. Dates are month first.
   - Offer: `BONUS` at the top → `free`; "qualified for money back acca" → `refund`; "qualified for Acca Boost" → `boost`; none of these or unsure → `other`.
   - Stake: "Total Stake". Never read Total Odds, Max return, Bonus or "incl. bonus" — they are not sent (Paddy's sums are sometimes wrong; Render works its own).
   - Each leg: event "Home v Away" from the bold line, kickoff as printed, market words as the board writes them, odds as printed (fraction or decimal; copy exactly, e.g. `2/7` → 1.2857, `1.28` → 1.28), and kind (corners/cards/goals/saves/other), line and side. Worked mappings: "Total Corners - Under 12.5" → corners, 12.5, under; "Goalkeeper To Make 2 Or More Saves / Burnley Goalkeeper" → saves, line 1.5, over, the team in market words; "Total Goals - Over 1.5" → goals, 1.5, over.
   - Fairs: `puntmore aw26 board --at "<placed time>" --match "<Home v Away>"`. The same game, same kind, line and side (and team for a team market) → `fair_odds` = its `fair`, `fixture_id` = its `event_id`, kickoff from `commence_time`. Not found → `fair_assumed: true`, no fair. Team names on the slip can differ from the board's (the terminal printed "Cardiff Metropolitan"); match on the other team and the kickoff when one name differs; still unsure → fair assumed.
   - Never guess. A field not read is left out.
   - Record: write the JSON to a file, `puntmore aw26 record <file>` with `photo_id`. `{"duplicate": ...}` is a finished job; stop.
   - One worked example, with an invented receipt number (`B0000000000000-000000-0`), built from the 16 Oct Hackney boost slip's legs.
2. **A slip photo on Telegram** (chief of staff): `puntmore aw26 upload <the image file> --source telegram`, reply "Queued for AW 26", and do not read it yourself.

**update.py:**
- `skill_targets(cfg) -> dict[str, list[Path]]`: `puntmore-scraper-ops` → `[hermes_home/profiles/<scraper profile>/skills]`; `puntmore-aw26` → `[hermes_home/profiles/<aw26 profile>/skills, hermes_home/skills]` (the chief of staff's home is `~/.hermes` itself). A target whose parent (the profile directory, or `hermes_home`) does not exist is skipped, as today.
- `sync_hermes_skills` copies into each target; returns `"<skill> -> <dir>"` per change.

- [ ] **Step 1: Failing tests** in `test_update.py`: with `hermes_home/profiles/betting-client-manager/` and `hermes_home` present, `sync_hermes_skills` installs `puntmore-aw26` into both; a second call changes nothing; a missing profile is skipped; the scraper runbook still goes only to its profile.
- [ ] **Step 2:** FAIL. **Step 3:** Implement update.py and write SKILL.md. **Step 4:** PASS.
- [ ] **Step 5:** Commit `HubAI: the AW 26 reading runbook, installed for the reader and the chief of staff`.

### Task 6: Words — register, rules, docs

**Files:**
- Modify: `docs/decisions.md` (AW 26 entry), `CLAUDE.md` (AW 26 paragraph), `README.md`, `hub/AGENTS.md`, `hub/README.md`, `docs/dataflow.md`, `docs/architecture.md`, `PLAN.md` if it lists this item

- [ ] **Step 1:** Register, dated 11 Nov 2026, Callum's words: "you should ignore doubles they're not in the project scope" → AW 26 records Paddy accumulators only; "paddy make mistakes on payout calcs. always calc your own"; "free bets will always say BONUS at the top of the slip"; slip photos "can't be made public" (never in git). And the spec's 9 Nov edit: the Hub daemon gains an AW 26 loop that talks to Render (takes a waiting photo, starts HubAI on it).
- [ ] **Step 2:** `hub/AGENTS.md`: the daemon now talks to Render for AW 26; `puntmore aw26` commands; the runbook and where it is installed. `docs/dataflow.md`/`architecture.md`: the photo's path page → Render queue → Hub daemon → HubAI → record.
- [ ] **Step 3:** Hub and Render suites pass; `cd render/frontend && npm test` untouched. Commit `Docs: AW 26 Plan B in the register and the rules`.

### Task 7: Live proof (controller, after the branch is reviewed; no product code)

- [ ] `llm-mode main` must be loaded (vision). Check with `curl -s 127.0.0.1:8080/props | grep -c mmproj` or the model status; if not, ask Callum to switch.
- [ ] For each photo in `~/aw26-training-slips/` (01–10), run the reader by hand with a brief that says to use `puntmore aw26 record --dry-run` and to print the fail instead of posting it. Expected: 01 refused (not Paddy / not an acca), 07 refused (not an acca), 06 fail or full receipt, the rest a dry-run body whose legs, odds and stake match the slip.
- [ ] Report a table of slip → outcome → mismatches to Callum. Any outright failure (cannot read slips at all) stops here, per the spec's "Prove first".
