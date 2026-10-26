"""Capture's disk budget and activity rules, with a stand-in page (no browser)."""
import json

from scraper.capture import Capture, DiskBudget

PP = "https://smp.paddypower.com/www/sports/fixedodds/readonly/v1/getMarketPrices"


class _Page:
    def __init__(self):
        self.handlers = {}
        self.main_frame = object()

    def on(self, event, fn):
        self.handlers[event] = fn


class _Req:
    def __init__(self, rtype):
        self.resource_type = rtype


class _Frame:
    def __init__(self, page):
        self.page = page


class _Resp:
    def __init__(self, page, url, text, status=200, rtype="xhr"):
        self.url, self.status, self._text = url, status, text
        self.request = _Req(rtype)
        self.frame = _Frame(page)
        self.headers = {"content-type": "application/json"}

    def text(self):
        return self._text

    def body(self):
        return self._text.encode()


def _cap(tmp_path, name, budget):
    page = _Page()
    return page, Capture(page, tmp_path / name, budget)


def _files(tmp_path, name):
    return sorted((tmp_path / name).glob("*-http.txt"))


def test_repeats_are_not_written_but_stay_in_the_ring(tmp_path):
    page, cap = _cap(tmp_path, "a", DiskBudget(10))
    body = json.dumps({"prices": [1.5] * 100})
    for _ in range(5):
        page.handlers["response"](_Resp(page, PP, body))
    page.handlers["response"](_Resp(page, PP, body.replace("1.5", "1.6")))  # same size
    page.handlers["response"](_Resp(page, PP, body + " "))                  # changed
    assert len(cap.recent) == 7                     # the parser sees every one
    assert len(_files(tmp_path, "a")) == 2          # disk: first of each URL+size


def test_each_tab_keeps_its_own_share(tmp_path):
    """A busy first tab spends its share, not the run's: later tabs still
    write their first payloads."""
    budget = DiskBudget(mb=1, tab_mb=0.3)
    page1, _ = _cap(tmp_path, "busy", budget)
    for i in range(200):
        page1.handlers["response"](_Resp(page1, PP, "x" * (5000 + i)))
    assert sum(f.stat().st_size for f in _files(tmp_path, "busy")) <= 0.3 * 1024 * 1024
    for name in ("t2", "t3"):
        page, _ = _cap(tmp_path, name, budget)
        page.handlers["response"](_Resp(page, PP, "y" * 5000))
        assert _files(tmp_path, name)


def test_third_party_hosts_are_not_written(tmp_path):
    page, cap = _cap(tmp_path, "a", DiskBudget(10))
    page.handlers["response"](_Resp(page, "https://cdn.onetrust.com/consent.json", "z" * 5000))
    assert len(cap.recent) == 1 and not _files(tmp_path, "a")


def test_refusals_are_not_activity(tmp_path):
    page, cap = _cap(tmp_path, "a", DiskBudget(10))
    cap.last_activity = 0
    page.handlers["response"](_Resp(page, PP, "<title>Too many</title>" * 20, status=429))
    page.handlers["response"](_Resp(page, "https://widget.example.com/x", "e" * 300, status=403))
    assert cap.last_activity == 0 and not cap.recent
    assert cap.refused_within(600) == 1             # the third-party 403 is not ours


def test_payloads_after_a_mark_are_pinned_and_labelled(tmp_path):
    page, cap = _cap(tmp_path, "a", DiskBudget(10))
    page.handlers["response"](_Resp(page, PP, "a" * 300))
    cap.mark("tab:Corners")
    page.handlers["response"](_Resp(page, PP + "?e=1", "b" * 300))
    assert [p[4] for p in cap.pinned] == ["tab:Corners"]
    rows = [json.loads(line) for line in (tmp_path / "a" / "index.jsonl").read_text().splitlines()]
    assert {"mark": "tab:Corners"}.items() <= rows[1].items()
    assert rows[2]["after"] == "tab:Corners"
