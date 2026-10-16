# Puntmore — project rules

Read `SPEC.md` for design detail and `PLAN.md` for current work. This file is
guardrails for the whole repo.

## What this is
A self-hosted betting-tools suite: three tools share one FastAPI backend and one
React frontend, behind a shared nginx layer.

> **Current status (July 2026):** only the **coupon tracker** is surfaced.
> AccaBonus and Cards/Corners are hidden — their routers are gated on
> `ENABLE_ACCABONUS` / `ENABLE_CARDS` (default False) and their nav tabs/routes
> are suppressed. Code, tests, and the seams are all kept; this is a visibility
> gate, not a removal. The disabled routers / coupon-only UI are intentional —
> do not "fix" them. The coupon tracker prices via **bettingiscool**
> (`COUPON_ODDS_SOURCE=bettingiscool`), de-vigs in-house, and derives
> Win-and-Over off the fitted score grid. OCR **auto-confirms**: rows save and
> price on upload, with inline edit and re-scan.

```
bettingtools/
├── api/              ← FastAPI backend; api/Dockerfile ALSO builds the frontend
│                       into this one image, and app/asgi.py serves both
├── frontend/         ← React app (Vite); built into the api image, not deployed alone
└── render.yaml       ← Render blueprint: ONE Docker web service, deploy on push to main
```

