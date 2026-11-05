import { describe, expect, test } from 'vitest'
import { priceBoost, verdictFor } from './boost'

describe('boost verdict', () => {
  test('a boost worth more each way says each way', () => {
    const v = verdictFor(priceBoost({ runners: 12, price: 4, boosted: 5 }))
    expect(v.word).toBe('Each way')
    expect(v.ew).toBe(true)
    expect(v.ev).toBeCloseTo(0.0592, 3)
  })
  test('a long boost in a small field says win only', () => {
    const v = verdictFor(priceBoost({ runners: 8, price: 6.5, boosted: 8 }))
    expect(v.word).toBe('Win only')
    expect(v.ew).toBe(false)
  })
  test('a boost too small to beat the overround is a skip with no mode', () => {
    const v = verdictFor(priceBoost({ runners: 12, price: 4, boosted: 4.5 }))
    expect(v).toMatchObject({ word: 'Skip', ew: null, tone: 'neg' })
  })
  test('nothing priced, no verdict', () => {
    expect(verdictFor(null)).toBeNull()
  })
})
