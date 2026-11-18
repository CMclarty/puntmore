import { describe, expect, test } from 'vitest'
import { boostEval, boostLegScore, bestBoostAcca, HILLS_BOOST } from './accaBoost'

const fair = (n, o) => [Array(n).fill(o), Array(n).fill(1 / o)]
const pct1 = x => Math.round(x * 1000) / 10

describe('acca boost maths (fair legs at 1.25)', () => {
  test.each([
    // legs, boost, EV % — EV = P_win·(1 + (A−1)(1+b)) − 1, worked by hand
    [4, 0.10, 5.9],
    [5, 0.20, 13.4],
    [6, 0.30, 22.1],
  ])('%s-fold boosted %s', (n, b, ev) => {
    const r = boostEval(...fair(n, 1.25))
    expect(r.boost).toBe(b)
    expect(pct1(r.evPct)).toBe(ev)
    expect(r.growth).toBeGreaterThan(0)
  })

  test('no boost outside 4-6 legs, or with a leg under the floor', () => {
    expect(boostEval(...fair(3, 1.25)).qualifies).toBe(false)
    expect(pct1(boostEval(...fair(3, 1.25)).evPct)).toBe(0)
    const r = boostEval([1.15, 1.25, 1.25, 1.25], [1 / 1.15, 0.8, 0.8, 0.8])
    expect(r.qualifies).toBe(false)
    expect(r.problems.join()).toMatch(/under 1.2/)
  })

  test('the growth is the peak of G at the Kelly stake', () => {
    const r = boostEval(...fair(5, 1.3))
    const B = r.boostedOdds - 1
    const G = f => r.pWin * Math.log(1 + f * B) + (1 - r.pWin) * Math.log(1 - f)
    expect(G(r.kelly)).toBeCloseTo(r.growth, 10)
    expect(G(r.kelly + 0.02)).toBeLessThan(r.growth)
  })
})

describe('legs and the builder', () => {
  test('a leg is scored in its best boosted fold', () => {
    const s = boostLegScore({ odds: 1.3, value: 0.03 })
    expect([4, 5, 6]).toContain(s.legs)
    expect(s.growth).toBeGreaterThan(boostLegScore({ odds: 1.3, value: 0 }).growth)
  })

  test('one leg per match, every leg 1.20+, a boosted fold size', () => {
    const legs = [1.25, 1.3, 1.22, 1.4, 1.28, 1.35, 1.24]
      .map((odds, i) => ({ matchId: 'm' + i, odds, value: 0.02 }))
      .concat([{ matchId: 'm0', odds: 1.3, value: 0.05 }, { matchId: 'm9', odds: 1.1, value: 0.2 }])
    const best = bestBoostAcca(legs)
    expect([4, 5, 6]).toContain(best.picks.length)
    expect(new Set(best.picks.map(p => p.matchId)).size).toBe(best.picks.length)
    expect(best.picks.every(p => p.odds >= 1.2)).toBe(true)
  })
})

describe("William Hill's Football Acca Boost", () => {
  const fair = n => [Array(n).fill(1.25), Array(n).fill(0.8)]
  test('the table: 3% on a double, 7.5% on five, 100% on fifteen, 200% on twenty', () => {
    for (const [n, b] of [[2, 0.03], [5, 0.075], [14, 0.5], [15, 1], [20, 2]]) {
      const r = boostEval(...fair(n), HILLS_BOOST)
      expect(r.qualifies).toBe(true)
      expect(r.boost).toBe(b)
    }
  })
  test('a single and a 21-fold are not scored', () => {
    expect(boostEval(...fair(1), HILLS_BOOST).qualifies).toBe(false)
    expect(boostEval(...fair(21), HILLS_BOOST).qualifies).toBe(false)
  })
  test('a leg under 1/5 does not qualify', () => {
    expect(boostEval([1.19, 1.5], [0.84, 0.66], HILLS_BOOST).qualifies).toBe(false)
  })
  test("a double's winnings are boosted 3%", () => {
    const r = boostEval([2, 2], [0.5, 0.5], HILLS_BOOST)
    expect(r.boostedOdds).toBeCloseTo(1 + 3 * 1.03, 10)
  })
  test('Paddy keeps its own table', () => {
    expect(boostEval(...fair(2)).qualifies).toBe(false)
    expect(boostEval(...fair(5)).boost).toBe(0.2)
  })
  test('only match goals qualify of the Hills board', () => {
    expect(HILLS_BOOST.markets).toEqual(['goals'])
  })
  test('a leg is scored in a boosted fold', () => {
    expect(boostLegScore({ odds: 1.9, value: 0.02 }, HILLS_BOOST).qualifies).toBe(true)
  })
})
