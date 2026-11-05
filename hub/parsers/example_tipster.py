"""Example parser: tips listed as ``<li class="tip">Event | Market | Selection | Price</li>``.

The parser contract: return a list of dicts with event, market, selection, and optionally
advised_price, advised_stake_units, tip_time, expiry, extra. Return [] when the page is not
what you expect; never guess.
"""
from __future__ import annotations

import html as html_lib
import re

ITEM = re.compile(r'<li[^>]*class="[^"]*\btip\b[^"]*"[^>]*>(.*?)</li>', re.S | re.I)
TAG = re.compile(r"<[^>]+>")


def parse(fetched, source) -> list[dict]:
    out = []
    for m in ITEM.finditer(fetched.html):
        text = html_lib.unescape(TAG.sub("", m.group(1))).strip()
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 3:
            continue
        event, market, selection = parts[0], parts[1], parts[2]
        price = None
        if len(parts) >= 4:
            try:
                price = float(parts[3])
            except ValueError:
                price = None
        out.append({"event": event, "market": market, "selection": selection, "advised_price": price,
                    "advised_stake_units": 1.0, "origin": "tipster"})
    return out
