import numpy as np, slots as S
rng = np.random.default_rng(11)
N = 40000
SL = S.slots(0.96)
print("== S2b GB-style sticky: separate bonus balance B=50, 10x B, on completion only (final - B) converts")
for vol, stake in (("low",0.2),("low",1),("med",1),("high",1),("high",5)):
    b,t,c = S.play(rng, SL[vol], 50.0, stake, 500.0, N)
    prof = np.where(c, np.maximum(b-50.0,0), 0.0)
    S.summ(f"{vol} £{stake}", prof, f"P(bust) {1-c.mean():.2f}")
for goal in (100,150,250):
    b,t,c = S.play(rng, SL["high"], 50.0, 5.0, 500.0, N, switch=(goal, SL["low"], 0.5))
    prof = np.where(c, np.maximum(b-50.0,0), 0.0)
    S.summ(f"high £5 until bal>={goal}, then low £0.50", prof, f"P(bust) {1-c.mean():.2f}")
print("\n== S1b cashable bonus-only: high £5 until bal>=goal then low £0.50 (lock in)")
for goal in (100,200):
    b,t,c = S.play(rng, SL["high"], 50.0, 5.0, 500.0, N, switch=(goal, SL["low"], 0.5))
    S.summ(f"high £5 until {goal}, then low £0.50", np.where(c,b,0.0), f"P(bust) {1-c.mean():.2f}")
print("\n== RTP sensitivity, cashable B=50 10x, low vol £0.20 (near-naive)")
for rtp in (0.99,0.97,0.96,0.94,0.92,0.90):
    sl = S.slots(rtp)["low"]
    b,t,c = S.play(rng, sl, 50.0, 0.2, 500.0, 20000)
    print(f"RTP {rtp:.2f}: naive {50-500*(1-rtp):6.2f}  sim {np.where(c,b,0).mean():6.2f}")