The three tools are routes in one React app (`/`, `/accabonus`, `/cards`), not
separate builds. The backend serves them all under namespaced routers.
(`/tennis` is the AccaBonus tool's legacy prefix — kept as redirect/API aliases
for stale PWA clients; don't add new code under it.)
**365** (Oct 2026) is the bet365 offers, one tool with a tab each at
`/365/<tab>`: `acca` (the AccaBonus page, `accabonus` grant) and `scorers`
(the goalscorer finder, `goalscorers` grant); 2-Up joins when its bet365 feed
exists. `/accabonus`, `/tennis` and `/goalscorers` redirect into it; the
grants, flags and API paths keep their old names.
The cards & corners tool is now **Paddy** (Oct 2026: corners, cards, goals and
saves, all against Paddy Power): page `/paddy`, API `/api/paddy/*`. `/cards`
is its legacy alias in the same way — page redirect, API mounted twice. The
permission, `ENABLE_CARDS` and the `app/cards/` package keep the old name on
purpose: renaming the permission revokes existing accounts' access.

## Odds sources
- **Coupon tracker** → `BettingIsCoolProvider` (`providers/bettingiscool.py`).
  Pinnacle-native and exposes team totals, which feed the derived score grid.
  Fair odds are de-vigged in-house from its raw prices.
- **AccaBonus + Cards** → the shared `TheOddsApiClient` (free tier). Unchanged.

`TheOddsApiProvider` (`providers/odds_api.py`) is the fallback and is **no
longer the live path**. It has no score grid of its own, so its compound-leg
pricing is still a naive product — if it is ever made live again, wire it to
`pricing.grid` first rather than shipping that path.

**The fallback is reached SILENTLY when `BETTINGISCOOL_API_KEY` is unset**, and
it serves h2h + totals only — no team totals, no grid. So TTS1/2/3, Win to Nil
and the GG family cannot be priced on it AT ALL, and a coupon of one of those
types opens with every selection blank. That is indistinguishable from a failed
scan by looking at it, which is how it went undiagnosed (Aug 2026: a
"Team to Score 2" sheet, 154 fixtures, 154 blanks). Three things keep it
diagnosable — do not quietly undo any of them:
- It **refuses** what it cannot price (`odds_api.unsupported_reason`) rather
  than approximating. Win to Nil is the one that matters: it contains "win", so
  it used to fall through and return the STRAIGHT WIN price — a confident wrong
  price on a much shorter bet.
- It logs ONE warning per pricing pass naming the market, the count and the
  missing env var — never one line per leg, and never none.
- `/api/ready` NAMES the provider (`providers.odds_detail`) with `degraded` and
  `cannot_price`. It used to report `"live"` for this fallback, which made a
  deploy that could price nothing look healthy. The diagnostics ring buffer's
  `boot` record carries the provider name too.

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
The fallback `TheOddsApiProvider` does NOT apply these checks (not the live
path) — port `_match_fixture` before it is ever made live again.

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

## Hard rules (do not violate)
- NEVER commit secrets. All credentials (bettingiscool key, Odds API key, OCR
  key, Discord webhook, auth users + session key) live in `.env` only.
  `.env` is git-ignored; only `.env.example` is committed.
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
- The Odds API free tier (AccaBonus/Cards) is 500 credits/month. The 24-hour
  cache (`Settings.odds_cache_ttl`, `app/odds/client.py`) keeps this safe — do
  not lower the default. A paid-tier upgrade is an `.env` change only.
- The refresh loop runs every 10 minutes; it recomputes value locally and reads
  through the odds cache, so it never increases API call frequency. Never make
  it bypass the cache.
- The coupon cleanup deletes BOTH database rows AND stored image files. Never
  one without the other. The same holds when an upload SUPERSEDES the previous
  issue of its (type, period) — `upload.py::_supersede_previous`. That runs only
  after the new coupon has read rows: a failed or empty scan must never delete a
  working coupon. Two issues of one type still coexist (GG midweek + weekend);
  the key is type AND period, never type alone.
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
| `BettingIsCoolProvider` | `providers/bettingiscool.py` | `COUPON_ODDS_SOURCE=bettingiscool` (default) |
| `TheOddsApiProvider` | `providers/odds_api.py` | `COUPON_ODDS_SOURCE=theoddsapi` (fallback) |
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

**NEVER send `bookmakers` AND `regions` together** (`odds/client.py`). Billing is
`markets × regions`, and naming books is the cheaper form — but the real damage
is filtering: bet365 is a `uk` book and Paddy Power a `uk` book, so a leftover
default `regions=eu` beside a named book can drop it from the response entirely.
AccaBonus skips any event missing bet365, so that produced a completely blank
feed with nothing logged. `fetch` and `fetch_event` both send one or the other,
and tests pin it on both.

### Odds data layer for value tools (`app/odds/client.py::TheOddsApiClient`)
AccaBonus + Cards read TheOddsApi through this one shared client (HTTP + cache).
`fetch(sport_key, markets, bookmakers=None) -> list[dict]`, cached in-memory
keyed by `(sport_key, markets, bookmakers)`.

- Free tier: 500 credits/month. Each `/v4/sports/{sport}/odds` call costs ~2
  credits. Cache TTL is `Settings.odds_cache_ttl` (default 86400 = 24 h). Do not
  lower the default: a 1-hour cache burns ~336 credits/day on a single sport.
- Off-season sport keys return 404 (free, no quota) and are cached empty — safe
  to keep all keys listed year-round.
- Fair odds come from de-vigging Pinnacle via the **power method**
  (`app/odds/utils.py::devigd` / `devigd_multi` — user decision, June 2026;
  don't revert to multiplicative). Pinnacle is the reference everywhere. The
  coupon tracker devigs the same way, over TheOddsApi prices.
- TheOddsApi returns the totals line as `"point"` (numeric) on each outcome.
  Do not confuse with `"description"` — that field does not exist in this API.

Consumers:
| Consumer | File | Source | Markets | Bookmakers |
|---|---|---|---|---|
| coupons | `providers/odds_api.py` | TheOddsApi | `h2h`, `totals` | Pinnacle (falls back to first listed) |
| arbs router | `routers/accabonus.py` + `accabonus/arbs.py` | TheOddsApi | `h2h` | bet365 vs Pinnacle |
| `CardsApiClient` | `cards/client.py` + `cards/value.py` | TheOddsApi | `alternate_totals` ⚠️ **broken, see below** | Pinnacle, Paddy Power |

⚠️ `CardsApiClient` asks the **bulk** endpoint for `alternate_totals`, which
that endpoint rejects (`INVALID_MARKET`) — so it has never returned a row. The
tool is gated off, so nothing is affected, but re-enabling it means rebuilding
against the per-event endpoint with the real corners/cards market keys. See
`docs/provider-verification.md` and PLAN item 7 before touching it.

`TheOddsApiProvider` satisfies the `OddsProvider` interface (used by the refresh
loop) and reads through the client on its own `COUPON_ODDS_CACHE_TTL`. AccaBonus
and cards consume the `TheOddsApiClient` directly via `app.state.arbs_client` /
`app.state.cards_client` because they query bespoke market/bookmaker combos.

### Cards & Corners (`app/cards/`) — TWO vendors, on purpose
Paddy Power's price comes from **TheOddsApi's per-event endpoint**
(`bookmakers=paddypower`, never a `regions` param beside it); Pinnacle's fair
reference comes from **bettingiscool**. That split is a COST decision, not a
preference: billing is `markets × regions` per event, Paddy Power is a `uk` book
and Pinnacle an `eu` one, so asking one vendor for both spans two regions.
- **Regions describe the BOOKMAKER, not the league.** Paddy Power prices La Liga
  from `uk` perfectly well; the competition is chosen by sport key and the
  fixture list is free. Getting this backwards is what produced the original
  two-region design.
- **The WINDOW and the CADENCE are separate dials, and widening one means
  slowing the other.** Cost per fixture is `sweeps × markets`. THREE tiers via
  the odds cache's per-read TTL: 48h–4h every 12h, 4h–1h every 30 min, then
  every 5 min inside the last hour — 43 credits per fixture, ~17k/month over
  the major UK/Euro leagues (84% of a 20k plan). Widening the window WITHOUT
  adding the coarse far tier would have been ~212 credits per fixture and
  ~83k/month, four times the plan. Never move `cards_window_hours` without
  recomputing that.
- **TWO budget guards, and they fail differently.** `cards_quota_reserve` stops
  the sweep when TheOddsApi's own `x-requests-remaining` header falls to it —
  authoritative, survives a restart, and already reflects the true per-call cost,
  which is still unmeasured (this file's Odds API section says
  `markets × regions`; `routers/accabonus.py` says naming bookmakers bills as
  one — a 2× difference). `cards_daily_call_budget` is local arithmetic that
  stops one bad day spending a month; keep it BELOW plan/30 (650 × 30 = 19,500
  against a 20k plan — 700 was 21,000, i.e. the guard permitted more than the
  budget it guarded). The reserve exists so a runaway sweep cannot take the
  coupon fallback and the goalscorer scans down with it.
- **Every line PADDY POWER quotes is priced, off Pinnacle's fitted ladder**
  (`cards/laddered.py`, `cards_use_ladder_model`). The matched-line path only
  compares lines BOTH books picked, and books rarely agree — so most of Paddy's
  board was invisible. Only lines Paddy actually offers are priced: a fair price
  for a line nobody quotes cannot be bet.
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
- **The sweep covers INTERNATIONALS too** (`cards_sport_keys`, Sept 2026):
  Nations League, Euro and European World Cup qualifiers. Without them an
  international window read as "no fixture within 48h". Club football mostly
  pauses in a window, so they replace club spend rather than add to it.
  Friendlies are deliberately left out. `list_events` answers an unknown key
  with a 404 that reads as "no fixtures", so the sweep checks its keys against
  the FREE `/v4/sports` list and names any it does not see (`inactive_keys`,
  out of season or misspelled), with `fixtures_listed` and `next_kickoff`, so
  "a quiet spell" and "a key list that misses the games" look different.
- **Paddy Power's side comes from ODDSPAPI when `ODDSPAPI_API_KEY` is set**
  (`cards/oddspapi.py`, Sept 2026). TheOddsApi does NOT carry Paddy's
  corners/cards: a probe asked by region with no bookmaker filter, hours from
  a Nations League kickoff, returned LeoVegas alone for corners, nobody for
  cards, no Paddy. OddsPapi facts the adapter rests on: ids are SOFASCORE's
  (tournaments, teams); a market id is ONE market at ONE line, so a corner
  ladder is several ids and the line comes from the `/markets` CATALOGUE;
  fixtures carry participant ids, named via `/participants`; it bills per
  REQUEST and `odds-by-tournaments` returns every fixture in the tournaments
  named, so cost is refreshes x requests, not per fixture. Refreshes follow
  kickoffs (`oddspapi_ttl_tiers`, read off the cached response so choosing
  costs nothing): 2 min inside 45 min, 5 inside 2h, 15 inside 6h, else
  hourly — ~15k/month on 13 competitions x 2 books, against a 100k plan
  (Oct 2026) capped at 3,000/day. The catalogue and participants field names were NOT verifiable
  when written (host blocked) — parsed tolerantly, and
  `/api/cards/oddspapi-check` shows what the live catalogue calls each corner
  and card market. Read it before trusting a price. It emits TheOddsApi-shaped
  events, so the Pinnacle join, the pricing model and the diagnostics are
  unchanged; unset the key and TheOddsApi's path is back.
- **Pinnacle's side comes from OddsPapi too** (`oddspapi_fair_book`, default
  `pinnacle`, Sept 2026). Checked live on the Nations League: Pinnacle posts a
  5-line corners ladder and a 5-line "Bookings" ladder (a CARD COUNT, 3.0–6.0 —
  not booking points). One request per book per chunk, joined on OddsPapi's
  FIXTURE ID (`merge_books`) — exact, where bettingiscool needed a team-name
  match. `merge_pinnacle` only goes to bettingiscool for events that arrive
  without a Pinnacle book; blank the setting to use bettingiscool for all.
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
  D prior 1.05 — a placeholder, like the cards one). Same three guards.
