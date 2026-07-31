"""Parser-level tests for ClaudeOCRProvider.extract — no live API.

The upload router turns ANY exception from extract() into a 502 "OCR extraction
failed", so a parser that throws on a real-world response shape is invisible
until a user hits it. These drive extract() with canned message shapes to pin
down which responses survive and which blow up.
"""
import json
from types import SimpleNamespace

import pytest

from app.providers.claude_ocr import ClaudeOCRProvider, _strip_code_fence, parse_odds
from app.providers.base import ExtractedCoupon


def _block(kind, text=None):
    return SimpleNamespace(type=kind, text=text)


def _provider(content, stop_reason="end_turn"):
    """A provider whose API call returns a canned message with `content` blocks."""
    p = ClaudeOCRProvider(api_key="x")
    fake = SimpleNamespace(content=content, stop_reason=stop_reason)
    p._client.messages.create = lambda **kw: fake
    return p


_VALID = json.dumps({
    "title": "GG Bonus",
    "period_text": "Mon 20 - Thu 23",
    "payout_ladder": [{"picks": 3, "fractional": "9/2"}],
    "selections": [
        {"match": "Mariehamn v Lahti", "kickoff": "MON 17:00",
         "market": None, "your_odds": None},
        {"match": "PSG v Lyon", "kickoff": "MON 20:00",
         "market": None, "your_odds": 1.85},
    ],
})


def test_parses_plain_json_object():
    coupon = _provider([_block("text", _VALID)]).extract(b"img", "image/png")
    assert isinstance(coupon, ExtractedCoupon)
    assert [r.match for r in coupon.rows] == ["Mariehamn v Lahti", "PSG v Lyon"]
    assert coupon.rows[1].your_odds == 1.85
    assert coupon.payout_ladder == {3: 5.5}


def test_survives_leading_thinking_block():
    content = [_block("thinking"), _block("text", _VALID)]
    coupon = _provider(content).extract(b"img", "image/png")
    assert len(coupon.rows) == 2


def test_survives_code_fenced_json():
    fenced = f"```json\n{_VALID}\n```"
    coupon = _provider([_block("text", fenced)]).extract(b"img", "image/png")
    assert len(coupon.rows) == 2


def test_survives_bare_array():
    arr = json.dumps([{"match": "A v B", "your_odds": None}])
    coupon = _provider([_block("text", arr)]).extract(b"img", "image/png")
    assert [r.match for r in coupon.rows] == ["A v B"]


def test_no_text_block_raises_clean_error():
    with pytest.raises(ValueError, match="no text block"):
        _provider([_block("thinking")]).extract(b"img", "image/png")


