/**
 * Acca insurance: an N-fold (default 5) where every leg is at MIN_ODDS (1.20)
 * or longer, and the stake is refunded if EXACTLY one leg loses. Two or more
 * losers return nothing; all winners pay the normal acca.
 *
 * The refund is a free bet / bonus, not cash, so it is worth `refundValue` of
 * its face (default 0.75: a £50 refund is worth £37.50), and only stake up to
 * `refundCap` is insured.
 *
 * Per £1 staked, legs independent, p_i the leg's TRUE win chance:
 *
 *   A      = ∏ o_i                                   acca odds
 *   P_win  = ∏ p_i                                   all legs win
 *   P_one  = Σ_j (1 − p_j) ∏_{i≠j} p_i               exactly one loses
 *   EV     = A·P_win − 1 + refundValue · P_one · min(S, cap) / S
 *
 * p_i comes from the leg's fair odds: p = 1 / fair. The feed carries the
 * edge (value = offered / fair − 1), so p = (1 + value) / offered.
 *
 * Legs are assumed independent, which is why the builder takes at most one
 * leg per match: two legs on one game are correlated and the product rule
 * would misprice them.
 *
 * Void legs are not modelled — a void happens after the bet is struck, and
 * the terms on whether a 4-leg remainder still qualifies are unconfirmed.
 */

export const DEFAULTS = { legs: 5, minOdds: 1.2, refundValue: 0.75, refundCap: 50, stake: 10 }

export const trueProb = leg => (1 + leg.value) / leg.odds

/** The acca's numbers, per £1 staked. `legs`: [{odds, value}]. */
export function accaEv(legs, { refundValue, refundCap, stake } = DEFAULTS) {
  const ps = legs.map(trueProb)
  const A = legs.reduce((a, l) => a * l.odds, 1)
  const pWin = ps.reduce((a, p) => a * p, 1)
  let pOne = 0
  for (let j = 0; j < ps.length; j++) {
    let t = 1 - ps[j]
    for (let i = 0; i < ps.length; i++) if (i !== j) t *= ps[i]
    pOne += t
  }
  const insured = stake > 0 ? Math.min(stake, refundCap ?? stake) / stake : 1
  const ev = A * pWin - 1 + refundValue * pOne * insured
  return { odds: A, pWin, pOne, ev, qualifies: legs.every(l => l.odds >= DEFAULTS.minOdds) }
}

/**
 * One leg's score: the EV of an acca made of `n` legs like it. Not a bet you
 * would place (they would all be one match), but it ranks a single leg by
 * exactly the quantity the offer pays on — value AND how often it wins, since
 * a short winner keeps the acca inside the one-loser refund.
 */
export function legScore(leg, cfg = DEFAULTS) {
  return accaEv(Array(cfg.legs).fill(leg), cfg).ev
}

/**
 * The best qualifying acca from `candidates` ([{matchId, odds, value, ...}]):
 * `cfg.legs` legs, all at minOdds or longer, at most one per match. Searches
 * every combination of the top `pool` legs by score — 15 choose 5 is 3,003,
 * which is nothing — so the answer is exact within the pool.
 */
export function bestAcca(candidates, cfg = DEFAULTS, pool = 15) {
  const perMatch = new Map()
  for (const c of candidates) {
    if (c.odds < cfg.minOdds) continue
    const s = legScore(c, cfg)
    const cur = perMatch.get(c.matchId)
    // Only one leg per match can go in; keep that match's best few so the
    // search can still swap between them.
    perMatch.set(c.matchId, [...(cur || []), { ...c, score: s }].sort((a, b) => b.score - a.score).slice(0, 3))
  }
  const legs = [...perMatch.values()].flat().sort((a, b) => b.score - a.score).slice(0, pool)
  if (new Set(legs.map(l => l.matchId)).size < cfg.legs) return null
  let best = null
  const pick = []
  const used = new Set()
  const walk = start => {
    if (pick.length === cfg.legs) {
      const r = accaEv(pick, cfg)
      if (!best || r.ev > best.ev) best = { ...r, legs: [...pick] }
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
