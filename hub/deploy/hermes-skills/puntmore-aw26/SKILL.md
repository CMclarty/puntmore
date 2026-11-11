---
name: puntmore-aw26
description: Read a queued AW 26 slip photo (a Paddy Power accumulator) and record the slip with `puntmore aw26`. Use when you are handed an AW 26 photo id and its photo to read. For the chief of staff only, queueing a slip photo sent on Telegram.
---

# AW 26 slips

AW 26 records Callum's **Paddy Power accumulators**, one slip per photo. The command is `puntmore` (`~/.venvs/puntmore/bin/puntmore` if it is not on your PATH). Every command prints JSON.

If you were given a photo id, you are the reader: go to Reading and never run `puntmore aw26 upload`.

## Reading a slip photo

You were given a photo id and the photo. You end with exactly one of:
- `puntmore aw26 record <file>` (the slip, with `"photo_id"`), or
- `puntmore aw26 fail <id> --reason "<reason>"`.

### 1. Refuse what is not a slip to record

Run `puntmore aw26 fail <id> --reason "<reason>"` and stop when:
- it is not a Paddy Power slip (William Hill or any other bookmaker): `not a Paddy slip`
- the bar under the receipt does not say `ACCUMULATOR` (DOUBLE, TREBLE, MULTIWAY, singles, anything else): `not an acca`
- the receipt number is not complete (any character hidden, cut off or unclear): `no receipt number`
- no leg can be read: `unreadable`
- a leg's teams cannot be read: `unreadable` (a slip with a leg missing is a different bet)

### 2. The slip

- `receipt`: the number under the shop name, shaped `B` + 13 digits + `-` + 6 digits + `-` + 1 digit. Copy every character.
- `placed_at`: the time beside the receipt, copied exactly as printed, e.g. `10/16/2026 4:17:52 PM`. Dates are month first. Do not convert it; `puntmore` does.
- `offer`, from the words on the slip:
  - `BONUS` at the top of the slip: `free`
  - "qualified for money back acca": `refund`
  - "qualified for Acca Boost": `boost`
  - none of these, or unsure: `other`
  - `offer` is always sent.
- `stake`: the number on the "Total Stake" line (`GBP 100.00` is `100.00`).
- Never read Total Odds, Max return, Bonus or "incl. bonus". They are not sent: Paddy's sums are sometimes wrong and Render works its own.

### 3. Each leg

A leg is the block from a bold "Home - Away" line down to the next one.
- `event`: the bold line, written `Home v Away` (`Burnley - Wolves` is `Burnley v Wolves`).
- `kickoff`: the date and time under the league, as printed (`10/16/2026, 7:45 PM`).
- `market` and `selection`: the market words as the slip prints them.
  - "Total Corners - Under 12.5": market `Total Corners`, selection `Under 12.5`.
  - "Goalkeeper To Make 2 Or More Saves" then "Burnley Goalkeeper": market `Goalkeeper To Make 2 Or More Saves`, selection `Burnley Goalkeeper`.
- `odds`: the price at the right of the leg, as printed. Ignore a `*` before it.
  - a decimal is copied: `1.28` is `1.28`
  - a fraction a/b is 1 + a/b, to 4 decimal places: `2/7` is `1.2857`, `5/4` is `2.25`, `1/3` is `1.3333`
- `kind`: `corners`, `cards`, `goals`, `saves` or `other`.
- `line` and `side` (`over` or `under`):
  - "Total Corners - Under 12.5": corners, line 12.5, under
  - "Total Goals - Over 1.5": goals, line 1.5, over
  - "Goalkeeper To Make N Or More Saves": saves, line N - 0.5, over (2 Or More is line 1.5)

### 4. The fair price of each leg

Run `puntmore aw26 board --at "<placed_at as printed>" --match "<Home v Away>"`. It prints the games on Paddy's board at that moment: `event_id`, `match`, `commence_time`, and `bets`, each with `market`, `line`, `side`, `pick`, `odds`, `fair`.

Find the leg's game. A team name on the slip can differ from the board's (the slip printed "Cardiff Metropolitan"): when one name differs, match on the other team and the kickoff.

In that game, find the bet with the leg's kind as `market`, the same `line` and the same `side` (the board's `side` is lower case, like the leg's). For a team leg the board's `market` is `team_corners` or `team_cards` and its `pick` names the team. The board's `saves` is the match total, so a goalkeeper leg is never found.

- Found: add `fair_odds` = its `fair`, `fixture_id` = the game's `event_id`, and `kickoff` = the game's `commence_time`. Never use the board's `odds`.
- Not found, unsure, `[]`, or the command errors: add `"fair_assumed": true` and no `fair_odds` or `fixture_id`.

### 5. Record

Never guess. `receipt`, `placed_at`, `offer` and each leg's `event` are always sent: with no full receipt the photo is failed `no receipt number` (step 1), and an offer you could not read is `other`. Any other field you did not read (`stake`, or a leg's `kickoff`, `market`, `selection`, `odds`, `kind`, `line`, `side`) is left out.

