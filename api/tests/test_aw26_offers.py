"""AW 26 offer maths — pinned to the SAME worked cases as the frontend's
accaInsurance.test.js and accaBoost.test.js, so the two implementations of
the offers cannot drift apart unnoticed."""
import pytest

from app.aw26.offers import expected_value, problems, settle


@pytest.mark.parametrize("o, r, ev_pct, ev_gbp", [
    # odds, acca %, EV %, EV £ on £50 — the insurance offer's test cases
    (1.30, 1.00, 30.3, 15.15),
    (1.25, 1.00, 30.7, 15.36),
    (1.50, 1.10, 35.6, 17.81),
    (1.40, 0.90, 17.0, 8.49),
])
def test_refund_matches_the_offers_worked_cases(o, r, ev_pct, ev_gbp):
    fair = [o / r ** (1 / 5)] * 5               # p_i = r^(1/n) / o
    ev = expected_value("refund", 50, [o] * 5, fair)
    assert round(100 * ev / 50, 1) == ev_pct
    assert ev == pytest.approx(ev_gbp, abs=0.05)


@pytest.mark.parametrize("n, ev_pct", [(4, 5.9), (5, 13.4), (6, 22.1)])
def test_boost_matches_the_offers_worked_cases(n, ev_pct):
    ev = expected_value("boost", 100, [1.25] * n, [1.25] * n)
    assert round(ev, 1) == ev_pct


def test_the_refund_has_no_cap():
    # Callum, Oct 2026: as many £50 slips as wanted — EV is linear in stake.
    small = expected_value("refund", 50, [1.3] * 5, [1.3] * 5)
    assert expected_value("refund", 500, [1.3] * 5, [1.3] * 5) == pytest.approx(10 * small)


def test_a_slip_off_its_offers_terms_is_valued_without_the_offer():
    four = [1.3] * 4
    assert problems("refund", four) == ["refund needs exactly 5 legs (has 4)"]
    # Fair odds and no refund: worth exactly nothing either way.
    assert expected_value("refund", 50, four, four) == pytest.approx(0)
    short = [1.15, 1.3, 1.3, 1.3, 1.3]
    assert problems("boost", short) == ["leg 1 under 1.20"]
    assert expected_value("boost", 50, short, short) == pytest.approx(0)
    assert problems("boost", [1.3] * 3) == ["no boost for a 3-fold"]


def test_other_is_a_straight_bet():
    assert expected_value("other", 10, [2.2], [2.0]) == pytest.approx(1.0)


def test_refund_settles_on_exactly_one_loser():
    legs = [(1.3, "won")] * 4
    assert settle("refund", 50, legs + [(1.3, "lost")]) == ("refunded", 37.5)
    assert settle("refund", 50, legs[:3] + [(1.3, "lost"), (1.3, "lost")]) == ("lost", 0.0)
    # One down and one to play: still alive either way.
    assert settle("refund", 50, legs[:3] + [(1.3, "lost"), (1.3, "pending")]) == ("open", None)
    assert settle("refund", 50, [(1.3, "won")] * 5) == ("won", round(50 * 1.3 ** 5, 2))


def test_boost_and_other_end_on_the_first_loser():
    assert settle("boost", 50, [(1.3, "lost")] + [(1.3, "pending")] * 3) == ("lost", 0.0)
    assert settle("other", 10, [(2.0, "pending")]) == ("open", None)


def test_boost_pays_boosted_winnings_at_the_tier_of_the_legs_that_stood():
    status, ret = settle("boost", 100, [(1.5, "won")] * 5)
    assert status == "won" and ret == round(100 * (1 + (1.5 ** 5 - 1) * 1.2), 2)
    # A void drops a 5-fold to a 4-fold: 10%, on the four that stood.
    status, ret = settle("boost", 100, [(1.5, "won")] * 4 + [(1.5, "void")])
    assert ret == round(100 * (1 + (1.5 ** 4 - 1) * 1.1), 2)


def test_a_void_keeps_a_refund_slip_insured_and_all_void_returns_the_stake():
    legs = [(1.3, "won")] * 3 + [(1.3, "void"), (1.3, "lost")]
    assert settle("refund", 50, legs) == ("refunded", 37.5)
    assert settle("other", 20, [(2.0, "void")]) == ("void", 20.0)
