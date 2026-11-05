import dataclasses

from puntmore_home.scraper.browser import BlockedError, Fetched, FetchError
from puntmore_home.scraper.runner import run_source
from puntmore_home.scraper.sources import load_sources


def example_source(cfg):
    # The shipped example is disabled; tests exercise it as if enabled.
    return dataclasses.replace(load_sources(cfg.sources_dir)["example-tipster"], enabled=True)


def test_ok_run_stores_tips_and_queues_them(cfg, store, fixture_html):
    src = example_source(cfg)
    fetcher = lambda url, s: Fetched(url=url, status=200, html=fixture_html)  # noqa: E731
    r = run_source(src, store, cfg=cfg, fetcher=fetcher, force=True, notify=False)
    assert r.status == "ok" and r.items == 3 and r.new_tips == 3
    assert store.conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0] == 3
    assert [row["type"] for row in store.outbox_pending()] == ["tip"] * 3
    assert store.source_state(src.name)["healthy"] == 1
    assert (cfg.raw_dir / src.name).exists() and r.raw_path.endswith(".html")
    # a second sweep of the same page adds nothing new
    r2 = run_source(src, store, cfg=cfg, fetcher=fetcher, force=True, notify=False)
    assert r2.status == "ok" and r2.new_tips == 0


def test_empty_page_marks_unhealthy_and_escalates(cfg, store):
    src = example_source(cfg)
    fetcher = lambda url, s: Fetched(url=url, status=200, html="<html><body>new layout</body></html>")  # noqa: E731
    r = run_source(src, store, cfg=cfg, fetcher=fetcher, force=True, notify=False)
    assert r.status == "empty"
    assert store.source_state(src.name)["healthy"] == 0
    esc = store.open_escalations()
    assert len(esc) == 1 and esc[0]["kind"] == "unhealthy_source" and esc[0]["ref"] == src.name


def test_block_sets_cooloff_and_skips_next_run(cfg, store):
    src = example_source(cfg)

    def blocked(url, s):
        raise BlockedError("captcha")

    r = run_source(src, store, cfg=cfg, fetcher=blocked, force=True, notify=False)
    assert r.status == "blocked"
    state = store.source_state(src.name)
    assert state["healthy"] == 0 and state["cooloff_until"]
    assert store.open_escalations()[0]["kind"] == "blocked_source"
    r2 = run_source(src, store, cfg=cfg, fetcher=blocked, force=False, notify=False)
    assert r2.status == "skipped" and "cooling off" in r2.error


def test_fetch_error_does_not_change_health(cfg, store):
    src = example_source(cfg)

    def flaky(url, s):
        raise FetchError("timeout")

    r = run_source(src, store, cfg=cfg, fetcher=flaky, force=True, notify=False)
    assert r.status == "fetch_error"
    assert store.source_state(src.name)["healthy"] == 1
    assert store.open_escalations() == []


def test_kill_switch_skips(cfg, store, fixture_html):
    cfg.kill_switch_file.write_text("x")
    src = example_source(cfg)
    r = run_source(src, store, cfg=cfg, fetcher=lambda u, s: Fetched(u, 200, fixture_html), force=True, notify=False)
    assert r.status == "skipped" and "kill" in r.error


def test_budget_exhausted_skips(cfg, store, fixture_html):
    src = example_source(cfg)
    for i in range(cfg.pages_per_hour):
        store.log_page_load("other", f"https://x/{i}", 200)
    r = run_source(src, store, cfg=cfg, fetcher=lambda u, s: Fetched(u, 200, fixture_html), force=False, notify=False)
    assert r.status == "skipped" and "budget" in r.error
