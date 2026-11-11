# api/ — rules for the Render service

`docs/decisions.md` is read first and wins over this file; the repo's
`CLAUDE.md` applies everywhere and wins over this file on any other conflict. This file is
what is specific to the FastAPI backend, with the reasons and incidents behind
each rule, because the rules are scars and the next agent "fixes" a rule it
does not understand.

## Two hosts (`app/site.py`)
The main domain serves every public tool; `me` serves Callum's personal tools
(`ME_TOOLS`), with its own login, because the session cookie is per host.
- **`me` exists only when `ME_HOST` names it.** Blank (the default) there is
  one site: `tool_site()` puts every tool on the main domain, login there
  accepts every account, and nothing points at `me`. Set it only after the
  host resolves to this service (its DNS record and Render custom domain),
  or the personal tools are sent to a DNS error. The frontend reads it at
  boot from the public `GET /api/auth/site` (site.js mirrors this file).
- **The retired shalgo host** is answered by `ShalgoRedirect` (asgi.py) before
  the app: a 301 (GET, HEAD) or 308 (anything else) to the same raw path on
  the main domain, the scheme from X-Forwarded-Proto when it is http or
  https. Its `/sw.js` is the exception: `shalgo_sw.js`, a worker that
  unregisters itself and reloads its windows on the main domain. A browser
  refuses a redirected worker script, so a redirect there would leave every
  app installed from shalgo on its old worker for good.

