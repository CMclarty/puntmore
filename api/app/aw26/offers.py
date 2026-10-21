"""What a slip is worth, and what it paid — by the offer it was struck under.

The SAME maths as the Paddy page's offers (frontend/src/accaInsurance.js and
accaBoost.js), held here because a slip's EV must be fixed on the server when
its fairs arrive and its result settled overnight, with no browser open. The
tests pin both to the same worked cases as the frontend's.

Legs are independent; p_i = 1 / fair_i, the leg's true win chance.

  A      = ∏ o_i                          acca odds, void legs dropped
  P_win  = ∏ p_i
  P_one  = Σ_j (1 − p_j) ∏_{i≠j} p_i      exactly one loses

  refund  E = S·P_win·A + S·REFUND_VALUE·P_one      (only if the slip qualifies)
  boost   E = S·P_win·(1 + (A − 1)(1 + b))          b by fold size
  other   E = S·P_win·A

  EV £ = E − S

Terms, as Callum gave them for AW 26 (Oct 2026):
- REFUND: a 5-fold, every leg 1.20+, stake back as a bonus if EXACTLY one leg
  loses. A refund is worth 0.75 of the stake, and there is NO cap — the team
  places as many £50 slips as it likes, so the cap never binds.
- BOOST: winnings +10% on a 4-fold, +20% on a 5-fold (+30% on a 6-fold, per the
  terms confirmed for the Paddy page — `BOOST_TIERS`), every leg 1.20+.
- OTHER: anything else, valued as a straight bet.
A slip that does not meet its offer's terms is valued WITHOUT the offer and
says why — the refund or boost is never counted on a slip it would not pay on.
"""
from math import prod

REFUND_VALUE = 0.75
REFUND_LEGS = 5
MIN_LEG_ODDS = 1.20
BOOST_TIERS = {4: 0.10, 5: 0.20, 6: 0.30}

OFFERS = ("refund", "boost", "other")


def _p_exactly_one(ps: list[float]) -> float:
    total = 0.0
    for j, pj in enumerate(ps):
        total += (1 - pj) * prod(p for i, p in enumerate(ps) if i != j)
    return total


def problems(offer: str, odds: list[float]) -> list[str]:
    """Why the slip does NOT get its offer (empty: it does)."""
    out = []
    if offer == "refund" and len(odds) != REFUND_LEGS:
        out.append(f"refund needs exactly {REFUND_LEGS} legs (has {len(odds)})")
    if offer == "boost" and len(odds) not in BOOST_TIERS:
        out.append(f"no boost for a {len(odds)}-fold")
    if offer in ("refund", "boost"):
        short = [i + 1 for i, o in enumerate(odds) if not o >= MIN_LEG_ODDS]
        if short:
            out.append(f"leg{'s' if len(short) > 1 else ''} {', '.join(map(str, short))} "
                       f"under {MIN_LEG_ODDS:.2f}")
    return out


def expected_value(offer: str, stake: float, odds: list[float], fair: list[float]) -> float:
    """EV in pounds. Every leg must carry a fair price — the caller checks, as a
    slip valued off the legs that DID price overstates it every time."""
    if len(odds) != len(fair) or not odds:
        raise ValueError("every leg needs its odds and its fair price")
    ps = [1.0 / f for f in fair]
    a = prod(odds)
    p_win = prod(ps)
    qualifies = not problems(offer, odds)
    if offer == "boost" and qualifies:
        ret = stake * p_win * (1 + (a - 1) * (1 + BOOST_TIERS[len(odds)]))
    else:
        ret = stake * p_win * a
        if offer == "refund" and qualifies:
            ret += stake * REFUND_VALUE * _p_exactly_one(ps)
    return ret - stake


def settle(offer: str, stake: float, legs: list[tuple[float, str]]) -> tuple[str, float | None]:
    """(status, returns) from each leg's (odds, result).

    result is 'won' | 'lost' | 'void' | 'pending'. The slip stays 'open' while
    its outcome still depends on a pending leg; it is decided as soon as it no
    longer does (a second loser ends a refund slip; one ends any other).

    Qualification is judged on the slip AS STRUCK — a void leg does not take a
    5-fold out of the refund (the frontend's `voidQualifies` default). A void
    drops the leg from the odds, and a boost is paid at the tier of the legs
    that stood.
    """
    results = [r for _, r in legs]
    lost = results.count("lost")
    pending = results.count("pending")
    struck_odds = [o for o, _ in legs]
    qualifies = not problems(offer, struck_odds)

    if offer == "refund" and qualifies:
        if lost >= 2:
            return "lost", 0.0
        if pending:
            return "open", None
        if lost == 1:
            return "refunded", round(stake * REFUND_VALUE, 2)
    else:
        if lost:
            return "lost", 0.0
        if pending:
            return "open", None

    standing = [o for o, r in legs if r == "won"]
    if not standing:
        return "void", round(stake, 2)
    a = prod(standing)
    if offer == "boost" and qualifies:
        b = BOOST_TIERS.get(len(standing), 0.0)
        return "won", round(stake * (1 + (a - 1) * (1 + b)), 2)
    return "won", round(stake * a, 2)
