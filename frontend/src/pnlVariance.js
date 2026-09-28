/**
 * Variance simulation for the P&L ledger.
 *
 * Replays every bet that carries a stake, the price taken and the Betfair SP
 * many times over, settling each one at random with win probability 1/SP —
 * the same "SP is the fair price" reading the EV line uses. The spread of those
 * replays is the band drawn around the P&L curve: how far luck alone could have
 * moved the same bets, at the same prices.
 *
 * EACH-WAY: the place half is VOIDED — treated as stake returned, so it adds no
 * variance and no EV. Only the BFSP of the win market is recorded, so there is
 * no fair place price to settle it at. The win half (stake / 2) is simulated
 * like any win bet. To compare like with like, the bet's ACTUAL win-half result
 * is recovered from its combined return (see `winHalfResult`).
 *
 * Everything the sim can't replay — flat entries, bets with no SP, tips, the
 * place halves — is held at what actually happened, so the band is "where this
 * line could have been" and the real curve can be read straight against it.
 *
 * Seeded, so a redraw draws the same band rather than one that shimmers.
 */

export const SIMS = 2000
// At most this many points along the band: the chart has fewer pixels than a
// long ledger has bets, and sorting every step is the only costly part.
const MAX_BAND_POINTS = 240

export const canSim = b =>
  !b.is_tip && b.stake != null && b.stake > 0 && b.price_taken != null && b.bfsp != null

const winStake = b => (b.each_way ? b.stake / 2 : b.stake)

/**
 * The actual result of the part of the bet the sim replays.
 *
 * A straight bet: its net. Each-way: the win half's net, recovered from the
 * combined return — which can only be 0 (unplaced), the place return alone
 * (placed, didn't win), or place + win returns (won). Split at the midpoint of
 * the last gap so a penny of rounding can't flip it.
 */
export function winHalfResult(b) {
  if (!b.each_way) return b.amount
  const unit = b.stake / 2
  const pf = b.place_fraction ?? 0.25
  const placeRet = unit * (1 + (b.price_taken - 1) * pf)
  const winRet = unit * b.price_taken
  const won = (b.returned ?? 0) > placeRet + winRet / 2
  return won ? unit * (b.price_taken - 1) : -unit
}

// mulberry32 — small, fast, good enough for a Monte Carlo band.
function rng(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const quantile = (sorted, q) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))]

/**
 * Simulate a run of bets (in ledger order).
 *
 * Returns null when there is nothing to simulate, else:
 *   band:       [[cumIndex, p5, p25, p75, p95], ...] — cumIndex as in the
 *               chart (bet i is point i + 1), values in the chart's pounds:
 *               the sim's cumulative spread plus everything held fixed.
 *   percentile: where the real result of the simulated bets sits among the
 *               replays, 0–100 (share of replays that did no better).
 *   simmed:     bets replayed; `total`: bets that could have been.
 */
export function simulate(bets, sims = SIMS, seed = 1) {
  const n = bets.length
  const simIdx = []
  for (let i = 0; i < n; i++) if (canSim(bets[i])) simIdx.push(i)
  if (!simIdx.length) return null

  const every = Math.max(1, Math.ceil(simIdx.length / MAX_BAND_POINTS))
  const cum = new Float64Array(sims)
  const scratch = new Float64Array(sims)
  const rand = rng(seed)
  const band = []
  let fixed = 0        // actual P&L of everything the sim holds constant
  let actualSim = 0    // actual P&L of the parts it replays
  let next = 0         // pointer into simIdx

  for (let i = 0; i < n; i++) {
    const b = bets[i]
    if (next < simIdx.length && simIdx[next] === i) {
      const s = winStake(b)
      const p = 1 / b.bfsp
      const up = s * (b.price_taken - 1)
      for (let k = 0; k < sims; k++) cum[k] += rand() < p ? up : -s
      const wh = winHalfResult(b)
      actualSim += wh
      fixed += b.amount - wh
      next++
      if (next % every === 0 || next === simIdx.length) {
        scratch.set(cum); scratch.sort()
        band.push([i + 1,
          fixed + quantile(scratch, 0.05), fixed + quantile(scratch, 0.25),
          fixed + quantile(scratch, 0.75), fixed + quantile(scratch, 0.95)])
      }
    } else {
      fixed += b.amount
    }
  }

  let atOrBelow = 0
  for (let k = 0; k < sims; k++) if (cum[k] <= actualSim + 1e-9) atOrBelow++
  return {
    band,
    percentile: (atOrBelow / sims) * 100,
    simmed: simIdx.length,
    total: bets.filter(b => !b.is_tip).length,
  }
}