## Odds sources
**OddsPapi prices Paddy, 2-Up and the coupon tracker; odds-api.net prices
Hills** (the register; Callum, 11 Nov 2026: "update the planning documents
with the 2nd providor", "Oddspapi is a subscription"). The coupon tracker
prices on `OddsPapiCouponProvider` (`providers/oddspapi_coupon.py`,
`COUPON_ODDS_SOURCE=oddspapi`, the default), the Paddy tool on
`cards/oddspapi.py`, 2-Up on `twoup/feed.py`. Hills prices on
`cards/oddsapinet.py` when `ODDSAPINET_API_KEY` is set, and on OddsPapi
without it (`hills/vendor.py`; below, "Hills (`cards/oddsapinet.py`) —
odds-api.net"). Fair odds are de-vigged in-house from either vendor's raw
prices. bettingiscool is gone, and so are the acca boost package and the
TheOddsApi coupon fallback (5 Nov 2026): without an OddsPapi key the tracker
runs on the mock and says so at boot and on `/api/ready`.

No other odds vendor exists in the code. UK Odds API (ukoddsapi.com) was
tested on 11 Nov 2026 and not adopted. **Goalscorers is PARKED** (Callum,
5 Nov 2026): its vendor was removed, so `goalscorers/` stays in the tree
behind `ENABLE_GOALSCORERS=false` with no client, and comes back on OddsPapi
after the open work is done (PLAN.md, Parked). The acca boost returns as a
365 tab on OddsPapi the same way.

What must stay diagnosable, whichever provider: a tool that cannot price a
market **refuses** it with a named reason rather than approximating; one
warning per pricing pass names the market and the count, never one line per
leg and never none; `/api/ready` names the provider (`providers.odds_detail`)
with `degraded` and `cannot_price`, so a deploy that can price nothing never
looks healthy. These came out of Aug 2026, when a silent fallback priced a
154-fixture "Team to Score 2" sheet as 154 blanks.

**Reading a pick's market and team is ONE implementation** (`app/picks.py`) —
`team_fragment`, `side_of`, `is_team_specific` and the market regexes. It was
duplicated per provider and the copies diverged: the fallback had no word
boundary on the over-line, no Win-to-Nil branch and no side resolution at all,
so the same coupon priced correctly or crossed onto the wrong team depending on
which provider the deploy happened to be running. Same reason `pricing/legs.py`
is one implementation. A side is resolved from the pick's TEAM FRAGMENT (the
text before the first market keyword) scored with `team_similarity` — never a
raw character ratio over the whole pick, which is mostly market words and
drowns out a short club name ("Lyon" scored 0.33 against "Lyon Win and Over
2.5"), and never the whole-pick path, which bypasses the alias table that got
the fixture matched in the first place.

**A youth, reserve or women's side is a DIFFERENT TEAM, however the board
spells the marker** (`normalise._squad_markers`, Sept 2026). `team_similarity`
returns 0 when the two names' markers differ, and the markers are found by
PATTERN and folded to one canonical token in the normalised name ("U-21",
"U21s", "Under 21", "(U21)" → `u21`; "Women", "Ladies", "(W)", "Frauen" →
`women`; "Reserves", "Res", "II", "B" → their own). It was a token list that
recognised the bare "U21" and nothing else, so a senior leg was priced off
the under-21 fixture at 0.90 similarity. Add a spelling to the regexes when a
board prints a new one — never relax the zero: a first team against its
reserves is the one comparison where "nearly the same name" means the wrong
prices.

**A name match is not enough: the fixture must be on the printed DAY and in a
SENIOR competition** (`app/fixture_checks.py`, Sept 2026). U19 games kept
pricing after the marker fix because the board can print a youth fixture's
runners with NO marker ("England vs Spain" under "UEFA U19 Championship"), and
because `get_fair_odds` kept only the FIRST board row per fixture name — the
board is fetched day by day from today, so whichever same-named game kicked
off first won. Now every row per name is kept, and `_match_fixture` applies:
- **Competition** — a league naming an age group, youth, reserve or women's
  competition (Primavera, Juvenil, Premier League 2 and PDL included, which
  name no age) never matches a row with no marker of its own. Time-independent,
  so it is what catches a youth game on the SAME day as the senior one.
- **Day** — a candidate more than `KICKOFF_EXCLUDE_HOURS` (12) from the
  printed slot is refused. Deliberately wide: delays (sometimes unannounced)
  and early-morning fixtures with time-zone confusion must still match. Never
  narrow it to make the check "stricter" — that unmatches real fixtures.
  Compared by MINUTE OF THE WEEK, circularly, because the printed weekday is
  dated forward from the upload and a late upload dates it a week out.
- **Closest** — among rows sharing the winning name, the nearest to the
  printed slot wins. A tie-break, never an exclusion.
Refusals happen BEFORE the name match, so a refused fixture leaves the leg
unmatched, never passed to the next-best name. They are counted
(`refused_youth`, `refused_day`, subsets of `no_match`) in the pass line and
`last_pass`. The check needs `legs.printed_kickoff` — kept apart from
`legs.kickoff`, which pricing OVERWRITES with the matched fixture's time and so
cannot check the match without a wrong match vouching for itself. Legs read
before it existed have none and are matched on name and competition only.
It costs nothing: pruning other days' fixtures before the fuzzy scoring made a
150-leg pass against a 1,000-fixture board ~5x FASTER (8.6s → 1.6s).

**A BLANK TRACKER HAS FIVE DIFFERENT CAUSES and they used to look identical**
— a selection with no fair odds, whichever one it was. `get_fair_odds` now
tallies them and logs ONE line per pass (never one per leg — a sheet is 154 of
them), also served at `/api/ready` → `providers.odds_detail.last_pass`:

| count | means | fix |
|---|---|---|
| `no_board` | the fixture board came back empty | config / vendor |
| `no_match` | fixture not on the board | naming, or the wrong week |
| `no_odds` | matched, odds fetch returned nothing | vendor error on that event |
| `no_market` | matched, market not posted | Pinnacle's choice — not a bug |
| `no_side` | market priced, pick's team unclear | the pick text |

An EMPTY BOARD is the one that hid: every day-slice answers 200 with no rows,
so nothing raises, nothing is logged, and every leg matches against an empty
list. It now logs an error saying the requests SUCCEEDED and returned nothing,
because that rules out the key and the network in one line.

Both live behind their seams. Core logic never calls a vendor API directly.

## 365 tool (2-Up; the acca boost returns on OddsPapi; goalscorers parked)
- **The 2-up edge is proven** (Callum, 5 Nov 2026). Do not re-litigate it,
  model it from scratch, or gate the build on a literature review.
- **The rescue model is fine-tuned during testing against Bookie Bashing's
  2-up tracker.** By hand: BB's terms ban automated access
  (`docs/decisions.md`, "Scraping conduct"), so neither the engine nor
  the Hub's Scraper ever fetches the tracker. The comparison is a
  user-triggered step and its results are recorded in the tool's notes.
- 2-Up is a board: a one-way HTTP feed of current value bets
  (`docs/2up-engine-spec.md`). Sizing, placing and the books are the Bet
  Placer's (`docs/placer-spec.md`): nothing in `api/` places a bet or keeps an
  account.

### 2-Up (`app/twoup/`, Nov 2026)
Router `routers/twoup.py` at `/api/2up/*` (`feed`, `diagnostics`; both
read-only), behind the `accabonus` grant and its own flag `ENABLE_TWOUP`.
Settings are `twoup_*` in `config.py`, each with a default and a reason.
- `model.py` — pure: Pinnacle 1X2 de-vigged with `devigd_multi` (power),
  rescue rate from `rescue_table.json` by fair-P(win) bucket, edge =
  P(win) + P(two up, fails to win) − 1/bet365 price, and the filters (price
  band, edge floor, minimum lead). `Candidate` lives here; every row carries
  `priced_at`, when the prices behind it were fetched.
- `feed.py` — OddsPapi only, through the SHARED `OddsPapiClient` (budget,
  spacing, quota guard, tiered cache unchanged). Both books via
  `resolve_book`. The default competition list chunks into the same Pinnacle
  requests the coupon tracker makes, so bet365 is the only new spend. One log
  line per pass. Also a `ValueSelection` source (`source="2up"`,
  `bookmaker="bet365"`, `edge` = EV per pound there, `basis="2up"`).
- **A served board, refreshed by a job, never by a request.** The scheduler
  job `twoup_refresh` (`scheduler.refresh_twoup`, every `twoup_feed_ttl`
  seconds, first run at start, registered only when `ENABLE_TWOUP` is on and
  there is an OddsPapi client) calls `TwoUpFeed.refresh()`, which prices a
  board and swaps it in whole under a lock. `served()` hands out
  `(generated_at, rows)` of that board and never fetches; `GET /api/2up/feed`,
  and the `2up` source of `/api/value`, read only `served()`. A row still
  drops out at `twoup_min_lead_minutes` before kick-off while its board is
  served.
- **Last good board on failure.** A pass with errors that priced nothing
  (every request failed, reference data down, no configured tournament
  resolved, or the pass itself raised — `refresh` catches it and records
  `pass failed: ...`) leaves the served board as it was, with its old
  `priced_at` and `generated_at`, and sets `status.stale: true`; an honest
  empty pass (no errors) replaces the board. The next good pass replaces it
  and clears `stale`. Before the first pass the list is empty and
  `status.not_yet_refreshed` is true. A pass where one chunk's request failed
  but another priced replaces the board and names each failed request in
  `cannot_price` (`<book> chunk [ids]: <error>`), so it is degraded; the failed
  chunk's rows are not carried forward.
- **The `2up` value source says when it is not fresh.** Its rows' `seen_at`
  is their `priced_at`, never now; killed, stale, not yet refreshed, cannot
  price (no board since boot: every pass failed or raised, named by the first
  `cannot_price` entry) and partly priced (a board with a failed chunk, every
  gap named, beside its rows) are named in `/api/value`'s `errors["2up"]`
  (stale beside the last good rows).
- `status()` (`/api/ready` → `providers.twoup_detail`, the feed's `status`,
  and `diagnostics.feed`) reports `provider`, `degraded`, `cannot_price`,
  `stale`, `served_at`, `not_yet_refreshed` and `last_pass`; a deploy that
  cannot price 2-Up never looks healthy. `/api/2up/feed` adds `killed`.

Invariants — do not relax:
- **Kill switch** (global `TWOUP_KILL_SWITCH`, read per request): the feed
  returns no rows with `status.killed: true`, and the `2up` source of
  `/api/value` returns none either (and names `killed` in its `errors`), so
  the two never disagree. It is the only kill switch on Render.
- **One feed, no per-reader view.** Every reader gets the same rows; a row's
  identity is `event_id` + `side`. Nothing comes back from a reader.
- **No ledger.** The `twoup_*`, `tips`, `betfinder_sources` and `hub_*`
  tables are dead and left on the disk, not dropped; the ORM classes are gone.
- The rescue table is tuned BY HAND (`tools/twoup_backtest.py` over a CSV
  you exported from Bookie Bashing's tracker; `--merge` writes a new table).
  Nothing here or on the Hub ever fetches the tracker.

## Finder (`app/finder/`, Nov 2026)
Callum's tips, on `me` (the main domain while `ME_HOST` is blank; `docs/finder-spec.md`; the register's "Tips on Finder are
Callum's only"). The tips are judged on the Hub by HubAI; Render only keeps,
serves and expires what HubAI decided. It never
loads a page, never prices a tip (no OddsPapi request) and never places a bet.
- **One table, `finder_rows`** (`models.FinderRow`, `app/finder/rows.py`),
  keyed on the Hub's `tip_id`; `odds` and `fair` are the prices the TIP
  quotes, `edge = odds / fair - 1` (null with no fair). Kept 30 days from
  `created_at` (`finder_sweep`, daily).
- **The status machine is the spec's, and nothing else**: `asked → offered |
  rejected | expired`, `offered → placed | gone | rejected | expired`;
  `placed`, `gone`, `rejected`, `expired` are final. `offered → rejected` is
  HubAI withdrawing a row; a `placed` row cannot be withdrawn. A re-post
  against a final row is refused (409 at the status route, `refused` in the
  rows answer), and so is a move the machine lacks (`offered → asked`). An
  `offered` row has `kickoff`, `odds` and `fair` (the schema 422s one
  without); an `asked` or `rejected` row may lack all three (HubAI asks when
  it cannot place a tip on a day and time). `expires_at` is the earlier of
  kick-off and the tip's own `expiry`, with neither `seen_at` + 48 h;
  `finder_expire` sets `expired` every minute, on `asked` rows too, and the
  rows route runs it first, so an answer after kick-off changes nothing. The
  rows answer carries each posted row's STORED status (`rows`), and the Hub
  records that, never what HubAI sent.
- **One bet per (event_id or event, market, line, selection)**, compared
  case- and space-folded: a second row while one is `asked`, `offered` or
  `placed` is stored `rejected`, reason `duplicate of <tip_id>` (a placed bet
  is the one most worth not repeating). `event_id` is the canonical key
  HubAI fills (`football:<YYYY-MM-DD>:<home>-v-<away>`,
  `horse:<YYYY-MM-DD>:<course>:<HH:MM>`; `hub/README.md`, "Finder"), the event's words the
  fallback. `tip_id` alone (a hash of the tip's words and day) cannot stop a
  reworded tip being placed twice.
- **Two keys, never a session for writes.** `POST /finder/rows` (HubAI) and
  `POST /finder/rows/{tip_id}/status` (the Bet Placer: `placed | gone`) take the
  `ingest` grant (`require_ingest_key`). `GET /finder/feed` (offered, soonest
  first) and `GET /finder/board` (every status, `?days=3`) take the `finder`
  grant on the route itself; the router has no router-level grant.
- **A private value source.** `GET /api/value/finder` serves the offered rows
  in the `ValueSelection` shape to the `finder` grant only: Finder is never in
  the combined `GET /api/value` ("*" included) and the `value` grant does not
  reach it (`Source.private`, `value/service.py`).
- `ENABLE_FINDER` (default on) switches the routes, the two jobs and the
  `finder` tool off together.

## Hard rules (do not violate)
- The provider seams MUST stay behind their interfaces (see Provider guide
  below). Never call external APIs directly from core logic.
- **Coupon fair odds are computed IN-HOUSE by de-vigging raw Pinnacle prices**
  (power method, `odds/utils.py`) — enforced in every provider since July 2026.
  Any vendor-supplied devigged field is for cross-check tests ONLY; the pricing
  path must not depend on one, so providers stay swappable.
- **Win-and-Over markets have NO direct Pinnacle price.** They MUST be derived
  via the calibrated score-grid model (SPEC §8, `pricing/grid.py`). NEVER price
  them as moneyline × over — result and total goals are correlated and naive
  multiplication overstates fair odds. The live provider derives them off the
  grid and refuses to price when the fit is too poor or under-constrained;
  keep that refusal, an unpriced leg beats a confidently wrong one.
- Derived-market value thresholds come from MEASURED backtest calibration
  (SPEC §8.4), not assumed. Until measured, use the provisional 8% threshold and
  display the uncertainty band.
- **OCR TRANSCRIBES; the app INTERPRETS.** Coupons are a fixed grid, so nothing
  about their structure is inferred by the model (July 2026):
  - It reads EVERY market block on the sheet into `prices`, keyed by the block
    heading as printed. It is never told to find one block and ignore the rest —
    picking among six look-alikes is a guess that fails invisibly. The app
    matches the heading (`normalise_board_label`) and selects (`_pair_for`).
  - Each block's two prices come back as a positional `["left", "right"]` pair.
    The model never sees the words home or away: **left = home, right = away**,
    matching the fixture's own order, assigned in `claude_ocr._sides_from`.
    Going by the printed "H"/"A" headers is what crossed prices onto the wrong
    team. Never reintroduce a labelled pair or a "pick this block" instruction.
    A response that *does* label the pair is parsed but flagged
    (`ExtractedRow.sides_positional`) and **refused on a two-sided coupon** —
    that pair is the model's judgement about sides, which is the one question
    it is never asked. On a two-sided coupon `row.market` is ignored outright:
    it is one value for a row that yields two legs, so using it filed the away
    price under the home team's selection.
- **THE SHEET IS A RECTANGLE — a coupon is a solved puzzle** (`coupon_grid.py`,
  Aug 2026). R fixtures × B market blocks, the same B on every row, so the cell
  count is knowable whether or not every cell can be READ. Three distinct facts
  that were previously one silent `(None, None)`:
  - **hole** — block reported, both prices illegible. Kept in place. An honest
    coupon and a poor photo; the grid is still solved.
  - **absent** — the row didn't carry a block the sheet has. The read losing
    structure. This is the number that means re-scan.
  - **malformed** — a "pair" that wasn't two items.
  Do NOT go back to dropping unreadable blocks: collapsing hole and absent is
  what made a bad photo indistinguishable from a coupon with fewer markets on
  it. One line per match is enforced too (a repeat is the read losing its
  place, never a coupon printing a fixture twice).
  Heading identity is `coupon_grid.headings_match`: **digits exactly, letters
  fuzzily**. Never a plain similarity score — the six blocks differ by one
  digit and `difflib` rates "TEAM TO SCORE 1…" vs "…2…" at 0.97, so any
  threshold loose enough to survive a typo will also file a pair under the
  neighbouring market.
  The verdict is stored per coupon (`coupons.scan_audit`) and surfaced as ONE
  sheet-level line in the tracker. It is not a per-row badge and must not
  become one — see the auto-confirm rule below.
- **One photo of a sheet fills every market on it.** The uploaded type is just
  the one it opens on; `upload.py::_build_sibling_coupons` creates a coupon per
  block found, each superseding its own previous issue. They SHARE the stored
  image, so `coupon_images.delete_coupon_image` removes the file only with the
  LAST coupon referencing it — both the supersede path and the scheduled
  cleanup go through it.
- **The upload form offers SHEETS, not markets** (`coupon_types.UPLOAD_SHEETS`,
  Sept 2026): "Win And coupon" (WO15, WO25, WTN) and "Team To Score" (TTS1-3),
  then the single-market sheets (GGB, GGND, HILLS, FREDB) as before. A sheet
  upload opens as the FIRST of its types whose block actually read — never an
  empty coupon of an unread one — and the rest are its siblings. None read is a
  FAILED upload with the reason in `scan_audit` (usually the other sheet
  photographed). Posting a single type code still works for old clients.
- **Coupon OCR is not blocking-reviewed** (phone uploads). Rows auto-save and
  price immediately with inline editing (SPEC §7). The per-row anomaly checks
  and ⚠ badges were REMOVED (July 2026, Callum's call — the rows are checked by
  hand anyway); do not reintroduce them. Re-scan preserves human-edited
  rows — never overwrite a correction with a machine re-read.
- **EVERY CACHE MUST BE BOUNDED** (`ttl_cache.py`, Aug 2026). The caches here
  were plain dicts keyed by fixture id with a timestamp beside each value: the
  timestamp decided whether a value could be REUSED and never removed the
  entry, so fed by the 10-minute refresh loop they accumulated every fixture the
  app had ever priced (~110 KB odds + ~14 KB grid each, measured). That is what
  ran the Render instance out of memory. Use `TTLCache` — it sweeps expired
  entries ON WRITE (a fixture stops being queried once it kicks off, so
  read-triggered eviction never fires) and has a hard ceiling for bursts. Never
  reintroduce a bare dict cache, and never "fall back to the stale value" on a
  fetch failure — holding the last good copy for ever is the leak.
  The same rule covers the request path: uploads are capped
  (`MAX_UPLOAD_MB`, the body is held in memory) and images above
  `images.MAX_DECODE_PIXELS` are stored un-rotated rather than decoded, since
  rotating holds two full-size copies.
- **AN UPLOAD IS CAPPED AT THE VISION API'S OWN RESOLUTION** (`images.py`,
  Aug 2026). `prepare_for_ocr` caps the long edge at `MAX_LONG_EDGE = 2576` —
  which is **Claude's high-resolution tier limit**, the size the API downscales
  everything to before the model sees it. So this is NOT a quality trade-off:
  past that point the pixels are discarded upstream either way. Sending them
  cost ~503 MB of peak RSS on a 48 MP photo (measured) on a 512 MB instance —
  one upload, one restart — and the API would have **rejected** the request
  anyway, since it refuses any image over 10 MB base64-encoded and a 25 MB JPEG
  inflates to ~34 MB. Now ~140 MB peak and a ~1 MB payload.
  Two details are load-bearing and easy to undo by accident:
  - `draft()` must be passed an **aspect-matched** target, never a square box.
    It picks its scale from `min(w // want_w, h // want_h)`, so `(2576, 2576)`
    against a 4:3 photo scores 1 on the short edge and declines to reduce at
    all — the exact full-size decode it exists to avoid.
  - **Shrink before rotating.** `exif_transpose` allocates a second copy;
    doing it second makes that copy the small one.
  Never LOWER `MAX_LONG_EDGE` to save more memory — below the API's cap it
  becomes a real loss of detail, and small print is what is being read here.
  What is stored is what is sent, so a re-scan costs the same nothing.
- **THE PHOTO IS STRAIGHTENED BEFORE IT IS READ** (`images.measure_skew`,
  Sept 2026). A hand-held photo is never square, and on a 78-row grid that is
  not cosmetic: at 2576 px the row pitch is ~30 px, and a tilt of 0.7° drifts
  the far edge of the page by ~24 px — most of a row. The price at the right
  of row 55 then sits, IN THE PHOTO, level with the fixture name of row 54,
  and a faithful transcription of that picture is a column of prices one row
  out. The tilt is measured off the text lines themselves (a projection
  profile, pure Pillow, ~300 ms) and rotated out in `prepare_for_ocr`, so the
  stored image is the level one. It corrects rotation only, never
  perspective; a sheet photographed at a steep angle is still a bad photo.
- **EVERY PRICE ON A PRICED GRID IS READ TWICE, AND KEPT ONLY WHERE THE READS
  AGREE** (`providers/verified_ocr.py`, Sept 2026). The rectangle check cannot
  see a column of prices one row out: every row still has every block and
  every price is a real price — only the association slipped, which is a
  confident wrong price on a real bet, and it reached the tracker more than
  once after the transcribe-don't-interpret fixes. So `VerifiedOCRProvider`
  wraps every REAL provider (never the mock): the sheet is read whole, then
  again as five overlapping horizontal strips (`images.strips`, a third of
  the page each, so most rows are in two), and each price SLOT is voted — a
  value survives only if `READS_TO_AGREE` (2) separate reads report it. A
  price one read saw alone, or reads disagree on, is left unpriced and
  counted as `disputed` in the scan audit, which fails `clean` and names the
  count. Which read was right is exactly the thing that cannot be known.
  - It is NOT a dial. A switch to turn the check off is a switch that ends up
    off, and the failure it catches is invisible on screen. Cost is about
    2.5 full reads per upload — a few pounds a month.
  - A strip read that fails FAILS THE UPLOAD (`OCRUnverified`), it does not
    fall back to the single read. A coupon that looks verified and is not is
    the exact thing this exists to prevent.
  - Ladder coupons (no per-fixture prices) and bundle sheets (not a grid) are
    read once and marked `verified=False`; nothing on them is cross-checked.
  - The whole-page read owns the fixture LIST and its ORDER, the headings, the
    title, the period and the ladder. Strips are told the headings in print
    order (`OcrHint.headings`) because the header is usually above the cut,
    and are told to leave out any row the cut slices through.
- The coupon board reads through `Settings.coupon_odds_cache_ttl` (600 s, the
  refresh cadence) on the shared OddsPapi client's budget.
- The refresh loop runs every 10 minutes; it recomputes value locally and reads
  through the odds cache, so it never increases API call frequency. Never make
  it bypass the cache.
- The coupon cleanup deletes BOTH database rows AND stored image files. Never
  one without the other. The same holds when an upload SUPERSEDES the previous
  issue of its (type, period) — `upload.py::_supersede_previous`. That runs only
  after the new coupon has read rows: a failed or empty scan must never delete a
  working coupon. Two issues of one type still coexist (GG midweek + weekend);
  the key is type AND period, never type alone.
- **A file no row points at goes at 04:00 London** (`app/orphan_files.py`,
  Callum, 10 Nov 2026): an upload cut off while storing leaves one. The sweep
  matches the folder's files to `coupons`, `aw_slips` and `aw_photos` by name,
  never touches a row, and leaves a file younger than an hour. A new table
  that stores a path in the upload folder MUST be added to its list, or the
  sweep deletes that table's files.
- **The sheet's header must not contradict the selected type**
  (`coupon_types.title_mismatch`, Aug 2026). The upload form asks which coupon
  was photographed and nothing checked the answer — and GG Bonus / GG No Draw
  print for the same dates over near-identical fixture lists, so the header is
  all that separates them. A wrong-type upload looks completely normal (real
  fixtures, real prices) while being valued against the other coupon's ladder.
  Checked BEFORE legs are built, on upload AND re-scan; a mismatch fails the
  coupon rather than saving it. It is a VETO, not a classifier — an unreadable
  or generic header ("GOALS GALORE" is on both sheets) passes, because blocking
  a real upload is worse than missing a mismatch.
- **A ladder belongs to ONE coupon — never borrow another's.** GG Bonus and GG
  No Draw are both Betfred, print for the same dates over near-identical fixture
  lists, and carry COMPLETELY different boards. Read off the Mon 10 – Thu 13 Aug
  2026 issues: GGB is trebles-and-up (9/2 at 3 … 300/1 at 10); GGND is
  **doubles**-and-up (11/2 for "BOTH correct" … **8000/1 at 10**, twenty-six
  times GGB's). No Draw is the harder market and every rung says so.
  GGND defaulted to GGB's table until Aug 2026 purely because nobody had read
  GGND's — an assumption wearing a fallback's clothes. **That crossed ladder is
  what produced the "+3300.7% 10-fold"**: GGND's 8000/1 rung against a fair
  price of ~235. It was not a misread digit. Only record a ladder you have READ
  off the issue it belongs to, and note the boards are now far enough apart that
  `sanitise_ladder` catches one landing on the other.
- **A scanned ladder is CHECKED before it is trusted**
  (`coupon_types.sanitise_ladder`, Aug 2026). The coupon's own reading still
  wins — ladders change between issues — but a rung that pays less than a
  shorter fold, or sits more than `LADDER_MAX_DRIFT` from the type's printed
  board, is a misread and is replaced by the board's. The ladder is what a
  combination is BET on, so one fumbled digit doesn't nudge a price, it invents
  value: a "300/1" rung read as ~7920/1 turned a +34% 8-fold into a "+3300.7%
  10-fold". Sanitised on the way in (upload) AND on the way out
  (`Coupon.ladder`), because a coupon already stored with a bad rung will not
  re-scan itself. Rejected rungs go into `scan_audit`.
- **FRED BUNDLES IS A SHEET OF BETS, NOT A LIST OF FIXTURES** (Aug 2026). Every
  other type is one market over a fixture list and the tracker picks the good
  rows out of it. Bundles inverts that: Betfred has already built the accas, so
  the sheet carries several complete bets, each a group of straight-win
  selections with ONE printed price for the whole group.
  - **Value belongs to the GROUP** (`coupon_types.bundles_on`). The sheet prices
    the bundle, never the selection, so `prices_each_selection=False` and a
    bundle leg's `odds` is NULL — any per-row number would be invented and a
    per-row edge is meaningless. The figure is the printed acca price against
    the product of its legs' fair odds, via the same `combination_value` a
    ladder rung uses. There is nothing to select, so EVERY bundle is listed
    whether or not it holds value.
  - **Any unpriced leg ⇒ the bundle has NO value**, never one computed off the
    legs that did price. Valuing a four-fold as a three-fold overstates it every
    time.
  - **It is NOT a rectangle**, so `solve_grid` must never run on it —
    `upload.audit_bundles` does instead. The same fixture legitimately appears
    in several bundles, and the one-line-per-match rule that protects a grid
    read would delete real selections from every bundle after the first.
  - A selection with no bundle heading is DROPPED, not kept loose: on this sheet
    a selection outside a bundle is not a bet, it is an acca whose grouping the
    read lost.
  - It has no `title_conflicts` yet — nobody has read a real sheet's header, and
    a guessed veto would block genuine uploads. Add one only off an issue you
    have actually read, same rule as the ladders.
- **A ladder combination belongs to an ISSUE, not to a type.** The legs of a
  fold are struck on one sheet, so never price one across two uploads, and never
  show a single type-wide figure when a type has more than one live issue — that
  hid the live coupon's value behind the newest upload's until July 2026.


## Provider guide — how to swap OCR or odds backends

### OCR provider (`app/providers/base.py::OCRProvider`)
Interface: `extract(image_bytes: bytes, media_type: str) -> list[ExtractedRow]`

| Class | File | Trigger |
|---|---|---|
| `GeminiOCRProvider` | `providers/gemini_ocr.py` | `GOOGLE_API_KEY` set in `.env` |
| `ClaudeOCRProvider` | `providers/claude_ocr.py` | `ANTHROPIC_API_KEY` set (fallback) |
| `MockOCRProvider` | `providers/mock_ocr.py` | neither key present (dev/test) |

Selection logic is in `app/main.py` lifespan — `GOOGLE_API_KEY` takes priority.
Whichever real provider is chosen is wrapped in `VerifiedOCRProvider`
(`providers/verified_ocr.py`), which reads a priced grid a second time in
strips and keeps only the prices the reads agree on. Diagnostics and the boot
log name both: `VerifiedOCRProvider(ClaudeOCRProvider)`.
To add a new OCR provider:
1. Create `providers/<name>_ocr.py` implementing `OCRProvider`.
2. Add its API key to `config.py` (`Settings`) and `.env.example`.
3. Add an `elif settings.<new_key>:` branch in `main.py` lifespan before the
   `MockOCRProvider` fallback — wrapped in `VerifiedOCRProvider`, like the
   others. Its prompt must honour `OcrHint.strip` / `OcrHint.headings`
   (share `claude_ocr.build_user_prompt`, as Gemini does).

Current model IDs (verify before changing — do not assume from memory):
- Gemini: `gemini-2.0-flash` (set in `providers/gemini_ocr.py::MODEL`)
- Claude: `claude-haiku-4-5` (set in `providers/claude_ocr.py::MODEL`)

### Coupon odds provider (`app/providers/base.py::OddsProvider`)
Interface: `get_fair_odds(queries) -> list[OddsResult]`. The provider de-vigs
internally and returns FAIR odds plus the fixture it fuzzy-matched.

| Class | File | Trigger |
|---|---|---|
| `OddsPapiCouponProvider` | `providers/oddspapi_coupon.py` | `COUPON_ODDS_SOURCE=oddspapi` (default, the only live path) |
| `MockOddsProvider` | `providers/mock_odds.py` | no provider key (dev/test) |

Legs are priced through `pricing/legs.py::price_legs` — one implementation
shared by upload, the on-demand refresh and the scheduler. Don't reintroduce a
second pricing path; that duplication caused drift before.

De-vigging happens in `_fair_h2h_price` / `_fair_totals_price` via
`odds/utils.py`. **Both require the complete outcome set** — all of 1X2, or both
sides of a total — because the margin is a property of the market, not of one
selection. Where that isn't available they return `None`. Never "fix" that by
falling back to the raw price: a vigged price returned as fair odds reads as
value that isn't there and fires false Discord alerts. That was a live bug until
July 2026.

Refresh cadence is 10 min, reading through the cache — it never adds vendor
calls. To add a provider, implement `OddsProvider` and add an `elif` on
`settings.coupon_odds_source` in the `main.py` lifespan.

### Paddy (`app/cards/`) — OddsPapi
Both sides come from **OddsPapi** (Oct 2026, below): Paddy Power's prices and
Pinnacle's fair reference, joined on the same fixture id.
- **Regions describe the BOOKMAKER, not the league.** Paddy Power prices La Liga
  from `uk` perfectly well; the competition is chosen by sport key and the
  fixture list is free. Getting this backwards is what produced the original
  two-region design.
- **OddsPapi is the ONLY source for the Paddy tool** (Oct 2026): Paddy's prices
  and Pinnacle's fair side, joined on the same fixture id. The TheOddsApi Paddy
  path (`CardsApiClient`, its tiered window/cadence, `probe-books`, the daily
  call budget) is gone, and TheOddsApi with it (5 Nov 2026).
- **Every line PADDY POWER quotes is priced, off Pinnacle's fitted ladder**
  (`cards/laddered.py`) — or the team split where Pinnacle posts one line, or
  Pinnacle's own rung de-vigged where nothing fits. The matched-line path is
  gone. Only lines Paddy actually offers are priced: a fair price for a line
  nobody quotes cannot be bet. Users only ever see .5 lines; integer lines are
  model information, read with the push rule.
  The model (`pricing/counts`) reads Pinnacle's ladder and nothing else — it has
  no football knowledge and **cannot beat Pinnacle**; between their rungs it
  interpolates their opinion, beyond them it decays toward a shape assumption.
  Three guards keep that honest and none may be relaxed to make more rows
  appear: hard bounds (4.5–13.5 corners, 0.5–6.5 cards), `require_band=True` on
  every edge, and a per-count penalty beyond Pinnacle's quoted range. Fewer than
  2 two-sided Pinnacle rungs and the fixture is skipped — one rung pins nothing
  and the shape would come entirely from a stale league prior.
  The FLOOR is `min_edge`, not the model: `edge_vs(allow_negative=True)`
  returns below-fair edges (still measured against the band's far end, still
  penalised beyond the ladder) so the Refund/Boost tabs get legs down to 95%
  of fair — a 1.30 leg at ~94% still belongs in a +EV insured 5-fold. Until
  Oct 2026 edge_vs floored at fair and silently overruled the page's request.
  The Value tab and Discord alerts (`value_tier`) still need edge > 0.
  **Lines Paddy SHOPS don't carry are never listed** (`paddy_excluded_lines`,
  default `corners:11.5`, Callum Oct 2026 — the team bets in shops). Only
  Paddy's price is dropped; Pinnacle's rung at that line still feeds the fit.
  It is Paddy Power's rule only (`laddered._price_paddy_lines`, keyed on the
  book): Hills shows William Hill's 11.5 (Callum, 11 Nov 2026).
- **THE COUNTS MODEL IS MEASURED, NOT ASSUMED** (`pricing/counts`, Oct 2026).
  Tested against real outcomes — football-data.co.uk matches via the public
  `xgabora/club-football-match-data-2000-2025` dataset (git clone, no API
  cost): ~107k matches with corners and cards, ~157k with Bet365 and
  best-of-17 over/under 2.5 prices. Do not quietly undo any of these:
  - **Devig = POWER** (`config.default_devig`). Calibration slope of each
    method on 156,667 two-way totals (1.00 = calibrated): multiplicative 1.109,
    Shin 1.035, odds-ratio 1.030, **power 0.998** — same order in every margin
    band and on ~2% best prices. Multiplicative overstates every long side; it
    is out of the band too (`devig.BAND_METHODS` = power, shin, odds_ratio).
    Read with multiplicative, a 7%-margin power-vigged ladder fitted D 1.15
    as 0.83 — the devig moves the shape that much.
  - **Shape: NB for D >= 1, Conway-Maxwell-Poisson for D < 1**, meeting at
    Poisson (`dist.py`). NB matched corners' tails 2-5 from the mean to ~1% on
    53k held-out matches, as well as the clustered-corners (geometric-Poisson,
    Yip et al. JORS 2024) and Poisson-lognormal alternatives. Total goals are
    UNDER-dispersed given the market (COM nu ~1.03) and NB could not go below
    Poisson — the fit used `1 + exp(x)` — so an under-dispersed ladder was
    forced to D = 1 with tails too fat. Never restore the D >= 1 floor.
  - **Goals carry a 0-0 excess** (`GOALS.zero_excess` 0.09: 9% of P(0) moved
    from exactly one goal). With D prior 0.97 (was a 1.05 placeholder, which
    put O5.5 ~17% too likely) every total 0-6 lands within 0.5pp.
  - **Dispersion priors are per league and measured** (`config.py`, with
    provenance): corners default 1.14 (HKJC's 1.19 ran high), cards 1.10
    overall but 1.04-1.32 by league. Upper bounds — the data's mean is
    noisier than Pinnacle's.
  - **The beyond-ladder penalty is a PROBABILITY per count**, charged on the
    side bet: `edge -= extrapolation_prob_penalty * beyond / p(side)`. A tail
    error is a probability error, so it costs a 6.0 long shot ~6x what it
    costs the 1.20 opposite. The flat 2% of edge per count it replaced was
    never measured and charged favourites ~2.9% for ~0.3% of real risk.
    `0.002` is derived by `api/tools/extrapolation_sim.py` (production fit,
    simulated Pinnacle ladders): it covers the true price on 91% of
    extrapolated favourites and all long shots. Callum accepts a small risk
    on short legs; do not go back to a flat rate.
  - **Shadow mode keeps measuring** (`cards/shadow_store.py`): each fitted
    Pinnacle ladder, once per fixture+market per 6 h, has rungs hidden and
    predicted (`extrapolation_errors`), stored 30 days in `ladder_shadow`,
    reported at `/api/paddy/shadow` by market, distance and favourite vs
    long side. Re-derive the penalty from LIVE ladders once there are
    enough; no vendor cost.
- **Paddy Power's side comes from ODDSPAPI when `ODDSPAPI_API_KEY` is set**
  (`cards/oddspapi.py`, Sept 2026). TheOddsApi does NOT carry Paddy's
  corners/cards: a probe asked by region with no bookmaker filter, hours from
  a Nations League kickoff, returned LeoVegas alone for corners, nobody for
  cards, no Paddy. OddsPapi facts the adapter rests on: ids are SOFASCORE's
  (tournaments, teams); a market id is ONE market at ONE line, so a corner
  ladder is several ids and the line comes from the `/markets` CATALOGUE;
  fixtures carry participant ids, named via `/participants`; it bills per
  REQUEST and `odds-by-tournaments` returns every fixture in the tournaments
  named, so cost is refreshes x requests, not per fixture. Its window is
  therefore its own (`oddspapi_window_hours`, 7 days). Refreshes follow
  kickoffs (`oddspapi_ttl_tiers`, read off the cached response so choosing
  costs nothing): 2 min inside 45 min, 5 inside 2h, 15 inside 6h, else
  hourly — ~15k/month on 13 competitions x 2 books, against a 100k plan
  (Oct 2026) capped at 3,000/day. The catalogue and participants field names were NOT verifiable
  when written (host blocked) — parsed tolerantly, and
  `/api/cards/oddspapi-check` shows what the live catalogue calls each corner
  and card market. Read it before trusting a price. It emits TheOddsApi-shaped
  events, so the Pinnacle join, the pricing model and the diagnostics are
  unchanged. The TheOddsApi path behind it is gone.
- **Pinnacle's side comes from OddsPapi too** (`oddspapi_fair_book`, default
  `pinnacle`, Sept 2026). Checked live on the Nations League: Pinnacle posts a
  5-line corners ladder and a 5-line "Bookings" ladder (a CARD COUNT, 3.0–6.0 —
  not booking points). One request per book per chunk, joined on OddsPapi's
  FIXTURE ID (`merge_books`) — exact, where the old team-name match was not.
  An event that arrives without a Pinnacle book is left unpriced.
- **OddsPapi billing, per its docs ("Understanding API Requests & Quota",
  read Oct 2026):** 1 request per call to a billable endpoint whatever the
  response — size, item count and status (4xx/5xx included) make no
  difference. Billable: odds-by-tournaments, odds, fixtures, fixture,
  markets, tournaments, participants, players, scores, settlements, sports,
  bookmakers, languages — so the FIXTURE LIST IS NOT FREE here (unlike
  TheOddsApi). Free: `/historical-odds`. Unmetered and never blocked:
  `/account` (`request_limit`, `request_count`). A spent quota answers 429
  `REQUEST_LIMIT_EXCEEDED`; calls rejected before the endpoint are not
  counted. `OddsPapiClient` never retries that 429 (unlike the rate-limit
  one) and sends nothing billable until the free `/account` shows the period
  has reset, checked every 30 min.
- **Live check, 15 Oct 2026 (dev plan, 100k/month):** the catalogue names
  corners "Corners - Over Under Full Time" (`totals-corners`, lines
  0.5-19.5), cards "Bookings - Over Under Full Time" (`totals-bookings`) and
  goals "Over Under Full Time" (quarter lines included); all 13 tournament
  ids resolve to the intended competitions; team names resolve. The plan
  grants Pinnacle as **`pinnacle+30`** (delayed) — plain `pinnacle` is a
  403 RESTRICTED_ACCESS, so `resolve_book` maps the configured book to the
  slug the plan grants (free `/account`). **There is NO saves market in the
  catalogue at all** — Paddy's saves cannot come from OddsPapi. Failed odds
  requests are held (10 min; 6 h for a plan refusal) so the 2-minute sweep
  does not re-send billed failures.
- **OddsPapi rate-limits per endpoint** — back-to-back requests get a 429
  ("wait 0.80 seconds"), which on 28 Sept cost a whole chunk of five
  competitions. `OddsPapiClient` spaces requests by `MIN_REQUEST_GAP` and
  retries a 429 ONCE after the wait it names. Don't parallelise the sweep.
- **Match GOALS totals run through the same ladder model** (Oct 2026):
  Paddy's goals lines and Pinnacle's goals ladder both from OddsPapi
  (`totals_goals`), fitted by `pricing/counts` under `GOALS` (bounds 0.5-5.5,
  D prior 0.97 and a 0-0 excess, both measured — see above). Same three guards.
- **Paddy's board is KEPT** (`cards/history.py`, table `paddy_lines`, Callum,
  10 Nov 2026): AW 26 values a slip read late at the fair of the moment it
  was struck, and the live board has dropped the game by then. `sweep_cards`
  prices once down to `history.FLOOR` (−0.50, every line) and hands every
  priced row to `history.record`: a row only when a line's Paddy price or
  fair (to the penny) moves, before kick-off only, deleted 14 days after
  kick-off. The last price per line is held in memory, loaded from the table
  on first use (a restart writes nothing again) and pruned at each kick-off,
  so it is bounded by the live board. `GET /api/paddy/board-at?at=` serves
  the board as it stood then in the feed's shape. Paddy only: the Hills
  router does not mount it. Never allowed to break the sweep.
- **Goalkeeper SAVES are priced off Bookie Bashing, not Pinnacle**
  (`cards/saves.py`, Oct 2026). Pinnacle posts no saves ladder; Bookie Bashing
  publishes fair saves prices behind Callum's login, so they are read by
  the Scraper service (`scraper/`, below) and POSTed to
  `/api/paddy/saves-fair` (`SAVES_INGEST_KEY`, key-guarded like `/state`).
  The login stays in the Scraper's browser profile, never on Render. Fair
  prices sit in a bounded TTLCache and are joined to Paddy's saves line by
  team similarity and day. MATCH totals only. The reader
  (`scraper/parsers/bb_saves.py`, 26 Oct 2026) DRIVES Bookie Bashing's Bet
  Builder — "BB Player/Team Stats" → "BB xSaves", both teams combined as
  "Total Over Players", one typed line at a time, fair read off "Total Odds"
  — because BB has no saves page. Only Over is read: the builder's "Under N.5"
  means fewer than N, not the standard line. The builder lists a game only
  when some bookmaker has posted a saves line for it (the Premier League is
  often empty until a day or two before kickoff). Its docstring has the rest.
  **OPEN, Callum to decide** (`docs/decisions.md`, "Scraping conduct"): this
  BB target (`bb-saves`, driven after a person logs in) predates the rule that
  BB is never automated; keep it as the single named exception, or disable it.
- `cards/oddspapi.py` returns **RAW** prices. De-vigging lives in
  `cards/value.py`; handing it fair prices would de-vig them twice.
- `merge_books` (`cards/oddspapi.py`) joins the books into one event on the
  fixture id so `value.py` stays vendor-agnostic.
- The sweep's Discord dedupe (`scheduler._cards_notified`) is IN MEMORY and is
  swept each pass. Cards rows are computed, never stored, so there is no row to
  flag — but the map must keep dropping bets that fall off the sweep, or it is
  the same unbounded-dict leak `ttl_cache.py` exists to prevent.

### Hills (`cards/oddsapinet.py`) — odds-api.net
William Hill's corners, cards and goals lines with Pinnacle's as the fair side, from
**odds-api.net**, Puntmore's second odds provider (the register, 11 Nov 2026). Design:
`docs/superpowers/specs/2026-11-11-hills-odds-api-net-design.md`.
- **Which vendor** (`hills/vendor.py::build_hills_source`): `ODDSAPINET_API_KEY` set →
  `OddsApiNetHillsSource` on its own `OddsApiNetClient` and its own daily budget;
  unset → `OddsPapiPaddySource(book=WILLIAM_HILL)` on the SHARED OddsPapi client, as before;
  neither → no source, and `/api/ready` says so. No switch setting: removing the key puts
  Hills back on OddsPapi. A target picked on one vendor keeps that vendor's id, so its legs
  come back empty on the other: re-pick it after a switch.
- **Billing** (`oddsapinet_client.py`): base `https://api.odds-api.net/v1`, the key in the
  `X-API-Key` header only (never a URL, a log line or an error). The plan is 250,000 credits
  a month and 120 requests a minute; **every call is one credit, failures included**. One
  request in flight, at least 0.5 s apart. Every request counts against
  `oddsapinet_daily_credit_budget`, default **8000** (Callum, 11 Nov 2026: "there is 250k
  credits per month"; 8,000 × 31 = 248,000); a spent cap sends nothing (`BudgetSpent`).
  The count is per process, so a deploy starts it again. A 429 raises `RateLimited` and is
  never retried.
- **The listing**: one `/events` call (at most 3 pages), reused 600 s, a failed one held as
  long; window `oddsapinet_window_hours` (168; the vendor lists about six days). Competitions
  are `HILLS_LEAGUES`, the vendor's league names mapped to the Sofascore ids the rest of the
  code keys on. The Championship is listed under two names; a game listed twice is kept once
  (same teams, same kick-off). The vendor sends no round name, so a fixture's `round` is
  None. Every listed game is a fixture the target can be; only games whose listing names
  `williamhill` get a snapshot.
- **The per-event snapshot**: `GET /events/{id}/odds/snapshot` with
  `bookmakers=williamhill,pinnacle`, `price_fields=odds`, `types=total,team total,handicap`,
  `periods=full time`: both books in one call (~160 KB, ~1.4 s). Only the parsed lines are
  kept, never the response. A board is reused for the vendor's own
  `target_refresh_interval_seconds`, clamped to 120–3,600 s.
- **Freshness**: the vendor's target is 60 s inside six hours of kick-off and ~2,559–3,599 s
  further out. Seen 11 Nov 2026: William Hill 1.5–1.7 min old inside six hours, 64–108 min old
  6–31 hours out; Pinnacle always under five minutes. A match's `priced_at` (feed, through
  `priced_at_for`) is the older of the two books' stamps, counting only a book that carried
  lines; the source's `priced_at` is the oldest on the board. A far-out game makes the band
  read an hour or more: that is the truth. A William Hill side the vendor marks stale is still
  priced and counted in `last_sweep.stale_williamhill`.
- **The page's pass**: `get_events()` serves the boards it holds at once and, when any
  William Hill game is due (no board, or older than its reuse time, and not held), starts ONE
  background pass (never two): nearest kick-off first, at most 60 snapshots. Only the first
  request after a boot waits for it, up to 15 s. Games not fetched yet are
  `last_sweep.pending`, and `/hills/diagnostics` says "Still fetching" rather than "nothing
  there". `/hills/legs` fetches the target's own board in the request when it is missing or
  due (one credit). A failed snapshot is not asked again for 600 s; a 429 or a spent budget
  ends the pass, holds nothing and is named in `last_sweep.errors`. Nothing retries.
- **A warmish board** (Callum, 11 Nov 2026: "could we get a warmish board, with slow
  pre-loads"): the scheduler job `hills_warm` (`scheduler.warm_hills` →
  `OddsApiNetHillsSource.warm`) runs every `oddsapinet_warm_every_min` (10; 0 turns it off),
  for an odds-api.net source only (`main._warmable`). It fetches a game with no board, a game
  more than `oddsapinet_warm_near_hours` (6) from kick-off whose board is over
  `oddsapinet_warm_far_s` (7200) old, and one inside that whose board is over
  `oddsapinet_warm_near_s` (900) old; nearest first, at most 60, held games skipped. It sends
  nothing once fewer than `oddsapinet_warm_reserve` (2000) credits are left today and never
  spends into them, so the page keeps them. It shares the page pass's flag, so the two never
  run together (a warm finding a pass running skips). A failure is logged, never raised: no
  Discord alert. There is no warm at boot; the first comes 10 minutes after a deploy. About
  1,000–2,500 credits a day.
- **The mapping** (`parse_snapshot`): full time, `is_available`, odds > 1.0, William Hill and
  Pinnacle only. Totals read `"<over|under> <n>"`; odd/even and quarter lines are dropped.
  `market_key` is inconsistent and never read; metrics are compared lower-case.

  | `bet_type`, `metric`, `side` | Pipeline key | Books |
  |---|---|---|
  | `total`, `corners` | `alternate_totals_corners` | WH, Pinnacle |
  | `total`, `cards` | `alternate_totals_cards` | WH, Pinnacle |
  | `total`, `standard` | `totals_goals` | WH, Pinnacle |
  | `team total`, `corners`, `home` / `away` | `team_corners_home` / `_away` | WH, Pinnacle |
  | `team total`, `cards`, `home` / `away` | `team_cards_home` / `_away` | WH, Pinnacle |
  | `handicap`, `corners` / `cards` | `corners_handicap` / `cards_handicap` | Pinnacle only |

  A handicap becomes `{name: Home | Away, point: the HOME side's handicap}`: the home item's
  line as given, the away item's negated, as `pricing/counts/split.py` reads it.
- **Pinnacle-only fairs, no fallback.** Prices stay raw; `value.py` and `laddered.py` de-vig
  them. The vendor's de-vigged and `fair_odds` fields are never requested or read. An event
  with no Pinnacle lines carries William Hill alone and gets no rows (counted in
  `carried_paddy_prices`, not `carried_pinnacle_prices`); no other book stands in.
- **No saves on Hills** (Callum, 11 Nov 2026: "Remove saves on hills"): odds-api.net has no
  match saves total, only per-keeper player props, so there are no saves rows and no saves
  legs.
- **The 11.5 corners line shows on Hills** (Callum, 11 Nov 2026: "add the 11.5 corner
  lines"): `paddy_excluded_lines` is Paddy Power's shop rule only, so William Hill's 11.5 is
  priced on either vendor.
- **Ids**: `oan:<vendor event id>` wherever Hills stores or serves an event (the feed's
  `event_id`, `HillsTarget.fixture_id`, `HillsBet.fixture_id`), so an OddsPapi id and an
  odds-api.net id never meet. A BB Boost leg is one of William Hill's half lines: `market_id`
  `"<key>:<line>"` (`"alternate_totals_corners:9.5"`), `outcome_id` `"Over"` or `"Under"`.
- **Settling** (`hills/settle.py`, on `GET /hills/bets`): open bets with legs, kick-off two
  hours to two days ago, each fixture at most every 30 minutes; one `GET /events/{id}/results`
  (one credit). Nothing settles until the result is `available`, the match `finished` with
  `status_detail` "FT" (90 minutes: "AET", "Pen" or no detail waits for a hand result), and
  the records the leg needs are full time and `final`. A match leg adds both sides, a team
  leg reads its own; cards are the vendor's count (William Hill's red-card rule may differ;
  anyone can override by hand). `settled_by` is `"odds-api.net"` and the page shows "auto".
  Each vendor settles only its own bets, and only while Hills is on it.
- **Diagnostics**: `last_sweep` keys are `source` (`"odds-api.net"`), `book`, `fair_source`
  (`"odds-api.net:pinnacle"`), `window_hours`, `fixtures_in_window`, `called`,
  `carried_paddy_prices`, `carried_pinnacle_prices`, `pending`, `stale_williamhill`, `failed`,
  `errors`, `requests_today`, `request_budget`, `next_kickoff`; each event's `fair_source` is
  `"odds-api.net"`. A warm's pass writes `last_sweep` as a page pass does. `/api/ready` → `providers.hills_detail`:
  `{provider, degraded, requests_today}`, `degraded` with no source or when the last sweep
  failed and carried nothing. `/hills/oddspapi-check` answers that Hills is not on OddsPapi.


### Notification provider (`app/notifications/`)
`send(message)`. `RoutedNotifier` (`DISCORD_WEBHOOK_URL` + `DISCORD_ROUTES`) →
`NullNotificationProvider` (neither set). Value alerts go through
`notify_rows(notifier, tool, header, rows)`, which routes each row to its
tool's channel — or a higher edge band's (`paddy@0.08=URL`) — and falls back
to the default webhook; system messages always use the default. Tools:
`coupons`, `paddy`, `365` (the 365 tool's channel; only parked goalscorers
wrote to it, and 2-Up sends no value alerts yet). Value-bet alerts from the refresh loop
are deduped via `legs.notified_tier` (notify only on new/changed tier) — keep
that behaviour when touching the loop, or live coupons re-alert every 10 min.

### State snapshot (`app/routers/state.py`)
`GET /state` returns the whole app state (coupons+legs, cards
feed) for machine consumers. It is NOT session-guarded — it self-guards with
the static `STATE_API_KEY` (`X-API-Key` header, constant-time compare; blank
key = 503/disabled). It reads through the odds cache, so polling it never
increases API call frequency. Keep it read-only — never add mutating
operations behind the API key.

### API clients — machine keys (`app/apikeys.py`, `security.py`, Oct 2026)
ONE mechanism for machine callers. A key is sent as `Authorization: Bearer
<key>` (preferred) or `X-API-Key`, and resolves in `security.api_key_auth` to
a principal: an `ApiClient` row (looked up by the key's sha256 — the key itself
is never stored, logged or echoed) or one of the static env keys, which are
the grants `state` (`STATE_API_KEY`) and `ingest` (`SAVES_INGEST_KEY`) and keep
working exactly as before. API clients resolve through `user_permissions`, so
every `require_tool` guard admits a client holding that grant with no
per-router change; a client acts as `api:<name>` where a route records who did
something. A key, when sent, IS the identity — a bad key is 401 even beside a
session cookie. The key-only routes (/state, ingest) use `require_key_grant`.
```
python -m app.apikeys create <name> --tools paddy,value  # prints the key ONCE
python -m app.apikeys grant <name> --tools aw26,cards    # adds tools to an existing key; never replaces or prints it
python -m app.apikeys list                              # never shows keys
python -m app.apikeys revoke <name>                     # disables at once
```
Grants are the tool names plus `value`, `state`, `ingest` (or `*`); `paddy` =
`cards`, `365` = `accabonus+goalscorers`. **The Bet Placer's key** (register,
6 Nov 2026) carries `accabonus` (the 2-Up feed), `cards` (the Paddy feed),
`finder` (the Finder feed) and `ingest` (Finder's `placed | gone`); **a third
party's** carries `accabonus` only; HubAI's on the Hub carries `ingest`, and `aw26` and
`cards` for AW 26 (added with `grant`). `*`
includes `finder` as it includes every tool, so **a key or account issued to
anyone but Callum never carries `*`**, only named grants. A key still holding
a grant that no longer exists is granted nothing for it. In production run it in Render's
shell (`cd /app`), which has `DATABASE_URL`. Full API docs: `docs/api.md`.

**The API is a contract** (`docs/api.md`, Oct 2026): every route under `/api`
has a `response_model` and a per-tool tag (`tests/test_openapi.py` fails
otherwise); `/api/docs` and `/api/openapi.json` are served behind a session or
any key, never public. Changes are ADDITIVE ONLY — a breaking change goes to
`/api/v2/...`, unless the register decides otherwise. `GET /api/value` is a cross-tool view in one
`ValueSelection` shape, built from each tool's own feed builder (never a
second pricing path). Tool logic lives in services (`cards/feed.py`,
`coupons/service.py`, `pnl/service.py`,
`shop/service.py`); routers stay thin. The Python client is
`clients/python/puntmore`. Nothing in the API places a bet — read and
report — and no bookmaker-account automation is ever added here.

## Lessons kept from the earlier plan

- **The acca boost's eligibility (R8, June 2026). Do not redo this research.** Verified against
  bet365's published terms: tennis all ATP/WTA/Challenger, MMA any event; basketball only
  NBA/Euroleague/EuroBasket/FIBA/Olympics, American football NFL only, baseball MLB/NPB/KBO, ice
  hockey NHL/Worlds/Olympics. **Cricket and rugby are NOT eligible.** Soccer IS eligible but was
  deliberately excluded (the coupon tracker and Paddy cover it). Pre-season, exhibition and
  All-Star games are excluded. Re-verify only if bet365 changes the offer; it applies when the
  acca boost returns on OddsPapi.
- **State the direction of a known bias, not just its existence (R10).** A note once called the
  un-de-vigged coupon prices "slightly conservative". That was backwards: with no de-vig the fair
  odds are too low, so value was overstated and Discord fired on non-value legs until July 2026.
