/**
 * Acca insurance — an N-fold (default 5), every leg at `minOdds` (1.20) or
 * longer, stake refunded if EXACTLY one leg loses. Two or more losers return
 * nothing; all winners pay the normal acca.
 *
 * The refund is a free bet / bonus, not cash: it is worth `refundValue` of its
 * face (default 0.75 — a £50 refund is worth £37.50), and only stake up to
 * `refundCap` is insured.
 *
 * Maths, legs independent, p_i each leg's TRUE win chance:
 *
 *   A       = ∏ o_i                                acca odds
 *   P_win   = ∏ p_i                                all legs win
 *   P_one   = Σ_j (1 − p_j) ∏_{i≠j} p_i            exactly one loses
 *             (= P_win · Σ (1 − p_i)/p_i, summed directly so p = 0 is safe)
 *   P_loss  = 1 − P_win − P_one                    2+ lose, nothing back
 *   S_ins   = min(S, refundCap)
 *   E       = S · P_win · A + S_ins · refundValue · P_one
 *   EV £    = E − S          EV % = (E − S) / S
 *
 * True probabilities come in one of two ways:
 *   a) per-leg fair odds:   p_i = 1 / fair_i
 *   b) one "acca %" r = offered acca odds / fair acca odds (1.00 neutral,
 *      1.10 = 10% value), spread equally:  p_i = r^(1/n) / o_i
 *
 * Most of the edge is the refund: at neutral odds a 5-fold of 1.30s is about
 * +30% purely from insurance, and shorter legs are better at the same acca %
 * because more losing outcomes land in the one-loser zone. EV is linear in
 * stake, so splitting a stake adds nothing — stake each acca up to the cap;
 * above the cap a pound is only worth the raw acca value.
 */

export const DEFAULTS = {
  legs: 5,            // the offer's fold size
  minOdds: 1.2,       // every leg must be at least this
  refundValue: 0.75,  // what £1 of refund bonus is worth in cash
  refundCap: 50,      // max stake refunded
  stake: 50,
  // A void leg drops the acca to n − 1 legs. The terms are unconfirmed, so
  // by default it still qualifies; set false to treat it as losing the cover.
  voidQualifies: true,
  // The neutral partner legs a single leg is scored against (legScore).
  refOdds: 1.25,
}

/** p_i from per-leg fair odds. */
export const probsFromFair = fair => fair.map(f => 1 / f)

/** p_i from one acca % (1.00 = neutral), spread equally across the legs. */
export const probsFromAccaPct = (odds, r) => odds.map(o => Math.pow(r, 1 / odds.length) / o)

function product(xs) { return xs.reduce((a, x) => a * x, 1) }

function pExactlyOne(ps) {
  let s = 0
  for (let j = 0; j < ps.length; j++) {
    let t = 1 - ps[j]
    for (let i = 0; i < ps.length; i++) if (i !== j) t *= ps[i]
    s += t
  }
  return s
}

/**
 * Evaluate one acca.
 *   odds:   offered decimal odds per leg
 *   probs:  true win chance per leg (see probsFromFair / probsFromAccaPct)
 *   voids:  optional booleans, true for a leg that was made void
 *   cfg:    DEFAULTS overrides
 * Returns every output the tool shows, plus `problems`: why the acca does not
 * qualify (any problem means the refund is NOT counted in the EV).
 */
export function evaluate({ odds, probs, voids = [] }, cfg = {}) {
  const c = { ...DEFAULTS, ...cfg }
  const live = odds.map((o, i) => ({ o, p: probs[i], i })).filter(l => !voids[l.i])
  const voided = odds.length - live.length

  const problems = []
  if (odds.length !== c.legs) problems.push(`needs exactly ${c.legs} legs (has ${odds.length})`)
  const short = odds.map((o, i) => [o, i]).filter(([o]) => !(o >= c.minOdds))
  if (short.length) problems.push(`leg${short.length > 1 ? 's' : ''} ${short.map(([, i]) => i + 1).join(', ')} under ${c.minOdds}`)
  if (voided && !c.voidQualifies) problems.push('a void leg loses the insurance')
  const insured = problems.length === 0

  const ps = live.map(l => l.p)
  const accaOdds = product(live.map(l => l.o))
  const pWin = product(ps)
  const pOne = pExactlyOne(ps)
  const pLoss = Math.max(0, 1 - pWin - pOne)
  const sIns = insured ? Math.min(c.stake, c.refundCap) : 0
  const ret = c.stake * pWin * accaOdds + sIns * c.refundValue * pOne
  const evGbp = ret - c.stake
  return {
    legs: live.length,
    accaOdds,
    rawAccaPct: accaOdds * pWin,     // offered / fair acca odds; 1.00 = neutral
    pWin,
    pRefund: insured ? pOne : 0,
    pOne,
    pLoss: insured ? pLoss : 1 - pWin,
    evGbp,
    evPct: c.stake > 0 ? evGbp / c.stake : 0,
    insured,
    problems,
  }
}

/** Equal legs at average odds `o` and acca % `r` — the grid's cell. */
export function evaluateEqual(o, r, cfg = {}) {
  const c = { ...DEFAULTS, ...cfg }
  const odds = Array(c.legs).fill(o)
  return evaluate({ odds, probs: probsFromAccaPct(odds, r) }, c)
}

/**
 * Breakeven true win chance per leg, for equal legs at odds `o`: the p where
 * the insured acca's EV is exactly zero. EV rises with p, so bisection.
 */
