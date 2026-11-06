import { describe, expect, it } from 'vitest'
import { betLabel, betNote, edgePct, kickoffLabel, signedGbp } from './twoup'
import { tabsFor } from './pages/Three65Page'

describe('the 2-Up tab', () => {
  it('shows edge as the price against fair', () => {
    expect(edgePct(0.023)).toBe('102.3%')
    expect(edgePct(-0.01)).toBe('99.0%')
  })

  it('labels a bet by its legs', () => {
    expect(betLabel({ legs: [{ selection: 'Arsenal', price: 2.1 }] })).toBe('Arsenal @ 2.10')
    expect(betLabel({ legs: [{ selection: 'A', price: 1.5 }, { selection: 'B', price: 2 }] })).toBe('A @ 1.50 / B @ 2.00')
    expect(betLabel({})).toBe('')
  })

  it('says what matters for each status and nothing for the rest', () => {
    expect(betNote({ status: 'placed', actual_price: 2.05 })).toBe('@ 2.05')
    expect(betNote({ status: 'price_changed', new_price: 1.9 })).toBe('now 1.90')
    expect(betNote({ status: 'failed', fail_reason: 'market suspended' })).toBe('market suspended')
    expect(betNote({ status: 'settled', outcome: 'won', return_amount: 21 })).toBe('won £21.00')
    expect(betNote({ status: 'settled', outcome: 'lost', return_amount: 0 })).toBe('lost £0.00')
    expect(betNote({ status: 'queued' })).toBe('')
    expect(betNote({ status: 'sent' })).toBe('')
  })

  it('keeps the day off a kick-off that is today', () => {
    const now = new Date('2026-11-06T10:00:00')
    expect(kickoffLabel('2026-11-06T15:00:00', now)).toMatch(/^\d\d:\d\d$/)
    expect(kickoffLabel('2026-11-07T15:00:00', now)).toMatch(/^Sat \d\d:\d\d$/)
  })

  it('signs a P&L', () => {
    expect(signedGbp(12.5)).toBe('+£12.50')
    expect(signedGbp(-3)).toBe('-£3.00')
    expect(signedGbp(0)).toBe('+£0.00')
  })

  it('puts 2-Up first on the 365 page and shows only granted tabs', () => {
    expect(tabsFor(['accabonus', 'goalscorers']).map(t => t.path)).toEqual(['2up', 'scorers'])
    expect(tabsFor(['goalscorers']).map(t => t.path)).toEqual(['scorers'])
    expect(tabsFor([])).toEqual([])
  })
})
