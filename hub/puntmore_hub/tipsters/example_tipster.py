"""Example tipster parser: an email whose body lists tips one per line as
``Event | Market | Selection | Price``.

The reference for the contract (see the package docstring). It reads the plain-text body,
or the HTML body's text when there is no plain part. A line with fewer than three fields is
not a tip and is ignored; an unreadable price is left empty rather than invented.
"""
from __future__ import annotations

from .text import html_to_text


def parse(subject: str, text: str, html: str | None) -> list[dict]:
    out = []
    for line in (text or html_to_text(html)).splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3 or not all(parts[:3]):
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