export function breakevenProb(o, cfg = {}) {
  const c = { ...DEFAULTS, ...cfg }
  const ev = p => evaluate({ odds: Array(c.legs).fill(o), probs: Array(c.legs).fill(p) }, c).evGbp
  let lo = 0, hi = Math.min(1, 1 / o * 2)
  if (ev(hi) < 0) hi = 1
  for (let k = 0; k < 100; k++) {
    const mid = (lo + hi) / 2
    if (ev(mid) < 0) lo = mid; else hi = mid
  }
  return (lo + hi) / 2
}

/** Grid mode: average leg odds × acca %, EV per cell. */
export const GRID_ODDS = [1.25, 1.3, 1.35, 1.4, 1.45, 1.5]
export const GRID_PCTS = [0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2]
export function grid(cfg = {}) {
  const c = { ...DEFAULTS, stake: 50, ...cfg }
  return GRID_ODDS.map(o => ({ odds: o, cells: GRID_PCTS.map(r => ({ r, ...evaluateEqual(o, r, c) })) }))
}

// ---- The Paddy feed's legs ------------------------------------------------

/** A feed bet's true win chance: its edge is offered / fair − 1. */
export const trueProb = leg => (1 + leg.value) / leg.odds

/**
 * A feed leg's score: the growth score (kellyGrowth) of an insured acca of
 * this leg plus `legs − 1` NEUTRAL legs at `refOdds` (fair, no value) — a
 * realistic qualifying acca.
 *
 * Not "an acca of `legs` copies of this leg": that raised the leg's edge to the
 * fifth power (1.08^5 = 1.47), so a long +8% leg scored as if it were a 79/1
 * fold and buried every short one. Against neutral partners the edge counts
 * once, as it does in the acca you actually build.
 */
export function legScore(leg, cfg = {}) {
  return kellyGrowth(legAcca(leg, cfg), cfg).growth
}

/** The evaluated acca a single leg is scored in (legScore). */
export function legAcca(leg, cfg = {}) {
  const c = { ...DEFAULTS, ...cfg }
  const n = c.legs - 1
  return evaluate({
    odds: [leg.odds, ...Array(n).fill(c.refOdds)],
    probs: [trueProb(leg), ...Array(n).fill(1 / c.refOdds)],
  }, c)
}

/**
 * The acca's growth score: the expected log-growth of a bankroll staked at
 * the acca's OWN optimal (Kelly) fraction f*, over the three outcomes —
 *
 *   G(f) = P_win·ln(1 + f(A − 1)) + P_refund·ln(1 − f(1 − rv)) + P_loss·ln(1 − f)
 *
 * and the score is max_f G(f). It needs no bankroll and no risk dial: an acca
 * that mostly loses cannot be staked heavily however large its EV, so its best
 * growth is low, while a short acca that pays back (win or refund) three
 * times in four supports a large stake and grows faster. "£20 in the pocket
 * beats £22 of theoretical EV" is what log utility means.
 *
 * Returns {growth, kelly}: growth per bet at f*, and f* itself (the share of
 * a bankroll full Kelly would stake). The refund is assumed to cover the
 * stake (stake within the cap). Zero when the acca has no edge at all.
 */
export function kellyGrowth(r, cfg = {}) {
  const c = { ...DEFAULTS, ...cfg }
  const rv = r.insured ? c.refundValue : 0
  const G = f => r.pWin * Math.log(1 + f * (r.accaOdds - 1))
    + r.pRefund * Math.log(1 - f * (1 - rv)) + r.pLoss * Math.log(1 - f)
  // G is concave in f on [0, 1): ternary search for its maximum.
  let lo = 0, hi = 0.999
  for (let i = 0; i < 100; i++) {
    const m1 = lo + (hi - lo) / 3, m2 = hi - (hi - lo) / 3
    if (G(m1) < G(m2)) lo = m1; else hi = m2
  }
  const f = (lo + hi) / 2
  const g = G(f)
  return g > 0 ? { growth: g, kelly: f } : { growth: 0, kelly: 0 }
}

/**
 * The best qualifying acca from feed legs ([{matchId, odds, value, ...}]):
 * `legs` legs, every one at minOdds or longer, at most one per match (two legs
 * on one game are correlated and the product rule would misprice them).
 * Exhaustive over the top `pool` legs by score — 15 choose 5 is 3,003.
 */
export function bestAcca(candidates, cfg = {}, pool = 15) {
  const c = { ...DEFAULTS, ...cfg }
  const perMatch = new Map()
  for (const leg of candidates) {
    if (!(leg.odds >= c.minOdds)) continue
    const scored = { ...leg, score: legScore(leg, c) }
    const cur = perMatch.get(leg.matchId) || []
    perMatch.set(leg.matchId, [...cur, scored].sort((a, b) => b.score - a.score).slice(0, 3))
  }
  const legs = [...perMatch.values()].flat().sort((a, b) => b.score - a.score).slice(0, pool)
  if (new Set(legs.map(l => l.matchId)).size < c.legs) return null
  let best = null
  const pick = [], used = new Set()
  const walk = start => {
    if (pick.length === c.legs) {
      const r = evaluate({ odds: pick.map(l => l.odds), probs: pick.map(trueProb) }, c)
      const k = kellyGrowth(r, c)
      if (!best || k.growth > best.growth) best = { ...r, ...k, picks: [...pick] }
      return
    }
    for (let i = start; i < legs.length; i++) {
      if (used.has(legs[i].matchId)) continue
      used.add(legs[i].matchId); pick.push(legs[i])
      walk(i + 1)
      pick.pop(); used.delete(legs[i].matchId)
    }
  }
  walk(0)
  return best
}
