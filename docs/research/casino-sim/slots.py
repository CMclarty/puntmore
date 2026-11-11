import numpy as np

def make_slot(values, rel, rtp):
    v = np.array(values, float); r = np.array(rel, float)
    scale = rtp / (r * v).sum()
    p = r * scale
    assert p.sum() < 1, p.sum()
    vals = np.concatenate([[0.0], v]); probs = np.concatenate([[1 - p.sum()], p])
    return vals, probs

def stats(slot):
    v, p = slot
    m = (v * p).sum(); sd = np.sqrt(((v - m) ** 2 * p).sum())
    return m, sd, 1 - p[0]

# three illustrative slot models (payout multiple of stake per spin)
LOW  = ([0.5,1,2,5,10,20,50],[10,8,6,2,0.8,0.3,0.05])
MED  = ([0.5,1,2,5,10,25,100,500],[10,6,5,2,0.8,0.3,0.04,0.004])
HIGH = ([0.5,1,2,5,20,100,500,2000,5000],[8,5,4,1.5,0.4,0.05,0.006,0.0008,0.00015])

def slots(rtp):
    return {k: make_slot(*d, rtp) for k, d in (("low", LOW), ("med", MED), ("high", HIGH))}

def spin(rng, slot, n):
    v, p = slot
    return v[rng.choice(len(v), size=n, p=p)]

def play(rng, slot, bal, stake, target_turnover, n, contrib=1.0, min_stake=0.10,
         stop_above=None, switch=None, max_spins=200000):
    """Vectorised play of n independent trials.
    bal: starting balance (array or scalar). Plays until counted turnover >= target
    or balance < min_stake (bust) or balance >= stop_above (optional early stop).
    switch=(threshold, slot2, stake2): once balance >= threshold, use slot2/stake2 (grind phase).
    Returns final balance, actual stake turnover, completed flag."""
    bal = np.full(n, bal, float) if np.isscalar(bal) else bal.astype(float).copy()
    counted = np.zeros(n); turn = np.zeros(n)
    active = np.ones(n, bool)
    v, p = slot
    for _ in range(max_spins):
        active &= (counted < target_turnover - 1e-9) & (bal >= min_stake - 1e-12)
        if stop_above is not None:
            active &= bal < stop_above
        idx = np.nonzero(active)[0]
        if idx.size == 0: break
        s = np.full(idx.size, stake, float)
        mult = spin(rng, slot, idx.size)
        if switch is not None:
            thr, slot2, stake2 = switch
            g = bal[idx] >= thr
            if g.any():
                s[g] = stake2
                mult[g] = spin(rng, slot2, int(g.sum()))
        s = np.minimum(s, bal[idx])
        remaining = (target_turnover - counted[idx]) / contrib
        s = np.minimum(s, np.maximum(remaining, min_stake))
        bal[idx] += s * (mult - 1)
        turn[idx] += s; counted[idx] += s * contrib
    completed = counted >= target_turnover - 1e-9
    return bal, turn, completed

def summ(name, profit, extra=""):
    m = profit.mean(); sd = profit.std(); se = sd / np.sqrt(len(profit))
    print(f"{name:58s} EV {m:7.2f} (±{1.96*se:4.2f})  SD {sd:7.2f}  P(profit>0) {np.mean(profit>0):5.2f}  {extra}")
    return m, sd
