"""GeminiOCRProvider — the output budget and the truncation guard.

Gemini is the provider that runs when GOOGLE_API_KEY is set, which is the
default priority in main.py's lifespan. It shares the prompt and parser with
the Claude path, so it also shares the failure a whole-sheet read introduces:
an output cap reached mid-JSON. A truncated read must fail, never be saved as
a short coupon.
"""
import json
from types import SimpleNamespace

import pytest

from app.providers.claude_ocr import MAX_TOKENS, OCRTruncated
from app.providers.gemini_ocr import GeminiOCRProvider, _hit_token_cap

_VALID = json.dumps({"selections": [{"match": "A v B", "kickoff": "SAT 15:00"}]})


def _response(text, finish_reason=None):
    reason = SimpleNamespace(name=finish_reason) if finish_reason else None
    return SimpleNamespace(
        text=text, candidates=[SimpleNamespace(finish_reason=reason)],
    )


def _provider(response):
    p = GeminiOCRProvider.__new__(GeminiOCRProvider)   # no live client
    captured = {}

    def generate_content(**kw):
        captured.update(kw)
        return response

    p._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return p, captured


def test_a_complete_read_parses():
    p, _ = _provider(_response(_VALID, "STOP"))
    coupon = p.extract(b"img", "image/png")
    assert [r.match for r in coupon.rows] == ["A v B"]


def test_the_request_carries_the_shared_budget():
    """Same budget as the Claude path — a whole-sheet read is the same size
    whoever produces it, and Gemini's own default is far below it."""
    p, captured = _provider(_response(_VALID, "STOP"))
    p.extract(b"img", "image/png")
    assert captured["config"].max_output_tokens == MAX_TOKENS


def test_a_truncated_read_raises_rather_than_parsing():
    p, _ = _provider(_response(_VALID[:20], "MAX_TOKENS"))
    with pytest.raises(OCRTruncated):
        p.extract(b"img", "image/png")


def test_the_finish_reason_is_read_defensively():
    """The enum's spelling has moved between SDK versions, and an undetected
    truncation is the failure this guard exists to prevent."""
    assert _hit_token_cap(_response("", "MAX_TOKENS"))
    assert _hit_token_cap(_response("", "MAX_TOKEN_LIMIT"))
    assert not _hit_token_cap(_response("", "STOP"))
    # No candidates, or no reason at all: not truncated — the JSON parse still
    # fails loudly if the body really was cut short.
    assert not _hit_token_cap(_response(""))
    assert not _hit_token_cap(SimpleNamespace(text="", candidates=None))
