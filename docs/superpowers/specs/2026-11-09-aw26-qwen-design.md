# AW 26: Qwen does the work, and the page is reworked

Design, 9 Nov 2026. Branch `aw26-qwen`. Nothing here is built yet.

The approved look is the picture beside this file:
`2026-11-09-aw26-qwen-mock.png` (two phone screens at 375 px: Summary, Slips).
The picture is the look, not code to copy.

## What this is and why

AW 26 is Callum's record of the slips he places by hand at the Paddy
terminal. Today (pull request #32) it is a page on `me` over stored slips,
with routes to record, correct and delete a slip, and the sums done on
Render when a request asks. Nothing puts slips in except typing them.

This stage makes the tool run itself. Callum photographs a slip, presses
one button, and walks away. Qwen (HubAI, on the Hub) reads the photo, finds
the fair prices, records the slip, and later checks the results. The page
changes from a list of slips into a summary Callum reads in a few seconds:
how much he is up, how today is going, and which selections won or lost.

Why now: the page exists but is empty work for Callum unless Qwen fills it.
Why the summary: Callum does not want an answer per slip; he wants the
running total ("I need to keep motivated") and the selections in aggregate.

## Goals

1. A slip gets from a photo to a priced row on the page with no typing.
2. A selection is marked won or lost about two hours after kick-off with
   no nudge from Callum.
3. The front screen answers three things at a glance: all-time profit
   against the £100,000 target, today against the £1,000 EV target, and
   how the selections went.
4. Every number Qwen was unsure of is visibly unsure and easy to fix.

## Not doing

- No bet placing, no stake advice, no alerts, no Discord post.
- No reminders to Callum about anything (he chose neutral EV over reminders).
- No reading of photos or results on Render. Render stores and does sums.
- No change to the Paddy tool, its feed, or how EV and returns are worked
  out (`aw26/offers.py` stays as it is).
- No result checking through the Scraper service, and no results API.
- No Console changes on the Hub.

## What would make this a failure even if it works

- Qwen guessing. A wrong odds figure, a result marked from a hunch, or a
  made-up fair quietly corrupts the profit figure Callum uses to keep
  going. Blank and flagged is always better than plausible and wrong.
- A slip counted twice.
- EV flattered. A selection without a real fair must add nothing to EV.
- The heavy version. This was built too big twice already. Render stays a
  thin page over rows; the thinking is Qwen's.
- Photos that vanish: a photo Callum sent must end as a slip or as a
  visible failed row, never as nothing.

## Rules this sits inside

- `docs/decisions.md` is the register and wins. The edits it needs are
  listed under "Register edits" and go in the same pull request as the code.
- Render never loads a bookmaker page, never places a bet, and runs
  nothing in the background for AW 26.
- Hub code (`hub/`) never loads a page. The one new exception is HubAI
  itself opening Flashscore in its own ordinary browser, by Callum's
  decision below. No stealth or fingerprint tooling.
- All traffic is Hub to Render. Render cannot call the Hub.
- Secrets live in `.env` only. Qwen never handles the Render key; it uses
  the `puntmore` command, which reads the key itself (as `puntmore finder`
  does).
- Frontend rule: add nothing that was not asked for.
- Qwen is local and free to run. Use it freely. Its accuracy is not to be
  argued again here; the answer to doubt is "leave it blank and flag it".

## What Callum decided, in his words

- "Qwen can check the results manually by browser on Flashscore." It
  checks on its own about two hours after kick-off, with no nudge ("Yeah.").
- "The UI should be summaries, individual slips on a sub screen."
- "While I don't want per slip answers, it would be useful to have the
  selections listed in aggregate with win or lose."
- "Carry the styling from the paddy tool."
- "If there is more than one selection per team combine them in a drop
  down under the game name."
- Slips that span days ("I agree with your suggestions"): a selection is
  listed under its kick-off day; EV counts on the day the slip was placed;
  profit counts on the day the slip settles; staked and slip count go on
  the day placed; an open slip says when its last leg plays; the day pill
  in the band steps back to earlier days.
- "Photo upload. Can you add an upload button on the page which goes to
  Qwen." and "Telegram backup."
- "Can we get an all time profit in the tool somewhere. I need to keep
  motivated." Profit is the headline, not EV: "it physically can't be red
  over a long period."
- "Fairs from paddy tool. Occasionally there will be other bets I manually
  found... just treat them all as neutral EV. Better to be overly cautious."
- Earlier and still governing: AW 26 is "just a front end with qwen doing
  the work"; "Remember qwen is local, so no usage costs."

Everything below that Callum did not say is marked **(my call)**.

## The page

Styling is the Paddy tool's (`CardsPage.jsx`, `CardsPage.module.css`):
the `#004833` band, the rounded heavy title, the pills, the grey ground,
the white list with hairline rules, `--good` green for won, and the tool
dock taking the band colour. Reuse its CSS module where the layout matches
rather than copying values. Lost is `#b91c1c` **(my call)**; the Paddy
page has no red of its own.

### Summary (the front screen)

Inside the band, top to bottom:

- Title "AW 26", the day count ("Day 12"), and a day pill showing the day
  being looked at. The pill steps back to earlier days and forward again
  as far as today.
- The headline: all-time profit in large rounded type. Under it a thin
  progress bar to £100,000, then "x% of £100,000" and "N slips · N days".
  All-time means every settled slip, whatever day is chosen. A loss shows
  with a minus sign in the same type and an empty bar **(my call)**.

Below the band:

- A small table, rows EV / Profit / Staked / Slips, columns for the chosen
  day and "To date". The EV targets sit under the EV figures (£1,000 a
  day; days × £1,000 to date). When the chosen day is today the column
  reads "Today".
- The selections, aggregated across slips, under day headings by kick-off:
  "Today" with a tally ("4 won · 3 lost · 3 to come"), "Tomorrow", then
  dated headings ("Thu 12 Nov"). Columns: kick-off time, how many slips
  carry it (×n), odds, result (Won in green, Lost in red, a dash while
  pending).
  - A game with one selection is a plain row: the pick, the match under it.
  - A game with more than one selection is one row, "Arsenal v Chelsea ▾ 3",
    with a small mark per selection (W W L), and it drops down to list its
    selections, like the ladder rows on the Paddy page.
  - Where the same selection was taken at different prices on different
    slips, the row shows the average **(my call)**.
  - A fair that was assumed (see Fairs) carries a small mark.
  - A result can be set by hand: tapping a selection's result offers Won,
    Lost, Void, Pending. Setting it marks that selection on every slip.
  - A selection still pending 24 hours after kick-off shows a "?" in place
    of the dash: Qwen has stopped trying and it needs Callum **(my call)**.
- On today, the list runs from today forward (today, tomorrow, later). On
  an earlier day it shows that day's selections only **(my call)**.

### Slips (the sub screen)

Reached by a floating Summary / Slips switch at the bottom right, the same
control as the Paddy page's offer switch. It follows the same day pill and
shows the slips placed on the chosen day **(my call)**.

- An upload button. It takes several photos at once.
- Above the slips, one row per photo still with Qwen: "Waiting for Qwen",
  or "Qwen could not read this" with the reason, and two actions on a
  failed row: send it round again, or delete it. A photo that turned out
  to be a slip already in says so and can be dismissed.
- Each slip: a grey heading (offer, time, stake, EV, and profit once
  settled, or "open to Thu" naming the day its last leg plays), then its
  legs (pick, match, odds, fair, result).
- Tap to correct stays as it is now, with the photo a tap away for
  checking. A slip with any field Qwen left blank is flagged so it stands
  out.
- Typing a fair over an assumed one makes it a real fair and removes the
  mark.

### Days

All days are London days, as the slips are printed.

| Figure | Counts on |
|---|---|
| A selection in the list | its kick-off day |
| EV | the day the slip was placed |
| Staked, slip count | the day the slip was placed |
| Profit | the day the slip settled |
| All-time profit, "To date" | everything up to now |

A selection with no known kick-off sits under the day its slip was placed
**(my call)**.

The page asks Render for one day at a time. It never pulls the whole
history; a season is thousands of legs.

## From photo to slip

```
Callum's phone ── upload ──> Render (stores the photo, state: waiting)
                                 ^            |
            every minute: "any   |            |  photo + its row
            photos waiting?"     |            v
                              Hub daemon ── runs Qwen once per photo
                                 |
                                 └─ Qwen: reads photo, asks for the Paddy
                                    board, records the slip on Render
```

1. The upload button sends the photos to Render. Render stores each one
   under `/data/uploads` with a row that says it is waiting. That is all
   Render does with it.
2. The Hub daemon gains a third loop beside the inbox pass and the mail
   poll **(my call: a loop in the existing daemon, not a new systemd
   timer, so it deploys by itself)**. Once a minute it asks Render for
   waiting photos. For each, it takes the photo (so it is not handed out
   twice), saves it locally, and starts Qwen once with the photo attached
   (`hermes ... chat -q <brief> --image <file>`), one photo per run, one
   run at a time, with a time limit.
3. Qwen follows a runbook (below). It ends in exactly one of: the slip is
   recorded; the photo is marked failed with a reason; the photo is marked
   a duplicate.
4. If Qwen's run ends, or runs out of time, without one of those, the Hub
   marks the photo failed ("Qwen did not finish"). A photo taken but never
   finished (the Hub went down) goes back to waiting after 15 minutes. A
   photo that has failed three times stays failed until Callum sends it
   round again **(my call)**.
5. The slip takes the photo as its picture, so it is there for checking.

**Telegram backup.** A slip photo Callum sends to HubAI on Telegram is put
on Render with `puntmore aw26 upload <file>` and joins the same queue
**(my call)**. HubAI replies that it is queued. There is one way of
reading a slip, not two.

### What Qwen reads, and how

From the photo: receipt number, date and time, stake, which offer, and for
each leg the game, market, line, side and odds.

- Never guess. A field it cannot read is left blank. Render already
  accepts blanks; the slip then has no EV and is flagged on the page.
- The offer is one of refund, boost, other. If the slip does not make it
  plain, "other" **(my call)**.
- A photo that is not a slip, or where no leg can be read at all, is
  marked failed with a short reason rather than recorded.
- Render refuses a receipt it already holds (409). Qwen then marks the
  photo a duplicate and stops.
- Market words are short and in the Paddy board's wording ("Over 10.5
  corners", "Over 3.5 Chelsea cards"), so the same selection on two slips
  reads the same.
- Alongside the words, each leg carries its kind (corners, cards, goals,
  saves, other), line, side, and the board's event id when the game was
  found there. `aw_legs` already has these columns; the record route does
  not take them yet.

### Fairs

At the moment of reading, Qwen asks for the Paddy board
(`GET /api/paddy/feed`, through the `puntmore` command) and looks for each
leg's game and selection on it.

- Found: the fair is the board's fair for that exact line and side. Qwen
  also takes the kick-off from the board.
- Not found (a bet Callum found by hand, or a game off the board by then):
  the fair is set equal to the odds taken, so the leg adds nothing to EV,
  and the leg is marked "fair assumed". No reminder.
- The board must be asked for with no value floor, so a selection Paddy
  now prices below fair is still found **(my call)**. `min_edge` already
  goes down to -0.50.
- A slip is read once, at upload. If Callum uploads hours late and the
  price has moved, the fair is the board's fair then. That is accepted;
  upload soon after placing.

## Results

1. The same daemon loop, every 30 minutes, asks Render which games are
   due: a game with a pending selection whose kick-off was at least 2
   hours ago and less than 24 hours ago. Render works this out when asked.
   **(The 30 minutes and the 24 hours are my call.)**
2. For each due game the Hub starts Qwen once, with its browser switched
   on, and tells it the game and that game's pending selections.
3. Qwen opens the match on Flashscore in its own browser, as a person
   would: finds the match, checks it shows as finished, reads the
   statistics.
4. For each selection it marks won or lost on Render. One mark settles
   that selection on every slip that carries it, and each of those slips
   is re-settled by the existing sums. This is a new action on Render.
   It also removes a trap in today's routes: correcting a slip replaces
   its whole list of legs, and a leg sent back without its fair loses the
   fair. Qwen never uses that route for results.

Qwen's rules for results:

- Mark only from a number it actually read on the page. Unsure, leave it.
- Match not finished, or no statistics for the figure it needs: leave it;
  the next pass tries again until 24 hours after kick-off.
- Never mark void. Postponed, abandoned, or a count exactly equal to the
  line: leave it for Callum **(my call)**.
- 90 minutes only. A match that went to extra time: leave it **(my call)**.
- Cards are counted by Paddy Power's published rule for card totals: a
  yellow is one card, a straight red is one card, and one player can
  count for at most two (two yellows and off is two; a yellow then a
  straight red is two). Cards to managers and to anyone not on the pitch
  do not count, nor do cards after the final whistle. So when a match has
  a red card, Qwen counts from the list of incidents player by player, not
  from the yellow and red totals; if it cannot be sure, it leaves it.
  (Source: Paddy Power help centre, "Football Cards Rules/Bookings
  Markets". The page is over a year old and the shop terminal's rules may
  differ. Callum, say if this is wrong.)
- Team selections ("Over 3.5 Chelsea cards") use that team's figure.

A selection with no known kick-off becomes due 2 hours after its slip was
placed **(my call)**.

## What changes where

Shapes and names here are suggestions; the builder may improve them as
long as the behaviour above holds.

**Render API (`render/api/app/aw26/`, `routers/aw26.py`, all behind the
`aw26` grant)**

- A table for photos in the queue: who, when, where the file is, where it
  came from (page or Telegram), state (waiting, reading, failed,
  duplicate, done), when taken, tries, reason, the slip it became.
- Photo actions: upload several; list those not done; take one; fetch its
  image; mark failed with a reason; mark duplicate; send round again;
  delete.
- The record route additionally takes, per leg, kind, line, side, event
  id and "fair assumed", and for the slip the photo it came from.
- A column on the leg for "fair assumed".
- A way to tell two legs are the same selection (same game, same pick),
  using the event id where there is one and the words where there is not.
  Slips already stored have words only and must still group.
- Summary for one day: the headline (all-time), the day and to-date
  figures by the Days table, and the selections for the list.
- Slips for one day.
- Games due a result.
- Mark a selection's result, settling every slip that carries it. Open to
  the page (hand-set) and to Qwen's key.
- `python -m app.apikeys` gains a way to add grants to an existing key, so
  the Hub's key can gain `aw26` and `cards` without being replaced
  **(my call)**.
- Still nothing in the background: no scheduler job, no reading.

**Render frontend (`Aw26Page.jsx` and its module)**

- Rebuilt as Summary and Slips in the Paddy styling, as described above.
  The current paper-theme page goes.

**Hub (`hub/puntmore_hub/`)**

- `puntmore aw26 ...` commands that Qwen calls: the board, record a slip,
  fail a photo, mark a duplicate, upload a file, mark a selection. They
  speak to Render with the Hub's existing key (`PUNTMORE_RENDER_HTTP`,
  `PUNTMORE_RENDER_API_KEY`), the way `finder.py` does.
