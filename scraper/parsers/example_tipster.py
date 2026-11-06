"""Example Finder-source parser: a page listing tips as
``<li class="tip">Event | Market | Selection | Price</li>``.

The contract for a fetch target's parser is the same as every other parser,
``fn(page, capture) -> list[dict]``, but the rows are TIPS: each needs ``event``,
``market`` and ``selection``, and may carry ``advised_price``,
``advised_stake_units``, ``tip_time`` (ISO), ``expiry`` (ISO) and ``origin``
(``tipster`` or ``scrape``). They are handed to the Hub (``post = "hub:tips"``),
where HubAI judges them for Finder. Return nothing rather than guess: a short
row count trips the target's ``min_rows`` health check.
"""
import html as html_lib
import re

ITEM = re.compile(r'<li[^>]*class="[^"]*\btip\b[^"]*"[^>]*>(.*?)</li>', re.S | re.I)
TAG = re.compile(r"<[^>]+>")


def example_tipster(page, capture) -> list[dict]:
    out = []
    for m in ITEM.finditer(page.content()):
        parts = [p.strip() for p in html_lib.unescape(TAG.sub("", m.group(1))).split("|")]
        if len(parts) < 3:
            continue
        price = None
        if len(parts) >= 4:
            try:
                price = float(parts[3])
            except ValueError:
                price = None
        out.append({"event": parts[0], "market": parts[1], "selection": parts[2],
                    "advised_price": price, "advised_stake_units": 1.0, "origin": "tipster"})
    return out
