"""Team Casino's reload-offers dashboard, read from the real page (13 Nov
2026, signed in; cut into fixtures/teamcasino/dashboard.html). The parser
reads the page's HTML and nothing else: the stand-in page below answers
`content()` and `url` and fails on anything else, so a click (or any other
call on the page) fails the test."""
import pathlib
import re

from scraper.parsers import PARSERS
from scraper.parsers.tc_reload import tc_reload

FIX = pathlib.Path(__file__).parent / "fixtures" / "teamcasino"
PAGE = (FIX / "dashboard.html").read_text(encoding="utf-8")
URL = "https://www.teamcasino.com/reload-offers-dashboard"

PP25 = "SuJWgIXXJHHg12ZXqsbkNt"          # Paddy Power - Wager £25 - Get £5 Bonus
BEAT = "uo8eT9bYXRdJGjXue7AswQ416"       # Paddy Power Beat the Drop
SPINUMO = "McBDK8PQqhx4pYD7E9mFjF254"    # Casumo Spinumo


class _Page:
    """content() and url only: any other call (a click, a locator) fails."""

    def __init__(self, html, url=URL):
        self._html = html
        self.url = url

    def content(self):
        return self._html

    def __getattr__(self, name):
        raise AssertionError(f"the parser touched page.{name}")


def _read(html=PAGE):
    return tc_reload(_Page(html), None)


def _by_id(rows):
    return {r["offer_id"]: r for r in rows}


def _table(offer_id, html=PAGE):
    """Where the offer block's own markup starts and ends (a table in its
    plan nests inside it)."""
    start = html.index(f'<table id="{offer_id}"')
    depth = 0
    for m in re.compile(r"<(/?)table\b[^>]*>").finditer(html, start):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return start, m.end()
    raise AssertionError("unclosed table")


def _edit_block(offer_id, old, new, html=PAGE):
    start, end = _table(offer_id, html)
    block = html[start:end]
    assert old in block, old
    return html[:start] + block.replace(old, new, 1) + html[end:]


def test_registered_for_targets():
    assert PARSERS["tc_reload"] is tc_reload


def test_every_offer_block_is_a_row_in_page_order_under_its_section():
    rows = _read()
    assert [r["offer_id"] for r in rows] == [
        SPINUMO, BEAT, "X7irBjoezZeePA3Zo1GDK1104", "u3y8AX5IrVcQppONPhJ4Mt",
        "pMviwXREnKvf79TwpcFAdD416", PP25, "j3N1PsOgiOruV59UTuJTuV", "8z3HBF179uq9Gse2uIi4oW",
        "Vddo8G8cZQYtIbGStgnztl", "5516cQeE0UYsuBUFZKA2kt100", "8jV9ZmquzpIdm76gELe7Qq",
        "YXTp6QpvYzMcS5hpNZRoTT"]
    assert [r["section"] for r in rows] == (["Daily Free Games"] * 2 + ["Risk Free"] * 3
                                            + ["Low Risk"] * 4 + ["Medium Risk"] * 2
                                            + ["Medium to High Risk"])
    assert rows[0]["section_key"] == "daily-free-games"
    assert all(r["flags"] == [] for r in rows)       # nothing hidden, pending, avoided or starred