- The daemon loop described above: photos every minute, results every 30
  minutes, one Qwen run at a time, each run's output kept in the data
  directory like an escalation's. It does nothing when the Render address
  or key is missing. The daemon's own docstring ("Nothing here talks to
  Render") changes.
- Which HubAI profile reads slips and checks results: the
  betting-client-manager ("records approved bets, reconciles") **(my
  call)**, set by a new setting so it can be changed.
- The Hub's code still loads no page. Only Qwen's browser does.

**HubAI runbook (`hub/deploy/hermes-skills/puntmore-aw26/`)**

- One runbook with three parts: read a slip photo; check a game's
  results; a slip photo arriving on Telegram.
- Installed by `update.py::sync_hermes_skills` for the profile the Hub
  starts and for the chief of staff on Telegram. That function maps a
  runbook to one profile directory today, and the chief of staff's home is
  `~/.hermes` itself, so it needs widening.
- The run that checks results must have the browser tool switched on; the
  command-line tool set does not include it by default.

**Words**

- The register edits below, `CLAUDE.md`'s AW 26 paragraph, `README.md`,
  `hub/AGENTS.md`, and any of `docs/dataflow.md` / `docs/architecture.md`
  that describe who loads pages or what the daemon talks to.

## Register edits (dated 9 Nov 2026, in the same pull request as the code)

