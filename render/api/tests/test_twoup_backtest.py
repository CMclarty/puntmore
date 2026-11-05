"""tools/twoup_backtest.py: reads a hand-exported tracker CSV, buckets it like
the model, compares, and --merge writes a valid table. Nothing is fetched."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import twoup_backtest as bt  # noqa: E402

from app.twoup import model  # noqa: E402


def _csv(tmp_path, lines):
    p = tmp_path / "tracker.csv"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_reads_aliases_flags_and_skips_bad_rows(tmp_path):
    p = _csv(tmp_path, [
        "Date,Home Team,Away,Bet365 Price,Two Up,Won",
        "2026-10-01,A,B,2.0,yes,no",
        "2026-10-01,C,D,1.5,1,1",
        "2026-10-01,E,F,abc,0,0",
        "2026-10-01,G,H,3.0,maybe,0",
    ])
    rows, problems = bt.read_rows(p)
    assert [r["p"] for r in rows] == pytest.approx([0.5, 1 / 1.5])
    assert rows[0] == {"p": 0.5, "two_up": True, "won": False}
    assert len(problems) == 2


def test_missing_columns_are_named(tmp_path):
    with pytest.raises(SystemExit, match="won"):
        bt.read_rows(_csv(tmp_path, ["date,home,away,price,two_up", "x,a,b,2,1"]))


def test_compare_and_merge(tmp_path):
    lines = ["date,home,away,implied_prob,two_up,won"]
    # 0.50-0.60 bucket: 100 rows, 4 rescues (two up, not won).
    lines += [f"d,h{i},a,55%,{1 if i < 30 else 0},{0 if i < 4 else 1}" for i in range(100)]
    p = _csv(tmp_path, lines)
    table = model.load_rescue_table()
    rows, _ = bt.read_rows(p)
    cmp = bt.compare(rows, table)
    hit = next(c for c in cmp if c["lo"] == 0.5)
    assert (hit["n"], hit["rescues"], hit["observed"]) == (100, 4, 0.04)
    assert "0.50-0.60" in bt.render(cmp)

    out = tmp_path / "new.json"
    assert bt.main([str(p), "--merge", "--out", str(out), "--prior-n", "100"]) == 0
    new = model.parse_rescue_table(json.loads(out.read_text()))
    b = new.bucket_of(0.55)
    assert b.rescue == pytest.approx((4 + table.rate(0.55) * 100) / 200)
    assert b.n == 100 and b.source.startswith("bb-tracker")
    assert new.rate(0.25) == table.rate(0.25)            # untouched without data


def test_margin_brings_implied_toward_fair(tmp_path):
    p = _csv(tmp_path, ["date,home,away,price,two_up,won", "d,a,b,2.0,0,1"])
    rows, _ = bt.read_rows(p, margin=0.05)
    assert rows[0]["p"] == pytest.approx(0.5 / 1.05)