1. Write the slip as JSON to `/tmp/aw26-<id>.json`, with `"photo_id": <id>`.
2. Check it: `puntmore aw26 record /tmp/aw26-<id>.json --dry-run`. It sends nothing.
3. Record it: `puntmore aw26 record /tmp/aw26-<id>.json`.
   - `{"recorded": ...}`: done. Stop.
   - `{"duplicate": ...}`: the slip is already in. That is a finished job. Stop.
4. An error from the check (step 2) or from the record (step 3), e.g. `legs[2].kickoff: unreadable time`: look at that field on the photo again, fix it or leave it out (never `receipt`, `placed_at` or `offer`), and go back to step 2 once. If it errors again, run `puntmore aw26 fail <id> --reason "<the error>"`.

### Worked example

A boost slip from the Hackney shop, placed 16 Oct 2026 (the receipt number here is invented):

```text
981 Hackney
B0000000000000-000000-0   10/16/2026 4:17:52 PM
ACCUMULATOR
Frosinone - Sassuolo / Italy - Italian Serie A / 10/16/2026, 7:45 PM
  Total Corners - Under 9.5                                  * 5/4
Burnley - Wolves / England - English Championship / 10/16/2026, 8:00 PM
  Goalkeeper To Make 2 Or More Saves / Burnley Goalkeeper    * 1/4
Everton - Chelsea / England - English Premier League / 10/17/2026, 12:30 PM
  Goalkeeper To Make 2 Or More Saves / Everton Goalkeeper    * 1/5
Venezia - Napoli / Italy - Italian Serie A / 10/17/2026, 2:00 PM
  Goalkeeper To Make 3 Or More Saves / Venezia Goalkeeper    * 2/7
Man City - Ipswich Town / England - English Premier League / 10/17/2026, 3:00 PM
  Goalkeeper To Make 2 Or More Saves / Man City Goalkeeper   * 1/3
Bonus information: *** Your bet has qualified for Acca Boost! ***
Total Odds 5.79 / Total Stake: GBP 100.00 / Max return ...  (Total Odds and Max return not read)
```

`puntmore aw26 board --at "10/16/2026 4:17:52 PM" --match "Frosinone v Sassuolo"` printed (example values):

```text
[{"event_id": "id1000001234567", "match": "Frosinone vs Sassuolo", "commence_time": "2026-10-16T18:45:00Z",
  "bets": [{"market": "corners", "line": 9.5, "side": "under", "pick": "Under 9.5 corners", "odds": 2.25, "fair": 2.1}]}]
```

The corners leg is found. The four goalkeeper legs are never on the board, so they are fair assumed. The file for photo 42:

```json
{
  "photo_id": 42,
  "receipt": "B0000000000000-000000-0",
  "placed_at": "10/16/2026 4:17:52 PM",
  "offer": "boost",
  "stake": 100.00,
  "legs": [
    {"event": "Frosinone v Sassuolo", "kickoff": "2026-10-16T18:45:00Z",
     "market": "Total Corners", "selection": "Under 9.5", "odds": 2.25,
     "kind": "corners", "line": 9.5, "side": "under",
     "fair_odds": 2.1, "fixture_id": "id1000001234567"},
    {"event": "Burnley v Wolves", "kickoff": "10/16/2026, 8:00 PM",
     "market": "Goalkeeper To Make 2 Or More Saves", "selection": "Burnley Goalkeeper", "odds": 1.25,
     "kind": "saves", "line": 1.5, "side": "over", "fair_assumed": true},
    {"event": "Everton v Chelsea", "kickoff": "10/17/2026, 12:30 PM",
     "market": "Goalkeeper To Make 2 Or More Saves", "selection": "Everton Goalkeeper", "odds": 1.2,
     "kind": "saves", "line": 1.5, "side": "over", "fair_assumed": true},
    {"event": "Venezia v Napoli", "kickoff": "10/17/2026, 2:00 PM",
     "market": "Goalkeeper To Make 3 Or More Saves", "selection": "Venezia Goalkeeper", "odds": 1.2857,
     "kind": "saves", "line": 2.5, "side": "over", "fair_assumed": true},
    {"event": "Man City v Ipswich Town", "kickoff": "10/17/2026, 3:00 PM",
     "market": "Goalkeeper To Make 2 Or More Saves", "selection": "Man City Goalkeeper", "odds": 1.3333,
     "kind": "saves", "line": 1.5, "side": "over", "fair_assumed": true}
  ]
}
```

## A slip photo on Telegram (chief of staff only)

Only the chief of staff does this, for a slip photo Callum sends on Telegram. The reader never does.

1. Run `puntmore aw26 upload <the image file> --source telegram`.
2. Reply "Queued for AW 26".
3. Do not read the slip yourself. The AW 26 reader does that.
