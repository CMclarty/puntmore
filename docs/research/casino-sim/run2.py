import numpy as np, slots as S
rng = np.random.default_rng(7)
N = 40000
SL = S.slots(0.96)
print("== S6 cashback 10% of net losses, max £20 cashback (i.e. losses up to £200 count), paid as cash, RTP 96%")
print("   formula: EV = -h*E[T] + r*E[min(netloss+, Lcap)]")
# (a) grind fixed turnover 200 on low vol at £0.20, starting bankroll 200
for vol, stake, turn in (("low",0.20,200),("low",1.0,200),("high",1.0,200),("high",5.0,200)):
    bal, t, comp = S.play(rng, SL[vol], 200.0, stake, turn, N)
    net = bal - 200.0
    cb = np.minimum(0.10*np.maximum(-net,0), 20.0)
    S.summ(f"fixed turnover £{turn}: {vol} £{stake}", net+cb, f"E[T] {t.mean():.0f}  mean cashback {cb.mean():.2f}")
# (b) deposit 200, play high vol £5 until bust or balance >= 400 (stop when doubled)
for vol, stake, stop in (("high",5.0,400),("high",5.0,300),("med",5.0,400)):
    bal, t, comp = S.play(rng, SL[vol], 200.0, stake, 1e9, N, stop_above=stop)
    net = bal - 200.0
    cb = np.minimum(0.10*np.maximum(-net,0), 20.0)
    S.summ(f"play-to-bust-or-{stop}: {vol} £{stake}", net+cb, f"E[T] {t.mean():.0f}  P(bust) {np.mean(bal<0.1):.2f}")
# (c) European roulette single £200 bet on red vs single number (if table max allows)
for name, p, pay in (("red",18/37,2),("single number",1/37,36)):
    ev = -0.027*200 + 0.10*200*(1-p)
    print(f"roulette one £200 bet on {name:14s} EV {ev:6.2f}  (analytic)")

print("\n== S7 cash-first deposit bonus: D=50 cash used first (cash play does NOT count), then B=50 bonus-only, 10x B, cashable")
print("   Withdrawing cash forfeits bonus. V(B) from S1 depends on how bonus is played.")
VB = {}
for vol, stake in (("low",0.2),("high",5.0)):
    b,t,c = S.play(rng, SL[vol], 50.0, stake, 500.0, N); VB[(vol,stake)] = np.where(c,b,0.0)
for cvol, cstake, target in (("low",0.2,None),("high",5.0,100),("high",5.0,150),("high",5.0,250)):
    # play cash until bust or >= target (then withdraw, forfeit bonus); target None = must lose it all (no early withdraw)
    stop = target if target else 1e12
    b,t,c = S.play(rng, SL[cvol], 50.0, cstake, 1e12, N, stop_above=stop)
    bust = b < 0.1
    for (bv, bs), v in VB.items():
        prof = np.where(bust, v[rng.integers(0,N,N)], b) - 50.0
        S.summ(f"cash {cvol} £{cstake} stop@{target}; bonus {bv} £{bs}", prof, f"P(cash bust) {bust.mean():.2f}")

print("\n== S5 'wager £20 on slots, get 50 x £0.10 free spins wager-free', RTP 96% on both")
print("   analytic EV = 50*0.10*0.96 - 20*0.04 = %.2f" % (50*0.1*0.96 - 20*0.04))
for vol, stake in (("low",0.20),("high",1.0)):
    b,t,c = S.play(rng, SL[vol], 20.0, stake, 20.0, N)  # start with £20 own cash
    w = S.spin(rng, SL["med"], N*50).reshape(N,50).sum(1)*0.10
    S.summ(f"qualify {vol} £{stake} from £20 cash", b-20.0+w, f"P(needed >£20 cash: bust before done) {1-c.mean():.2f}")
