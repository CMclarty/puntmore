import numpy as np, slots as S
rng = np.random.default_rng(2026)
N = 30000
SL = S.slots(0.96)
def bonus_value(start, vol, stake, W, cap=None, sticky=False):
    start = np.asarray(start, float)
    tgt = W*start; res = np.zeros(len(start))
    order = np.argsort(tgt)
    for ch in np.array_split(order, 100):
        b,t,c = S.play(rng, SL[vol], start[ch], stake, tgt[ch].max(), len(ch))
        v = np.where(c, b, 0.0)
        if cap is not None: v = np.minimum(v, cap)
        res[ch] = v
    res[start <= 0] = 0
    return res
print("== 888 100% to £100: D=100 cash-first (only bonus stakes count), B=100, 10x, winnings cap £100")
for bvol, bst in (("low",0.2),("low",1.0),("high",1.0)):
    vb = bonus_value(np.full(N,100.0), bvol, bst, 10, cap=100.0)
    for cpol in ("grind", "high5_stop200"):
        if cpol == "grind":
            b,t,c = S.play(rng, SL["low"], 100.0, 0.2, 1e12, N)   # play cash to zero
        else:
            b,t,c = S.play(rng, SL["high"], 100.0, 5.0, 1e12, N, stop_above=200)
        bust = b < 0.1
        prof = np.where(bust, vb[rng.integers(0,N,N)], b) - 100
        S.summ(f"cash {cpol}; bonus {bvol} £{bst} (cap 100)", prof, f"P(cash exhausted) {bust.mean():.2f}  V(B) {vb.mean():.2f}")
print("\n== WH Vegas (3rd-party terms): 200 x 10p FS, winnings cap £30")
for vol in ("med","high"):
    w = S.spin(rng, SL[vol], N*200).reshape(N,200).sum(1)*0.10
    S.summ(f"{vol} uncapped", w); S.summ(f"{vol} cap 30", np.minimum(w,30))
print("\n== WH Vegas (3rd-party): £20 no-deposit bonus, 10x, max winnings £25")
for vol, st in (("low",0.2),("low",1.0),("high",1.0),("high",2.0)):
    S.summ(f"{vol} £{st}", bonus_value(np.full(N,20.0), vol, st, 10, cap=25.0))
print("\n== 888 200 FS: 200 x 10p -> winnings (cap £100 conversion) -> 10x wagering; minus £20 x 4% qualifying")
for vol in ("med",):
    w = np.minimum(S.spin(rng, SL[vol], N*200).reshape(N,200).sum(1)*0.10, 100.0)
    for bvol, bst in (("low",0.2),("high",1.0)):
        v = bonus_value(w, bvol, bst, 10)
        S.summ(f"spins {vol}; wager {bvol} £{bst}", v - 0.8, f"mean winnings {w.mean():.2f}")
print("\n== Unibet 300 FS: 300 x 10p (RTP 96% model) -> 10x on winnings; minus £30 x 4% qualifying")
w = S.spin(rng, SL["high"], N*300).reshape(N,300).sum(1)*0.10
for bvol, bst in (("low",0.2),("high",1.0),("high",5.0)):
    v = bonus_value(w, bvol, bst, 10)
    S.summ(f"wager {bvol} £{bst}", v - 1.2, f"mean winnings {w.mean():.2f}")
