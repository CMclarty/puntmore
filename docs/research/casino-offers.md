# Casino offers: research notes (11 Nov 2026)

**Status: research only.** Nothing here is a decision and nothing is built. The casino stream is
not in the register (`docs/decisions.md`), has no spec and no plan, and no code in this repo
reads, values or completes a casino offer. An earlier attempt by a local model (a spec and a
`hub/puntmore_hub/casino/` package) was discarded unmerged; a copy is on the Hub machine in
`~/qwen-notes/leftover-2026-11-10/`, kept for reference, not as a starting point. Before any code: a design conversation with Callum, then a spec, then
a plan, the same as every other tool.

## What this file is

The findings of one research pass on how casino offers are valued and completed for profit in
the UK: the principle, the maths, the game choice, the terms that matter, the regulation, twenty
real offers read on 11 Nov 2026, and what a valuer would need as inputs. It was written by
Claude from public pages. Every claim carries a mark saying how well it is sourced; the marks
are explained below.

## What Callum said (11 Nov 2026)

The research first concluded that recurring offers for an existing customer were worth little.
Callum corrected that:

> "you're incorrect, there are additional opt in offers worth a few quid a week, it all adds up"
>
> "but not advertised publicly of course"

So:

- The recurring value is in the weekly opt-in offers inside each account. They are shown only
  to the account holder, and this research saw none of them, because it logged into nothing.
- The public offers in section 6 are mostly new-customer offers. They show how the terms and
  the maths work. They do not measure what the stream is worth week to week.
- Do not size the stream from this file's numbers. Size it from what the accounts actually
  offer.

## Open questions for the design conversation

None of these is answered. They are Callum's to answer.

1. How do the in-account offers reach him today (an offers page, an inbox, email, SMS, a
   pop-up), and what does he do when one arrives?
2. Which operators and which accounts are in scope?
3. What should the tool do: value an offer he types in, keep a list of what is open and when
   it expires, track progress through the wagering, or more?
4. Where would it live: a page on `me`, the Console on the Hub, or HubAI?
5. Who reads the offer's terms: Callum by hand, or HubAI from a screenshot, the way AW 26
   slips are read?
6. Does it keep a profit and loss ledger, and does that join the existing P&L?

## What was not verified

- The in-account personalised offers: not seen at all.
- The Gambling Commission's own pages (a certificate failure on the day); the rule wording
  comes from law firms' summaries.
- Every EV assumes a 96% RTP. The same game runs at different RTPs at different operators, so
  an EV is wrong until the operator's own figure is read.
- William Hill, BetMGM, Grosvenor, Midnite and Coral come from third-party listings. Ladbrokes
  and Betfred were not read.
- No operator's full list of prohibited play patterns was collected.

---

# The research report

## Casino offers for profit: principle, maths and decision rules (UK, as of 11 Nov 2026)

Read-only research; nothing was signed up to or logged into.

**How claims are marked**

- **[P]** verified from a primary source (operator terms, regulator, government). URL given.
- **[S]** from a secondary source (law firm, trade press, matched-betting site, affiliate listing). URL given.
- **[D]** my own derivation or simulation, shown in full so it can be checked.
- **[NV]** not verified.

**Simulation note.** Several numbers below come from a Monte Carlo simulator I wrote for this report: `docs/research/casino-sim/` (`slots.py` plus `run1.py` to `run6.py`, 30,000 to 60,000 trials per row; needs numpy). It uses three made-up slot payout distributions, all set to 96% RTP. Their standard deviations per spin, in multiples of the stake, are: "low" 2.6, "med" 5.6 and "high" 14.3, with top prizes of 50x, 500x and 5,000x. Real slots don't publish their full payout distribution, so these are stand-ins for the volatility classes, not models of named games. Treat the simulated EVs as showing how big an effect is and which way it pushes, not as prices for a real offer. A ± figure is the 95% confidence half-width.

---

## 0. The principle in one paragraph

A casino game has negative expectation: every £1 staked loses `h = 1 − RTP` on average. An offer adds value (a bonus, spins or cashback) in exchange for conditions, which usually mean staking a set amount. The offer is worth doing when the value added is bigger than the expected loss on the play the conditions force on you. That is all of it. Everything else is detail that changes one of those two quantities:

- which game you play, and so the house edge and how much of each stake counts;
- whether you go bust before you finish, which stops your losses early;
- caps and stickiness, which cut off part of what you can win;
- terms that can void the whole offer.

**Wald's identity is the core tool [D].** Each round has expected net result `−h × stake`. Under any stopping rule, such as "stop when the wagering is done or the balance is gone":

```
E[final balance] = starting balance − Σ h_g × E[stake turnover on game g]
```

This is exact, not an approximation. It is Wald's identity applied to a sum of round results with mean −h × stake. It holds because play stops at a bounded time. Every formula in section 2 comes from it, together with a description of what you are allowed to withdraw at the end.

---

## 1. Offer types and their mechanics (UK, late 2026)

Since 19 January 2026 a GB-licensed offer cannot mix products. "Bet £10 on football, get 50 free spins" is now banned. For mixed-product offers it means "bet on X, get Y" offers must be all-casino, unless the customer is free to choose which product the reward is spent on (section 5). Wagering on bonus funds is capped at 10x. Remote gaming duty doubled on 1 April 2026. Trade press reports operators shrinking promotional budgets and shifting to small "stake £10, get N free spins" offers [S]. The structures below are what is on offer now.

| Type | Trigger | What is credited | Before it is withdrawable |
|---|---|---|---|
| **Deposit match** ("100% up to £X") | Deposit ≥ minimum, opt in or code, often first deposit only | Bonus funds = match % × deposit, up to a cap | Wagering of bonus ≤ 10x (GB). Bonus may be **cashable** (converts to cash in full) or **sticky** (only winnings above the bonus amount convert). Cash is normally played before bonus. The deposit and its winnings can always be withdrawn, which forfeits the bonus (CMA undertakings, [P] section 5). |
| **"Stake £X, get Y"** (bonus or free spins) | Opt in, then stake £X of **your own cash** on eligible games (often named slots) within N days | Bonus funds, free spins at a stated spin value, or a "live bonus" | The qualifying stake is just play with your own money. What the reward needs depends on its type (below). |
| **Free spins, wager-free** | Part of the above, or a no-deposit or daily gift | N spins at £s each on a named slot | Winnings paid straight to cash. Sometimes capped. Spins expire, typically in 1–7 days. |
| **Free spins with wagering** | Same | N spins | Winnings become bonus funds with up to 10x wagering. |
| **Bonus stake, winnings as cash** ("bonus non-withdrawable, winnings paid as cash", e.g. Sky Vegas £10 Live Bonus [P]) | Deposit or stake requirement | £B of bonus funds that can only be staked | Like a free bet: the bonus is used up as you stake it, and returns go to cash. Whether returns include the stake decides the value (section 2.7). |
| **Cashback / loss-back** | Opt in, play eligible games in a window | ρ% of net losses, up to a cap, as cash or as bonus | Paid as cash: nothing. Paid as bonus: up to 10x wagering. |
| **Reload / weekly offers** | Existing-customer deposit or stake on a set day | Any of the above | As above. |
| **Prize wheels, daily free games, "pick a box"** | Opt in daily, sometimes needs a qualifying stake that day | Random prize: spins, bonus, cash, or nothing | Each prize carries its own terms. Sky Vegas: "Each prize is subject to its own T&Cs with a variable expiry" [P]. |
| **Live casino / table offers** | Stake £X on live games, or a live bonus | Live bonus, cashback on live losses, or free-bet-style chips | Often low contribution weights for table games when wagering slot bonuses. |
| **Bingo / poker** | Separate products since the mixed-product ban. A bingo offer must pay out in bingo. | Bingo tickets, poker tournament tickets | Out of scope here except to note they can no longer be bundled into casino offers. |

