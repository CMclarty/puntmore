"""The morning Paddy health lines: a day of sweeps summarised, then reset."""
from app.cards import health


def _run(rows=3, hybrid=0, disagree=0, events=10):
    return {"events": events, "by_market": {
        "corners": dict(rows=rows, hybrid=hybrid, split_disagreement=disagree),
        "cards": dict(rows=0, hybrid=0, split_disagreement=0),
    }}


def setup_function():
    health.report()                                     # start every test on an empty day


def test_a_day_of_sweeps_is_summarised_and_the_counter_resets():
    health.record(_run(rows=3, hybrid=2), 3)
    health.record(_run(rows=5, hybrid=1, events=12), 5)
    lines = health.report(saves=4, most=1)
    assert lines[0] == "Paddy: 2 sweeps · 12 fixtures · 8 value rows (corners 8)"
    assert lines[1] == "Team split: 3 hybrid rows · 0 disagreements"
    assert lines[2] == "Scraper: saves 4 · most 1"
    assert len(lines) == 3
    assert health.report()[0] == "Paddy: ⚠ no sweep ran"


def test_a_pattern_of_split_disagreements_is_flagged_but_a_stray_one_is_not():
    for _ in range(5):
        health.record(_run(hybrid=1, disagree=1), 1)
    assert "⚠ check the handicap sign" in health.report()[1]
    health.record(_run(hybrid=40, disagree=1), 40)
    assert "⚠" not in health.report()[1]


def test_failed_sweeps_and_silent_feeds_are_called_out():
    health.record(None, 0, error=True)
    health.record(_run(), 3)
    lines = health.report(saves=0, most=0)
    assert lines[0].endswith("⚠ 1 failed")
    assert lines[2].endswith("⚠ nothing posted")
