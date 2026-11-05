"""Parser loading and the parser contract.

A parser is ``parsers/<name>.py`` exposing::

    def parse(fetched, source) -> list[dict]

Each dict is a candidate tip with at least ``event``, ``market``, ``selection`` and optionally
``advised_price``, ``advised_stake_units``, ``tip_time`` (ISO), ``expiry`` (ISO), ``extra`` (dict).
``source_id`` and ``origin`` are filled in by the runner. A parser never guesses: if a page is
not what it expects it returns an empty list, which the runner treats as a suspected break.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable

ParseFn = Callable[[object, object], list[dict]]


def load_parser(name: str, parsers_dir: Path) -> ParseFn:
    path = Path(parsers_dir) / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"parser {name!r} not found at {path}")
    spec = importlib.util.spec_from_file_location(f"puntmore_parsers.{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, "parse", None)
    if fn is None:
        raise AttributeError(f"parser {name!r} has no parse(fetched, source) function")
    return fn
