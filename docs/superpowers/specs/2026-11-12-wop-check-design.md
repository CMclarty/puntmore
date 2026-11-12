# "Check wops for weekend": design

Callum, 12 Nov 2026. A WOP is a WhatOddsPaddy: a prebuilt bet builder Paddy Power prices,
found on a tab in each game's own menu. Callum checks them by hand each weekend against
Bookie Bashing's bet builder. This is that check as one request.

## What it does

Said as "check wops for weekend" (to Claude Code on the box, or to HubAI):

1. Lists the weekend's games (Friday 17:00 to Sunday night, London) in the in-scope leagues
   (the Scraper's `COMPETITIONS`: Premier League, La Liga, Serie A first).
2. For each game and each team, reads Paddy's price for three WOPs:
   - team 1+ goals and 5+ corners
   - team 2+ goals and 6+ corners
   - team 1+ goals and 5+ corners, and the other team 2+ cards
3. For the same game and team, gets Bookie Bashing's fair price for the same bet from its bet
   builder, by loading Callum's saved preset (`Home 1,5`, `Away 1,5`, `Home 2,6`, `Away 2,6`,
   and the preset he saved on 12 Nov 2026 for the third) - presets wherever one exists
   (Callum: "Use presets wherever possible").
4. Saves each comparison to Callum's Bookie Bashing tracker with the builder's own Save
   button: bookmaker Paddy Power, odds = Paddy's price.
5. Reports every WOP rated above 100 (Paddy's price above the fair: Callum, "anything above
   100"), best first: game, team, WOP, Paddy's price, fair, rating.

## Decisions

- **Callum's**: presets first; saves go to the Bookie Bashing tracker by its Save button;
  value is anything above 100.
- **Mine, to confirm**: tracker saves are **Private** (the panel defaults to Private + Group
  and Share Now, which would post a weekend's hundred-odd entries to his groups); every
  comparison is saved, not only the value ones; a run is started by hand, not on a timer.
- **Every page load is the Scraper's** (the register): the skill starts Scraper jobs and
  reads what they produce; it loads nothing itself. So the work is two Scraper readers and
  one Scraper action, and the skill is thin.
- **Register**: the rule that `bb-saves` was the only automated use of Bookie Bashing was
  removed by Callum on 12 Nov 2026 ("remove that rule"), so this needs no exception.
- **A general bet-builder job** (Callum, 12 Nov 2026: "I will probably have more requests to
  add things to bookie bashing, so lets make that simpler"): the Bookie Bashing side is not a
  WOP-only driver but one job kind driven by presets - a job names the presets to load and the
  leagues to cover and returns the fair for each game and team; a new request is a preset
  saved in Bookie Bashing plus a job, not new code. The tracker save is a switch on the job,
  off by default, Private when on.
- **Pacing**: the Scraper's budgets and the 48-hour window apply as they stand. Paddy shows a
  WOP tab only near kick-off, so a Friday run will find Friday-night and Saturday games and
  may find Sunday's not yet up; the report says which games had no WOP tab.

## Pieces

1. **Paddy WOP reader** (`scraper/parsers/pp_wop.py`): a market tab on the match page
   ("WhatOddsPaddy" or as Paddy names it), read like Corners / Cards are. Written ONLY from a
   real WOP tab: none has been seen yet (games on 12 Nov were nine days out). First real ones
   about 18-20 Nov 2026 for the 20-23 Nov round. Rows: game, team, WOP kind, price.
2. **Bookie Bashing preset reader** (in `bb_saves`'s driver, shared): select the game, open
   "Show/Hide Presets" (it appears once a game is selected), load the preset, read Total Odds.
   The presets panel has not been seen open in a saved page; learn it from one dump of the
   builder with a game selected. Home / Away: how the preset picks the team must be read from
   the saved preset recipes before it is relied on.
3. **Tracker save** (same driver): fill Bet Name / Bookmaker / Odds, set Private, press Save.
   Learn the panel from a dump; dry-run mode that fills and does not press.
4. **A job that pairs them**: for each (game, team, WOP) with a Paddy price, ask the builder
   for the fair, save, and write the comparison rows to `data/hub/` for the Hub.
5. **The skill** (`hub/deploy/hermes-skills/` and a Claude Code skill): starts the job for the
   weekend's games, waits, reads the rows, reports.

## Order

A. Now: pieces 2 and 3 against the real builder (it has games listed and Callum's presets),
   Save in dry-run until the register line is in.
B. About 18-20 Nov: piece 1, from the first real WOP tabs.
C. Then pieces 4 and 5, and a first full run for the 21-23 Nov weekend with Callum watching.

## Not in this

Placing any bet. Reading WOPs other than the three. Other bookmakers.
