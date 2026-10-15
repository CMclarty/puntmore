"""Discord routing: one channel per tool, optionally per edge band.

DISCORD_ROUTES lists webhooks by tool, with an optional minimum edge:

    coupons=https://discord.com/api/webhooks/…
    paddy=https://…            paddy's channel
    paddy@0.08=https://…       paddy rows at +8% or better go here instead
    365=https://…

Entries are separated by whitespace, commas or semicolons. A row goes to the
route for its tool with the HIGHEST minimum edge it clears; with none, to the
tool's plain route; with none of those, to DISCORD_WEBHOOK_URL. A batch is
split by destination, so one sweep can post to two channels.

Tools: coupons, paddy, 365. System messages (job failures, cleanups) always
go to DISCORD_WEBHOOK_URL.
"""
import logging
import re

from .base import NotificationProvider
from .discord import DiscordNotificationProvider

logger = logging.getLogger("uvicorn.error")

TOOLS = ("coupons", "paddy", "365")
_ENTRY_RE = re.compile(r"^([a-z0-9]+)(?:@(-?\d*\.?\d+))?=(\S+)$", re.I)


def parse_routes(spec: str) -> dict[str, list[tuple[float, str]]]:
    """{tool: [(min_edge, url), …]} sorted highest band first. Bad entries are
    logged and skipped, never fatal: a typo must not silence every alert."""
    routes: dict[str, list[tuple[float, str]]] = {}
    for raw in re.split(r"[\s,;]+", spec or ""):
        if not raw:
            continue
        m = _ENTRY_RE.match(raw)
        if not m or m.group(1).lower() not in TOOLS:
            logger.error("DISCORD_ROUTES: ignoring %r (expected tool[@min_edge]=url, tool one of %s)",
                         raw.split("=")[0], ", ".join(TOOLS))
            continue
        tool, edge, url = m.group(1).lower(), m.group(2), m.group(3)
        routes.setdefault(tool, []).append((float(edge) if edge else float("-inf"), url))
    for bands in routes.values():
        bands.sort(key=lambda b: b[0], reverse=True)
    return routes


class RoutedNotifier(NotificationProvider):
    def __init__(self, default_url: str = "", routes: str = "", provider=DiscordNotificationProvider):
        self._provider = provider
        self._default = provider(default_url) if default_url else None
        self._routes = {
            tool: [(edge, provider(url)) for edge, url in bands]
            for tool, bands in parse_routes(routes).items()
        }

    @property
    def configured(self) -> bool:
        return self._default is not None or bool(self._routes)

    def send(self, message: str) -> None:
        """System messages: the default channel only."""
        if self._default:
            self._default.send(message)

    def destination(self, tool: str, edge: float | None):
        # Highest band first; the plain route (no minimum) sorts last.
        for min_edge, dest in self._routes.get(tool, []):
            if min_edge == float("-inf") or (edge is not None and edge >= min_edge):
                return dest
        return self._default

    def send_rows(self, tool: str, header: str, rows: list[tuple[float | None, str]]) -> None:
        """Post `rows` ((edge, line) pairs) under `header`, split by channel.
        `header` may contain {n}, the number of rows in that message."""
        groups: dict[int, tuple[object, list[str]]] = {}
        for edge, line in rows:
            dest = self.destination(tool, edge)
            if dest is None:
                continue
            groups.setdefault(id(dest), (dest, []))[1].append(line)
        for dest, lines in groups.values():
            dest.send(header.format(n=len(lines)) + "\n" + "\n".join(lines))


def notify_rows(notifier, tool: str, header: str, rows: list[tuple[float | None, str]]) -> None:
    """Route rows when the notifier can; otherwise one plain message."""
    if not notifier or not rows:
        return
    if hasattr(notifier, "send_rows"):
        notifier.send_rows(tool, header, rows)
    else:
        notifier.send(header.format(n=len(rows)) + "\n" + "\n".join(line for _, line in rows))
