"""Layout configuration loader for bookie-specific UI mappings."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("puntmore.placement.layout")

# Base directory for layout configs
LAYOUTS_DIR = Path(__file__).parent / "layouts"


class LayoutConfig:
    """Holds layout configuration for a bookie."""

    def __init__(self, data: dict[str, Any]):
        self.bookmaker = data.get("bookmaker", "unknown")
        self.package = data.get("package", "")
        self.screen = data.get("screen", {})
        self.coords = data.get("coords", {})
        self.selectors = data.get("selectors", {})
        self.popups = data.get("popups", [])
        self.timing = data.get("timing", {})
        self.price_format = data.get("price_format", "decimal")
        self.quirks = data.get("quirks", [])

    @classmethod
    def load(cls, bookmaker: str) -> "LayoutConfig":
        """Load layout config for a bookie."""
        path = LAYOUTS_DIR / f"{bookmaker}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Layout config not found: {path}")
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(data)

    def get_coord(self, name: str, default: tuple[int, int] | None = None) -> tuple[int, int] | None:
        """Get coordinate by name."""
        coord = self.coords.get(name)
        if coord and len(coord) >= 2:
            return (coord[0], coord[1])
        return default

    def get_selector(self, name: str) -> str | None:
        """Get XPath selector by name."""
        return self.selectors.get(name)

    def get_timing(self, name: str, default_ms: int = 0) -> int:
        """Get timing in milliseconds."""
        return self.timing.get(name, default_ms)

    def has_quirk(self, name: str) -> bool:
        """Check if a quirk is defined."""
        return any(q.get("name") == name for q in self.quirks)

    def get_quirk(self, name: str) -> dict[str, Any] | None:
        """Get quirk by name."""
        for q in self.quirks:
            if q.get("name") == name:
                return q
        return None
