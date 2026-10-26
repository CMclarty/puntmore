"""Paddy Power's "most corners" / "most cards" three-ways on a match page,
for POST /api/paddy/most-prices.

NOT WRITTEN: the samples hold no priced "most" market. What the 18 Oct 2026
run (data/samples/20261018-172951, three EPL match pages with payloads) shows:

- Prices arrive in `getMarketPrices` (smp.paddypower.com/.../readonly/v1/
  getMarketPrices): a list of {marketId, marketStatus, runnerDetails:
  [{selectionId, winRunnerOdds: {trueOdds: {decimalOdds: ...}}}]}. NO market
  name, type or runner name - those come from `event-page/v5`
  (apisms.paddypower.com/smspp/event-page/v5), `attachments.markets`, keyed
  by marketId, with marketType, marketName and runners[].runnerName.
- Paddy calls the markets CORNERS_MATCH_BET ("Corner Match Bet", Corners tab)
  and TEAM_TO_RECEIVE_THE_MOST_CARDS ("Team To Receive The Most Cards", the
  "Cards & Fouls" tab). Both appear ONLY as names in the page layout (the
  event page's tab card list and the pub/v7 MarketCard titles). Neither
  market's object - id, runners, prices - is in any captured payload: the
  event page is fetched once, for the default tab, and every getMarketPrices
  market id is either named there or not a three-runner market.

So the tab clicks did not bring these markets in, or brought them in by a
response the capture does not keep. Next step: with the market enabled on
the /scraper page, let it capture, then look for CORNERS_MATCH_BET in
data/samples/ and write this from that payload (and a trimmed fixture under
tests/fixtures/).

Row shape the endpoint takes:
    {"home": "Arsenal", "away": "Chelsea", "kickoff": "2026-10-18T15:00:00Z",
     "market": "most_corners" | "most_cards",
     "home_price": 2.0, "tie_price": 7.0, "away_price": 2.4}
"""


def pp_most(page, capture) -> list[dict]:
    raise NotImplementedError("pp_most: no priced most-corners/cards market captured yet")
