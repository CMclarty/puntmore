import { describe, expect, it } from 'vitest'
import { pricesStaleSince, STALE_AFTER_MS } from './staleness'

const at = (minsAgo, now) => new Date(now - minsAgo * 60_000).toISOString()

describe('pricesStaleSince', () => {
  const now = Date.UTC(2026, 10, 6, 12, 0, 0)

  it('is twice the refresh interval', () => {
    expect(STALE_AFTER_MS).toBe(20 * 60_000)
  })

  it('says nothing while the newest price is fresh', () => {
    const legs = [{ fair_odds: 2.1, fair_odds_updated_at: at(25, now) }, { fair_odds: 1.8, fair_odds_updated_at: at(5, now) }]
    expect(pricesStaleSince(legs, now)).toBeNull()
  })

  it('names the newest price time once every price is old', () => {
    const legs = [{ fair_odds: 2.1, fair_odds_updated_at: at(45, now) }, { fair_odds: 1.8, fair_odds_updated_at: at(21, now) }]
    expect(pricesStaleSince(legs, now)?.getTime()).toBe(now - 21 * 60_000)
  })

  it('ignores unpriced legs and odd timestamps', () => {
    expect(pricesStaleSince([{ fair_odds: null, fair_odds_updated_at: at(90, now) }], now)).toBeNull()
    expect(pricesStaleSince([{ fair_odds: 2, fair_odds_updated_at: 'never' }], now)).toBeNull()
    expect(pricesStaleSince([], now)).toBeNull()
    expect(pricesStaleSince(undefined, now)).toBeNull()
  })
})