def test_a_wager_get_bonus_offer_in_full():
    row = _by_id(_read())[PP25]
    assert row == {
        "offer_id": PP25,
        "casino_id": 37,
        "casino": "Paddy Power Games",
        "section": "Low Risk",
        "section_key": "low-risk",
        "title": "Paddy Power - Wager £25 - Get £5 Bonus",
        "ev": 4.88,
        "ev_per_hour": 36.6,
        "bankroll": 250.0,
        "minutes": 8.0,
        "expires_at": 1795046400,                     # Thu 19 Nov 2026 00:00 UTC
        "expires_text": "Expires: Thu 19th",
        "steps": [
            {"heading": "1) Opt In?", "text": "Opt in through the link below."},
            {"heading": "2) Wagering",
             "text": "- Wager £25 on eligible Live Casino games, using low stakes to reduce the variance.\n"
                     "- I recommend a high RTP game such as Paddy Power One Blackjack (99.59% RTP) or "
                     "Paddy Power Live Speed Baccarat (98.94% RTP on Banker)."},
            {"heading": "3) Bonus",
             "text": "You will then receive a £5 bonus which must must be wagered once on the same games "
                     "within 3 days."},
        ],
        "notes": [],
        "plan_text": "1) Opt In?\nOpt in through the link below.\n2) Wagering\n"
                     "- Wager £25 on eligible Live Casino games, using low stakes to reduce the variance.\n"
                     "- I recommend a high RTP game such as Paddy Power One Blackjack (99.59% RTP) or "
                     "Paddy Power Live Speed Baccarat (98.94% RTP on Banker).\n3) Bonus\n"
                     "You will then receive a £5 bonus which must must be wagered once on the same games "
                     "within 3 days.",
        "links": [{"text": "GO TO OFFER",
                   "url": "https://href.li/?https://promos.paddypower.com/promotion?promoCode=GAMOLCBX141126"}],
        "games": [{"name": "Paddy Power One Blackjack", "rtp": 99.59},
                  {"name": "Paddy Power Live Speed Baccarat", "rtp": 98.94, "note": "on Banker"}],
        "flags": [],
    }


def test_the_casino_is_the_casino_list_name_for_the_blocks_casino_id_never_data_casino_name():
    """Every block's data-casino-name says "YeeeHaaa" (the last option of the
    page's "Select casino site" list); its data-casino-id names the casino
    the logo and the title show."""
    assert 'data-casino-name="YeeeHaaa"' in PAGE[PAGE.index(f'<table id="{BEAT}"'):]
    rows = _by_id(_read())
    assert (rows[BEAT]["casino_id"], rows[BEAT]["casino"]) == (39, "Paddy Power")
    assert rows[SPINUMO]["casino"] == "Casumo"
    assert rows["5516cQeE0UYsuBUFZKA2kt100"]["casino"] == "888 Casino"
    assert not any("YeeeHaaa" in str(r) for r in rows.values())


def test_a_casino_id_missing_from_the_list_gives_the_id_and_no_name():
    html = _edit_block(BEAT, 'data-casino-id="39"', 'data-casino-id="99999"')
    row = _by_id(_read(html))[BEAT]
    assert row["casino_id"] == 99999 and "casino" not in row


def test_steps_with_no_heading_keep_their_text_and_empty_numbered_steps_are_dropped():
    rows = _by_id(_read())
    assert rows[SPINUMO]["steps"] == [
        {"heading": None, "text": "- Spin the Spinumo Wheel.\n- If you receive no win, you can make a "
                                  "deposit to receive a second spin on the wheel."},
        {"heading": None, "text": "Prizes include:\nFree spins\nCasino Rewards"},
        {"heading": None, "text": "Please contact us if you receive a prize and are unsure as to how "
                                  "to complete it."}]
    goodwin = rows["pMviwXREnKvf79TwpcFAdD416"]        # steps "2)".."5)" are empty on the page
    assert [s["heading"] for s in goodwin["steps"]] == ["1) Free Spins"]
    assert goodwin["notes"] == []                       # its two note boxes hold only a <br>


def test_note_boxes_are_notes_and_part_of_the_plan_text():
    rows = _by_id(_read())
    assert rows[BEAT]["notes"] == ["Answer questions each day for the chance to win up to £5,000."]
    assert rows[BEAT]["plan_text"].startswith("Answer questions each day for the chance to win up to "
                                              "£5,000.\nLog in each day and play the daily game.")
    club = rows["8jV9ZmquzpIdm76gELe7Qq"]
    assert club["notes"][0].startswith("I recommend not completing the wagering for this offer until "
                                       "the weekend,")
    assert "Wager £10 Get 5 Free Spins £0.29 EV\nWager £20 Get 10 Free Spins £0.58 EV\n" in club["notes"][0]


def test_a_table_in_the_plan_keeps_its_cells_apart():
    chips = _by_id(_read())["Vddo8G8cZQYtIbGStgnztl"]
    wagering = chips["steps"][1]
    assert wagering["heading"] == "2) Wagering"
    assert wagering["text"] == (
        "Play various amounts on eligible live casino games. Stake will depend on the game.\n"
        "Slot Wagering | Reward | Estimated Value\n"
        "20 x £0.10 rounds on Mega Fire Blaze Lucky Ball Live (97.32% RTP) | "
        "10 Golden Chips valued at £0.10 each | £0.92")
    assert chips["games"] == [{"name": "Mega Fire Blaze Lucky Ball Live", "rtp": 97.32}]
    assert chips["steps"][3]["heading"] is None         # a fourth block with no heading


