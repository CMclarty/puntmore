You are taking over the AW 26 tool in the puntmore repo (CMclarty/puntmore, branch `main`). Pushing to `main` deploys to Render (puntmore.com); the health check is `/api/ready`. Read CLAUDE.md first: the AW 26 paragraph under "What this is", the UI rules and the hard rules. PLAN.md item 9 has the full brief and Callum's answers.

WHAT AW 26 IS
- The acca project's slip ledger: one page at `/aw26`, behind the `aw26` grant (`*` includes it). Built and deployed in commit 17d83ff (21 Oct 2026).
- Callum photographs the Paddy Power terminal, one photo per bet. OCR transcribes it, and the slip saves at once with inline edit.
- Targets: £1,000 EV per day and £100,000 total profit. The whole team is one pot.
- Daily Discord post at 08:47 London, to Callum only for now. "AW 26" is just the project's name; there is no end date.
- Offers:
  - Paddy refund: a 5-fold, every leg 1.20+. The refund is worth 0.75 × stake, with no cap.
  - Acca boost: +10% on a 4-fold, +20% on a 5-fold. `BOOST_TIERS` also gives +30% on a 6-fold, from the Paddy page's terms; Callum hasn't confirmed that.
  - Other: occasional bets, valued as stake × (odds / fair − 1).
- Fair prices come from Bookie Bashing's Group Tracker, scraped daily. A slip's EV is fixed only once EVERY leg has a fair price, and a later scrape never moves it. An edit re-prices, because an edit is a correction.

WHAT'S BUILT
- `api/app/aw26/offers.py`: the EV and settlement maths. It is pinned to the same worked cases as `accaInsurance.js` / `accaBoost.js`, so change both or neither.
- `api/app/aw26/legs.py`: reads market text and matches fair prices and fixtures to legs.
- `api/app/aw26/service.py`: pricing, settling, the summary, and the daily message (`run_daily`).
- `api/app/routers/aw26.py`: upload, list, edit, delete, the photo, and `/api/aw26/results-check`.
- `api/app/routers/aw26_ingest.py`: `POST /api/aw26/fairs`, guarded by `SAVES_INGEST_KEY`.
- `providers/slip_ocr.py` and `read_slip` on the Claude and Gemini providers. The photo is transcribed only, never interpreted.
- Tables `aw_slips`, `aw_legs` and `aw_fairs`.
- Scheduler job `aw26_daily` at 08:47 London (`api/app/scheduler.py`): settles open slips and posts to the `aw26` Discord route.
- `frontend/src/pages/Aw26Page.jsx`, plus the nav entry and the `aw26` grant.
- `local/bookiebashing_tracker.py`, run on Callum's PC. Commands: `login`, `dump`, `once`, `run`.
- Tests: `api/tests/test_aw26.py` and `test_aw26_offers.py`. At 17d83ff, 741 backend and 27 frontend tests passed.
- Until automatic results exist, leg results are set by hand on the page.

OPEN ITEMS (write each one only from real output; never guess)
1. Group Tracker reader. Callum runs `python local/bookiebashing_tracker.py dump --url <Group Tracker URL>`, which saves `bb-tracker-dump/page.html` and `page.png`. Write `parse_page` from those so it emits `{home, away, kickoff, market, selection, fair_odds}`, with market and selection in words as the tracker prints them; `kickoff` is optional. This blocks every slip's EV.
2. Automatic results. Open `/api/aw26/results-check?fixture=<fixture_id>` for a finished match (the leg's `fixture_id` is in `/api/aw26/slips`). If OddsPapi's `/scores` or `/settlements` carry corners, cards and goals, write a settlement source that sets leg results before `run_daily` settles the slips. Otherwise propose a web source.
3. Real terminal photo. Upload one through the page, then fix the `read_slip` prompt or parser from whatever it misreads.
4. Render settings. Add `aw26=<webhook>` to `DISCORD_ROUTES`, and make sure `SAVES_INGEST_KEY` is set.

AUTOMATING THE SCRAPE
It must run on Callum's PC, because Bookie Bashing's login lives in the browser profile there and the login never goes to the server. A cloud Claude Code container can't do it: its network policy blocked bookiebashing.net, puntmore.com and api.oddspapi.io, it can't reach his browser, and it's wiped after inactivity. Run this session on his PC (Claude Desktop, or `claude remote-control` in the puntmore folder), then:
1. `pip install playwright httpx`, `playwright install chromium`, then `python local/bookiebashing_tracker.py login` (log in, close the window).
2. Run `dump`, then write and test `parse_page` against the saved page.
3. Set `BB_TRACKER_URL` and `SAVES_INGEST_KEY` (the same value as on Render). Run `once` and check the slips price.
4. Schedule `once` daily in Windows Task Scheduler, before the 08:47 settle.

RULES
- Before every push: `cd api && .venv/bin/python -m pytest tests/ -q`, then `cd frontend && npx vitest run && npx vite build`. Create the venv per CLAUDE.md "Fresh clone" if it's missing.
- Build only what Callum asks. No unrequested UI, labels, colours, pages or recommendations.
- Never commit secrets. They live in `.env` and Render only (gitleaks hook: `git config core.hooksPath .githooks`).
- Callum wants concise replies with no filler.
