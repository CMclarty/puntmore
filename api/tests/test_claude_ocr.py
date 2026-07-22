"""Parser-level tests for ClaudeOCRProvider.extract — no live API.

The upload router turns ANY exception from extract() into a 502 "OCR extraction
failed", so a parser that throws on a real-world response shape is invisible
until a user hits it. These drive extract() with canned message shapes to pin
down which responses survive and which blow up.
"""
import json
from types import SimpleNamespace

import pytest

from app.providers.claude_ocr import ClaudeOCRProvider, _strip_code_fence
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
