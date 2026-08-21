"""Reading a coupon selection's TEXT: which market it names, and which team.

A leg's `pick` is the selection as a human reads it — "Kairat to Score 2",
"Lyon Win and Over 2.5", "GG No Draw". Every odds provider has to answer the
same two questions about that string before it can price anything:

  WHICH MARKET does it name?   →  the regexes below, and `is_team_specific`
  WHICH TEAM does it name?     →  `team_fragment` + `side_of`

Both providers used to answer them separately, and they answered differently:
one resolved the side and one silently assumed the home team, so the same
coupon priced correctly or crossed onto the wrong team depending on which
provider a deploy happened to be running. That is exactly the kind of drift
`pricing/legs.py` exists to prevent on the pricing side, so the pick-reading
side lives in one place too.

This module is pure text logic — no vendor, no HTTP, no market data. Providers
map the answers onto their own vendor markets; nothing here knows what a
vendor row looks like.
"""
import re

from .normalise import team_similarity

# \b on both, and load-bearing. Without the leading boundary "over\s+[\d.]+"
# matches inside a club name that ends in a number — "Hannover 96 Win and Over
# 2.5" found "over 96" FIRST and derived the leg at a 96-goal line, which is
# p=0, which is no price at all. Every Hannover Win-and-Over leg went unpriced
# and looked like a fixture the board did not carry.
OVER_RE = re.compile(r"\bover\s+([\d.]+)", re.IGNORECASE)
UNDER_RE = re.compile(r"\bunder\s+([\d.]+)", re.IGNORECASE)
WIN_RE = re.compile(r"\bwin\b", re.IGNORECASE)
# "Win to Nil" also contains "win", so it MUST be tested before the plain
# moneyline branch or it silently prices as a straight win — a different and
# much shorter bet.
WIN_TO_NIL_RE = re.compile(r"\bwin\b.*\bto\s+nil\b|\bwin\s+to\s+nil\b", re.IGNORECASE)
# "Team to Score N" means N OR MORE, so the line is N-0.5 (SPEC §3). Getting
# this off by one misprices every TTS leg.
TTS_RE = re.compile(r"to\s+score\s+(\d+)", re.IGNORECASE)
# GG = Goal Goal = both teams score. "No Draw" additionally excludes the draw.
GG_NO_DRAW_RE = re.compile(r"\bgg\b.*\bno\s+draw\b|\bno\s+draw\b.*\bgg\b", re.IGNORECASE)
GG_RE = re.compile(r"\bgg\b|\bbtts\b|both\s+teams\s+to\s+score", re.IGNORECASE)

# Every market keyword, for finding where the team name stops. Order is
# irrelevant here — `team_fragment` takes the EARLIEST match, not the first
# pattern to hit.
_MARKET_RES = (
    GG_NO_DRAW_RE, GG_RE, TTS_RE, WIN_TO_NIL_RE, WIN_RE, OVER_RE, UNDER_RE,
)

# Resolving which side a pick names. The TEAM FRAGMENT is compared, not the
# whole pick, so this is a team-name comparison — and it is scored with
# team_similarity, not a raw character ratio, for the same reasons that
# function exists: the coupon and the board disagree by shortening, word order
# and outright different names.
#
# The bar matches fixture matching's, and that is not arbitrary: a fixture only
# gets priced after clearing that threshold on BOTH sides, and a two-sided
# coupon builds its pick from the same fixture name it matched on. So the
# naming side is guaranteed to clear this bar; anything that does not is a pick
# that no longer resembles either runner. The margin keeps two look-alike
# runners ("United v United") from resolving on noise.
# Fail to None; an unresolved side is unpriced, never assumed.
SIDE_THRESHOLD = 0.72
SIDE_MARGIN = 0.10


def team_fragment(pick: str) -> str:
    """The team name out of a pick, with the market words cut off.

    A pick reads "{team} {market}" — "Lyon Win and Over 2.5", "Kairat to
    Score 1" — so the team is everything before the first market keyword.

    Cutting it matters because the market words are NOISE against a runner
    name, and on a short name they outweigh the signal: "Lyon Win and Over 2.5"
    is 20 characters of which 4 are the team, so it scored 0.33 against "Lyon"
    and the side would not resolve at all. Comparing the fragment compares two
    club names, which is the only comparison that means anything here — and it
    is what lets team_similarity's alias, containment and word-order handling
    apply, none of which survives being diluted by a market suffix.

    Returns "" when the pick is market words only ("Win and Over 2.5"), which
    names no side and must stay unresolved.
    """
    key = (pick or "").strip()
    cut = len(key)
    for pattern in _MARKET_RES:
        m = pattern.search(key)
        if m:
            cut = min(cut, m.start())
    return key[:cut].strip()


def side_of(pick: str, runner_home, runner_away) -> bool | None:
    """Which side a pick names: True home, False away, None unresolved.

    Scores the pick's team fragment against both runner names with
    team_similarity — the same function fixture matching uses, so a side
    resolves through the same alias table, containment and word-order rules
    that got the fixture matched in the first place.

    Requires a clear winner. A pick that scores alike against both
    ("United v United") is ambiguous, and an ambiguous side must go unpriced
    rather than default to one — defaulting to home is what priced away
    selections as their opponent, and since home is usually the favourite that
    INVENTS value rather than hiding it.
    """
    if not isinstance(runner_home, str) or not isinstance(runner_away, str):
        return None
    team = team_fragment(pick)
    if not team:
        return None
    h = team_similarity(team, runner_home)
    a = team_similarity(team, runner_away)
    if max(h, a) < SIDE_THRESHOLD or abs(h - a) < SIDE_MARGIN:
        return None
    return h > a


def is_team_specific(pick: str) -> bool:
    """True when the market has a home and an away version.

    Match totals and GG do not — "Over 2.5" and "both teams score" are
    properties of the game, so both sides would return the same price and the
    distinction is meaningless.
    """
    key = (pick or "").strip().lower()
    if GG_RE.search(key) or GG_NO_DRAW_RE.search(key):
        return False
    return bool(WIN_RE.search(key) or TTS_RE.search(key))
