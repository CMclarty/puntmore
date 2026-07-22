# Provider verification

Findings verified against live vendor APIs. Recorded because re-deriving them
costs API credits — read this before spending more.

---

## TheOddsApi — first-goalscorer (NOT yet verified against live data)

**Status:** the goalscorer tool (`app/goalscorers/`) was built 22 Jul 2026 but
its per-event response shape is **unverified** — first goalscorer only posts
near kickoff for major fixtures, and late July is off-season, so there was no
live fixture to probe.

Assumptions baked in, to confirm on the first in-season major fixture:

1. **Market key** is `player_first_goal_scorer` (per-event endpoint only, like
   cards/corners — the bulk `/odds` endpoint rejects it).
2. **Player identity** — `value.py::_player_prices` reads `outcome["description"]`
   then falls back to `outcome["name"]`. Confirm which field actually carries
   the player name (note the CLAUDE.md warning that `description` is absent on
   *featured* markets — player props may differ).
3. **bet365 coverage** — bet365 is confirmed on TheOddsApi for h2h, but returned
   NO corners/cards below. Confirm it prices `player_first_goal_scorer`.
4. **Pinnacle coverage** — user states Pinnacle prices it, but only close to KO
   and for majors. Confirm the "no goalscorer" outcome is included (the de-vig
   needs the complete book).

5. **Name matching** — bet365 and Pinnacle spell players differently, so names
   are fuzzy-matched (`value.py::player_match_score`, difflib + accent-fold, same
   approach as the fixture matcher). The floor (`GOALSCORER_NAME_MATCH_FLOOR`,
   default 0.8) is biased for precision but **uncalibrated** — collect real
   bet365/Pinnacle name pairs from the first live fixtures and tune it. Rows
   below the confident band (0.9) are surfaced with a ⚠ rather than trusted. A
   learned alias table was deliberately deferred (no data to learn from yet).

Until confirmed, treat the stored edges as unvalidated. One probe of a real
fixture settles the schema; a handful of fixtures calibrates the name floor.

---

## TheOddsApi — cards/bookings & corners markets (verified 21 July 2026)

**Question:** does TheOddsApi carry Paddy Power bookings and corners markets?

### The markets exist

| Market key | Meaning |
|---|---|
| `alternate_totals_corners` | Total corners over/under |
| `alternate_spreads_corners` | Handicap corners |
| `alternate_team_totals_corners` | Team total corners |
| `corners_1x2` | Most corners (1X2) |
| `alternate_totals_cards` | Total cards / bookings over/under |
| `alternate_spreads_cards` | Handicap cards / bookings |
| `player_to_receive_card` | Player to be carded (Yes/No) |
| `player_to_receive_red_card` | Player red-carded (Yes/No) |

### They are NOT available on the bulk endpoint

`GET /v4/sports/{sport}/odds` serves **featured markets only** (`h2h`,
`spreads`, `totals`, `outrights`). Anything else is rejected outright:

```
GET /v4/sports/soccer_brazil_campeonato/odds/?markets=alternate_totals
→ {"message":"Markets not supported by this endpoint: alternate_totals",
   "error_code":"INVALID_MARKET"}   # costs 0 credits
```

Cards/corners require the **per-event** endpoint:
`GET /v4/sports/{sport}/events/{eventId}/odds`.

**Cost = (unique markets *returned*) × (regions).** Charged per event, so a
league-wide sweep costs roughly `events × markets × regions` — e.g. 10 fixtures
× (corners + cards) × 1 region = 20 credits per refresh. That is an order of
magnitude more expensive than the featured-market path and is **not viable on
the 500-credit testing tier** at league scope.

### Paddy Power carries these markets rarely, if at all

`paddypower` is a supported `uk` bookmaker key. But across every same-day
fixture sampled it returned **no** corners or cards markets:

| Fixture | UK books returning corners/cards |
|---|---|
| Atletico Mineiro vs Bahia (kickoff same day) | `leovegas` only |
| Coritiba vs Palmeiras (kickoff next day) | `leovegas` only |
| Wolves vs Blackburn (days out) | none |
| Inter Miami vs Chicago Fire (days out) | none |

Pinnacle (`eu` region) **did** return both `alternate_totals_cards` (6
outcomes) and `alternate_totals_corners` (10 outcomes) when requested by
bookmaker key.

**Caveats on the above:** 4 fixtures, mostly one league (Brazil), in a European
off-season week. Markets clearly appear only near kickoff — the two fixtures
several days out returned nothing from any book. This is enough to say Paddy
Power corners are not reliably available; it is **not** enough to say they never
appear. Re-sample during an EPL matchweek before concluding.

### Consequence: the Cards/Corners tool never worked

`cards/client.py` requests `alternate_totals` (which is *goal* totals, not
cards or corners) from the **bulk** endpoint — the exact combination the API
rejects with `INVALID_MARKET`. So `CardsApiClient` has been receiving an error
payload and yielding no rows for its whole life.

No user impact: the tool is gated off (`ENABLE_CARDS=False`). But if it is ever
re-enabled it needs, at minimum:
1. the per-event endpoint, not the bulk client;
2. the real market keys (`alternate_totals_corners` / `alternate_totals_cards`);
3. a bookmaker other than Paddy Power on the value side, or acceptance that
   most fixtures will have no counterparty;
4. a paid tier — see the cost note above.
