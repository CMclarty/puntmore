# "Check X for the weekend", on demand: design

Callum, 26 Nov 2026. Replaces `2026-11-12-wop-check-design.md` (the WOP check as first
drawn); what that file settled and this one does not repeat still stands.

## What it is for

Callum checks Paddy Power's WhatOddsPaddy bet builders (WOPs) by hand each weekend against
Bookie Bashing's bet builder. He wants to say "check wops for the weekend" to Claude Code on
the Hub and get the value ones back, and to ask for other checks the same way later.

## What a request does

"Check wops for the weekend" (Friday 17:00 to Sunday 23:59, London):

1. **Screen.** Every game in the covered leagues (the Paddy sweep's listing jobs' listings:
   Premier League, La Liga, Serie A, growing as listings are added; a listing the sweep does
   not hold open and fresh, the request loads itself), both teams, on two
   WOPs: team 1+ goals, 5+ corners and the other team 2+ cards ("1,5,2"), and team 2+ goals
   and 6+ corners ("2,6"). Paddy's price comes off the game's match page; the fair comes from
   Bookie Bashing's builder by loading a preset. Plain "1,5" is not in the screen.
2. **Ladder.** For any game where a screened WOP rates above 100 (Paddy's price above the
   fair), every one of Bookie Bashing's own Paddy presets (named `#WOP - ...`; 58 seen on
   12 Nov 2026, 31 for one team and 27 for the match) that Paddy is also offering on that game
   is priced the same way.
3. **Tracker.** Every comparison made, screen and ladder, is saved to Callum's Bookie Bashing
   tracker, Private, with the builder's own Save button.
4. **Report.** In the chat: every comparison above 100, best first (game, team, WOP, Paddy's
   price, fair, rating), then the games that had no WOPs up, and anything that could not be
   matched or priced.

## Decisions

Callum's, 26 Nov 2026:

- It is asked for on demand, of Claude Code. The answer comes back in the chat.
- The screen is 1,5,2 and 2,6; the ladder is Bookie Bashing's Paddy presets, on value games only.
- All comparisons go to the **Private** tracker.
- **A request disregards Paddy's load budget.** ("Paddy budget should be disregarded for
  requests made.")
- There is to be a standing way for Claude Code to have the Scraper read Paddy on request.

Mine, to confirm (each is a line in the register when the code lands):

- A request also reads games more than 48 hours out. The register's 48-hour rule is about not
  spending the sweep's budget; a Friday request for Sunday's games cannot work inside it.
- A request ignores Paddy's quiet hours as well as its hourly and daily numbers, but keeps
  the gap between loads (`paddypower.load_gap_s`, 90 s) and stops at once on a cool-off. A
  challenge is never answered. So thirty games take about three quarters of an hour.
- Requested loads are recorded and shown in `status` on their own line, and do not use up the
  sweep's budget.
- On a Paddy page a request clicks nothing but the cookie banner, as now: WOP prices arrive on
  the page's first load. `look` stays closed to Paddy.
- The Scraper presses Save in Bookie Bashing's tracker panel, Private only, and nothing else
  there that changes the account. This is the first control it presses that writes to
  Callum's account; until Callum has watched one run, Save runs dry (fills the panel, does
  not press).
- Not signed in to Paddy. Prices and WOPs show without an account.
- Bookie Bashing's budget is untouched: choosing a game and a preset happens inside one open
  builder page.

## How it is built

Every page load is the Scraper's. Claude Code and the `check` command load nothing; they
write a request for the running service and read what it produces.

1. **Requests** (`scraper/ask.py`, the runner, the CLI). A request is a JSON file in
   `data/requests/`, answered by the running service with a result file in
   `data/requests/done/`. Two kinds:
   - `read`: load these Paddy match pages (named by listing job and a kick-off range, or by
     address), one at a time in a tab of its own, run a named parser over what arrived, save
     the page and its captured payloads, close the tab. For a job, its listings are read
     where the sweep holds them open and fresher than the job's relist time; any other is
     loaded by the request (a requested load, in a tab it closes once read).
   - `price`: on the open Bookie Bashing builder page, for each (game, preset) return Total
     Odds, and if asked save it to the tracker.
   The CLI writes the request and waits for the result.
2. **Paddy WOP reader** (`scraper/parsers/pp_wop.py`): the `#WhatOddsPaddy - ...` markets
   (seen on 12 Nov 2026 grouped by price band: Featured, Up to 9/1, 10/1 to 24/1, 25/1 to
   79/1, 80/1 +). Rows: game, kick-off, WOP name as Paddy words it, price. Written only from
   real saved payloads; the 12 Nov page may do, and the first `read` request on a live game
   saves more.
3. **Builder presets** (`scraper/parsers/bb_presets.py`, sharing `bb_saves`'s helpers): select
   the game, open "Show/Hide Presets", choose the preset in "Use Preset", read Total Odds
   once settled. The Save panel is learned with `look` on `bb-look` before it is driven.
4. **Matching** (`scraper/checks/match.py`): Paddy names a WOP with the teams' names, Bookie
   Bashing with "Home"/"Away". Names are matched after replacing the two teams with Home and
   Away and normalising spacing, case and punctuation. No match is reported, never guessed.
5. **A check** (`scraper/checks/`): a recipe (`wops`: the screen presets, the ladder prefix
   `#WOP`, the threshold 100, Private saves) and one runner that does screen, then ladder,
   then writes `data/checks/<name>-<time>.json`. `python -m scraper check wops --weekend`.
   A new check that reads a market the Scraper already reads is a new recipe; a new kind of
   page needs a reader first.
6. **The skill** (`.claude/skills/check-weekend/SKILL.md`): "check <name> for the weekend"
   runs the command, waits, and reports from the result file.

## Not in this

Placing any bet or adding to a betslip. A board on Render. Running on a timer. Bookmakers
other than Paddy Power. Signing in to Paddy. A HubAI skill (Claude Code only, for now).