- **Goalkeeper SAVES are priced off Bookie Bashing, not Pinnacle**
  (`cards/saves.py`, Oct 2026). Pinnacle posts no saves ladder; Bookie Bashing
  publishes fair saves prices behind Callum's login, so they are read by
  `local/bookiebashing_saves.py` ON HIS PC and POSTed to
  `/api/paddy/saves-fair` (`SAVES_INGEST_KEY`, key-guarded like `/state`).
  The login never reaches the server. Fair prices sit in a bounded TTLCache
  and are joined to Paddy's OddsPapi saves line by team similarity and day.
  MATCH totals only; the page reader (`parse_page`) is unwritten until a
  `dump` of the real page has been read — do not guess its layout.
- **Acca insurance** (`frontend/src/accaInsurance.js`, Oct 2026): the Acca Min
  tab value a 5-fold (every leg 1.20+) with the stake
  refunded as a bonus (`refund_value` 0.75, `refund_cap`) if exactly one leg
  loses. Client-side maths, pinned by `accaInsurance.test.js` to the offer's
  own worked cases — keep them passing. The builder takes ONE leg per match:
  the product rule assumes independent legs.
- **Acca views rank by Kelly growth** (Oct 2026): every offer's view scores a
  leg by the expected log-growth of an acca with it at that acca's OWN
  optimal stake (`accaInsurance.js::growthOf`) — bankroll-free, no risk dial;
  short legs that keep an acca paying back rise without an odds cap. The
  page is THREE TABS — Value (singles at fair or better), Refund (the
  insurance offer), Boost — each ONE list, best down (Callum, Oct 2026:
  "this is a tool — keep it clean"; open, tap one of three, bet in seconds).
  Filters (market, kick-off, max odds, min value, sort) are FOLDED
  under one line naming what is applied — never an always-open panel.
  Refund and Boost show a SCORE column: Kelly growth scaled so the best leg
  on the list is 100 (relative, so it reads at a glance).
  Above the list, ONE line: the acca of the top 5 by score (one per match)
  — its odds and absolute EV, so "is it worth betting" reads at a glance.
  Boost adds a stake: 0.10 Kelly on a £50k bankroll (`BOOST_KELLY`,
  `BOOST_BANKROLL` in CardsPage.jsx).
  Both have a floating "1 per match" button bottom-right (keeps each match's
  top-scoring leg — an acca takes one leg per game). NO clock / "updated"
  time or other decoration in the header: Callum has asked twice.
  Refund = the insurance offer; **Acca Boost** (`accaBoost.js`) = winnings +10/20/30%
  on 4/5/6 legs, win-or-lose, each leg scored in its best fold. Both are a
  plain list of qualifying legs, best down — NO recommended acca on the page
  (Callum: "this is a tool"); `bestAcca`/`bestBoostAcca` stay, tested. There is
  NO calculator page (removed Oct 2026, never asked for); the offer's terms
  are `DEFAULTS` in code. Tested by vitest.