def test_truncation_fails_loudly_with_cause():
    """A coupon long enough to hit max_tokens truncates the JSON. The provider
    detects stop_reason == 'max_tokens' and raises a clear cause BEFORE the
    opaque JSONDecodeError — so the 502 is diagnosable, not a mystery."""
    truncated = _VALID[: len(_VALID) // 2]  # cut mid-structure
    with pytest.raises(ValueError, match="truncated"):
        _provider([_block("text", truncated)], stop_reason="max_tokens").extract(
            b"img", "image/png"
        )


def test_strip_code_fence_plain_passthrough():
    assert _strip_code_fence('{"a":1}') == '{"a":1}'
    assert _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'


# --- Printed odds: fractions with a hyphen, and EVS (William Hill boards) ---

def test_parse_odds_reads_hyphenated_fractions_and_evs():
    """Hill prints "6-4", not "6/4", and evens as "EVS". These used to reach
    float() and raise, which 502'd the whole upload and deleted the image."""
    assert parse_odds("6-4") == 2.5
    assert parse_odds("4-11") == 1.3636
    assert parse_odds("1-50") == 1.02
    assert parse_odds("13-8") == 2.625
    assert parse_odds("20-1") == 21.0
    assert parse_odds("EVS") == 2.0
    assert parse_odds("9/2") == 5.5      # slash form still works
    assert parse_odds(1.85) == 1.85      # already decimal
    assert parse_odds("1.85") == 1.85


def test_parse_odds_returns_none_rather_than_raising():
    """An unreadable price must not take the coupon down with it."""
    for bad in (None, "", "  ", "n/a", "-", "abc", "1-0"):
        assert parse_odds(bad) is None


def test_two_sided_rows_carry_both_prices():
    body = json.dumps({
        "selections": [
            {"match": "Kairat v Omonia Nicosia", "kickoff": "WED 16:00",
             "home_odds": "1-3", "away_odds": "4-11"},
        ]
    })
    coupon = _provider([_block("text", body)]).extract(b"img", "image/png")
    row = coupon.rows[0]
    assert row.home_odds == 1.3333 and row.away_odds == 1.3636
    assert row.your_odds is None


# --- Home/away is POSITIONAL ------------------------------------------------
#
# Coupons are always laid out left-to-right: the left price of a pair belongs to
# the home team, the right to the away team, and the fixture is printed the same
# way round. A live coupon came back with the sides crossed while the prompt
# described the columns only by their "H"/"A" headers — position is the thing
# that never changes, so it is what the prompt now instructs on.

def test_prompt_asks_for_a_positional_pair_not_labelled_sides():
    """OCR transcribes the pairs; the software decides whose price is whose."""
    from app.providers.base import OcrHint
    from app.providers.claude_ocr import build_user_prompt

    prompt = build_user_prompt(OcrHint(priced=True, two_sided=True,
                                       board_label="TEAM TO SCORE 2 OR MORE GOALS"))
    assert '"prices"' in prompt and "PRINTED order" in prompt
    # It must not be asked to label the sides at all — that is the judgement
    # call that put prices on the wrong team.
    assert "home_odds" not in prompt and "away_odds" not in prompt
    assert "do not judge which team" in prompt


def test_prompt_asks_for_every_block_not_one():
    """Finding one block among six look-alikes was the other invisible guess.

    The requested heading is still named — as the heading to expect, not as a
    filter to apply — and the rest are asked for too.
    """
    from app.providers.base import OcrHint
    from app.providers.claude_ocr import build_user_prompt

    prompt = build_user_prompt(OcrHint(priced=True, two_sided=True,
                                       board_label="TEAM TO SCORE 2 OR MORE GOALS"))
    assert "Transcribe them ALL" in prompt
    assert "do not pick one block and do not leave one out" in prompt
    assert "Ignore every other market block" not in prompt
    assert "TEAM TO SCORE 2 OR MORE GOALS" in prompt


def test_prompt_forbids_reordering_the_pair():
    from app.providers.base import OcrHint
    from app.providers.claude_ocr import build_user_prompt

    prompt = build_user_prompt(OcrHint(priced=True, two_sided=True))
    assert "do not reorder" in prompt.lower()


def test_system_prompt_puts_side_assignment_outside_the_model():
    from app.providers.claude_ocr import SYSTEM_PROMPT
    assert "DO NOT LABEL THE TWO PRICES" in SYSTEM_PROMPT
    assert "by the software" in SYSTEM_PROMPT


def test_positional_pair_becomes_home_then_away():
    from app.providers.claude_ocr import parse_rows
    rows = parse_rows({"selections": [
        {"match": "Kairat v Omonia Nicosia", "kickoff": "WED 16:00",
         "odds": ["1-3", "4-11"]},
    ]})
    assert rows[0].home_odds == 1.3333    # printed first
    assert rows[0].away_odds == 1.3636    # printed second


def test_a_missing_price_keeps_its_slot():
    """null in a slot must not shift the other price onto the wrong team."""
    from app.providers.claude_ocr import parse_rows
    rows = parse_rows({"selections": [
        {"match": "A v B", "odds": [None, "4-11"]},
    ]})
    assert rows[0].home_odds is None and rows[0].away_odds == 1.3636


def test_a_pair_of_the_wrong_length_is_dropped_entirely():
    """One price with no way to know which sub-column it came from is
    ambiguous; an unpriced leg beats a leg priced on the wrong team."""
    from app.providers.claude_ocr import parse_rows
    rows = parse_rows({"selections": [{"match": "A v B", "odds": ["4-11"]}]})
    assert rows[0].home_odds is None and rows[0].away_odds is None


def test_the_older_labelled_shape_still_parses():
    """A provider or model answering in the previous shape keeps working."""
    from app.providers.claude_ocr import parse_rows
    rows = parse_rows({"selections": [
        {"match": "A v B", "home_odds": "1-3", "away_odds": "4-11"},
    ]})
    assert rows[0].home_odds == 1.3333 and rows[0].away_odds == 1.3636


def test_one_sided_coupons_are_not_told_about_columns():
    from app.providers.base import OcrHint
    from app.providers.claude_ocr import build_user_prompt

    prompt = build_user_prompt(OcrHint(priced=True, two_sided=False))
    assert '"your_odds"' in prompt
    assert "home_odds" not in prompt


def test_left_price_becomes_the_home_teams_leg():
    """End to end: the LEFT price must land on the team named first."""
    from datetime import date
    from app.coupon_types import BY_CODE
    from app.providers.base import ExtractedCoupon, ExtractedRow
    from app.routers.upload import _build_legs

    extracted = ExtractedCoupon(rows=[
        ExtractedRow("Kairat v Omonia Nicosia", None, None, "WED 16:00",
                     home_odds=1.33, away_odds=4.50),
    ])
    legs = _build_legs(1, extracted, BY_CODE["TTS2"], date(2026, 7, 29))
    by_pick = {l.pick: l.odds for l in legs}
    assert by_pick["Kairat to Score 2"] == 1.33            # left column
    assert by_pick["Omonia Nicosia to Score 2"] == 4.50    # right column
