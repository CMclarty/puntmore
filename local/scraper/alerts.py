"""Alerts: always to data/alerts.log, and to Discord when a webhook is set.

Callers alert on a CHANGE of state (blocked, clear again, cooling off), never
on every tick, so a stuck page is one message, not one every few minutes.
"""
import pathlib
from datetime import datetime


class Alerts:
    def __init__(self, log_path: pathlib.Path, webhook: str | None):
        self.log_path = log_path
        self.webhook = webhook

    def send(self, message: str):
        line = f"{datetime.now():%Y-%m-%d %H:%M}  {message}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if not self.webhook:
            return
        import httpx
        try:
            httpx.post(self.webhook, json={"content": f"scraper: {message}"}, timeout=15)
        except Exception as exc:
            print(f"         alert not sent: {exc}", flush=True)
