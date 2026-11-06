"""The visible text of an HTML email body, for a parser that works on lines."""
from __future__ import annotations

import html as html_lib
import re

_BREAK = re.compile(r"<\s*(?:br|/p|/div|/li|/tr|/h[1-6])\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1\s*>", re.S | re.I)


def html_to_text(html: str | None) -> str:
    """One line per block element, tags removed, entities decoded. Nothing is inferred:
    tags go, text stays, so a parser reads what the tipster wrote and no more."""
    if not html:
        return ""
    text = _SCRIPT.sub("", html)
    text = _BREAK.sub("\n", text)
    text = _TAG.sub("", text)
    return "\n".join(line.strip() for line in html_lib.unescape(text).splitlines())
