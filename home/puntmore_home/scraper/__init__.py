"""Scraper worker: the home side of the Bet Finder.

A source is a YAML file in ``sources/``; a parser is a small Python file in ``parsers/`` with a
``parse(fetched, source) -> list[dict]`` function. The runner fetches with real headful Chrome
under a request budget, archives the raw page, parses, validates, stores tips and queues them
for Render. The model is never in the happy path.
"""