- `cards/pinnacle.py` returns **RAW** prices. De-vigging lives in
  `cards/value.py`; handing it fair prices would de-vig them twice.
- `merge_pinnacle` joins the two vendors into one event so `value.py` stays
  vendor-agnostic — swapping either side back is a change in that function
  alone.
- **AccaBonus eligibility is bet365's published list, not a guess.** Checked
  against UK sources Aug 2026 (bet365's own pages are egress-blocked): football
  (80+ competitions), tennis, MMA, basketball, NFL, baseball, ice hockey. Two
  corrections came out of that check and both were the same failure — a value
  figure measured against a promotion that would not pay on it: **ITF tennis is
  NOT eligible** (the list names Grand Slams/ATP/WTA/Davis Cup/BJK/Challenger),
  and **soccer IS** — it had been excluded since June 2026 on the incorrect
  grounds that the coupon and cards tools cover football, which they do for
  different MARKETS. Soccer and tennis are PREFIX lists, never whole groups:
  the groups carry friendlies, youth and ITF events the offer excludes.
  `/accabonus/eligible-keys` lists what the filter accepts plus near-misses, off
  the FREE `/v4/sports` endpoint, so re-checking against the terms costs nothing.
- **AccaBonus is bet365-only, and Pinnacle is the MEASURING STICK.** The Acca
  Boost is a bet365 promotion, so bet365 is where every row is placed;
  de-vigged Pinnacle is just what "fair odds" means, as in every other tool.
  There is deliberately no cross-book arb flag — one existed and rendered as
  the row's most prominent badge, describing a bet that needs a Pinnacle leg
  nobody here places. Don't re-add it.
  `ACCABONUS_CACHE_TTL` is the cost dial: 1 credit per sport key per pull, so
  24h is ~450 credits/month and 1h is ~3.6k–10.8k depending on how many keys
  discovery finds. Cards is ~17k of a 20k plan, so hourly only fits alongside
  it with `ACCABONUS_SPORT_KEYS` pinned to a handful — watch the
  "accabonus pull" log line for the live key count.
- The sweep's Discord dedupe (`scheduler._cards_notified`) is IN MEMORY and is
  swept each pass. Cards rows are computed, never stored, so there is no row to
  flag — but the map must keep dropping bets that fall off the sweep, or it is
  the same unbounded-dict leak `ttl_cache.py` exists to prevent.

### Notification provider (`app/notifications/`)
`send(message)`. `RoutedNotifier` (`DISCORD_WEBHOOK_URL` + `DISCORD_ROUTES`) →
`NullNotificationProvider` (neither set). Value alerts go through
`notify_rows(notifier, tool, header, rows)`, which routes each row to its
tool's channel — or a higher edge band's (`paddy@0.08=URL`) — and falls back
to the default webhook; system messages always use the default. Tools:
`coupons`, `paddy`, `365` (acca boost + goalscorers). Value-bet alerts from the refresh loop
are deduped via `legs.notified_tier` (notify only on new/changed tier) — keep
that behaviour when touching the loop, or live coupons re-alert every 10 min.

### State snapshot (`app/routers/state.py`)
`GET /state` returns the whole app state (coupons+legs, AccaBonus feed, cards
feed) for machine consumers. It is NOT session-guarded — it self-guards with
the static `STATE_API_KEY` (`X-API-Key` header, constant-time compare; blank
key = 503/disabled). It reads through the odds cache, so polling it never
increases API call frequency. Keep it read-only — never add mutating
operations behind the API key.

## UI rules — these are TOOLS, build only what is asked
Callum has had to strip unrequested additions out repeatedly (Oct 2026). A
screen is for finding a bet and placing it in seconds.
- **Add NOTHING to a page that was not asked for.** No clocks or "updated at"
  times, no page titles or subtitles, no explanatory notes or captions, no
  section headings ("Other legs, best fit first"), no helper text, tooltips,
  badges, legends, summary stats or extra colours. If you think one would
  help, ASK — never ship it and mention it afterwards.
- **No new pages, panels or calculators unless asked.** A pasted spec or
  worked example is the maths to implement, not a request for a page that
  shows it (the acca calculator was built that way and removed).
- **Labels only where a control or column would be unreadable without one**
  — a column header, a button's own text. Never a sentence describing what
  the screen does.
- **Monochrome by default.** Colour only for meaning that was asked for.
- **Don't recommend; list.** Rank the options and let the user pick — no
  "best acca", "suggested" or "pick of the day" blocks unless asked.
- **Every live-price screen refreshes itself** (`hooks/useAutoRefresh.js`):
  every 2 min while on screen, and at once when the app is reopened after
  30 s away — silently, in place. Paddy, 365 acca, scorers and the coupon
  tracker use it; a new tool showing prices must too. A failed refresh must
  never leave old prices looking current. The feeds read the server's
  caches, so refreshing costs no vendor quota.
- When asked to change one thing, change that thing. Don't tidy, restyle or
  "improve" the rest of the page alongside it.

## Workflow
- Work the numbered items in `PLAN.md` → **Open work**, in order. Complete and
  verify one before starting the next.
- Tests/verification steps are part of each item, not an afterthought.
- `PLAN.md` → **Decisions** is settled — do not re-litigate.
- Secrets: a gitleaks pre-commit hook blocks commits containing them. On a fresh
  clone, enable it with `git config core.hooksPath .githooks` (gitleaks must be
  on PATH). Rules are in `.gitleaks.toml` — the tool's defaults miss this repo's
  known leak shape, so don't replace them with a stock config.

## Stack & deployment
- Backend: FastAPI (Python), one app, routers namespaced per tool.
- Frontend: one React (Vite) app. `api/Dockerfile` builds it and copies the
  static bundle into the API image; `app/asgi.py` serves the SPA at `/` and the
  API under `/api` — ONE origin (required: the session cookie is `same_site=lax`
  and the frontend calls relative `/api/...`). CSS modules + vars in `index.css`.
- **Deployment is Render only.** ONE Docker web service (`render.yaml`),
  `autoDeploy: true` → **every push to `main` deploys** (`puntmore.com`, served at
  216.24.57.x). There is NO local docker-compose / nginx stack — it was removed;
  don't reintroduce one. To deploy: push to `main`. Health gate is `/api/ready`.
- Storage: SQLite on the Render persistent disk at `/data` (`DATABASE_URL`);
  uploaded images under `/data/uploads`. Single instance — SQLite has one writer.
- Scheduler: APScheduler (in-process).

## Adding a new tool page
1. Add the compute logic under `api/app/<tool>/` (and a client consumer of
   `TheOddsApiClient` if it needs odds).
2. Add a router under `api/app/routers/<tool>.py`; include it in `main.py`.
3. Add a Pydantic response schema in `schemas.py`.
4. Frontend: add a page under `frontend/src/pages/`, a route in `App.jsx`, and a
   tab in `components/NavTabs.jsx`. Reuse `DataTable.module.css` for tables.

## Fresh clone
Everything except `.env` is in the repo. Verified July 2026 by cloning from
GitHub and building from nothing: the test suite, the frontend build and the
Render Docker image all come up clean with NO credentials present.

```
git clone git@github.com:CMclarty/puntmore.git && cd puntmore
uv venv api/.venv
uv pip install -r api/requirements.txt -r api/requirements-dev.txt \
   --python api/.venv/bin/python3
(cd frontend && npm install)
git config core.hooksPath .githooks     # gitleaks pre-commit (needs gitleaks on PATH)
```

`.env` is the ONLY thing that cannot be cloned — copy it across by hand; every
variable is documented in `.env.example`. Without it you still get the full test
suite (321 tests, hermetic: the OCR and odds providers are mocked), the frontend
build, and deploys. What needs real keys is LIVE verification — running the real
OCR over a coupon photo and pricing it against the live board — which is what
catches the bugs unit tests cannot.

## Commands
```
# Run tests (from api/):
.venv/bin/python -m pytest tests/ -q

# Run API locally (from api/):
.venv/bin/uvicorn app.main:app --reload

# Run the frontend dev server (from frontend/):
npm run dev

# Deploy: push to main — Render autoDeploys (no local stack).
git push origin HEAD:main

# Re-run the derived-market calibration backtest (from api/):
.venv/bin/python tools/backtest_calibration.py

# Frontend unit tests (from frontend/) — vitest, currently the acca-insurance maths:
npm test

# Install a new Python package into the venv (no pip in venv — use uv):
uv pip install <package> --python api/.venv/bin/python3
```
