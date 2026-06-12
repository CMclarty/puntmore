"""Cards/corners client — fetches alternate_totals for Pinnacle and Paddy Power."""
from ..odds.client import TheOddsApiClient

SOCCER_SPORT_KEYS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
]

CARDS_MARKET_KEYS = ["alternate_totals"]

_BOOKMAKERS = "pinnacle,paddypower"


class CardsApiClient:
    def __init__(self, client: TheOddsApiClient):
        self._client = client

    def get_events(self) -> list[dict]:
        events = []
        for sport_key in SOCCER_SPORT_KEYS:
            for market_key in CARDS_MARKET_KEYS:
                try:
                    events.extend(
                        self._client.fetch(sport_key, market_key, _BOOKMAKERS)
                    )
                except Exception:
                    continue
        return events