def test_links_are_every_link_in_the_plan_then_go_to_offer():
    rows = _by_id(_read())
    assert rows[SPINUMO]["links"] == [
        {"text": "contact us", "url": "https://www.teamcasino.com/contact-us"},
        {"text": "GO TO OFFER", "url": "https://href.li/?https://www.casumo.com/en-gb/"}]
    assert rows[BEAT]["links"][0] == {"text": "Betfair Beat The Drop",
                                      "url": "https://href.li/?https://beatthedrop.betfair.com/"}
    club = rows["8jV9ZmquzpIdm76gELe7Qq"]
    assert club["links"][0] == {"text": "Paddy Rewards Club",
                                "url": "https://href.li/?https://promos.paddypower.com/promotion?promoCode=PPREWARDSGAM"}


def test_a_relative_link_is_made_absolute_against_the_page():
    html = _edit_block(SPINUMO, 'href="https://www.teamcasino.com/contact-us"', 'href="/contact-us"')
    assert _by_id(_read(html))[SPINUMO]["links"][0]["url"] == "https://www.teamcasino.com/contact-us"


def test_games_are_only_what_names_an_rtp_in_brackets():
    rows = _by_id(_read())
    assert rows["j3N1PsOgiOruV59UTuJTuV"]["games"] == [
        {"name": "Blackjack Classic", "rtp": 99.59}, {"name": "Big Bass Vegas 1000", "rtp": 94.52}]
    assert rows["8jV9ZmquzpIdm76gELe7Qq"]["games"] == [
        {"name": "Mystery Reels Megaways", "rtp": 98.06}, {"name": "Eye of Horus", "rtp": 96.31}]
    assert rows["5516cQeE0UYsuBUFZKA2kt100"]["games"] == [
        {"name": "Eruption Blaze CASHINGO Rumble", "rtp": 90.0}]
    assert rows["pMviwXREnKvf79TwpcFAdD416"]["games"] == [{"name": "Chaos Crew Scratch", "rtp": 60.12}]
    # "All Bets Blackjack Live" is recommended with no RTP given: not a game here
    assert rows["YXTp6QpvYzMcS5hpNZRoTT"]["games"] == []
    assert rows[SPINUMO]["games"] == []


def test_a_game_name_in_bold_just_before_its_bracket_is_read():
    html = _edit_block("j3N1PsOgiOruV59UTuJTuV", "<b>Blackjack Classic (99.59% RTP)</b>",
                       "<b>Blackjack Classic</b> (99.59% RTP)")
    assert _by_id(_read(html))["j3N1PsOgiOruV59UTuJTuV"]["games"][0] == {"name": "Blackjack Classic",
                                                                          "rtp": 99.59}


def test_bold_runs_with_only_a_space_between_are_one_name():
    """As the page draws "<b>Bigger Bass Splash Big Bass Jackpot</b> <b>Bonanza
    (92% RTP including Jackpot)</b>" (Midnite, 13 Nov): one bold name on screen."""
    html = _edit_block("j3N1PsOgiOruV59UTuJTuV", "<b>Blackjack Classic (99.59% RTP)</b>",
                       "<b>Blackjack</b> <b>Classic (99.59% RTP)</b>")
    assert _by_id(_read(html))["j3N1PsOgiOruV59UTuJTuV"]["games"][0] == {"name": "Blackjack Classic",
                                                                          "rtp": 99.59}


def test_an_rtp_with_no_name_set_apart_is_not_a_game():
    html = _edit_block("j3N1PsOgiOruV59UTuJTuV", "<b>Blackjack Classic (99.59% RTP)</b>",
                       "Blackjack Classic (99.59% RTP)")
    assert _by_id(_read(html))["j3N1PsOgiOruV59UTuJTuV"]["games"] == [
        {"name": "Big Bass Vegas 1000", "rtp": 94.52}]


