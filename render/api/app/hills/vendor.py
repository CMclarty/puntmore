"""Which odds vendor Hills prices on (Callum, 11 Nov 2026).

ODDSAPINET_API_KEY set: odds-api.net, William Hill and Pinnacle in one
snapshot per match (cards/oddsapinet.py), on its own client and daily credit
budget. Unset: OddsPapi, as before, on the SHARED OddsPapi client (one budget,
one rate limit), which stays a running subscription for Paddy, 2-Up and the
coupon tracker either way. Neither: no Hills source, and /api/ready says so.
"""
from __future__ import annotations

from ..config import settings


def build_hills_source(oddsapinet_key: str, oddspapi_client) -> object | None:
    if oddsapinet_key:
        from ..cards.oddsapinet import OddsApiNetHillsSource
        from ..cards.oddsapinet_client import OddsApiNetClient
        client = OddsApiNetClient(oddsapinet_key,
                                  daily_budget=settings.oddsapinet_daily_credit_budget)
        return OddsApiNetHillsSource(client)
    if oddspapi_client is not None:
        from ..cards.oddspapi import WILLIAM_HILL, OddsPapiPaddySource
        from .target import EXTRA_TOURNAMENTS
        return OddsPapiPaddySource(oddspapi_client, book=WILLIAM_HILL,
                                   extra_tournaments=EXTRA_TOURNAMENTS)
    return None
