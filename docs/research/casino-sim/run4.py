import numpy as np, slots as S
from run3 import minbank
rng = np.random.default_rng(5)
N = 40000
SL = S.slots(0.96)
print("== S2 (fixed) sticky: profit = max(final-B,0)-D if completed, else -D")
for vol in ("low","med","high"):
    for stake in (1.0, 5.0):
        bal, turn, comp = S.play(rng, SL[vol], 100.0, stake, 500.0, N)
        profit = np.where(comp, np.maximum(bal - 100.0, 0) - 50.0, -50.0)
        S.summ(f"{vol:4s} stake £{stake:.2f}", profit, f"P(bust) {1-comp.mean():.2f}  minBR {minbank(profit):.0f}")
for goal in (150, 200, 300):
    bal, turn, comp = S.play(rng, SL["high"], 100.0, 5.0, 500.0, N, switch=(goal, SL["low"], 0.5))
    profit = np.where(comp, np.maximum(bal - 100.0, 0) - 50.0, -50.0)
    S.summ(f"high £5 until bal>={goal}, then low £0.50", profit, f"P(bust) {1-comp.mean():.2f}  minBR {minbank(profit):.0f}")