To the AW 26 entry, in Callum's words:

- The page is "summaries, individual slips on a sub screen", with "the
  selections listed in aggregate with win or lose" and "an all time
  profit". Styling is carried from the Paddy tool.
- "Photo upload. Can you add an upload button on the page which goes to
  Qwen", with "Telegram backup". Render stores the photo and hands it over
  when the Hub asks; it still reads nothing and runs nothing in the
  background.
- "Qwen can check the results manually by browser on Flashscore." This is
  the one exception to "every page load lives in the Scraper service" and
  to `hub/AGENTS.md`'s "`hub/` never loads a page": HubAI's own browser,
  Flashscore only, AW 26 results only.
- Fairs come from the Paddy board; a bet not on it is "neutral EV. Better
  to be overly cautious."
- The Hub daemon gains an AW 26 loop that talks to Render.

## Prove first

Two things here have never been tried on this box. They are tried on real
examples before anything is built on them:

1. Qwen reading a slip. Vision is off today (llama-server runs without
   its projector) and no photo has ever gone through HubAI. With vision
   on, give Qwen five real slip photos one at a time and compare what it
   reads with the slips.
2. Qwen reading a result. Give it one finished match and ask for total
   corners, total cards, and each by team, off Flashscore.

If either fails outright, stop and go back to Callum; do not build round
it. Hiccups short of that (a field misread now and then) are what the
blank-and-flag rule and tap-to-correct are for.

