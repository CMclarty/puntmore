"""Paddy Power's "most corners" / "most cards" three-ways on a match page,
for POST /api/paddy/most-prices.

Written from the 29 Oct 2026 capture (Chelsea v Man Utd, Corners and Cards &
Fouls tabs clicked; trimmed into tests/fixtures/pp_most/):

- Clicking a market tab makes the page fetch `event-page/v5`
  (apisms.paddypower.com/smspp/event-page/v5). Its `attachments.events`
  holds the match ({eventId, name: "Chelsea v Man Utd", openDate}) and its
  `attachments.markets` the tab's markets, keyed by marketId, each with
  marketType, marketStatus and runners[] ({selectionId, runnerName,
  runnerStatus, winRunnerOdds.trueOdds.decimalOdds.decimalOdds}).
- The two markets are CORNERS_MATCH_BET ("Corner Match Bet": home / Draw /
  away) and TEAM_TO_RECEIVE_THE_MOST_CARDS (home / away / Tie).
- After that the page polls `getMarketPrices` (smp.paddypower.com/.../
  readonly/v1/getMarketPrices): a list of {marketId, marketStatus,
  runnerDetails: [{selectionId, runnerStatus, winRunnerOdds...}]} - prices
  only, no names. It polls only the markets of the tab on screen: once Cards
  & Fouls is clicked, Corners is no longer refreshed. So a price is sent only
  while it is fresh (PRICE_FRESH_S), and the runner clicks the market tabs
  again now and then (runner.CLICK_EVERY_S) to bring each back.

Names and kickoff come from the event-page; the newest price for each
selection wins, whichever payload it came in. trueOdds is the fractional
price Paddy pays (8/15 = 1.5333), not the rounded display (1.53).
"""
import json
import time

PRICE_FRESH_S = 15 * 60
MARKETS = {"CORNERS_MATCH_BET": "most_corners",
           "TEAM_TO_RECEIVE_THE_MOST_CARDS": "most_cards"}
TIE_NAMES = {"draw", "tie"}


def _odds(runner) -> float | None:
    try:
        return float(runner["winRunnerOdds"]["trueOdds"]["decimalOdds"]["decimalOdds"])
    except (KeyError, TypeError, ValueError):
        return None


def _payloads(capture):
    """Every kept payload, oldest first: the ring plus the pins (the
    event-page a click brought in may have rolled out of the ring)."""
    seen = {(r[0], r[2]): r[3] for r in capture.pinned}
    seen.update({(r[0], r[2]): r[3] for r in capture.recent})
    return [(ts, url, text) for (ts, url), text in sorted(seen.items(), key=lambda kv: kv[0][0])]


def pp_most(page, capture, now=None) -> list[dict]:
    now = now or time.time()
    events: dict[str, dict] = {}       # eventId -> the event
    markets: dict[str, dict] = {}      # marketId -> {market, event, open, runners}
    prices: dict[tuple, tuple] = {}    # (marketId, selectionId) -> (ts, price)

    def price(mid, runner, ts):
        p = _odds(runner)
        if p is not None and runner.get("runnerStatus", "ACTIVE") == "ACTIVE":
            prices[(mid, runner.get("selectionId"))] = (ts, p)
        else:
            prices.pop((mid, runner.get("selectionId")), None)

    for ts, _url, text in _payloads(capture):
        if '"attachments"' not in text and '"runnerDetails"' not in text:
            continue
        try:
            body = json.loads(text)
        except ValueError:
            continue
        if isinstance(body, dict) and isinstance(body.get("attachments"), dict):
            att = body["attachments"]
            for eid, ev in (att.get("events") or {}).items():
                events[str(eid)] = ev
            for mid, m in (att.get("markets") or {}).items():
                kind = MARKETS.get(m.get("marketType"))
                if not kind:
                    continue
                markets[mid] = {"market": kind, "event": str(m.get("eventId")),
                                "open": m.get("marketStatus") == "OPEN",
                                "runners": {r.get("selectionId"): r.get("runnerName", "")
                                            for r in m.get("runners", [])}}
                for r in m.get("runners", []):
                    price(mid, r, ts)
        elif isinstance(body, list):
            for m in body:
                if not isinstance(m, dict) or m.get("marketId") not in markets:
                    continue
                markets[m["marketId"]]["open"] = m.get("marketStatus") == "OPEN"
                for r in m.get("runnerDetails", []):
                    price(m["marketId"], r, ts)

    rows = []
    for mid, m in markets.items():
        ev = events.get(m["event"])
        if not m["open"] or not ev or " v " not in ev.get("name", ""):
            continue
        home, away = (s.strip() for s in ev["name"].split(" v ", 1))
        got = {}
        for sel, name in m["runners"].items():
            seen = prices.get((mid, sel))
            if seen is None or now - seen[0] > PRICE_FRESH_S:
                continue
            side = ("home" if name == home else "away" if name == away
                    else "tie" if name.lower() in TIE_NAMES else None)
            if side:
                got[side] = seen[1]
        if len(got) == 3:
            rows.append({"home": home, "away": away, "kickoff": ev.get("openDate"),
                         "market": m["market"], "home_price": round(got["home"], 4),
                         "tie_price": round(got["tie"], 4), "away_price": round(got["away"], 4)})
    return rows
