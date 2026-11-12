"""Parsers: turn a kept-open page into rows. One module per parser.

A parser is `fn(page, capture) -> list[dict]`, named in a target's `parser`
in targets.toml. It may read the page's DOM,
or the payloads the page received (`capture.recent`: (ts, kind, url, text)
tuples, newest last). Rows are handed to the Hub as a `scraper_prices` drop
for the target's `post` route, which the Hub posts as {"prices": rows}, so a
parser still emits exactly the shape that endpoint takes.

Parsers are written ONLY from captured samples (data/samples/) or dumps
(data/dumps/) - never from a guessed layout. One that raises
NotImplementedError is reported once ("capturing only") and its target keeps
capturing, which is how the samples to write it get collected.
"""
from .bb_builder import bb_builder
from .bb_saves import bb_saves
from .example_tipster import example_tipster
from .pp_most import pp_most

PARSERS = {"bb_saves": bb_saves, "bb_builder": bb_builder, "pp_most": pp_most,
           "example_tipster": example_tipster}
