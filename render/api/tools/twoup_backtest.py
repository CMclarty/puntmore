"""Compare the 2-Up rescue table with Bookie Bashing's 2-up tracker, by hand.

Bookie Bashing's terms ban automated access, so this NEVER fetches anything:
export the tracker yourself as a CSV and point this at the file. It buckets the
rows by win probability exactly as the model does (`app/twoup/model.py`),
compares the observed rescue rate per bucket with `rescue_table.json`, prints
the comparison and, with `--merge`, writes a new table.

EXPECTED CSV (one row per backed selection; header names are matched
case-insensitively, spaces/dashes treated as underscores; aliases in brackets):

  date            the match date, any format (only echoed)            [match_date]
  home            home team                                          [home_team]
  away            away team                                          [away_team]
  bet365_price    bet365 decimal price of the selection backed       [price, odds, b365]
    -- or --
  implied_prob    1 / bet365 price, as 0.47 or 47 (%)                 [implied, implied_probability]
  two_up          did the selection go two goals up: 1/0, yes/no, y/n, true/false
                                                                      [two_up_occurred, went_two_up, 2up]
  won             did the selection win at full time: same spellings  [win, result_won]

Optional:
  fair_prob       a de-vigged (e.g. Pinnacle) win probability. When present it
                  is the bucket key, matching the live model, which buckets by
                  Pinnacle's fair P(win). Without it the bucket key is
                  1/bet365 price divided by (1 + --margin): bet365's implied
                  probability carries its margin, so pass its typical 1X2
                  overround (e.g. --margin 0.05) to bring it near fair.   [fair, pinnacle_prob]

A row is a RESCUE when two_up is true and won is false: the selection went two
up, bet365 paid it early, and it did not go on to win. The observed rate per
bucket is rescues / rows in the bucket — the same unconditional quantity the
table holds.

With --merge each bucket is the observed rate shrunk toward the current table
by --prior-n pseudo-rows (default 200), so a thin bucket moves a little and a
well-sampled one moves a lot; `n` accumulates and `source` names the file.

  python tools/twoup_backtest.py tracker.csv
  python tools/twoup_backtest.py tracker.csv --margin 0.05 --merge
  python tools/twoup_backtest.py tracker.csv --merge --out /tmp/rescue_table.json

Record the comparison in the tool's notes (render/api/AGENTS.md, 365 tool).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.twoup import model  # noqa: E402

ALIASES = {
    "date": ("date", "match_date"),
    "home": ("home", "home_team"),
    "away": ("away", "away_team"),
    "price": ("bet365_price", "price", "odds", "b365"),
    "implied": ("implied_prob", "implied", "implied_probability"),
    "fair": ("fair_prob", "fair", "pinnacle_prob"),
    "two_up": ("two_up", "two_up_occurred", "went_two_up", "2up"),
    "won": ("won", "win", "result_won"),
}
TRUE = {"1", "y", "yes", "true", "t", "won", "w"}
FALSE = {"0", "n", "no", "false", "f", "lost", "l", ""}


def _key(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _col(header: dict[str, str], field: str) -> str | None:
    for alias in ALIASES[field]:
        if alias in header:
            return header[alias]
    return None


def _flag(value: str) -> bool:
    v = (value or "").strip().lower()
    if v in TRUE:
        return True
    if v in FALSE:
        return False
    raise ValueError(f"not a yes/no value: {value!r}")


def _prob(value: str) -> float:
    p = float(str(value).strip().rstrip("%"))
    return p / 100.0 if p > 1.0 else p


def read_rows(path: Path, margin: float = 0.0) -> tuple[list[dict], list[str]]:
    """Parsed rows {p, two_up, won} and the problems with the rest (never a guess)."""
    rows, problems = [], []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = {_key(h): h for h in (reader.fieldnames or [])}
        cols = {f: _col(header, f) for f in ALIASES}
        missing = [f for f in ("two_up", "won") if cols[f] is None]
        if cols["price"] is None and cols["implied"] is None and cols["fair"] is None:
            missing.append("bet365_price or implied_prob")
        if missing:
            raise SystemExit(f"{path}: missing column(s): {', '.join(missing)}")
        for i, raw in enumerate(reader, start=2):
            try:
                if cols["fair"] and (raw.get(cols["fair"]) or "").strip():
                    p = _prob(raw[cols["fair"]])
                elif cols["implied"] and (raw.get(cols["implied"]) or "").strip():
                    p = _prob(raw[cols["implied"]]) / (1.0 + margin)
                else:
                    price = float(raw[cols["price"]])
                    if price <= 1.0:
                        raise ValueError(f"price {price}")
                    p = (1.0 / price) / (1.0 + margin)
                if not 0.0 < p < 1.0:
                    raise ValueError(f"probability {p}")
                rows.append({"p": p, "two_up": _flag(raw[cols["two_up"]]),
                             "won": _flag(raw[cols["won"]])})
            except (ValueError, KeyError, TypeError) as exc:
                problems.append(f"line {i}: {exc}")
    return rows, problems


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def compare(rows: list[dict], table: model.RescueTable) -> list[dict]:
    """Per bucket: n, rescues, observed rate and its 95% interval, the model's
    rate, and whether the model sits inside the interval."""
    out = []
    for b in table.buckets:
        inb = [r for r in rows if table.bucket_of(r["p"]) == b]
        k = sum(1 for r in inb if r["two_up"] and not r["won"])
        n = len(inb)
        lo, hi = wilson(k, n)
        out.append({"lo": b.lo, "hi": b.hi, "n": n, "rescues": k,
                    "observed": (k / n) if n else None, "ci": (lo, hi),
                    "model": b.rescue, "inside": (lo <= b.rescue <= hi) if n else None})
    outside = [r for r in rows if table.bucket_of(r["p"]) is None]
    if outside:
        k = sum(1 for r in outside if r["two_up"] and not r["won"])
        out.append({"lo": None, "hi": None, "n": len(outside), "rescues": k,
                    "observed": k / len(outside), "ci": wilson(k, len(outside)),
                    "model": table.default, "inside": None})
    return out


def merged(table: model.RescueTable, cmp: list[dict], prior_n: float, label: str
           ) -> model.RescueTable:
    buckets = []
    for b, c in zip(table.buckets, cmp):
        if not c["n"]:
            buckets.append(b)
            continue
        rate = (c["rescues"] + b.rescue * prior_n) / (c["n"] + prior_n)
        buckets.append(model.Bucket(lo=b.lo, hi=b.hi, rescue=round(rate, 5),
                                    n=b.n + c["n"], source=label))
    return model.RescueTable(buckets=tuple(buckets), default=table.default, key=table.key)


def render(cmp: list[dict]) -> str:
    lines = [f"{'bucket':<12}{'n':>6}{'rescues':>9}{'observed':>10}{'95% CI':>17}"
             f"{'model':>8}{'diff':>8}  ok"]
    for c in cmp:
        name = "outside" if c["lo"] is None else f"{c['lo']:.2f}-{c['hi']:.2f}"
        if c["observed"] is None:
            lines.append(f"{name:<12}{0:>6}{'':>9}{'—':>10}{'':>17}{c['model']:>8.4f}")
            continue
        lo, hi = c["ci"]
        ok = "" if c["inside"] is None else ("yes" if c["inside"] else "NO")
        lines.append(f"{name:<12}{c['n']:>6}{c['rescues']:>9}{c['observed']:>10.4f}"
                     f"{f'{lo:.4f}-{hi:.4f}':>17}{c['model']:>8.4f}"
                     f"{c['observed'] - c['model']:>+8.4f}  {ok}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("csv", type=Path, help="Bookie Bashing 2-up tracker export (CSV)")
    ap.add_argument("--table", type=Path, default=model.DEFAULT_TABLE_PATH,
                    help="rescue table to compare against (default: the shipped one)")
    ap.add_argument("--margin", type=float, default=0.0,
                    help="bet365 1X2 overround to strip from an implied probability")
    ap.add_argument("--merge", action="store_true", help="write a new rescue table")
    ap.add_argument("--prior-n", type=float, default=200.0,
                    help="pseudo-rows of the current table each bucket is shrunk toward")
    ap.add_argument("--out", type=Path, default=None,
                    help="where --merge writes (default: over --table)")
    args = ap.parse_args(argv)

    table = model.load_rescue_table(args.table)
    if not table.loaded:
        raise SystemExit(f"cannot read the rescue table: {table.path}")
    rows, problems = read_rows(args.csv, args.margin)
    print(f"{len(rows)} row(s) read from {args.csv}"
          + (f", {len(problems)} skipped" if problems else ""))
    for p in problems[:10]:
        print(f"  skipped {p}")
    cmp = compare(rows, table)
    print(render(cmp))
    if args.merge:
        label = f"bb-tracker {args.csv.name} {date.today().isoformat()}"
        new = merged(table, cmp, args.prior_n, label)
        out = args.out or args.table
        notes = (f"Merged {date.today().isoformat()} from {args.csv.name} "
                 f"({len(rows)} rows, prior_n {args.prior_n:g}, margin {args.margin:g}) "
                 f"by tools/twoup_backtest.py. Bucket key: the row's fair_prob where "
                 f"present, else 1/bet365 price / (1 + margin).")
        out.write_text(json.dumps(new.to_json(notes=notes), indent=2) + "\n")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
