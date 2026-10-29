"""Paddy's most corners / most cards, read from the 29 Oct 2026 capture
(Chelsea v Man Utd, trimmed into fixtures/pp_most/)."""
import collections
import pathlib

from scraper.parsers.pp_most import PRICE_FRESH_S, pp_most

FIX = pathlib.Path(__file__).parent / "fixtures" / "pp_most"
EP = "https://apisms.paddypower.com/smspp/event-page/v5?eventId=36206188"
PRICES = "https://smp.paddypower.com/www/sports/fixedodds/readonly/v1/getMarketPrices"
T0 = 1_793_278_400.0


class _Cap:
    def __init__(self, payloads, pinned=()):
        self.recent = collections.deque(payloads)
        self.pinned = collections.deque(pinned)


def _payloads():
    return [(T0 + 0, "http", EP, (FIX / "1-event-page-corners.json").read_text()),
            (T0 + 1, "http", PRICES, (FIX / "2-prices-corners.json").read_text()),
            (T0 + 10, "http", EP, (FIX / "3-event-page-cards.json").read_text()),
            (T0 + 11, "http", PRICES, (FIX / "4-prices-cards.json").read_text())]


def _by_market(rows):
    return {r["market"]: r for r in rows}


def test_both_three_ways_are_read_with_names_kickoff_and_true_odds():
    rows = _by_market(pp_most(None, _Cap(_payloads()), now=T0 + 60))
    assert rows["most_corners"] == {
        "home": "Chelsea", "away": "Man Utd", "kickoff": "2026-10-31T12:30:00.000Z",
        "market": "most_corners", "home_price": 1.5333, "tie_price": 7.5, "away_price": 3.0}
    assert rows["most_cards"] == {
        "home": "Chelsea", "away": "Man Utd", "kickoff": "2026-10-31T12:30:00.000Z",
        "market": "most_cards", "home_price": 2.4, "tie_price": 4.3333, "away_price": 2.1}


def test_a_market_the_page_stopped_refreshing_goes_once_its_price_is_stale():
    """After Cards & Fouls is clicked the page polls only the cards market:
    Corners' last price ages out rather than being sent for hours."""
    later = T0 + 1 + PRICE_FRESH_S + 5
    assert set(_by_market(pp_most(None, _Cap(_payloads()), now=later))) == {"most_cards"}


def test_an_event_page_only_in_the_pins_still_names_the_markets():
    """The ring rolls over on a busy page; the click's pinned event-page
    still names the markets the later price polls carry."""
    p = _payloads()
    pinned = [(*p[0], "tab:Corners"), (*p[2], "tab:Cards & Fouls")]
    rows = pp_most(None, _Cap([p[1], p[3]], pinned), now=T0 + 60)
    assert set(_by_market(rows)) == {"most_corners", "most_cards"}


def test_a_suspended_market_is_not_sent():
    p = _payloads()
    p[3] = (p[3][0], "http", PRICES, p[3][3].replace('"marketStatus": "OPEN"', '"marketStatus": "SUSPENDED"'))
    assert "most_cards" not in _by_market(pp_most(None, _Cap(p), now=T0 + 60))


def test_nothing_captured_is_no_rows():
    assert pp_most(None, _Cap([]), now=T0) == []