The typical existing-customer flow at the big brands is opt in, then stake £X on named slots, then receive spins or a bonus. One live deposit-match example: 888casino's 100% up to £100 is played cash first, and only bonus-balance stakes count toward its 10x wagering [P, section 6 #10]. Sky Vegas's current published offers are a "£10 deposit & staking requirement" for 250 x 10p spins, a £10 live bonus, and a daily free mini-game [P, https://www.skyvegas.com/promotions]. Section 6 gives the detail for each operator.

---

## 2. The value maths

### 2.1 Notation

| Symbol | Meaning |
|---|---|
| `RTP_g`, `h_g = 1 − RTP_g` | Return to player and house edge of game g, **as configured at this operator** (section 3) |
| `c_g` | Contribution weight: the fraction of each stake that counts toward wagering (100% for slots, often 10% or 0% for tables) |
| `B` | Bonus amount |
| `W` | Wagering multiple (≤ 10 in GB since 19 Jan 2026) |
| `T = W·B` | Counted wagering required. Actual stake needed is `T / c_g`. |
| `D` | Your own cash deposited or put at risk |
| `Q` | Qualifying stake (own money) for "stake £Q, get Y" offers |
| `N, s` | Number of free spins and the value of each spin |
| `C` | Winnings cap or maximum conversion |
| `ρ, K` | Cashback rate and maximum cashback |

**Cost per £1 of wagering requirement = `h_g / c_g`.** This one number ranks games for clearing a wagering requirement [D]:

| Game (example settings) | h | c | Cost per £1 of WR | Naive EV of a £50 bonus at 10x |
|---|---|---|---|---|
| Slot 97% | 3.0% | 100% | 3.0p | +£35.00 |
| Slot 96% | 4.0% | 100% | 4.0p | +£30.00 |
| Slot 94% | 6.0% | 100% | 6.0p | +£20.00 |
| Slot 92% | 8.0% | 100% | 8.0p | +£10.00 |
| Blackjack 99.5% (basic strategy) | 0.5% | 10% | 5.0p | +£25.00 |
| Blackjack 99.5% | 0.5% | 20% | 2.5p | +£37.50 |
| European roulette 97.3% | 2.7% | 10% | 27p | −£85 (avoid) |
| Crazy Time 96.08% (best spot) | 3.92% | 10% | 39p | −£146 (avoid) |

### 2.2 Cashable bonus on a separate bonus balance (the base case)

Bonus funds sit in their own balance. Wagering is on the bonus only. When wagering completes, the remaining bonus balance becomes cash. Your own money is not at risk.

- **Exact [D]:** `EV = B − h·E[actual turnover]`. Play stops either at completion or at bust.
- **Naive formula:** `EV_naive = B − h·T/c = B·(1 − W·h/c)`. This is the "bonus − wagering × house edge" formula found in every guide. Examples: OddsMonkey [S, https://www.oddsmonkey.com/blog/matched-betting/casino/best-casino-offer/] and Extra Place [S, https://extraplace.co.uk/blog/casino-offer-matched-betting/].
- **Why the naive formula is a lower bound.** If you go bust before completing, you stop paying the house edge on the rest of the wagering, so `E[actual turnover] ≤ T/c`. That gives `EV ≥ EV_naive`, and also `EV ≥ 0` because none of your money is at risk. Going bust is free; finishing is not.
- **Worked example.** B = £50, 10x, 96% slot: naive EV = 50 − 500 × 0.04 = **£30.00**. Simulated [D, run1.py]:

| Volatility, stake | EV | P(bust) | E[turnover] | SD of result | P(any profit) |
|---|---|---|---|---|---|
| low, 20p | £30.89 | 0.18 | £478 | £24 | 0.82 |
| low, £1 | £34.29 | 0.51 | £381 | £47 | 0.49 |
| med, £1 | £37.83 | 0.69 | £308 | £95 | 0.31 |
| high, £1 | £39.27 | 0.83 | £234 | £214 | 0.17 |
| high, £5 | £45.56 ± 4.7 | 0.90 | £170 | £479 | 0.10 |

  In every row, Wald's check `50 − 0.04 × E[turnover]` matches the simulated EV within sampling error.

- **RTP sensitivity** (low volatility, 20p stake) [D, run5.py]:

| RTP | Naive EV | Simulated EV |
|---|---|---|
| 99% | £45.00 | £45.17 |
| 97% | £35.00 | £35.57 |
| 96% | £30.00 | £30.94 |
| 94% | £20.00 | £22.41 |
| 92% | £10.00 | £15.17 |
| 90% | £0.00 | £9.37 |

**Conclusion.** Under the 10x cap, a cashable bonus with 100% slot contribution is +EV on any slot with RTP above 90%. Before the cap, a 35x bonus needed RTP above 97.14% just to break even (`1 − 1/35`). This is the biggest change from older guides [D].

### 2.3 Where the simple formula is wrong (adjustments)

1. **Wagering on deposit + bonus** (`T = W·(D+B)`) doubles the turnover on a 100% match. The GC rule is written around "play[ing] through bonus funds" (section 5). A requirement on D + B worked out as more than 10x the bonus looks non-compliant in GB, though I have not seen this confirmed [NV]. A checker should flag it as suspect or stale.
2. **Sticky bonus** (only winnings above B convert): see 2.4. The naive formula overstates it badly.
3. **Order of funds.** In GB cash is normally played first, and the deposit can always be withdrawn, which forfeits the bonus. This creates an option that is worth a lot (2.5).
4. **Winnings cap or maximum conversion C:** `EV = E[min(converted, C)]`. The cap removes the upper tail, which is exactly where the variance strategies get their value. With a tight cap, play low variance and value the offer at about `min(EV_naive, C)` (2.6 shows the size of the effect).
5. **Contribution weights:** use `T/c` for turnover. When games are mixed, total cost is `Σ h_g × stake_g`, and the counted amount is `Σ c_g × stake_g`.
6. **Expiry:** the turnover has to fit inside the time limit. At a fixed stake, rounds needed = `T/(c·s)`; check that this is feasible and set a minimum stake. If the bonus expires before completion, the whole bonus is lost, so a valuer treats any chance of missing the deadline as P(bust).
7. **Max bet with bonus funds:** caps `s`, and so caps how much variance you can use.

### 2.4 Sticky (non-withdrawable) bonus

Profit at the end is `1{completed} × max(F − B, 0)`, where F is the final bonus balance [D]. Using Wald:

```
EV_sticky = B·P(bust) − h·E[turnover] + E[(B − F)⁺ · 1{completed}]
```

With low variance P(bust) is close to 0, so EV ≈ −h·T + (small): close to nothing or negative. A sticky bonus is valuable only as a cushion you get to lose. The classic advice for sticky bonuses is to bet big for variance, with a win goal, and then play conservatively once it is reached [S: Arnold Snyder, Las Vegas Advisor, 2005, https://www.lasvegasadvisor.com/blog/online-casino-sticky-bonuses/]. His one-bet example gives the same result as this formula.

Simulated, B = £50 on a separate bonus balance, 10x, 96% slot [D, run5.py]:

| Strategy | EV | P(bust) |
|---|---|---|
| low, 20p | **£3.48** | 0.18 |
| low, £1 | £14.01 | 0.50 |
| med, £1 | £24.58 | 0.70 |
| high, £1 | £33.40 | 0.83 |
| high, £5 | **£37.80 ± 3.3** | 0.90 |
| high £5 until balance ≥ £150, then low 50p | £36.95 | 0.89 |

The naive formula says £30 here, which is wrong by an order of magnitude for a grinder. **A valuer must know whether the bonus is sticky.**

The old offshore style of sticky bonus combined cash and bonus and locked the deposit (run4.py: low-variance grind EV −£35, high variance +£17). That structure conflicts with the CMA undertakings in GB (section 5), so it should not appear on GB-licensed sites.

### 2.5 Deposit match with cash played first (common GB structure)

You deposit D and receive B. Cash is played first. Withdrawing cash forfeits the bonus, and the deposit is always withdrawable. Assume cash play does not count toward the bonus wagering; this varies, so it is a parameter. Then [D]:

```
EV = E[cash outcome under the cash policy] − D + P(cash exhausted)·V(B)
   = −h·E[cash turnover] + P(cash exhausted)·V(B)          (by Wald)
```

V(B) comes from 2.2 or 2.4. The best cash policy has as little turnover as possible and as much chance of exhausting the cash as possible. In practice: high volatility at max stake, and if a big win lifts the cash above a target, withdraw it and walk away from the bonus. Simulated, D = £50, B = £50 cashable, 10x [D, run2.py]:

| Cash policy | Bonus policy | EV | P(cash exhausted) |
|---|---|---|---|
| low 20p, play to zero | low 20p | **−£19.01** | 1.00 |
| low 20p, play to zero | high £5 | −£6.32 | 1.00 |
| high £5, withdraw if cash ≥ £100 | low 20p | £19.12 | 0.80 |
| high £5, withdraw if cash ≥ £150 | high £5 | £30.83 | 0.89 |

The same offer is worth anywhere from −£19 to +£31 depending only on how it is played. **The rules for funds order, withdrawal and which play counts must be captured exactly.** If cash play does count toward wagering, recompute with that turnover credited.

### 2.6 Free spins

- **Wager-free:** `EV = N·s·RTP_g`, using the RTP of the named game at this operator. For example, 50 × 10p at 96% = **£4.80** (same method as Outplayed's "50 x £0.12 = £6 … £6 x 0.9671 = £5.80" [S, https://outplayed.com/blog/mindset-for-casino-offers]).
- **With a winnings cap C:** `EV = E[min(X, C)]`, where X is the total spin winnings. Simulated, 50 × 10p [D, run1.py]:

| Volatility | No cap | Cap £20 | Cap £10 |
|---|---|---|---|
| low | £4.82 | £4.82 | £4.79 |
| med | £4.79 | £4.67 | £4.47 |
| high | £4.78 | £4.31 | £4.03 |

  Rule of thumb [D]: ignore the cap when C is at least 4× the expected winnings and the game isn't high volatility. Otherwise simulate, or take about 85% of the uncapped figure as a conservative estimate.
- **Winnings become bonus with W× wagering:** `EV = E[V_bonus(X)]`, using 2.2 with B = X. The lower bound is `N·s·RTP·(1 − W·h/c)`. For 50 × 10p at 10x and 96%, the naive figure is £2.88. Simulated: low/10p £3.30, low/£1 £3.99, high/£1 £4.53 [D]. The bust effect is proportionally large here because the balances are small.
- **Free spins are fixed-stake.** The operator sets the spin value, so the only choice is which game, and only when the offer allows a choice.

### 2.7 "Bonus non-withdrawable, winnings paid as cash" (free-bet-like bonus)

Each £1 of bonus can be staked once, and anything it returns is cash.

- **Returns include the stake** (slot-style payouts): `EV = B·RTP`. A £10 bonus on a 96% game is worth £9.60.
- **Stake not returned** (free-bet style on table bets): `EV = B·(RTP − P(win))`. On European roulette that is £0.4865 per £1 on an even-money bet, but £0.9459 per £1 on a straight-up number [D]. So, as with a sports free bet, use the longest odds available.
- Which of these applies has to be read from the terms. Sky Vegas's live bonus says only "Bonus non-withdrawable, winnings paid as cash" [P]. **Not verified [NV]:** whether the stake is returned in "winnings".

### 2.8 "Stake £Q on slots, get reward Y"

`EV = V(Y) − h_q·Q/c_q`, where `h_q` is the house edge of the cheapest eligible qualifying game [D].

- The cost is exact by Wald if you stake exactly Q. Volatility and stake size **do not change** the EV of the qualifying play, only its spread. This matches Outplayed: "stake size and slot variance don't affect the overall EV of the offer" [S]. So use the lowest-volatility eligible game at a low stake, unless there is a separate reason to play otherwise.
- **Cash needed ≤ Q.** Turnover is always at least the amount lost, so you can always complete Q of wagering with Q of cash [D].
- **Example:** stake £20 on 96% slots, get 50 × 10p wager-free spins: EV = 4.80 − 0.80 = **£4.00**. Simulated: £3.97 (low/20p) and £4.68 ± 0.7 (high/£1).
- Watch for offers where Q is large relative to Y. "Stake £50, get 20 spins at 10p" is 1.92 − 2.00 = −£0.08: avoid.

### 2.9 Cashback / loss-back

The cashback is ρ times net loss over the period, capped at K, paid as cash [D]:

```
EV = −h·E[turnover] + ρ·E[min(L⁺, K/ρ)]          (L⁺ = net loss if negative result, else 0)
```

**Why variance raises EV.** `L⁺` is a convex function of the result. By Jensen, `E[L⁺] ≥ (E[L])⁺ = h·E[T]`, and the gap grows with the spread of the result. What actually matters is **loss realised per £ of turnover**, so the ideal is a single all-or-nothing bet.

For a single bet of S that loses everything with probability q: `EV = S·(ρ·q − h)`. On European roulette:

- Straight-up number (q = 36/37): +EV whenever ρ > 2.78%.
- Even money (q = 19/37): needs ρ > 5.26%.
- With ρ = 10% and S = £200: **straight-up +£14.06; red +£4.87** [D].

On slots, wins are recycled, so turnover per £ lost is high. Simulated 10% cashback (cap £20) on a 96% slot came out −£3 to −£7 at £200 turnover and −£8 at "play to bust or double" [D, run2.py]. **Slot cashback at ρ ≤ 10% is roughly break-even to negative; table or live cashback with long-odds single bets can be +EV**, if the terms allow those games and that pattern (check the irregular-play rules, section 4).

If cashback is paid as bonus with wagering, replace the `ρ·L⁺` term with `V_bonus(ρ·L⁺)` from 2.2.

### 2.10 Prize wheels and daily free games

`EV = Σ_i p_i·V(prize_i) − cost of any qualifying play` [D]. Prize probabilities are rarely published [NV for every operator checked]. Estimate them from your own logged outcomes: the mean observed value per play, with a standard error, updated over time. With no qualifying stake, EV ≥ 0 and the only cost is time.

### 2.11 Bankroll, variance and risk of ruin

- **Bounded downside.** The downside of every offer type above is bounded by **own cash put at risk** (D, Q or the cashback exposure). Offers with no own cash at risk (bonus-only balances, free spins, daily games) have a downside of zero. Take them whatever the bankroll, provided EV > 0 [D].
- **Kelly acceptance test for a fixed-size offer [D].** Take an offer with random profit X if

  ```
  E[ln(1 + X/BR)] ≥ 0      (full Kelly)
  ```

  For half Kelly, evaluate with BR/2. The usual approximation `BR ≥ σ²/(2μ)` treats upside variance as harmful, which it isn't here. It badly overstates the bankroll needed for right-skewed offers, so compute the log test on the simulated distribution instead. Minimum bankrolls from run3.py and run4.py:

| Offer and strategy | EV | Worst case | Min BR (full Kelly) | σ²/2μ |
|---|---|---|---|---|
| Deposit £50 + £50 cashable, cash-first high £5 to target, bonus low 20p | £21.7 | −£50 | £170 | £2,514 |
| Old-style sticky D £50 + B £50, high £5 | £17.1 | −£50 | £2,648 | n/a |
| Cashback £200 on one roulette number at 10% | £18.8 | −£180 | £32,511 | £36,971 |
| Cashback £200 on red at 10% | £5.1 | −£180 | £3,551 | £3,560 |

- **Risk of ruin over a sequence.** With drift μ per offer and variance σ² per offer, the diffusion approximation is `RoR ≈ exp(−2·μ·BR/σ²)`. It is conservative (too pessimistic) for right-skewed offers, so simulate the actual queue of offers when it matters [D].
- **Simple rule that works well [D].** Cap own cash at risk per offer at 5% of the casino bankroll (Extra Place suggests "no more than 10% of bankroll in one offer" and a bankroll of 10× a typical deposit [S]). Among acceptable plays, choose the strategy that maximises EV subject to the Kelly test. Track cumulative profit against cumulative EV, as Outplayed recommends [S].
- **Choosing variance (summary) [D]:**

| Offer structure | Variance choice | Why |
|---|---|---|
| Cashable bonus-only | Higher volatility and stake raise EV with zero downside | Lower variance gives a steadier income but lower EV. A policy choice: default to EV-max, with a "smooth" option. |
| Sticky | Variance is required | A low-variance grind is close to worthless. |
| Deposit with cash first | High variance on the cash, with a stop-win target | Then play the bonus according to its type. |
| Own-money qualifying play | Lowest variance | Volatility doesn't change EV, only spread. |
| Cashback | Minimum turnover per £ at risk | Long-odds single bets where allowed. |

The GB £5 per-spin slots limit (£2 for ages 18–24) and the offer's own max bet cap the stake.

---

## 3. Game choice

1. **Eligibility first.** The game must be on the eligible list and not on the excluded list. Its contribution weight must be known.
2. **Rank by `h/c`** (cost per £1 of wagering, 2.1). With full slot contribution, the eligible slot with the highest RTP **at this operator** almost always wins.
3. **Read the RTP from the game's own info or paytable at the operator.** Providers ship several RTP builds of the same title and the operator chooses one.
   - **Book of Dead (Play'n GO):** 96.21% / 94.25% / 91.25% / 87.25% / 84.18% [S, OLBG https://www.olbg.com/slots/games/book-of-dead]. The page advises opening the paytable "which shows the RTP you're playing with".
   - **Pragmatic Play:** reported as offering 96.5 / 95.5 / 94.5% builds [S, low-quality sources; NV against the provider].
   - **Since the 40% duty (April 2026):** trade press reports UK operators lowering RTP settings [S: European Gaming https://europeangaming.eu/portal/latest-news/2026/04/30/203196/cutting-rtp-to-offset-the-uks-40-gaming-tax-why-the-maths-doesnt-add-up/; IBTimes 23 Apr 2026 https://www.ibtimes.co.uk/uk-casino-operators-adjust-slot-game-rtp-1793332, which names no operators and gives no figures]. No regulator or company data quantifies this [NV].
   - **So the RTP must be stored per (operator, game) and re-read periodically.** Never assume the provider's headline figure.
4. **Volatility** comes from the provider's rating (usually 1–5 or low/med/high), max win and hit rate where published. It matters only for offers where going bust changes EV (2.2–2.5, 2.9). For qualifying play with your own money, choose low volatility.
5. **Max stake.** The GB slots limit is £5 per game cycle for ages 25+ and £2 for ages 18–24 (section 5), and an offer's max bet may be lower. Free spins are fixed-value.
6. **Typical RTP ranges:**

| Game | Typical RTP | Status in offers |
|---|---|---|
| Slots | Provider defaults usually about 94–97%. UK builds can be lower (Book of Dead down to 84.18%). Slots are 2–15% house edge per Wizard of Odds [S, https://wizardofodds.com/gambling/house-edge/]. | Usually 100% contribution, with excluded lists (often high-RTP titles and jackpots). |
| Blackjack | About 99.5% with basic strategy. 99.72% (0.28% edge) on liberal Vegas rules [S, Wizard of Odds]; online rules vary, so read the game help. OddsMonkey uses 99.5% [S]. | Usually weighted to 10–25% or excluded [S, Extra Place]. |
| European roulette | 97.30% (2.70% edge). 98.65% on even-money bets with La Partage [S, https://wizardofodds.com/games/roulette/basics/]. | Usually weighted down or excluded. |
| Live game shows | Crazy Time 94.41–96.08% depending on bet spot [S, PokerListings; per-spot figures NV] | Low weight, or eligible only for "live bonus" offers. |
| Baccarat | Banker 98.94%, Player 98.76% [S, Wizard of Odds] | Often excluded from wagering. |

---

## 4. Terms that decide whether an offer pays (checker rules)

Each rule is phrased as a check the logic runs before choosing a play, and again before every session.

1. **Opt-in or code required.** If the offer needs an opt-in or code, record a timestamped opt-in **before** any qualifying play. Qualifying play before opting in usually doesn't count.
2. **Eligibility.** New or existing customer, account age, "one per person" (Sky's general terms 25.3: "All promotions and offers are limited to one per person" [P, https://support.skyvegas.com/app/answers/detail/sky-betting-and-gaming-general-terms-conditions/]), household, device or IP limits. If you are not eligible, the value is 0.
3. **Payment method.** Many offers exclude e-wallet or prepaid deposits. Third-party reviews of Ladbrokes list Neteller, PayPal, Paysafe and Skrill as excluded for one offer [S/NV]. A checker must store the excluded methods and the method actually used.
4. **Minimum deposit or qualifying stake, and the window.** For example, "£10 deposit & staking requirement must be met within 30 days of sign up" (Sky) [P].
5. **Eligible and excluded games.** Every planned play must be on an eligible game with known `c`. Excluded games may count for 0, or void the bonus if played with bonus funds. Store which of the two.
6. **Max bet while a bonus is active.** Planned stake ≤ max bet, measured per spin, hand or round. Exceeding it is the most common way to void a bonus [S, Extra Place]. Also: stake ≤ the legal slot limit for the account holder's age.
7. **Time limits.** Bonus or spin expiry and the wagering deadline. Check that `T/(c·s)` rounds fit before the deadline at a plausible session rate.
8. **Winnings cap / max conversion.** Feeds 2.3(4) and changes the variance policy.
9. **Funds order and withdrawal.** Cash first or bonus first? Does cash play count toward wagering? Does withdrawing forfeit the bonus? (In GB the deposit and its winnings must stay withdrawable [P, CMA].) Is the bonus sticky?
10. **Mixed-product sports legs.** Banned for GB offers since 19 Jan 2026. If an offer still needs a sports bet, the min-odds rule applies, but treat the offer as **suspect**: the listing may be stale or non-GB.
11. **Irregular or low-risk play clauses.** GC guidance (via search-result text; the page itself could not be read, see section 5) says terms "setting out any Promotional Play Restrictions [must] clearly specify all prohibited types or patterns of play". So the clauses are enumerable and should be stored verbatim per offer [S/NV]. Patterns commonly listed in operator terms [NV for any specific GB operator in this research]:
    - covering both sides (red and black, odd and even, banker and player), or covering most of the roulette table;
    - zero- or low-margin hedging across games or accounts;
    - martingale or other progressive staking;
    - big changes in stake size once wagering is nearly done (low stakes while the bonus is at risk, then a max-stake switch);
    - playing the bonus on excluded or weighted-down games;
    - "bonus abuse" catch-alls.

    Real GB examples found [P]:
    - Paddy Power live offer: "Low-risk wagering… any bet spread that covers 25 or more of the 37 unique positions… will not contribute".
    - Unibet general promo terms list as breaches "Betting more than 30% of the initial bonus amount in any one game round" and "Betting more than £15 per spin on slots while using bonus funds".

    **The logic must never choose a play matching a listed pattern.** That limits the variance strategies in 2.2–2.5. A "high stake until target, then low stake" switch may count as a "significant change in betting pattern" under some terms. If the offer's terms list stake-pattern restrictions, use a single constant-stake policy.
12. **Stake-not-returned vs returned** for bonus-stake offers (2.7).

---

## 5. UK regulation as of 11 Nov 2026 (each item checked)

**TLS note.** The Gambling Commission's site (gamblingcommission.gov.uk) failed TLS verification from this machine with "unable to get local issuer certificate", so I could not read its pages directly. I didn't bypass certificate checks. GC wording below is taken from law-firm summaries that quote it, and from search-engine extracts of the GC pages. A one-line check by Callum in a browser would make these [P].

| Rule | What it says | In force | What it changes for offer value | Status |
|---|---|---|---|---|
| **Wagering cap**, LCCP SR Code 5.1.1 | Licensees must not "Apply wagering requirements, which requires a customer to play through bonus funds, over a maximum of 10 times". A wagering requirement is "where a customer is required to make wagers totalling a particular value for funds to become withdrawable". | **19 January 2026**, moved from 19 Dec 2025 at industry request. GC page title "Gambling promotions to be safer and simpler". | W ≤ 10 on bonus funds. Cashable 100%-slot bonuses become +EV at any RTP above 90% (2.2). Older 35x–45x guides are out of date for GB sites. Before the cap, "most local welcome offers have been running with 35x to 45x" [S, casinos.com, 16 Jan 2026]. The cap applies to bonus funds, so qualifying stakes on your own money ("stake £50 to get spins") are **not** capped by it. My reading: the wording covers wagering of bonus funds, not qualifying activity [D/NV]. Operators can still use weighting: 10x at 25% weighting is effectively 40x. | [S] Wiggin https://www.wiggin.co.uk/?p=24416; Harris Hagan https://www.harrishagan.com/white-paper-series-gambling-commission-update-on-the-new-gambling-promotion-rules/; CMS https://cms.law/en/gbr/legal-updates/ban-on-mixed-product-gambling-incentives-coming-into-force-soon |
| **Mixed-product ban**, SR 5.1.1 | Licensees must not "include more than one type of gambling product (betting, casino, bingo and lottery) within an incentive". CMS examples: "bet £5 to receive a free £5 bet" is compliant; "bet £5 and receive 20 free casino spins" is not; a reward of "£1 free bet, 5 free spins or £10 in cash" is not. Allowed: "bet £5 and receive £5 to spend on any gambling product of their choice". | 19 Jan 2026 | Bet-and-get-spins offers are gone from GB-licensed sites, so casino offers are self-contained casino transactions. Any listing that still mixes products is stale or non-GB. | [S] CMS (above). Wiggin quotes the consultation response: the ban does not affect incentives "which allow the customer to select the product". |
| **Online slots stake limit** | £5 per game cycle for adults 25+; £2 for 18–24. Made under the Gambling Act 2005 (Operating Licence Conditions) (Amendment) Regulations 2025 (made 25 Feb 2025). | **£5 from 9 April 2025** (applied to all adults until 21 May). **£2 for 18–24 from 21 May 2025.** | Caps per-spin stake, and so the variance available in 2.2–2.5. Bonus max bets can be lower. | [S] search extract of GC guidance; Harris Hagan https://www.harrishagan.com/white-paper-series-gambling-commission-publishes-online-slots-stake-limit-guidance/ (game cycle "begins when the customer presses spin"). Whether bonus-funded spins count toward the limit is not stated [NV]. |
| **Remote Gaming Duty** | **21% → 40% from 1 April 2026.** New 25% remote betting rate from 1 April 2027 (UK horse racing stays 15%). Bingo duty abolished from 1 April 2026. | 1 Apr 2026 | The operator's take on casino gross gaming yield drops sharply, so promotions are less generous and RTPs may be configured lower (section 3). Entain and Evoke reportedly named promotional spend as the first cut [S, gambling.com summary via search; NV]. | [P] https://www.gov.uk/government/publications/changes-to-gambling-duties |
| **Player tax** | HMRC Business Income Manual: "betting and gambling, as such, do not constitute trading". Winnings are not taxed and losses are not relievable. Exception: organised activity making profit **from** the gambling public, i.e. being the bookmaker. | Long-standing | None: casino offer profits are tax-free for the player. Interest on winnings is taxable as normal. | [P] https://www.gov.uk/hmrc-internal-manuals/business-income-manual/bim22015; [S] RSM https://www.rsmuk.com/insights/weekly-tax-brief/the-tax-free-nature-of-gambling-proceeds |
| **CMA undertakings (2018)** | Operators must "Allow customers to exit promotions at any time with their remaining deposit, plus any winnings from play with their deposit", and must make clear when you are playing with your own money and what restrictions apply to bonus funds. | Aug 2018 | Creates the cash-first option value in 2.5 and rules out deposit-locking sticky bonuses on GB sites. | [P] https://www.gov.uk/government/publications/online-gambling-promotions-dos-and-donts/online-gambling-promotions-dos-and-donts-for-online-gambling-firms |
| Other SR 5.1.1 changes | Terms must be clear, fair and readily accessible. Incentives must not change in value if the qualifying activity is reached sooner than the offer period. | 19 Jan 2026 / earlier | Offers' published terms should contain every number the valuer needs. | [S] Harris Hagan (2025 white paper) |

**Re-basing the maths.** Every example in this report uses W ≤ 10 on bonus funds. Guides with 35x or 40x examples, such as Extra Place's −£11.25 example (which its own update note flags as pre-2026), describe a structure GB-licensed sites can no longer offer.

---

## 6. What offers look like in the UK in late 2026 (real examples)

### How these were collected

A research sub-agent read the operators' own promotions and terms pages on 11 Nov 2026. Sky Vegas, Paddy Power and Betfair load their terms from a public promotions API with no login; the raw JSON and HTML (89 files, 24 MB) are kept out of git, on the Hub machine in `~/casino-offers-raw/`. Quoted wording is verbatim. "[P]" means the operator's own page and "[3P]" means a third-party listing.

### How the EVs were worked out

- **RTP assumption.** None of the operators publishes the RTP build it runs for the named games, so every EV assumes **96% (h = 4%)** for the named slot unless stated. Each figure also shows how much it moves per 1 point of RTP. Before acting, read the real RTP from the game's info page at that operator (section 3). Jackpot King titles fund a jackpot from stakes, so their base-game RTP is likely lower; this is not verified.
- **Rounding.** EVs are rounded to the nearest 10p.
- **Simulated figures** come from `sim/run6.py`.

### Offer-by-offer

| # | Operator / offer [source] | Key terms (verbatim where quoted) | EV estimate |
|---|---|---|---|
| 1 | **Sky Vegas – New Customer 250 Free Spins**, VOAFNTACQ50FS200 [P] https://promos.skyvegas.com/promotion?promoCode=VOAFNTACQ50FS200 | 50 spins with no deposit, then 200 more after "deposit £10" and "Only cash stakes placed on slot and instant win games will qualify". The page says "Deposit and spend £10"; the legal text omits the stake amount. Spins "valued at 10p", locked to the first eligible game (Eye Of Horus, Fishin' Frenzy, several Jackpot King titles, Deal Or No Deal Megaways). "no wagering requirements attached to any winnings". 7-day expiry. Max winnings not stated. | Spins: 250 × £0.10 × 0.96 = £24.00. Minus the £10 qualifying stake × 4% = £0.40. **≈ £23.60.** Moves ±£2.50 per RTP point. Zero own-cash risk apart from the £10 stake. |
| 2 | **Sky Vegas – £10 Live Bonus**, VOSB01VLDS10G10L [P] | "stake £10 cumulatively on the Eligible Games" (live roulette, Live ONE Blackjack, Crazy Time and others) within 30 days. Bonus "non-withdrawable… no wagering requirements", and "The Live Bonus value is not included in any returns", i.e. stake not returned. Neteller and Skrill deposits don't qualify. | Qualifying £10 on Live ONE Blackjack at about 0.5% edge = −£0.05 (blackjack RTP at Sky not read). Bonus, stake not returned (2.7): straight-up roulette 10 × 0.9459 = £9.46; even money 10 × 0.4865 = £4.86. **≈ £9.40 using single-number bets**, about £4.80 on even money. Check that bonus play on single numbers isn't a restricted pattern; nothing seen prohibits it. |
| 3 | **Sky Vegas – Free Daily Chance to Win** (Prize Machine) [P] | Daily opt-in. "There are no staking requirements". Prizes are scratchcards, 10p free spins or cash, each with no wagering. Prize values and odds not stated. | **≥ £0 a day, value unknown.** Estimate it from logged outcomes (2.10). |
| 4 | **Paddy Power Games – 70 Free Spins + Deposit £10 Get 100 Free Spins** [P] https://promos.paddypower.com/promotion?promoCode=GASUP100FSN | 70 spins "worth £0.10 each" with no deposit. Then "deposit and wager £10 to receive 100 more free spins". The deposit must be by "debit card, Pay by Bank or Apple Pay". "There are no wagering requirements on Free Spins." 7-day expiry. The value of the 100 spins was not retrieved (it sits in a chained promo). | 70 × 0.10 × 0.96 = £6.72. Plus 100 spins, assumed 10p: £9.60 − £0.40 = £9.20. **≈ £15.90**, if the 100 spins are 10p each (not verified). |
| 5 | **Paddy Power – £5 Live Casino Bonus + Bet £10 Get £5** [P] https://promos.paddypower.com/promotion?promoCode=GAMOLCSUO0926N | £5 bonus with no deposit, then "bet £10 on Paddy Power Live Casino and receive a free £5 Bonus". "one-time (1x) wagering", 7-day expiry. Games include PP Live Blackjack 1–12 and PP Live Roulette. "Low-risk wagering… any bet spread that covers 25 or more of the 37 unique positions… will not contribute". Contribution weights not stated. | 1x on live blackjack at about 0.5%: each £5 bonus is worth about £4.97. The £10 qualifying bet costs £0.05. **≈ £9.90.** Assumes 100% contribution on live blackjack, which is not stated; on roulette, take 2.7% instead. The 25-of-37 rule is an explicit prohibited pattern for the checker. |
| 6 | **Paddy Power – Rewards Club weekly spins** (existing customers) [P] https://promos.paddypower.com/promotion?promoCode=PPREWARDSGAM | Wager £10, £20 or £50 on slots in a week for 5, 10 or 25 spins at £0.10. Roulette, blackjack and live games excluded. Terms contradict themselves: claim then "used within 12 hours" vs "Spins valid for 7 days". Wording mentions "free bet" and Bingo/Poker channels, so it is possibly stale. | £10 tier: 0.48 − 0.40 = **+£0.08**. £20: +£0.16. £50: 2.40 − 2.00 = **+£0.40** at 96%; −£0.65 at 94%; +£0.93 at 97%. **Marginal:** worth it only on a high-RTP slot, or alongside other required play. |
| 7 | **Betfair Casino – 50 Free Spins + Deposit £10 Get 50** [P] https://promos.betfair.com/promotion?promoCode=CASAF5109252020 | 50 spins with no deposit on a Jackpot King game, then "Deposit and spend a minimum of £10 on eligible Jackpot King games" for 50 more. 10p each, 7-day expiry. "Winnings are paid in cash". "Deposits using E-Wallets (Paypal, Neteller, Moneybookers etc.) are not valid". | 100 × 0.10 × RTP − 10 × h = 9.60 − 0.40 = **≈ £9.20** at 96%; £8.80 at 94%. The Jackpot King base RTP is unknown, so read it in-game. |
| 8 | **Betfair – Prize Pinball** (selected customers) [P] https://promos.betfair.com/promotion?promoCode=PMUK271025UF | Free daily play. Prizes are 1 free spin (10p, 2h expiry), a Slingo bonus with 1x wagering, or cash of 10p to £10, plus a jackpot of "at least £1,000 per day". Odds not stated. Follow-on: "an extra 5 free spins when you wager £10". One per household, IP and payment account. | Free play: **≥ £0, value unknown** (log it). Follow-on 5 spins for £10 wager: 0.48 − 0.40 = +£0.08, so only worth it alongside other play. |
| 9 | **888casino – 200 Free Spins**, HELLO200FS [P] https://www.888casino.com/promotions/hello200fs/ | First deposit ≥ £20, and "stake at least £/€20 in real money on the Selected Games within seven (7) days". 200 spins at £0.10. "A maximum of £/€100 of winnings… may be converted into Bonus". The bonus must be wagered "ten (10) times… within seven (7) days" on the Selected Games (Game of Thrones, Fishin' Frenzy variants, King Kong Splash). Cash is played first. | Spin winnings average £19.20. 10x on that, minus the £0.80 qualifying cost. Floor: 19.2 × 0.6 − 0.8 = £10.70. Simulated: **£11.60** (low volatility, 20p), £14.20 ± 1.2 (high volatility, £1). The £100 conversion cap barely binds. |
| 10 | **888casino – 100% bonus up to £100** [P] https://www.888casino.com/promotions/deposit-welcome-uk/ | First deposit ≥ £10, and "stake that Deposit within 48 hours". 100% bonus up to £100, wagered "ten (10) times within five (5) days". Only bonus-balance stakes count, and only once cash = £0. "maximum amount that can be redeemed as winnings from this Promotion is £100". Withdrawing the deposit forfeits the bonus. Excluded slots list not read. | Simulated with a £100 deposit (2.5 structure plus cap) [D]: cash ground to zero then bonus at 20p: **−£42.70**. Cash at high volatility £5, withdraw if ≥ £200, then bonus at **low** volatility 20p: **+£36.90 ± 5.7**. Same cash policy but bonus at high volatility £1: −£2.00. The £100 cap punishes variance on the bonus phase. **The best play is the opposite in the two phases.** Verified [P]: the bonus "may be used… once: the Deposit has been used to place a stake; and there are no other Available Funds… in the player's cash balance", and "Any amount of stake taken from the player's cash balance will not count". That is exactly the 2.5 structure. The 5-day wagering clock starts when the bonus is credited, so the cash phase has to be quick. |
| 11 | **888casino – 10 Free Spins Every Month** [P] https://www.888casino.com/promotions/10-free-spins-every-month/ | 10 × £0.10 on one game, no deposit, winnings withdrawable as cash. The expiry is given as both 24h and 48h. | **≈ £0.96 a month.** |
| 12 | **888casino – Daily Wish wheel** [P] https://www.888casino.com/promotions/dailywishgame-register-uk/ | One daily spin with a minimum £10 deposit (lifetime vs last 30 days, contradictory). Prizes: cash; free spins at £0.10 with no wagering; or a bonus with "wagered 5 times within 5 days". Prize values and odds not stated. | **≥ £0, value unknown.** A bonus prize is worth about B × (1 − 5 × 0.04) = 0.8B. |
| 13 | **Unibet UK – 300 Free Spins** [P] https://www.unibet.co.uk/promotions/casino-promotions/free-spins-welcome-offer | "Deposit £30+ via debit card or instant bank transfer only. Stake £30 on Slots". 300 × 10p on Big Bass Splash. "Any winnings must be wagered x10". Slots 100%, tables 10%. Breaches include betting "more than 30% of the initial bonus amount in any one game round". Credit card, Apple Pay, PayPal, Paysafe, Skrill and Neteller excluded. Three different expiries are stated (48h, 7 days, 30 days). | Spin winnings average about £29. 10x, minus the £1.20 qualifying cost. Floor: 29 × 0.6 − 1.2 = £16.20. Simulated: **£16.50** (low, 20p), £22.50 ± 1.9 (high, £1). The max-bet rule (30% of the winnings credited) limits the stake. |
| 14 | **Virgin Games – Play £10, Get 100 Free Spins** [P] https://www.virgingames.com/uk/terms | Opt in before the first deposit (≥ £10), then "wager at least £10 in cash on any slot game(s)" within 30 days. 100 spins at 10p on Game of Thrones, "no wagering requirements". The landing page says "no caps on what you win". | 9.60 − 0.40 = **≈ £9.20.** |
| 15 | **Virgin Games – Prize Wheel** (invited) [P] https://www.virgingames.com/uk/red-hot-rewards/prize-wheel/terms | Wager a level-specific "Specified Wager" of £10 to £1,000 on slots in a day. Cash prizes "£0.05 to £60". Distribution not stated. | **Not valuable as a target.** The required wager costs 4% × W, e.g. £0.40 to £40, against an unknown prize distribution. Take it only if the wager is already happening for another offer. |
| 16 | **PlayOJO – 50 Free Spins** [P, summary only] https://www.playojo.com/ | "First deposit only. 50 Free Spins on Big Bass Bonanza slot. Free Spin valued at £0.10." "No wagering requirements on bonuses". Full terms need JavaScript and weren't read. The site also shows a slots-and/or-bingo Uber voucher offer, which looks inconsistent with the mixed-product ban. | 50 × 0.10 × 0.96 = **≈ £4.80**, with no qualifying stake stated. |
| 17 | **bet365 Games – up to 500 Free Spins** [P card] + [3P] https://www.racingpost.com/online-casino/articles/bet365-free-spins/ | [P]: "Min. £10 lifetime deposits… Max. 10 selections in 20 days, 24+ hours apart. Max. prize… apply." [3P]: each selection gives 5, 10, 20 or 50 spins at £0.10, "no wagering requirements", Paysafecard excluded. Prize distribution not stated. | Bounds: 10 × 5 spins = **£4.80** to 500 spins = **£48.00**. True EV unknown, so log outcomes. |
| 18 | **William Hill Vegas – 200 Free Spins** [3P only] https://www.racingpost.com/free-bets/casino/ | "200 Free Spins… on Big Bass Splash following a £10 qualifying stake", "maximum redeemable cap of £30". Affiliates disagree on the code and details. | Simulated cap effect on 200 × 10p (spins worth £19.20 uncapped): £18.60 (med) or £17.10 (high). Minus £0.40: **≈ £16.70–£18.20.** Not verified against William Hill's own terms. |
| 19 | **William Hill Vegas – £20 No-Deposit Bonus** [3P only] | "x10 wagering… within that 72h… Max redeemable winnings - £25". | Simulated: **£11.00** (low, 20p), £7.40 (low, £1), £2.60 (high, £1). With a tight cap, play low variance at a small stake. Not verified. |
| 20 | **BetMGM – 225 Free Spins** [3P, affiliate version] | "Deposit £10+ and wager 1x on casino games… Get 225 Free Spins on Big Bass Splash (worth 10p each), valid 3 days, no wagering". The same article also says 200 spins. | 225 × 0.10 × 0.96 − 10 × 0.04 = **≈ £21.20** (or £18.80 for 200 spins). Not verified. |

### What this says about current offers [D]

1. **New-customer offers at the big brands are now worth roughly £5–£25 each, with almost no risk.** The usual shape is "stake £10–£30 of your own money on slots, get 100–300 spins at 10p". About a third of the offers seen have no wagering at all. The qualifying cost is pennies (£0.40–£1.20), and own cash at risk equals the qualifying stake.
2. **The biggest single EV is a classic deposit match with a winnings cap** (888's 100% up to £100, about £37). It is also the one where the wrong play loses money (−£43). That is exactly where a valuer earns its keep.
3. **Existing-customer value is small:**
   - daily free games (≥ £0, unknown, worth logging);
   - weekly stake-for-spins (+£0.10 to +£0.90 at 96–97% RTP, negative at 94%);
   - monthly free spins (£1).
   A steady existing-customer stream is worth single-digit pounds per brand per week unless in-account personalised offers, which can't be seen without logging in, are better. That last point is not verified.

   **Corrected by Callum, 11 Nov 2026.** This item counts public offers only and undersells the stream. Callum, who holds the accounts: "there are additional opt in offers worth a few quid a week, it all adds up", and they are "not advertised publicly of course". Those in-account opt-in offers are the main recurring value. Nothing here measured them; see "What Callum said" at the top.
4. **Data quality is a real problem:**
   - several primary terms pages contradict themselves (expiry 24h vs 48h; three expiries at Unibet; Sky's missing stake amount);
   - some pages still carry mixed-product wording (Betfair Refer and Earn, PlayOJO Uber voucher, PP Rewards Club).
   The checker must store the verbatim terms and flag internal conflicts. When terms conflict, use the **stricter** reading (shortest expiry, highest stake).
5. **Operators whose terms could not be read from the operator's own pages** (pages built in JavaScript or blocked): William Hill, bet365 (full terms), Coral, Ladbrokes, Betfred, Grosvenor, BetMGM, Midnite and talkSPORT BET. The Flutter brands (Sky, Paddy Power, Betfair) can be read through their public promotions API. The others need a rendered browser session.

---

## 7. Tracking: state to keep per offer

Experienced players keep a spreadsheet or tracker with one row per offer and a running "profit vs EV" graph. Outplayed tells members to compare cumulative profit against total EV [S]. OddsMonkey and Outplayed members log offers in the sites' own trackers [S/NV for exact fields]. The state a correct implementation needs:

**Offer (static, from the terms; store the verbatim terms text and its URL and retrieval time):**

- operator, offer id or code, URL, `terms_text`, `terms_fetched_at`
- audience (new/existing), eligibility notes, opt-in required, payment-method exclusions
- type: `deposit_match | stake_get | free_spins | bonus_stake | cashback | prize_game | reload`
- trigger: min deposit; qualifying stake Q; qualifying games with RTP and c; window start/end
- reward: B, or N and s, or ρ and K; reward game list; whether the reward is cash, bonus or free-bet-like (`stake_returned` y/n)
- wagering: W; base (`bonus | winnings | deposit+bonus`); contribution weights per game or category; excluded games and the consequence (0% or void)
- conversion: cashable or sticky; winnings cap C; max conversion
- funds order (`cash_first | bonus_first`); does cash play count toward WR; does withdrawal forfeit the bonus
- max bet with bonus; legal slot stake limit for the account holder (£5 or £2)
- expiries: opt-in deadline, qualifying deadline, bonus/spin expiry, wagering deadline
- prohibited play patterns (verbatim list)

**Progress (dynamic):**

- `opted_in_at`, `deposit_made` (amount, method, time)
- `qualifying_staked`, `qualifying_remaining`; `qualified_at`
- `reward_credited_at`; credited amount, spins remaining
- `cash_balance`, `bonus_balance` (kept separate); `wagering_counted`, `wagering_remaining`
- `stake_turnover_by_game` (for the actual cost via Wald: Σ h_g × stake_g), session log
- `status`: `found → assessed → opted_in → qualifying → reward_pending → wagering → completed | busted | expired | voided | abandoned`
- result: cash withdrawn or retained, net profit
- valuation snapshot: EV, SD, P(bust) at decision time, and strategy chosen (game, stake, stop rules)

**Portfolio:** bankroll, cash at risk across open offers, cumulative profit, cumulative EV, and (profit − EV) in units of √Σσ², which shows whether results are within expected noise.

---

## 8. Where offers are found

| Source | What it provides | Status |
|---|---|---|
| Operator promotions pages | Public offers with headline terms and links to full T&Cs. Sky Vegas's terms pages load the terms text with JavaScript, so a fetcher has to render the page or find the data endpoint. | [P] skyvegas.com/promotions |
| In-account offers ("My Offers", inbox, pop-ups) | Personalised reloads and free games. Often the most valuable existing-customer offers. Visible only when logged in. | Typical, per OddsMonkey: invite-only offers need manual EV [S] |
| Emails / SMS | Personalised offers with codes. Need parsing into the offer schema. | |
| **OddsMonkey** | Paid tiers (Advanced £49.99/month and up) include "100+ casino signup offers", "50+ weekly casino offers", "advanced casino software tools" and casino training. The help centre describes an **EV Calculator** (0x/1x wagering offers) and an **EV Simulator** (runs higher-wagering or complex offers many times to estimate EV). Blackjack strategy calculator. Offer list shows EV per offer. | [S] https://www.oddsmonkey.com/ (pricing as listed on the page); help-centre snippet via search |
| **Outplayed** (Profit Accumulator redirects here) | Casino offer list (bookmakers and casinos), EV calculator, recommended slots and stakes per offer for higher tiers ("Diamond and Pro"), blackjack calculator, and guidance to plot profit against EV. | [S] https://outplayed.com/blog/mindset-for-casino-offers (updated 20 Aug 2026) |
| Matched Betting Blog, Extra Place, Caan Berry and other blogs | Explanatory guides. Extra Place gives formulas, a bankroll rule of thumb and notes on the 10x change (13 Oct 2026). Most other guides predate the 10x cap. | [S] |
| Affiliate listings (Oddschecker, OLBG, Gambling.com, casinos.com) | Current headline offers and key terms, plus slot RTP-variant information (OLBG's per-game pages). Can be stale. | [S] |
| RTP | No single reliable cross-operator RTP list was found. The authoritative figure is the in-game info or paytable at the operator. | [NV] for any "RTP database" |

---

## 9. What a valuer needs

### 9.1 Inputs an offer record must carry

1. **Identity and provenance:** operator, offer id or code, URL, verbatim terms, fetch time, and source type (operator / in-account / email / third-party).
2. **Eligibility:** audience, opt-in flag, per-person limits, excluded payment methods, the account holder's age band (sets the £5 or £2 slot limit).
3. **Trigger:** minimum deposit D; qualifying stake Q; qualifying game set with `(RTP, c)` per game; window.
4. **Reward:** type and amount (B; N and s; ρ and K; prize table if known); reward game(s) with RTP; `stake_returned` for bonus-stake offers.
5. **Wagering:** W, base, per-game contribution weights, excluded games and the consequence.
6. **Conversion:** cashable or sticky, cap C, max conversion.
7. **Funds rules:** cash or bonus first; whether cash play counts toward wagering; whether withdrawal forfeits the bonus.
8. **Limits:** max bet with bonus, legal stake limit, expiry times for each stage.
9. **Prohibited play patterns** (list).
10. **Game data per (operator, game):** RTP read from the game's info at that operator, with date; volatility class; max win; provider; the default build where known.
11. **Bankroll state:** current bankroll, open cash at risk.

### 9.2 Decision procedure

1. **Compliance and suspicion check.** If W > 10 on bonus funds, or wagering is on deposit + bonus, or the offer mixes products on a GB site, mark it **stale/suspect** and re-fetch the terms before going further.
2. **Eligibility check.** Fail any rule in section 4 (2–4) and the value is 0. Stop.
3. **Choose the cheapest eligible games:**
   - for the qualifying stake, the lowest `h/c`;
   - for wagering, the lowest `h/c`, with volatility as a secondary key chosen by offer type in rule 6;
   - discard any game whose stored RTP is older than the refresh window (re-read it).
4. **Conservative EV (closed form):**
   - Cashable bonus: `max(0, B·(1 − W·h/c))`.
   - Sticky bonus: simulate (step 5); do not use a closed form.
   - Free spins: `N·s·RTP`, × (1 − W·h/c) if wagered; × 0.85 if capped and C < 4 × expected winnings (or simulate).
   - Bonus stake: `B·RTP` if returned, else `B·(RTP − p_win)` at the longest allowed odds.
   - Stake-and-get: `V(reward) − Q·h_q/c_q`.
   - Cashback: `−h·T + ρ·E[L⁺]` (simulate or use the single-bet formula).
   - Deposit match, cash first: `−h·E[cash turnover] + P(cash exhausted)·V(B)` (simulate).
   - Subtract nothing for tax (winnings are untaxed).
5. **Simulate strategies when step 4 is not closed-form, or bust or variance matters:**
   - candidate stakes: from the minimum up to min(max bet, legal limit, prohibited-pattern limits);
   - candidate games: volatility classes from rule 3;
   - stop rules: complete / bust / withdraw-cash-at-target;
   - outputs: EV, SD, P(bust), P(profit > 0), the full profit distribution.
6. **Pick the strategy:**
   - **Own-money qualifying play:** lowest variance (EV is fixed by `Q·h/c`).
   - **Cashable bonus-only:** maximise EV (high volatility, max allowed stake) by default. Offer a "smooth" option (low volatility, low stake) that loses little EV (about £30 vs £45 on a £50 bonus in 2.2).
   - **Sticky:** high variance; a lock-in rule (once above a target, drop to low volatility) is allowed only if stake changes are not a prohibited pattern.
   - **Cash first:** high variance on the cash with a withdraw-at-target rule, then the bonus per its type.
   - **Cashback:** fewest, longest-odds bets allowed on eligible games.
7. **Is it worth doing?**
   - Accept if EV > threshold.
   - For offers with own cash at risk, also require the Kelly log test `E[ln(1 + X/BR)] ≥ 0` (half Kelly: use BR/2) and own cash at risk ≤ 5% of the bankroll.
   - The threshold covers time (minutes needed = rounds × seconds per round) and account-wear considerations Callum may want to set. A sensible default is EV ≥ £1 and EV per hour above a set rate.
   - Offers with zero own-cash downside and EV > 0 pass the Kelly test automatically.
8. **Before any play:** confirm the opt-in is recorded, the deposit method is allowed, the planned stake ≤ max bet and the legal limit, the game is eligible, and the deadline is feasible.
9. **During play:**
   - log every stake by game, both counted wagering and actual turnover;
   - update the cash and bonus balances separately;
   - re-check max bet and eligibility at each game switch;
   - never play a prohibited pattern.
10. **When to stop:**
    - (a) wagering complete: stop **immediately**, because any further play is pure house edge; withdraw or keep the cash as a policy decision;
    - (b) bonus balance below the minimum stake: bust, record it, stop;
    - (c) cash-first policy: cash ≥ target, withdraw and forfeit the bonus;
    - (d) any terms change or ambiguity: stop and re-assess;
    - (e) deadline unreachable: finish what still adds value, otherwise abandon.
11. **After:** record the result. Update the empirical EV of prize games and the per-operator RTP table. Plot cumulative profit against cumulative EV, and flag if (profit − EV)/√Σσ² < −3, which suggests a wrong input such as an RTP, weight or stickiness assumption.
