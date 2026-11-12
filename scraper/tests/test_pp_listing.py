"""Kick-off times off a Paddy Power listing (parsers/pp_listing.py), read in a
headless browser from the fixture cut from the real page of 12 Nov 2026. The
page is served from a stand-in host inside the browser: no request leaves it."""
import os
import pathlib
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from scraper.parsers import pp_listing

LONDON = ZoneInfo("Europe/London")
FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "pp_listing" / "epl.html").read_text()
BASE = "http://127.0.0.1:9"
LISTING = BASE + "/football/english-premier-league"
NOW = datetime(2026, 11, 12, 10, 52, tzinfo=LONDON).timestamp()     # when the page was saved


def _at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=LONDON).timestamp()


@pytest.fixture
def show():
    """show(html) -> a page holding that html at LISTING, as a listing tab would."""
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = {"headless": True}
        if os.environ.get("SCRAPER_CHROME_PATH"):
            kw["executable_path"] = os.environ["SCRAPER_CHROME_PATH"]
        browser = p.chromium.launch(**kw)
        ctx = browser.new_context()
        body = {"html": FIXTURE}
        ctx.route(BASE + "/**", lambda route: route.fulfill(status=200, body=body["html"],
                                                             content_type="text/html"))
        page = ctx.new_page()

        def go(html=FIXTURE):
            body["html"] = html
            page.goto(LISTING)
            return page
        yield go
        browser.close()


def test_every_fixture_on_the_real_listing_has_its_kickoff(show):
    k = pp_listing.kickoffs(show(), NOW)
    league = BASE + "/football/english-premier-league/"
    assert k == {
        league + "man-city-v-fulham-36293092": _at(2026, 11, 21, 12, 30),
        league + "aston-villa-v-sunderland-36294872": _at(2026, 11, 21, 15, 0),
        league + "bournemouth-v-nottm-forest-36292923": _at(2026, 11, 21, 15, 0),
        league + "chelsea-v-leeds-36294873": _at(2026, 11, 21, 15, 0),
        league + "coventry-v-crystal-palace-36294871": _at(2026, 11, 21, 15, 0),
        league + "tottenham-v-ipswich-36293779": _at(2026, 11, 21, 15, 0),
        league + "newcastle-v-arsenal-36293556": _at(2026, 11, 21, 17, 30),
        league + "hull-v-brighton-36296140": _at(2026, 11, 22, 14, 0),
        league + "liverpool-v-man-utd-36225147": _at(2026, 11, 22, 16, 30),
        league + "brentford-v-everton-36303120": _at(2026, 11, 23, 20, 0),
    }
    # London time is the time the page's own payload gives in UTC (GMT in November)
    assert k[league + "man-city-v-fulham-36293092"] == datetime(2026, 11, 21, 12, 30,
                                                                  tzinfo=ZoneInfo("UTC")).timestamp()


def test_the_urls_are_the_ones_match_links_gives(show):
    from scraper.runner import match_links
    page = show()
    links = match_links(page, r"-v-", LISTING)
    assert links and set(links) == set(pp_listing.kickoffs(page, NOW))   # the nations-league link is not one


def test_fixtures_carry_the_names_the_listing_gives(show):
    f = pp_listing.fixtures(show(), NOW)
    assert f[BASE + "/football/english-premier-league/liverpool-v-man-utd-36225147"].name == "Liverpool v Man Utd"
    assert len(f) == 10


def test_a_day_in_next_month(show):
    # "Monday 23" written as Paddy would write 1 December, read on Sat 28 Nov
    html = FIXTURE.replace("<span>Monday 23</span>", "<span>Tuesday 1</span>")
    k = pp_listing.kickoffs(show(html), _at(2026, 11, 28, 9, 0))
    assert k[BASE + "/football/english-premier-league/brentford-v-everton-36303120"] == _at(2026, 12, 1, 20, 0)


def test_a_header_it_has_not_seen_gives_no_kickoff(show):
    html = FIXTURE.replace("<span>Sunday 22</span>", "<span>Tomorrow</span>")
    f = pp_listing.fixtures(show(html), NOW)
    league = BASE + "/football/english-premier-league/"
    assert f[league + "hull-v-brighton-36296140"].kickoff is None
    assert f[league + "liverpool-v-man-utd-36225147"].kickoff is None
    assert league + "hull-v-brighton-36296140" not in pp_listing.kickoffs(show(html), NOW)
    assert f[league + "brentford-v-everton-36303120"].kickoff == _at(2026, 11, 23, 20, 0)


def test_a_time_label_that_is_not_a_time_gives_no_kickoff(show):
    html = FIXTURE.replace('data-testid="line-footer-left-label">12:30<', 'data-testid="line-footer-left-label">HT<')
    k = pp_listing.kickoffs(show(html), NOW)
    assert BASE + "/football/english-premier-league/man-city-v-fulham-36293092" not in k and len(k) == 9


def test_a_page_with_no_fixture_card_has_none(show):
    assert pp_listing.fixtures(show("<html><body><a href='/football/a-v-b'>A v B</a></body></html>"), NOW) == {}


def test_a_page_that_cannot_be_read_is_empty():
    class Closed:
        def evaluate(self, js):
            raise RuntimeError("Target page, context or browser has been closed")
    assert pp_listing.fixtures(Closed(), NOW) == {}


@pytest.mark.parametrize("header, today, want", [
    ("Saturday 21", date(2026, 11, 12), date(2026, 11, 21)),
    ("Thursday 12", date(2026, 11, 12), date(2026, 11, 12)),       # today, written as a day
    ("Wednesday 11", date(2026, 11, 12), date(2026, 11, 11)),      # yesterday's, still listed
    ("Monday 1", date(2026, 12, 30), date(2027, 2, 1)),            # into the next year
    ("Friday 30", date(2026, 11, 12), None),                       # no Friday 30th within two months
    ("Today", date(2026, 11, 12), None),
    ("In-Play", date(2026, 11, 12), None),
    ("Popular Football Betting", date(2026, 11, 12), None),
])
def test_day_headers(header, today, want):
    assert pp_listing.day_date(header, today) == want


def test_the_name_falls_back_to_the_urls_slug():
    f = pp_listing.from_rows([{"url": LISTING + "/a-v-b-1#x", "day": "Saturday 21", "time": "15:00",
                               "home": "", "away": ""}], NOW)
    assert f == {LISTING + "/a-v-b-1": pp_listing.Fixture(LISTING + "/a-v-b-1", "a-v-b-1", _at(2026, 11, 21, 15, 0))}
