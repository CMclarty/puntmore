import numpy as np, slots as S
rng = np.random.default_rng(99)
N = 60000
SL = S.slots(0.96)
def minbank(x, lo=1, hi=1e6):
    # smallest bankroll BR with E[log(BR+x)] >= log(BR)   (full Kelly acceptance)
    if x.min() >= 0: return 0.0
    f = lambda br: np.mean(np.log1p(x/br))
    if f(hi) < 0: return float('inf')
    lo = max(lo, -x.min()+1e-6)
    for _ in range(80):
        mid = np.sqrt(lo*hi)
        if f(mid) >= 0: hi = mid
        else: lo = mid
    return hi
if __name__ == "__main__":
    cases = {}
    b,t,c = S.play(rng, SL["high"], 100.0, 5.0, 500.0, N); cases["S2 sticky D50+B50 high £5"] = np.where(c,b-100,-50.)
    b,t,c = S.play(rng, SL["med"], 100.0, 1.0, 500.0, N); cases["S2 sticky D50+B50 med £1"] = np.where(c,b-100,-50.)
    b,t,c = S.play(rng, SL["high"], 50.0, 5.0, 1e12, N, stop_above=100)
    v_b,_,cb = S.play(rng, SL["low"], 50.0, 0.2, 500.0, N); vb = np.where(cb, v_b, 0.)
    cases["S7 cash-first: cash high £5 stop@100, bonus low £0.20"] = np.where(b<0.1, vb, b) - 50
    # roulette cashback single number: stake 200, lose w.p. 36/37 -> -200+20 ; win -> +7000
    x = np.where(rng.random(N) < 1/37, 7000.0, -180.0); cases["S6 cashback £200 on one roulette number"] = x
    x = np.where(rng.random(N) < 18/37, 200.0, -180.0); cases["S6 cashback £200 on red"] = x
    for k, x in cases.items():
        mu, sd = x.mean(), x.std()
        print(f"{k:55s} EV {mu:7.2f} SD {sd:7.2f} worst {x.min():7.1f}  minBR(full Kelly) {minbank(x):9.0f}  minBR(half Kelly≈2x) {2*minbank(x):9.0f}  sigma^2/2mu {sd*sd/(2*mu):9.0f}")
