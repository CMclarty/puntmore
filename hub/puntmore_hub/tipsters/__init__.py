"""Tipster email parsers: one module per tipster, named in `tipsters.toml`.

A parser is `parse(subject, text, html) -> list[dict]`: the email's subject, its plain-text
body and its HTML body (None when the email has no such part). The rows are TIPS with the same
contract as the Scraper's parsers (`scraper/parsers/example_tipster.py`): each needs `event`,
`market` and `selection`, and may carry `advised_price`, `advised_stake_units`, `tip_time`
(ISO; the email's Date when absent), `expiry` (ISO) and `origin` (`tipster` when absent). The
`tip_id` is not the parser's business: `mail.py` mints it when the row is first seen.

Parsers are written only from real emails a tipster sent, never from a guessed layout, and
return nothing rather than guess: an email that parses to zero rows is escalated as an
unhealthy source, which is the alarm. `puntmore mail --dry-run` shows what a parser reads.
`text.html_to_text` is there for a parser whose tipster sends no plain-text part.
"""
from __future__ import annotations

from .example_tipster import parse as example_tipster
from .text import html_to_text  # noqa: F401  (re-exported for parsers)

PARSERS = {"example_tipster": example_tipster}