def test_promo_codes_only_where_the_plan_says_promo_code():
    rows = _by_id(_read())
    assert rows["8z3HBF179uq9Gse2uIi4oW"]["promo_code"] == "VEGAS50"      # Fitzwilliam
    assert rows["u3y8AX5IrVcQppONPhJ4Mt"]["promo_code"] == "FREE5"        # William Hill bingo
    assert rows["5516cQeE0UYsuBUFZKA2kt100"]["promo_code"] == "BLAZE30"   # 888casino, "BLAZE30."
    others = [r for k, r in rows.items() if k not in ("8z3HBF179uq9Gse2uIi4oW", "u3y8AX5IrVcQppONPhJ4Mt",
                                                      "5516cQeE0UYsuBUFZKA2kt100")]
    assert all("promo_code" not in r for r in others)   # nor "promoCode=" in a link


def test_expiry_text_is_verbatim_and_the_epoch_is_the_attribute():
    rows = _by_id(_read())
    club = rows["8jV9ZmquzpIdm76gELe7Qq"]
    assert (club["expires_text"], club["expires_at"]) == ("Weekly: Mon-Sun", 1794700800)
    boyle = rows["X7irBjoezZeePA3Zo1GDK1104"]
    assert (boyle["expires_text"], boyle["expires_at"]) == ("Daily Until Sunday", 1794528000)
    assert rows[SPINUMO]["expires_text"] == "Daily Until Further Notice"


def test_an_expiry_epoch_that_is_not_sane_is_left_out():
    for bad in ('data-offer-expires=""', 'data-offer-expires="soon"', 'data-offer-expires="0"',
                'data-offer-expires="99999999999"'):
        row = _by_id(_read(_edit_block(PP25, 'data-offer-expires="1795046400"', bad)))[PP25]
        assert "expires_at" not in row and row["expires_text"] == "Expires: Thu 19th"


def test_an_expiry_epoch_on_another_day_than_the_text_names_is_left_out():
    # "Expires: Thu 19th" with the epoch of Sun 15th: one of them is wrong
    row = _by_id(_read(_edit_block(PP25, 'data-offer-expires="1795046400"',
                                   'data-offer-expires="1794700800"')))[PP25]
    assert "expires_at" not in row


def test_a_block_missing_a_field_gives_the_row_without_it():
    html = _edit_block(PP25, 'data-ev-amount="4.88"', "")
    html = _edit_block(PP25, 'data-bank-amount="250"', 'data-bank-amount="lots"', html)
    row = _by_id(_read(html))[PP25]
    assert "ev" not in row and "bankroll" not in row
    assert row["ev_per_hour"] == 36.6 and row["minutes"] == 8.0
    html = _edit_block(PP25, '<div class="expires-orange">', '<div class="no-expiry">')
    assert "expires_text" not in _by_id(_read(html))[PP25]


def test_a_block_with_no_title_is_skipped_and_counted():
    html = _edit_block(PP25, '<h5 class="offer-title"', '<h5 class="not-a-title"')
    rows = _read(html)
    assert len(rows) == 11 and PP25 not in _by_id(rows)
    assert "1 offer block with no title skipped" in rows.said


def test_a_block_with_no_offer_id_gets_a_key_from_casino_and_title():
    html = _edit_block(PP25, f'data-offer-id="{PP25}" ', "")
    html = html.replace(f'<table id="{PP25}"', "<table", 1)
    rows = _read(html)
    keys = [r["offer_id"] for r in rows]
    assert "Paddy Power Games|Paddy Power - Wager £25 - Get £5 Bonus" in keys


def test_the_counter_compared_with_the_rows_read():
    rows = _read()                                       # the cut page still says 121
    assert len(rows) == 12
    assert rows.said == "the page says 121 offers available, 12 read"
    agreed = PAGE.replace('<span class="offers-total-count">121</span>',
                          '<span class="offers-total-count">12</span>')
    assert _read(agreed).said is None


