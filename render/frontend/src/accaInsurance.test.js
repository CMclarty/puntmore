import { describe, expect, test } from 'vitest'
import {
  DEFAULTS, bestAcca, kellyGrowth, legScore, breakevenProb, evaluate, evaluateEqual, grid,
  probsFromAccaPct, probsFromFair,
} from './accaInsurance'

const pct1 = x => Math.round(x * 1000) / 10          // fraction → % at 1dp
const gbp2 = x => Math.round(x * 100) / 100

describe('the offer\'s test cases (equal legs, refund worth 0.75, £50 stake)', () => {
  test.each([
    // odds, acca %, P_win %, P_one %, EV %, EV £
    [1.30, 1.00, 26.9, 40.4, 30.3, 15.15],
    [1.25, 1.00, 32.8, 41.0, 30.7, 15.36],
    [1.50, 1.10, 14.5, 34.2, 35.6, 17.81],
    [1.40, 0.90, 16.7, 36.0, 17.0, 8.49],
  ])('%s avg at %s', (o, r, pWin, pOne, evPct, evGbp) => {
    const out = evaluateEqual(o, r, { stake: 50, refundValue: 0.75 })
    expect(pct1(out.pWin)).toBe(pWin)
    expect(pct1(out.pRefund)).toBe(pOne)
    expect(pct1(out.evPct)).toBe(evPct)
    expect(gbp2(out.evGbp)).toBeCloseTo(evGbp, 1)
    expect(out.insured).toBe(true)
  })

  test('breakeven: 1.25 legs, refund worth 0.80 → true p per leg 0.7422', () => {
    expect(breakevenProb(1.25, { refundValue: 0.8 })).toBeCloseTo(0.7422, 4)
  })
})

describe('the maths', () => {
  test('unequal legs: P(exactly one loses) matches enumerating every outcome', () => {
    const odds = [1.25, 1.5, 2.1, 1.3, 3.0]
    const fair = [1.22, 1.5, 2.0, 1.33, 2.8]
    const ps = probsFromFair(fair)
    let one = 0, two = 0
    for (let m = 0; m < 32; m++) {
      let pr = 1, losers = 0
      ps.forEach((p, i) => { if (m >> i & 1) { pr *= 1 - p; losers++ } else pr *= p })
      if (losers === 1) one += pr
      if (losers >= 2) two += pr
    }
    const out = evaluate({ odds, probs: ps })
    expect(out.pOne).toBeCloseTo(one, 12)
    expect(out.pLoss).toBeCloseTo(two, 12)
    expect(out.pWin + out.pRefund + out.pLoss).toBeCloseTo(1, 12)
  })

  test('acca % is spread so the raw acca % comes back exactly', () => {
    const odds = [1.25, 1.4, 1.3, 1.5, 1.22]
    expect(evaluate({ odds, probs: probsFromAccaPct(odds, 1.07) }).rawAccaPct).toBeCloseTo(1.07, 12)
  })

  test('stake above the cap is only worth the raw acca value', () => {
    const at = evaluateEqual(1.3, 1.0, { stake: 50, refundCap: 50 })
    const over = evaluateEqual(1.3, 1.0, { stake: 100, refundCap: 50 })
    // At neutral odds the uninsured £50 is worth exactly nothing extra.
    expect(over.evGbp).toBeCloseTo(at.evGbp, 10)
  })

  test('shorter legs are better at the same acca %', () => {
    const row = GRID => GRID.map(g => g.cells.find(c => c.r === 1.0).evPct)
    const evs = row(grid())
    expect(evs[0]).toBeGreaterThan(evs[evs.length - 1])
  })
})

describe('qualification', () => {
  test('a leg under 1.20 is flagged and gets no refund', () => {
    const odds = [1.25, 1.15, 1.3, 1.3, 1.3]
    const out = evaluate({ odds, probs: probsFromAccaPct(odds, 1.0) })
    expect(out.insured).toBe(false)
    expect(out.problems.join()).toMatch(/leg 2 under 1.2/)
    expect(out.pRefund).toBe(0)
    expect(out.evPct).toBeCloseTo(0, 10)          // neutral and uninsured
  })

  test('the wrong number of legs is flagged', () => {
    const odds = [1.3, 1.3, 1.3, 1.3]
    expect(evaluate({ odds, probs: probsFromAccaPct(odds, 1) }).problems.join()).toMatch(/exactly 5 legs/)
  })

  test('a void leg drops to four legs and still qualifies by default', () => {
    const odds = [1.3, 1.3, 1.3, 1.3, 1.3]
    const probs = probsFromAccaPct(odds, 1)
    const voids = [false, false, true, false, false]
    const kept = evaluate({ odds, probs, voids })
    expect(kept.legs).toBe(4)
    expect(kept.insured).toBe(true)
    const lost = evaluate({ odds, probs, voids }, { voidQualifies: false })
    expect(lost.insured).toBe(false)
  })
})

describe('leg scores', () => {
  test('a leg is scored against neutral partners: its EV counts the edge once', () => {
    // Four fair 1.25s alongside: 2.40 +8% vs 1.25 +2% (worked in chat, Oct 2026).
    const ev = leg => evaluate({ odds: [leg.odds, 1.25, 1.25, 1.25, 1.25],
      probs: [(1 + leg.value) / leg.odds, 0.8, 0.8, 0.8, 0.8] }).evPct
    expect(pct1(ev({ odds: 2.4, value: 0.08 }))).toBe(38.7)
    expect(pct1(ev({ odds: 1.25, value: 0.02 }))).toBe(32.7)
  })

  test('growth at the optimal stake favours short legs without ignoring value', () => {
    const s = (odds, value) => legScore({ odds, value })
    expect(s(1.3, 0.04)).toBeGreaterThan(s(1.22, 0.01))      // value still counts
    expect(s(1.3, 0.04)).toBeGreaterThan(s(2.4, 0.08))       // short beats long
    expect(s(1.25, 0.02)).toBeGreaterThan(s(3.0, 0.10))
    expect(s(1.3, 0.04)).toBeCloseTo(0.0402, 3)              // 4.02% a bet (chat)
  })

  test('the optimal stake is where growth peaks', () => {
    const r = evaluateEqual(1.3, 1.0)
    const { growth, kelly } = kellyGrowth(r)
    const G = f => r.pWin * Math.log(1 + f * (r.accaOdds - 1)) + r.pRefund * Math.log(1 - f * 0.25) + r.pLoss * Math.log(1 - f)
    expect(G(kelly)).toBeCloseTo(growth, 10)
    expect(G(kelly + 0.02)).toBeLessThan(growth)
    expect(G(kelly - 0.02)).toBeLessThan(growth)
  })
})

describe('the builder', () => {
  test('one leg per match, all at 1.20+', () => {
    const legs = [
      ...[1.25, 1.3, 1.22, 1.4, 1.28, 1.35].map((odds, i) => ({ matchId: 'm' + i, odds, value: 0.01 })),
      { matchId: 'm0', odds: 1.3, value: 0.05 },   // a second leg on m0
      { matchId: 'm9', odds: 1.1, value: 0.2 },    // too short
    ]
    const best = bestAcca(legs, DEFAULTS)
    expect(best.picks).toHaveLength(5)
    expect(new Set(best.picks.map(p => p.matchId)).size).toBe(5)
    expect(best.picks.every(p => p.odds >= 1.2)).toBe(true)
    expect(best.insured).toBe(true)
  })
})
