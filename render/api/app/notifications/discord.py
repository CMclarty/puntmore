import httpx

from .base import NotificationProvider


class DiscordNotificationProvider(NotificationProvider):
    def __init__(self, webhook_url: str):
        self._webhook_url = webhook_url

    def send(self, message: str) -> None:
        httpx.post(self._webhook_url, json={"content": message}, timeout=10)


class NullNotificationProvider(NotificationProvider):
    """No-op provider used when no webhook is configured."""

    def send(self, message: str) -> None:
        pass
