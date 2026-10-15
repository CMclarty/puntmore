/**
 * Acca boost — the stake's WINNINGS are boosted by a percentage that depends
 * on the number of legs: +10% for a 4-fold, +20% for a 5-fold, +30% for a
 * 6-fold (`tiers`). No refund: the acca wins or it does not. Terms confirmed
 * (Oct 2026): winnings boost, every leg 1.20+, and it ENDS at 6 folds — a
 * 7-fold or longer is not offered here, so the builder never builds one.
 *
 * Same maths as acca insurance (accaInsurance.js), with two outcomes:
 *
 *   A        = ∏ o_i            acca odds
 *   B        = (A − 1)(1 + b)   boosted winnings per £1
 *   P_win    = ∏ p_i
 *   EV       = P_win·(1 + B) − 1
 *   G(f)     = P_win·ln(1 + f·B) + (1 − P_win)·ln(1 − f)
 *
 * and an acca is scored by its growth at its own optimal (Kelly) stake,
 * max_f G(f). The boost grows with the fold but every extra leg is another
 * way to lose, so growth — unlike EV — finds the fold size worth playing.
 */
import { growthOf, trueProb } from './accaInsurance'

export const BOOST_DEFAULTS = {
  tiers: { 4: 0.10, 5: 0.20, 6: 0.30 },
  minOdds: 1.2,      // per leg (confirmed by Callum, Oct 2026)
  refOdds: 1.25,     // neutral partners a single leg is scored against
}

/** Evaluate one boosted acca from offered odds and true win probabilities. */
export function boostEval(odds, probs, cfg = {}) {
  const c = { ...BOOST_DEFAULTS, ...cfg }
  const n = odds.length
  const b = c.tiers[n] ?? 0
  const problems = []
  if (!(n in c.tiers)) problems.push(`no boost for a ${n}-fold`)
  if (odds.some(o => !(o >= c.minOdds))) problems.push(`a leg under ${c.minOdds}`)
  const accaOdds = odds.reduce((a, o) => a * o, 1)
  const won = (accaOdds - 1) * (problems.length ? 1 : 1 + b)   // boosted winnings per £1
  const pWin = probs.reduce((a, p) => a * p, 1)
  return {
    legs: n, boost: problems.length ? 0 : b, accaOdds, boostedOdds: 1 + won, pWin,
    evPct: pWin * (1 + won) - 1, qualifies: problems.length === 0, problems,
    ...growthOf([[pWin, won], [1 - pWin, -1]]),
  }
}

/** A leg in its best fold: alongside n − 1 neutral legs, for each boosted n. */
export function boostLegScore(leg, cfg = {}) {
  const c = { ...BOOST_DEFAULTS, ...cfg }
  let best = null
  for (const n of Object.keys(c.tiers).map(Number)) {
    const r = boostEval([leg.odds, ...Array(n - 1).fill(c.refOdds)],
      [trueProb(leg), ...Array(n - 1).fill(1 / c.refOdds)], c)
    if (!best || r.growth > best.growth) best = r
  }
  return best
}

/**
 * The best boosted acca from feed legs ([{matchId, odds, value, ...}]): any
 * boosted fold size, every leg at minOdds or longer, one leg per match.
 * Exhaustive over the top `pool` legs by score for each fold size.
 */
export function bestBoostAcca(candidates, cfg = {}, pool = 14) {
  const c = { ...BOOST_DEFAULTS, ...cfg }
  const perMatch = new Map()
  for (const leg of candidates) {
    if (!(leg.odds >= c.minOdds)) continue
    const scored = { ...leg, score: boostLegScore(leg, c).growth }
    const cur = perMatch.get(leg.matchId) || []
    perMatch.set(leg.matchId, [...cur, scored].sort((a, b) => b.score - a.score).slice(0, 3))
  }
  const legs = [...perMatch.values()].flat().sort((a, b) => b.score - a.score).slice(0, pool)
  const matches = new Set(legs.map(l => l.matchId)).size
  let best = null
  for (const n of Object.keys(c.tiers).map(Number)) {
    if (matches < n) continue
    const pick = [], used = new Set()
    const walk = start => {
      if (pick.length === n) {
        const r = boostEval(pick.map(l => l.odds), pick.map(trueProb), c)
        if (!best || r.growth > best.growth) best = { ...r, picks: [...pick] }
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
  }
  return best
}
