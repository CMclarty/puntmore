"""Fetch a page with the repo's own real-Chrome setup (``scraper/browser.py``).

The Chrome configuration (one persistent profile, a person's Chrome rather than a test run, no
fingerprint spoofing, consent dismissed the minimal way, a reader's dwell) lives in
``scraper/browser.py`` and is tested there. This module borrows it rather than carrying a second
copy, so the hard rules stay in one place. The Hub adds: one load per run, block detection
that raises ``BlockedError`` so the runner applies the 24 h cool-off, and capture of JSON responses
the page itself requested.

Playwright and the scraper module are imported lazily so the rest of the package (and the tests)
work without a browser.
"""
from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..config import Settings, settings as default_settings
from .sources import Source

log = logging.getLogger("puntmore.browser")

BLOCK_STATUS = {403, 429, 503}
BLOCK_PATTERNS = re.compile(
    r"captcha|access denied|verify you are human|unusual traffic|cf-challenge|challenge-platform|"
    r"attention required|request blocked|bot detection|press and hold|pardon our interruption",
    re.I,
)


class BlockedError(Exception):
    """The site challenged or blocked us. Cool off; do not retry."""


class FetchError(Exception):
    """Transient fetch failure (timeout, network). Not a block."""


@dataclass
class Fetched:
    url: str
    status: int | None
    html: str
    title: str = ""
    json_responses: list[dict[str, Any]] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)

    @property
    def text(self) -> str:
        return self.html


def looks_blocked(status: int | None, title: str, html: str) -> bool:
    if status in BLOCK_STATUS:
        return True
    head = f"{title}\n{html[:20000]}"
    return bool(BLOCK_PATTERNS.search(head))


def _repo_browser():
    """``scraper.browser`` from the repo root, whether or not the package is installed."""
    try:
        from scraper import browser as sb  # type: ignore[import-not-found]
    except ImportError:
        root = Path(__file__).resolve().parents[3]   # hub/puntmore_hub/scraper/browser.py -> repo root
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from scraper import browser as sb  # type: ignore[import-not-found]
    return sb


def fetch(url: str, source: Source, cfg: Settings = default_settings) -> Fetched:
    """Load ``url`` in the persistent Chrome profile and return the page plus captured JSON."""
    from playwright.sync_api import Error as PwError, TimeoutError as PwTimeout, sync_playwright

    sb = _repo_browser()
    chrome = SimpleNamespace(profile_dir=cfg.chrome_profile_dir, channel="chrome" if cfg.use_real_chrome else None)
    captured: list[dict[str, Any]] = []
    patterns = [p for p in source.capture_json if p]

    def on_response(resp):  # type: ignore[no-untyped-def]
        try:
            if patterns and any(p in resp.url for p in patterns) and "json" in (resp.headers.get("content-type") or ""):
                captured.append({"url": resp.url, "status": resp.status, "body": resp.json()})
        except Exception:  # noqa: BLE001 - a bad body must not kill the page load
            pass

    with sync_playwright() as pw:
        try:
            ctx = sb.launch(pw, chrome, headless=cfg.headless)
        except PwError as exc:
            raise FetchError(f"browser would not start: {exc}") from exc
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("response", on_response)
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except PwTimeout as exc:
                raise FetchError(f"timeout loading {url}") from exc
            status = resp.status if resp else None
            reason = sb.blocked(page, status)
            if reason:
                # Only our own words in the error: page text is untrusted and
                # would otherwise travel into the escalation brief the agent reads.
                raise BlockedError(f"{url}: {reason}")
            sb.dismiss_consent(page)
            if source.wait_selector:
                try:
                    page.wait_for_selector(source.wait_selector, timeout=30_000)
                except PwTimeout:
                    log.warning("wait_selector %r not found on %s", source.wait_selector, url)
            sb.settle(page)
            html = page.content()
            title = page.title()
        finally:
            ctx.close()
    if looks_blocked(status, title, html):
        raise BlockedError(f"{url}: challenge or block detected (status={status})")
    return Fetched(url=url, status=status, html=html, title=title, json_responses=captured)
