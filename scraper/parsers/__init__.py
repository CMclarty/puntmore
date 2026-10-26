"""Parsers: turn a kept-open page into rows. One module per parser.

A parser is `fn(page, capture) -> list[dict]`, named in a target's `parser`
(the market's parser on Puntmore's /scraper page). It may read the page's DOM,
or the payloads the page received (`capture.recent`: (ts, kind, url, text)
tuples, newest last). Rows are POSTed as {"prices": rows} to the target's
`post` path, so a parser emits exactly the shape that endpoint takes.

Parsers are written ONLY from captured samples (data/samples/) or dumps
(data/dumps/) - never from a guessed layout. One that raises
NotImplementedError is reported once ("capturing only") and its target keeps
capturing, which is how the samples to write it get collected.
"""
from .bb_saves import bb_saves
from .pp_most import pp_most

PARSERS = {"bb_saves": bb_saves, "pp_most": pp_most}
