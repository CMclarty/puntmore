"""Source registry: one YAML file per scrape source.

Adding a source is a config entry plus a parser, never a change elsewhere. Files whose name
starts with ``_`` are templates and are ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Schedule:
    every: str | None = None           # "2h", "30m", "1d"
    at: list[str] = field(default_factory=list)   # ["09:00", "17:30"] local time
    kickoff_window: str | None = None  # e.g. "3h": sweep every 30 min within this window before kick-off


@dataclass
class Health:
    min_items: int = 1                 # fewer parsed items than this = suspected layout change
    price_min: float = 1.01
    price_max: float = 1000.0
    max_stale_hours: float | None = None


@dataclass
class Source:
    name: str
    parser: str
    url: str = ""
    enabled: bool = True
    kind: str = "page"                 # page (headful Chrome) | json (captured XHR) | email (IMAP, Bet Finder tipsters)
    schedule: Schedule = field(default_factory=Schedule)
    wait_selector: str | None = None   # CSS selector to wait for before reading the page
    capture_json: list[str] = field(default_factory=list)   # URL substrings of XHR responses to capture
    trust_weight: float = 1.0          # feeds the decision layer's per-source weighting
    health: Health = field(default_factory=Health)
    market: str | None = None
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any], path: Path | None = None) -> "Source":
        d = dict(d)
        sched = d.pop("schedule", {}) or {}
        health = d.pop("health", {}) or {}
        known = {f for f in cls.__dataclass_fields__} - {"schedule", "health", "extra", "path"}
        extra = {k: v for k, v in d.items() if k not in known}
        core = {k: v for k, v in d.items() if k in known}
        if "name" not in core and path is not None:
            core["name"] = path.stem
        if "parser" not in core:
            core["parser"] = core["name"].replace("-", "_")
        return cls(schedule=Schedule(**sched), health=Health(**health), extra=extra, path=path, **core)


def load_source(path: Path) -> Source:
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    return Source.from_dict(data, path=path)


def load_sources(directory: Path) -> dict[str, Source]:
    out: dict[str, Source] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        src = load_source(path)
        out[src.name] = src
    return out
