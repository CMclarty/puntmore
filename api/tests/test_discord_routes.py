"""Discord routing: one channel per tool, optionally per edge band."""
from types import SimpleNamespace

from app import scheduler
from app.config import settings
from app.notifications.routes import RoutedNotifier, notify_rows, parse_routes


class _Hook:
    sent: list = []

    def __init__(self, url):
        self.url = url

    def send(self, message):
        _Hook.sent.append((self.url, message))


def _notifier(default="D", routes=""):
    _Hook.sent = []
    return RoutedNotifier(default, routes, provider=_Hook)


def test_routes_parse_by_tool_and_band_and_skip_bad_entries():
    r = parse_routes("coupons=A, paddy=B; paddy@0.08=C  365=D nonsense=E paddy@x=F")
    assert [u for _, u in r["paddy"]] == ["C", "B"]          # highest band first
    assert set(r) == {"coupons", "paddy", "365"}


def test_rows_split_by_edge_band_into_separate_messages():
    n = _notifier(routes="paddy=P paddy@0.08=BIG")
    notify_rows(n, "paddy", "Paddy {n}:", [(0.03, "a"), (0.09, "b"), (0.12, "c")])
    assert sorted(_Hook.sent) == [("BIG", "Paddy 2:\nb\nc"), ("P", "Paddy 1:\na")]


def test_an_unrouted_tool_falls_back_to_the_default_webhook():
    n = _notifier(routes="paddy=P")
    notify_rows(n, "coupons", "C {n}:", [(0.05, "x")])
    assert _Hook.sent == [("D", "C 1:\nx")]


def test_rows_below_every_band_with_no_plain_route_use_the_default():
    n = _notifier(routes="365@0.10=BIG")
    notify_rows(n, "365", "{n}", [(0.03, "small"), (0.2, "big")])
    assert sorted(_Hook.sent) == [("BIG", "1\nbig"), ("D", "1\nsmall")]


def test_nothing_configured_sends_nothing_and_system_messages_use_the_default():
    n = _notifier(default="", routes="")
    assert not n.configured
    notify_rows(n, "paddy", "{n}", [(0.1, "x")])
    n.send("boot")
    assert _Hook.sent == []
    n2 = _notifier(routes="paddy=P")
    n2.send("job failed")
    assert _Hook.sent == [("D", "job failed")]


def test_a_plain_notifier_still_gets_one_message():
    got = []
    notify_rows(SimpleNamespace(send=got.append), "coupons", "H {n}:", [(0.1, "a"), (0.2, "b")])
    assert got == ["H 2:\na\nb"]


def test_acca_boost_alerts_once_per_pick_and_again_on_a_tier_change(monkeypatch):
    feed = [SimpleNamespace(match="A vs B", sport="EPL", pick="A", odds=2.1, value=0.03)]
    import app.routers.accabonus as acc
    monkeypatch.setattr(acc, "build_feed", lambda client, min_edge: feed)
    monkeypatch.setattr(settings, "accabonus_discord_alerts", True)
    scheduler._acca_notified.clear()
    n = _notifier(routes="365=T")
    scheduler.sweep_accabonus(object(), n)
    scheduler.sweep_accabonus(object(), n)                 # unchanged: silent
    feed[0].value = 0.07                                   # lo -> hi
    scheduler.sweep_accabonus(object(), n)
    assert [u for u, _ in _Hook.sent] == ["T", "T"]
    assert "A vs B (EPL) — A @ 2.1, +7.0%" in _Hook.sent[1][1]
    feed.clear()
    scheduler.sweep_accabonus(object(), n)
    assert scheduler._acca_notified == {}                  # swept, no leak
