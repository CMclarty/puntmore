import numpy as np, slots as S
rng = np.random.default_rng(20261111)
N = 40000
SL = S.slots(0.96)
for k in SL: print(k, ["%.3f" % x for x in S.stats(SL[k])])
print("\n== S1 cashable bonus, bonus-only balance: B=50, 10x B (T=500), RTP 96%. Naive EV = 50-500*0.04 = 30")
for vol in ("low","med","high"):
    for stake in (0.20, 1.0, 2.0, 5.0):
        bal, turn, comp = S.play(rng, SL[vol], 50.0, stake, 500.0, N)
        profit = np.where(comp, bal, 0.0)
        S.summ(f"{vol:4s} stake £{stake:.2f}", profit, f"P(bust) {1-comp.mean():.2f}  E[turnover] {turn.mean():6.1f}  Wald check 50-0.04*E[T]={50-0.04*turn.mean():.2f}")

print("\n== S2 sticky bonus: D=50 + B=50 combined, 10x B (T=500), bonus removed on withdrawal. Formula EV = B*P(bust) - h*E[T]")
for vol in ("low","med","high"):
    for stake in (1.0, 5.0):
        bal, turn, comp = S.play(rng, SL[vol], 100.0, stake, 500.0, N)
        profit = np.where(comp, bal - 100.0, -50.0)
        S.summ(f"{vol:4s} stake £{stake:.2f}", profit, f"P(bust) {1-comp.mean():.2f}  formula {50*(1-comp.mean())-0.04*turn.mean():.2f}")
# Snyder-style: high vol max stake until balance >= goal, then grind low vol small stake
for goal in (150, 200, 300):
    bal, turn, comp = S.play(rng, SL["high"], 100.0, 5.0, 500.0, N, switch=(goal, SL["low"], 0.5))
    profit = np.where(comp, bal - 100.0, -50.0)
    S.summ(f"high £5 until bal>={goal}, then low £0.50", profit, f"P(bust) {1-comp.mean():.2f}")

print("\n== S3 free spins 50 x £0.10, RTP 96%. Wager-free EV = 4.80")
for vol in ("low","med","high"):
    w = S.spin(rng, SL[vol], N*50).reshape(N,50).sum(1)*0.10
    for cap in (None, 20, 10):
        x = w if cap is None else np.minimum(w, cap)
        S.summ(f"{vol:4s} wager-free cap {cap}", x)
print("-- winnings become bonus with 10x wagering on winnings, then played on same slot at £0.10/£1")
for vol in ("low","high"):
    w = S.spin(rng, SL[vol], N*50).reshape(N,50).sum(1)*0.10
    for stake in (0.10, 1.0):
        bal, turn, comp = S.play(rng, SL[vol], w, stake, 0, N)  # placeholder, replaced below
        # target turnover differs per trial: emulate by running per-trial target via scaling
    # per-trial targets: loop in chunks by rounding winnings
    for stake in (0.10, 1.0):
        prof = np.zeros(N)
        tgt = 10*w
        # run with large target then truncate is wrong; do exact per-trial: group by unique targets
        order = np.argsort(tgt)
        res = np.zeros(N)
        for chunk in np.array_split(order, 200):
            t = tgt[chunk].max()
            # approximate: use chunk max target (chunks are narrow)
            b, tt, c = S.play(rng, SL[vol], w[chunk], stake, t, len(chunk))
            res[chunk] = np.where(c, b, 0.0)
        res[w == 0] = 0
        S.summ(f"{vol:4s} FS winnings 10x, play stake £{stake:.2f}", res, f"naive 4.80*(1-10*0.04)={4.8*0.6:.2f}")