def test_flags_as_the_pages_own_controls_leave_a_block():
    """Hide moves a block into the Hidden section with data-offerwrap
    "hidden" (the page's hideOffer()); Pending and Avoid are sections the
    same way (loadOffers()); the star is data-status "selected"
    (favouriteOffer()). Not seen set on the saved page: built here as the
    page's script would leave them. A hidden or avoided offer is not in the
    page's own count (leftOffers())."""
    html = _edit_block(PP25, 'data-offerwrap="low-risk"', 'data-offerwrap="hidden"')
    html = _edit_block(BEAT, 'data-offerwrap="daily-free-games"', 'data-offerwrap="avoid"', html)
    html = _edit_block("8z3HBF179uq9Gse2uIi4oW", 'data-offerwrap="low-risk"', 'data-offerwrap="pending"', html)
    html = _edit_block(SPINUMO, 'data-status="not-selected"', 'data-status="selected"', html)
    html = html.replace('<span class="offers-total-count">121</span>',
                        '<span class="offers-total-count">10</span>')
    rows = _read(html)
    by = _by_id(rows)
    assert len(rows) == 12                               # flagged offers are kept, not dropped
    assert by[PP25]["flags"] == ["hidden"] and by[PP25]["section"] == "Low Risk"
    assert by[BEAT]["flags"] == ["avoid"] and by[BEAT]["section"] == "Daily Free Games"
    assert by["8z3HBF179uq9Gse2uIi4oW"]["flags"] == ["pending"]
    assert by[SPINUMO]["flags"] == ["favourite"]
    assert rows.said is None                             # 10 = 12 less the hidden and the avoided


def test_the_message_the_form_and_the_charts_are_not_offers():
    rows = _read()
    titles = {r["title"] for r in rows}
    assert "BingoPlus - Deposit + Wager £10 - Get £5 Bonus" not in titles   # only in "Today's Top Offers"
    assert not any(r["offer_id"].startswith("add-custom") for r in rows)


def test_a_page_still_loading_gives_no_rows_and_says_so():
    """The offers drawn in later: the loading notice showing, no block yet."""
    start = PAGE.index('<div id="offers-main-wrap"')
    loading = (PAGE[:start].replace('<div id="offers-loading-icon" style="display: none;">',
                                    '<div id="offers-loading-icon" style="">')
               + '<div id="offers-main-wrap" style="display: none;"></div></body></html>')
    rows = _read(loading)
    assert rows == [] and rows.said == "still loading: Searching for most profitable offers.."


def test_a_page_with_no_offer_blocks_gives_no_rows_and_says_so():
    rows = _read((FIX / "logged_out.html").read_text(encoding="utf-8"))
    assert rows == [] and rows.said == "no offer blocks on the page"
    assert tc_reload(_Page(""), None).said == "no offer blocks on the page"


def test_a_teamcasino_job_with_no_post_reads_counts_and_hands_nothing_over(tmp_path, monkeypatch):
    """No `post`: the rows are read and counted (the status says how many),
    kept in data/latest/<job>.json as every parse is, and handed to nobody.
    The page's note is a line in the job's log once, not every minute."""
    import json
    import time

    from scraper import browser, config, joblog
    from scraper import runner as runner_mod
    from scraper.tests.test_pacing import _runner

    monkeypatch.setattr(browser, "logged_out", lambda *a, **k: False)
    monkeypatch.setitem(runner_mod.PARSERS, "tc_reload", lambda page, cap: tc_reload(_Page(PAGE), cap))
    t = config.Target(name="tc", site="teamcasino", url=URL, stale_min=10_000, parser="tc_reload")
    r = _runner(tmp_path, [t])
    t0 = time.time()
    r.tick(t0)
    r.tick(t0 + 61)
    st = r.status(t0 + 61)["targets"][0]
    assert st["state"] == "running" and st["rows_posted"] == 0
    assert "12 rows read, sent nowhere (no post)" in st["detail"]
    assert "the page says 121 offers available, 12 read" in st["detail"]
    assert len(json.loads((tmp_path / "latest" / "tc.json").read_text())["rows"]) == 12
    assert [p for p in (tmp_path / "hub").rglob("*") if p.is_file()] == []    # no drop for the Hub
    said = [x["msg"] for x in joblog.read(tmp_path, "tc", n=50)
            if "the page says" in x["msg"]]
    assert said == ["12 rows read, sent nowhere (no post); the page says 121 offers available, 12 read"]