## Order of work

**(My call.)** Each step merges and deploys by itself:

1. Prove first (no product code).
2. Render and the page: the new actions, Summary and Slips, the upload
   button and the queue rows. Useful at once with hand entry.
3. The Hub: commands, the photo loop, the runbook's reading part,
   Telegram backup.
4. Results: due games, mark a selection, the runbook's results part.

## Left to the builder

- Names and shapes of routes, tables, columns and commands.
- How "the same selection" is keyed, within the rule above.
- How the Hub hands the Render address and its commands to Qwen in the
  brief, and the time limit per run.
- How Hermes is told to allow the browser for one run, and how a photo
  arriving on Telegram reaches the runbook as a file.
- Whether Flashscore is reached by search or by a known address pattern.
- Layout details not fixed by the picture, within the Paddy styling.
- Tests: Render and Hub suites as they stand today, plus the new
  behaviour; Qwen's reading is proved by the examples, not by unit tests.

## Callum's own jobs

These cannot be done for him:

1. Switch Qwen's vision on: the projector line in
   `~/.config/llama-server/env`, then restart `llama-server`.
2. Put five or more real slip photos where they can be read, for the proof.
3. Check his `AUTH_USERS` entry on Render carries `aw26`.
4. After the Render step ships: one command in Render's Shell to add
   `aw26` and `cards` to the Hub's existing key.
5. Read the card-counting rule above and say if the terminal settles
   differently.

## Risks, said plainly

- Qwen's reading of printed slips is unproven. The proof step is there for
  this.
- Flashscore does not publish statistics for every league, and its pages
  change. Those selections end as "?" for Callum to set by hand.
- Flashscore's terms have not been read. If they ban automated reading the
  way Bookie Bashing's do, this is the same kind of risk, at a handful of
  page views a day. It is Callum's decision and he has made it.
- One model serves everything on the Hub. A slip read or a results check
  waits its turn behind an escalation or a Telegram chat, and they behind
  it.
- A fair taken at upload, not at placing, drifts with the market.
